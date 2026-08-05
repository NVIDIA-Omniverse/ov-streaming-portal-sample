# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import asyncio
import datetime
import http.cookies
import logging
import urllib.parse
import uuid
from math import ceil
from textwrap import dedent
from typing import Annotated, Optional

import httpx
from fastapi import (
    APIRouter, HTTPException, Query, Depends, Request, Response
)
from fastapi.params import Cookie
from pydantic import BaseModel, Field
from starlette import status
from starlette.datastructures import MutableHeaders, QueryParams
from starlette.websockets import WebSocket, WebSocketDisconnect
from tortoise.transactions import in_transaction
from websockets import ConnectionClosedError, InvalidStatus
from websockets.asyncio.client import connect
import websockets.exceptions

from app.auth import authenticated_only, User
from app.models import (
    SessionModel, SessionStatus, SessionResponse, Page, PublishedAppModel,
    AuthenticationType
)
from app.observability import metrics
from app.settings import settings

# Maximum length of an error message accepted via the PATCH endpoint or
# stored from upstream failures. Keeps the column from growing unbounded
# while still allowing a few stack frames or a verbose SDK message.
MAX_SESSION_ERROR_LENGTH = 4000

router = APIRouter()
logger = logging.getLogger(__name__)

SESSION_ORDER_FIELDS = {
    "id": "id",
    "app": "app__title",
    "user_name": "user_name",
    "start_date": "start_date",
    "end_date": "end_date",
    "duration": "duration",
}

# Statuses that represent a session that has ended (no longer running).
TERMINAL_STATUSES = (
    SessionStatus.stopped.value,
    SessionStatus.expired.value,
    SessionStatus.failed.value,
)

# Statuses that represent a session that is still running.
ALIVE_STATUSES = (
    SessionStatus.connecting.value,
    SessionStatus.active.value,
    SessionStatus.idle.value,
)

# Stores a mapping between session id and the corresponding client websocket.
session_websockets: dict[str, WebSocket] = {}


def watcher_terminal_status(session: SessionModel) -> SessionStatus:
    """Classifies an abandoned session as FAILED when an error has been
    recorded for it (typically by the streaming SDK via PATCH
    /sessions/{id}/) and as EXPIRED otherwise.
    """
    return SessionStatus.failed if session.error else SessionStatus.expired


async def watch_session_timeout():
    while True:
        logger.debug("Retrieving timed-outed sessions...")

        sessions = await SessionModel.filter(
            status__in=ALIVE_STATUSES,
            start_date__lte=(
                datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(seconds=settings.session_ttl)
            ),
        ).prefetch_related("app")

        logger.debug(f"Found {len(sessions)} timed-outed sessions.")

        for session in sessions:
            await end_session(
                session,
                reason="Timed out.",
                final_status=watcher_terminal_status(session),
            )
        await asyncio.sleep(settings.session_watch_interval)


async def watch_idle_sessions():
    while True:
        logger.debug("Check idle sessions...")

        expired_sessions = await SessionModel.filter(
            status=SessionStatus.idle.value,
            end_date__lte=(
                datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(seconds=settings.session_idle_timeout)
            )
        )
        orphaned_sessions = await SessionModel.filter(
            status=SessionStatus.idle.value,
            start_date__lte=(
                datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(seconds=settings.session_idle_timeout)
            ),
            end_date=None
        )
        sessions = expired_sessions + orphaned_sessions

        logger.debug(f"Found {len(sessions)} idle sessions.")
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        for session in sessions:
            logger.debug(f"[{session.id}] Stop as idle.")
            session.status = watcher_terminal_status(session)
            session.duration = int(((session.end_date or now) - session.start_date).total_seconds())
            await session.save(update_fields=["status", "duration"])
            metrics.emit_session_end_metrics(session)
        await asyncio.sleep(settings.session_watch_interval)


async def watch_session_data_purge():
    """Periodically deletes terminated session records older than the configured retention period."""
    while True:
        retention_days = settings.session_retention_days
        if retention_days > 0:
            cutoff = (
                datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(days=retention_days)
            )
            deleted_count = await SessionModel.filter(
                status__in=TERMINAL_STATUSES,
                start_date__lte=cutoff,
            ).delete()
            if deleted_count:
                logger.info(f"Purged {deleted_count} terminated session(s) older than {retention_days} days.")

        await asyncio.sleep(settings.session_watch_interval)


@router.head("/sessions/{session_id}/sign_in")
async def check_session(
    request: Request,
    session_id: str,
    user: Annotated[User, Depends(authenticated_only)],
):
    logger.info(
        dedent(
            f"""Check a streaming session:
                    session_id: {session_id}
            """
        )
    )

    session = await SessionModel.get_or_none(
        id=session_id,
        user_id=user.sub,
    )
    if session is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    if session.status in TERMINAL_STATUSES:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    if (
        session.status == SessionStatus.idle and
        session.end_date is not None and
        session.end_date.replace(tzinfo=datetime.timezone.utc) <= (
            datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(seconds=settings.session_ttl)
        )
    ):
        # The session has expired with no client connected; classify as FAILED
        # if an error has been recorded, otherwise as EXPIRED.
        session.status = watcher_terminal_status(session)
        await session.save(update_fields=["status"])

        return Response(
            status_code=status.HTTP_404_NOT_FOUND,
            headers=set_session_expired_cookies()
        )

    api_cookies = http.cookies.SimpleCookie()
    api_cookies["nvcf-request-id"] = session.nvcf_request_id
    api_cookies["nvcf-request-id"]["max-age"] = settings.session_ttl
    api_cookies["nvcf-request-id"]["path"] = request.url.path
    api_cookies["nvcf-request-id"]["httponly"] = "true"
    return Response(
        status_code=status.HTTP_200_OK,
        headers={
            "set-cookie": get_cookie_values(api_cookies),
        }
    )


@router.post("/sessions/")
async def start_stream(
    request: Request,
    app_id: Annotated[str, Query(description="Specifies the application ID that should be started.")],
    user: Annotated[User, Depends(authenticated_only)],
):
    assert settings.nvcf_api_key, (
        "Starfleet API Key must be configured for "
        "establishing a streaming session."
    )

    logger.info(
        dedent(
            f"""Starting a streaming session:
                        app_id: {app_id}
                """
        )
    )

    # Start a transaction to block SQLite database until this is resolved.
    # Use `select_for_update` as a lock mechanism in MySQL or PostgreSQL.
    async with in_transaction():
        # Count all other sessions of this user that are still running.
        running_instances = await SessionModel.filter(
            app_id=app_id,
            status__in=ALIVE_STATUSES,
            user_id=user.sub,
        ).count()

        if running_instances + 1 > settings.max_app_instances_count:
            logger.info(
                "Block session request - maximum number of instances reached."
            )
            return Response(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content=(
                    "Maximum number of instances reached "
                    f"({running_instances})."
                ),
            )

        app = await PublishedAppModel.get_or_none(id=app_id)
        if app is None:
            return Response(
                status_code=status.HTTP_404_NOT_FOUND,
                content="Application not found.",
            )

        url = construct_nvcf_endpoint(request.query_params)
        headers = {
            # Specify the Starfleet API Key for calling an NVCF function
            "Authorization": f"Bearer {settings.nvcf_api_key}",

            # Specify which NVCF function must be called
            "Function-ID": str(app.function_id),

            # Specify what version of the NVCF function must be called
            "Function-Version-ID": str(app.function_version_id),

            # Required NVCF header for starting a streaming session
            "X-NVCF-ABSORB": "true",

            # How long the control plane waits after a disconnect
            # before terminating the session
            "X-NVCF-RECONNECT-WINDOW-SECS": str(settings.session_idle_timeout),
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers)
                content = response.content

                if response.status_code == status.HTTP_200_OK:
                    cookies = response.headers.pop("set-cookie", "")
                    cookies = http.cookies.SimpleCookie(cookies)
                    nvcf_request_id = cookies.get("nvcf-request-id")

                    session_model = await SessionModel.create(
                        id=str(uuid.uuid4()),
                        nvcf_request_id=nvcf_request_id.value,
                        function_id=app.function_id,
                        function_version_id=app.function_version_id,
                        user_id=user.sub,
                        user_name=user.username,
                        status=SessionStatus.idle,
                        app=app,
                        duration=0,
                    )
                    session = SessionResponse.model_validate(session_model, from_attributes=True)
                    content = session.model_dump_json()

                    logger.info(f"[{session_model.id}] Started.")

                    # Return the session ID as a header for the streaming library
                    response.headers["nvcf-request-id"] = nvcf_request_id.value

                response = Response(content=content, status_code=response.status_code)
                if response.status_code == status.HTTP_200_OK:
                    metrics.session_start.add(1, metrics.attributes({
                        "session.app": session_model.app_id,
                        "session.user": user.sub,
                        "session.username": user.username,
                        "nvcf.function_id": str(app.function_id),
                        "nvcf.function_version_id": str(app.function_version_id),
                    }))

                    api_cookies = http.cookies.SimpleCookie()
                    api_cookies["nvcf-request-id"] = nvcf_request_id.value
                    api_cookies["nvcf-request-id"]["max-age"] = settings.session_ttl

                    path = request.url.path
                    if not path.endswith("/"):
                        path += "/"
                    path += session_model.id + "/sign_in"
                    api_cookies["nvcf-request-id"]["path"] = path
                    api_cookies["nvcf-request-id"]["httponly"] = "true"

                    response.headers.append("set-cookie", get_cookie_values(api_cookies))
                return response
        except httpx.TimeoutException:
            return Response(
                content=(
                    "This could be caused by network congestion or no GPUs available. "
                    "Please try again."
                ),
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
            )


# This path is used by Omniverse Web Streaming Library internally
# and must end with /sign_in.
@router.delete("/sessions/{session_id}/sign_in")
async def stop_stream(
    session_id: str,
    user: Annotated[User, Depends(authenticated_only)],
    nvcf_request_id: Annotated[str | None, Cookie(alias="nvcf-request-id")] = None,
):
    if nvcf_request_id is None:
        return Response(
            status_code=status.HTTP_400_BAD_REQUEST,
            content="Session ID is not specified.",
            headers=set_session_expired_cookies(),
        )

    session = await SessionModel.get_or_none(
        id=session_id,
        nvcf_request_id=nvcf_request_id,
    )
    if (
        session is None
        or session.user_id != user.sub
        or session.status in TERMINAL_STATUSES
    ):
        return Response(
            status_code=status.HTTP_404_NOT_FOUND,
            content=f"Session not found.",
            headers=set_session_expired_cookies()
        )
    response = await end_session(
        session,
        reason="Stopped by client.",
        final_status=SessionStatus.stopped,
    )
    response.headers.update(set_session_expired_cookies())
    return response


async def end_session(
    session: SessionModel,
    reason: str,
    final_status: SessionStatus,
) -> Response:
    """Terminates a streaming session and records its terminal status.

    `final_status` controls how the session is classified once terminated:
    `SessionStatus.stopped` for explicit user / administrator termination,
    `SessionStatus.expired` for watcher-driven (timeout / idle) cleanup.
    """
    url = construct_nvcf_endpoint()

    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"[{session.id}] Terminating session, reason: {reason}")
            response = await client.delete(
                url,
                cookies={
                    "nvcf-request-id": session.nvcf_request_id,
                },
                headers={
                    # Specify the Starfleet API Key for calling an NVCF function
                    "Authorization": f"Bearer {settings.nvcf_api_key}",

                    # Specify which NVCF function must be called
                    "Function-ID": str(session.function_id),

                    # Specify what version of the NVCF function must be called
                    "Function-Version-ID": str(session.function_version_id),

                    # Required NVCF header for stopping a streaming session
                    "X-NVCF-ABSORB": "true"
                }
            )

            if response.status_code == status.HTTP_200_OK:
                ws = session_websockets.get(session.id)
                if ws is not None:
                    await ws.close(code=3010, reason=reason)
            else:
                logger.error(f"[{session.id}] Failed to terminate session: HTTP{response.status_code} -- {response.text}.\nHeaders:\n{response.headers}")

            session.status = final_status
            session.end_date = datetime.datetime.now(tz=datetime.timezone.utc)
            session.duration = int((session.end_date - session.start_date).total_seconds())
            await session.save(update_fields=["status", "end_date", "duration"])

            metrics.emit_session_end_metrics(session)

        if response.status_code == status.HTTP_404_NOT_FOUND:
            # The session with the specified nvcf_request_id does not exist,
            # ignore the error.
            return Response(
                status_code=status.HTTP_204_NO_CONTENT,
                headers=set_session_expired_cookies(),
            )

        response.headers.update(set_session_expired_cookies())
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=response.headers,
        )
    except httpx.TimeoutException:
        return Response(
            content="Failed to terminate session with a timeout -- try again later.",
            status_code=status.HTTP_408_REQUEST_TIMEOUT
        )


# This path is used by Omniverse Web Streaming Library internally
# and must end with /sign_in.
@router.websocket(
    "/sessions/{session_id}/sign_in",
)
async def connect_to_stream(
    websocket: WebSocket,
    session_id: str,
    user: Annotated[User, Depends(authenticated_only)],
):
    """
    This endpoint is called by the client for establishing
    the signaling connection and exchanging WebRTC information with the
    Kit container.

    The connection must be forwarded to NVCF with the Authorization header
    that includes the API key, and Function-ID and Function-Version-ID headers
    to specify which container needs to be invoked.

    :param websocket:
    :param session_id: Specifies the ID of previously created session.
    :param user:
    :return:
    """

    assert settings.nvcf_api_key, (
        "Starfleet API Key must be configured for "
        "establishing a streaming session."
    )

    await websocket.accept(
        # Must send the sub-protocol back to the client to accept the connection
        subprotocol=websocket.headers.get("sec-websocket-protocol"),
    )

    nvcf_request_id = websocket.cookies.get("nvcf-request-id")
    if not nvcf_request_id:
        await websocket.close(
            code=3000,
            reason="The session has expired."
        )
        return

    session = await SessionModel.all().prefetch_related("app").get_or_none(
        id=session_id,
        nvcf_request_id=nvcf_request_id,
        user_id=user.sub,
    )
    if session is None or session.status in TERMINAL_STATUSES:
        # If session is not found or has already ended, close the connection.
        await websocket.close(
            code=3004,
            reason="Session with the specified ID not found."
        )
        return

    if session.status == SessionStatus.active:
        # Disallow more than one websocket connection to the same session.
        await websocket.close(
            code=3005,
            reason="Client is connected already."
        )
        return

    if session.app_id is None:
        await websocket.close(
            code=3006,
            reason="Application associated with this session is no longer available."
        )
        return

    app: PublishedAppModel = session.app

    # Store the websocket connection in memory
    # to let other endpoints terminate it
    session_websockets[session.id] = websocket

    logger.info(
        dedent(
            f"""Connecting to a streaming session:
                    session_id: {session.id}
                    function_id: {app.function_id}
                    function_version_id: {app.function_version_id}
                    nvcf_request_id: {nvcf_request_id} 
            """
        )
    )

    code, reason = 1000, None
    upstream_failed = False
    upstream_error: Optional[str] = None
    try:
        session.status = SessionStatus.connecting

        # Reset the end date and any previously reported error so the new
        # streaming attempt starts from a clean slate.
        session.end_date = None
        session.error = None
        session.duration = int(
            (datetime.datetime.now(tz=datetime.timezone.utc) - session.start_date).total_seconds()
        )
        # Restrict the save to the columns we just mutated; PATCH
        # /sessions/{id}/ requests landing later are then free to record a
        # new client-side error without being clobbered.
        await session.save(update_fields=["status", "end_date", "error", "duration"])

        query_params = {**websocket.query_params}
        query = urllib.parse.urlencode(query_params)

        # The StreamSDK inside the Kit application listens for
        # incoming WebSocket connections on /sign_in endpoint.
        url = f"{settings.nvcf_signaling_endpoint}/sign_in?{query}"
        logger.info(f"[{session.id}] Connecting to NVCF: {url}")

        headers = {
            # Specify the Starfleet API Key for calling an NVCF function
            "Authorization": f"Bearer {settings.nvcf_api_key}",

            # Specify which NVCF function must be called
            "Function-ID": app.function_id,

            # Specify what version of the NVCF function must be called
            "Function-Version-ID": app.function_version_id,

            # Specify the streaming session ID in cookies
            "Cookie": f"nvcf-request-id={nvcf_request_id}",

            # How long the control plane waits after a disconnect
            # before terminating the session
            "X-NVCF-RECONNECT-WINDOW-SECS": str(settings.session_idle_timeout),
        }

        logger.debug(f"[{session.id}] WebSocket cookies: {websocket.cookies}")
        logger.debug(f"[{session.id}] WebSocket headers: {websocket.headers}")

        if app.authentication_type == AuthenticationType.openid.value:
            # Specify the access token received from the IdP
            headers["Nucleus-Token"] = user.access_token
        elif app.authentication_type == AuthenticationType.nucleus.value:
            # Specify the Nucleus access token
            # to let the stream connect to a Nucleus server
            headers["Nucleus-Token"] = websocket.cookies.get("nucleus_token")

        logger.debug(f"[{session.id}] Headers: {headers}")
        async with connect(
            url,
            additional_headers=headers,
            user_agent_header=websocket.headers.get("user-agent"),
        ) as forward_to:
            logger.info(f"[{session.id}] Connected.")

            session.status = SessionStatus.active
            await session.save(update_fields=["status"])

            metrics.active_sessions.add(1, metrics.attributes({
                "session.app": session.app_id,
                "session.user": user.sub,
                "session.username": user.username,
                "nvcf.function_id": str(app.function_id),
                "nvcf.function_version_id": str(app.function_version_id),
            }))

            async def consumer():
                """
                Consumes messages from the client and sends them to NVCF.
                """
                nonlocal code, reason, upstream_failed, upstream_error
                try:
                    while True:
                        msg = await websocket.receive_text()
                        await forward_to.send(msg)
                except WebSocketDisconnect as err:
                    code, reason = err.code, err.reason
                    raise
                except ConnectionClosedError as err:
                    code, reason = upstream_close_frame(err)
                    if code != 1000:
                        upstream_failed = True
                        upstream_error = format_upstream_close_error(err)
                    raise

            async def producer():
                """
                Receives messages from NVCF and sends them to the client.
                """
                nonlocal code, reason, upstream_failed, upstream_error
                try:
                    while True:
                        msg = await forward_to.recv()
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except WebSocketDisconnect as err:
                    code, reason = err.code, err.reason
                    raise
                except ConnectionClosedError as err:
                    code, reason = upstream_close_frame(err)
                    if code != 1000:
                        upstream_failed = True
                        upstream_error = format_upstream_close_error(err)
                    raise

            # Start consuming and producing client messages in parallel
            consumer_task = asyncio.create_task(consumer())
            producer_task = asyncio.create_task(producer())

            # Wait until any of the task will fail.
            # Closing a WebSocket connection will raise a connection close error
            done, pending = await asyncio.wait(
                [consumer_task, producer_task],
                return_when=asyncio.FIRST_EXCEPTION,
            )

            # Cancel all tasks that didn't raise a close exception
            for task in pending:
                task.cancel()
    except InvalidStatus as error:
        body = error.response.body.decode()
        code, reason = 3001, body
        upstream_failed = True
        upstream_error = (
            f"NVCF rejected the streaming connection (HTTP "
            f"{error.response.status_code}): {body}"
        )
        logger.error(f"[{session.id}] Disconnected with invalid status: {error.response.status_code} -- {error.response.body}.\nHeaders:\n{error.response.headers}")
    except WebSocketDisconnect as disconnect:
        # Handle exceptions that can be raised during the handshake
        code, reason = disconnect.code, disconnect.reason
        logger.error(f"[{session.id}] Disconnected with an error: {code} -- {reason}.")
    except TimeoutError:
        code, reason = 3008, "Failed to connect a streaming session with a timeout -- try again later."
        upstream_failed = True
        upstream_error = reason
        logger.error(f"[{session.id}] Disconnected with a timeout.")
    finally:
        reason_msg = f" ({reason})" if reason else ""
        logger.info(f"[{session.id}] Closed with code {code}{reason_msg}.")

        if session.status == SessionStatus.active:
            metrics.active_sessions.add(-1, metrics.attributes({
                "session.app": session.app_id,
                "session.user": user.sub,
                "session.username": user.username,
                "nvcf.function_id": str(app.function_id),
                "nvcf.function_version_id": str(app.function_version_id),
            }))

        if upstream_failed:
            # Upstream (NVCF / streaming application) closed the connection
            # abnormally or rejected the handshake; mark the session FAILED
            # and emit terminal session metrics.
            session.status = SessionStatus.failed
            session.end_date = datetime.datetime.now(tz=datetime.timezone.utc)
            session.duration = int((session.end_date - session.start_date).total_seconds())
            await session.save(update_fields=["status", "end_date", "duration"])
            if upstream_error:
                # Atomic conditional update preserves a more specific error
                # already reported by the client (e.g. a WebRTC failure) over
                # the generic backend view of the close. The in-memory
                # `session.error` cannot be trusted here because PATCH
                # /sessions/{id}/ may have written to the row after this
                # handler loaded the session.
                truncated = upstream_error[:MAX_SESSION_ERROR_LENGTH]
                affected = await SessionModel.filter(
                    id=session.id, error__isnull=True,
                ).update(error=truncated)
                if affected:
                    session.error = truncated
            metrics.emit_session_end_metrics(session)
        elif not (3000 <= code < 4000):
            # Client closed the connection. The idle watcher will eventually
            # promote this to EXPIRED. API-driven closes (3000-3999) have
            # already updated the session status.
            session.status = SessionStatus.idle
            session.end_date = datetime.datetime.now(tz=datetime.timezone.utc)
            session.duration = int((session.end_date - session.start_date).total_seconds())
            await session.save(update_fields=["status", "end_date", "duration"])

        # Remove websocket from memory
        session_websockets.pop(session.id, None)
        try:
            # Try to close a client connection if it's not closed yet.
            await websocket.close(code, reason)
        except (RuntimeError, websockets.exceptions.ProtocolError):
            pass


def upstream_close_frame(err: ConnectionClosedError) -> tuple[int, str]:
    """Returns the (code, reason) carried by the upstream close frame.

    Falls back to WebSocket abnormal-closure code 1006 with an empty reason
    when the peer dropped the connection without sending a close frame.
    """
    if err.rcvd is None:
        return 1006, ""
    return err.rcvd.code, err.rcvd.reason


def format_upstream_close_error(err: ConnectionClosedError) -> str:
    """Builds a human-readable error message for an abnormal upstream close."""
    code, reason = upstream_close_frame(err)
    return (
        f"NVCF closed the streaming connection "
        f"(code {code}): {reason or 'no reason provided'}"
    )


def construct_nvcf_endpoint(query: QueryParams = None) -> str:
    endpoint = settings.nvcf_signaling_endpoint
    if endpoint.startswith("ws"):
        endpoint = "http" + endpoint[2:]
    return f"{endpoint}/sign_in?{query if query else ''}"


def encode_cookies(cookies: http.cookies.SimpleCookie) -> bytes:
    return get_cookie_values(cookies).encode()


def get_cookie_values(cookies: http.cookies.SimpleCookie) -> str:
    return str(cookies.output(header="")).strip()


def set_session_expired_cookies() -> MutableHeaders:
    api_cookies = http.cookies.SimpleCookie()
    api_cookies["nvcf-request-id"] = ""
    api_cookies["nvcf-request-id"]["expires"] = 0

    headers = MutableHeaders()
    headers.append("set-cookie", get_cookie_values(api_cookies))
    return headers


@router.get(
    "/sessions/",
    description="Returns all streaming sessions.",
    response_model=Page[SessionResponse],
)
async def get_sessions(
    user: Annotated[User, Depends(authenticated_only)],
    filtered_status: Annotated[SessionStatus, Query(alias="status")] = None,
    filtered_app: Annotated[str, Query(alias="app_id")] = None,
    own: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=5)] = 25,
    order_by: Annotated[str, Query()] = "-start_date",
):
    descending = order_by.startswith("-")
    field_name = order_by.lstrip("-")

    if field_name not in SESSION_ORDER_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_by field: '{field_name}'. "
                   f"Allowed fields: {', '.join(SESSION_ORDER_FIELDS.keys())}",
        )

    queryset = SessionModel.all()

    if filtered_status:
        if filtered_status == SessionStatus.alive:
            # "alive" is a special status that represents sessions that are
            # either in "connecting", "active" or "idle" statuses
            queryset = queryset.filter(status__in=[
                SessionStatus.connecting.value,
                SessionStatus.active.value,
                SessionStatus.idle.value,
            ])
        else:
            queryset = queryset.filter(status=filtered_status.value.upper())

    if filtered_app:
        queryset = queryset.filter(app=filtered_app)

    if own or not await user.is_admin():
        # Regular users can only see their own sessions.
        # Administrators opt into the same scope by passing `own`.
        queryset = queryset.filter(user_id=user.sub)

    total_size = await queryset.count()

    order_prefix = "-" if descending else ""
    queryset = queryset.order_by(f"{order_prefix}{SESSION_ORDER_FIELDS[field_name]}")

    results = await (
        queryset.offset((page - 1) * page_size)
        .limit(page_size)
        .prefetch_related("app")
    )

    items = [
        SessionResponse.model_validate(result, from_attributes=True)
        for result in results
    ]

    return Page(
        items=items,
        page=page,
        page_size=len(items),
        total_pages=ceil(total_size / page_size),
        total_size=total_size,
    )


@router.get(
    "/sessions/{session_id}",
    description="Returns the streaming session with the specified session ID.",
    response_model=SessionResponse,
)
async def get_session(
    user: Annotated[User, Depends(authenticated_only)],
    session_id: str,
):
    session = await query_session(user, session_id)
    if isinstance(session, Response):
        return session

    return SessionResponse.model_validate(session, from_attributes=True)


class SessionUpdateRequest(BaseModel):
    """Body of the PATCH /sessions/{session_id}/ endpoint."""

    error: Optional[str] = Field(
        default=None,
        max_length=MAX_SESSION_ERROR_LENGTH,
        description=(
            "Last error observed for the session by the client (e.g. a "
            "WebRTC failure surfaced by the streaming SDK). Pass `null` "
            "to clear a previously reported error."
        ),
    )


@router.patch(
    "/sessions/{session_id}/",
    description=(
        "Updates the streaming session with the specified ID. Currently "
        "supports recording the last error reported by the client."
    ),
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_session(
    user: Annotated[User, Depends(authenticated_only)],
    session_id: str,
    body: SessionUpdateRequest,
):
    session = await query_session(user, session_id)
    if isinstance(session, Response):
        return session

    if "error" in body.model_fields_set:
        session.error = body.error
        await session.save(update_fields=["error"])

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/sessions/{session_id}",
    description=(
        "Destroys the session with the specified ID."
    ),
    status_code=status.HTTP_204_NO_CONTENT,
)
async def terminate_session(
    user: Annotated[User, Depends(authenticated_only)],
    session_id: str
):
    session = await query_session(user, session_id)
    if isinstance(session, Response):
        return session

    if session.status in TERMINAL_STATUSES:
        # The session has already ended, ignore the request and clear cookies.
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers=set_session_expired_cookies(),
        )
    reason = (
        "Terminated by the user."
        if session.user_id == user.sub
        else "Terminated by a system administrator."
    )
    return await end_session(
        session,
        reason=reason,
        final_status=SessionStatus.stopped,
    )


async def query_session(
    user: Annotated[User, Depends(authenticated_only)],
    session_id: str
):
    session = await SessionModel.all().prefetch_related("app").get_or_none(id=session_id)
    if session is None:
        return Response(
            status_code=status.HTTP_404_NOT_FOUND,
            content=f"Session with id {session_id} was not found.",
        )
    if session.user_id != user.sub and not await user.is_admin():
        # 404 is returned instead of 403 to avoid information leakage
        return Response(
            status_code=status.HTTP_404_NOT_FOUND,
            content=f"Session with id {session_id} was not found.",
        )
    return session
