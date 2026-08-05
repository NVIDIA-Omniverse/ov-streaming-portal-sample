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
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
import websockets
from fastapi import Response
from freezegun import freeze_time
from tortoise.signals import Signals
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve, ServerConnection

from app.models import SessionModel, SessionStatus, PublishedAppModel
from app.routers.sessions import construct_nvcf_endpoint
from app.settings import settings

HOST = "127.0.0.1:12340"
FUNCTION_ID = "20e5086a-832a-43a6-87c9-6784e2d1e4bd"
FUNCTION_VERSION_ID = "ba4e628b-975b-4e28-9e06-492016a94ec7"
SESSION_ID = "14e74c6a-38c8-427e-88de-d8847ffea5af"
SESSION_PATH = f"/sessions/{SESSION_ID}/sign_in"
SESSION_URL = f"ws://{HOST}{SESSION_PATH}"
SESSION_NVCF_REQUEST_ID = "af2698db-0a8f-4701-a1a4-f14ad8b242b5"


@pytest.fixture
def nvcf_endpoint():
    original_nvcf_signaling_endpoint = settings.nvcf_signaling_endpoint
    settings.nvcf_signaling_endpoint = f"ws://127.0.0.1:12345"
    yield settings.nvcf_signaling_endpoint
    settings.nvcf_signaling_endpoint = original_nvcf_signaling_endpoint


@pytest.fixture
def session_timeout():
    session_ttl = settings.session_ttl
    settings.session_ttl = 1

    session_watch_interval = settings.session_watch_interval
    settings.session_watch_interval = 1

    session_idle_timeout = settings.session_idle_timeout
    settings.session_idle_timeout = 1

    yield settings.session_ttl
    settings.session_ttl = session_ttl
    settings.session_watch_interval = session_watch_interval
    settings.session_idle_timeout = session_idle_timeout


@pytest.fixture
def nvcf_server(nvcf_endpoint):
    @asynccontextmanager
    async def start_nvcf(accept, *, process_request = None):
        async with serve(accept, "localhost", 12345, process_request=process_request):
            yield

    return start_nvcf


@pytest.fixture
async def websocket_server(database):
    config = uvicorn.Config(
        "app.main:api",
        host="127.0.0.1",
        port=12340,
        log_level="debug",
        reload=False,
        ws_ping_interval=None,
        ws_ping_timeout=None,
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    server.should_exit = True

    await server.serve()
    try:
        yield server
    finally:
        await server.shutdown()


@pytest.fixture
def create_session():
    count = 0
    sentinel = object()

    async def create(
        session_id: str = None,
        status: SessionStatus = SessionStatus.idle,
        app: PublishedAppModel | None = None,
        user_id: str = "omniverse",
        nvcf_request_id: str = SESSION_NVCF_REQUEST_ID,
        start_date: datetime.datetime | None = sentinel,
        end_date: datetime.datetime | None = sentinel,
        error: str | None = None,
    ):
        nonlocal count
        count += 1

        if session_id is None:
            session_id = str(uuid.uuid4())

        if start_date is sentinel:
            start_date = (
                datetime.datetime.now(datetime.timezone.utc) +
                datetime.timedelta(seconds=count)
            )

        if end_date is sentinel:
            end_date = (
                datetime.datetime.now(datetime.timezone.utc) +
                datetime.timedelta(seconds=count)
            )

        return await SessionModel.create(
            id=session_id,
            function_id=FUNCTION_ID,
            function_version_id=FUNCTION_VERSION_ID,
            nvcf_request_id=nvcf_request_id,
            user_id=user_id,
            user_name=user_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            app=app,
            error=error,
        )

    return create


@pytest.fixture
def create_app():
    async def create(
        function_id: str = FUNCTION_ID,
        function_version_id: str = FUNCTION_VERSION_ID,
    ):
        return await PublishedAppModel.create(
            id=str(uuid.uuid4()),
            slug="test-app",
            function_id=function_id,
            function_version_id=function_version_id,
            title="Test App",
            description="",
            version="1.0.0",
            image="https://www.python.org/static/img/python-logo@2x.png",
            icon="https://www.python.org/static/favicon.ico",
            page="",
            category="",
            product_area="",
        )

    return create


@pytest.fixture
async def cookies(user_token, create_session, create_app):
    app = await create_app()
    session = await create_session(
        app=app,
        session_id=SESSION_ID,
        status=SessionStatus.idle
    )
    return f"id_token={user_token}; nvcf-request-id={session.nvcf_request_id}"


@freeze_time("2024-09-03 23:00:00")
async def test_session_start(nvcf_endpoint, respx_mock, user_client, create_app):
    nvcf_endpoint_http = construct_nvcf_endpoint()
    nvcf = respx_mock.post(nvcf_endpoint_http).respond(cookies={
        "nvcf-request-id": "test-id"
    })

    app = await create_app()

    response = await user_client.post("/sessions/", params={"app_id": str(app.id)})
    assert response.status_code == 200

    session = response.json()
    session_path = f"/sessions/{session['id']}/sign_in"
    nvcf_request_id = response.cookies.get(
        "nvcf-request-id",
        path=session_path
    )
    assert nvcf_request_id == "test-id"
    assert nvcf.called

    session = await SessionModel.get(
        function_id=FUNCTION_ID,
        function_version_id=FUNCTION_VERSION_ID,
        user_id="omniverse",
    )
    assert session.status == SessionStatus.idle
    assert str(session.start_date) == "2024-09-03 23:00:00+00:00"


async def test_session_start_too_many_sessions(nvcf_endpoint, respx_mock, user_client, create_app):
    nvcf_endpoint_http = construct_nvcf_endpoint()
    nvcf = respx_mock.post(nvcf_endpoint_http).respond(cookies={
        "nvcf-request-id": "test-id"
    })

    app = await create_app()

    for _ in range(0, settings.max_app_instances_count):
        response = await user_client.post("/sessions/", params={
            "app_id": str(app.id),
        })
        assert response.status_code == 200

    response = await user_client.post("/sessions/", params={
        "app_id": str(app.id),
    })
    assert response.status_code == 429

    assert response.text == f"Maximum number of instances reached ({settings.max_app_instances_count})."
    assert nvcf.call_count == settings.max_app_instances_count


async def test_session_start_unknown_app(
    user_client
):
    response = await user_client.post("/sessions/", params={"app_id": "test"})
    assert response.status_code == 404
    assert response.text == "Application not found."


async def test_session_start_timeout(
    nvcf_endpoint, respx_mock, user_client, create_app
):
    nvcf_endpoint_http = construct_nvcf_endpoint()
    nvcf = respx_mock.post(nvcf_endpoint_http).mock(side_effect=httpx.TimeoutException)
    
    app = await create_app()
    response = await user_client.post("/sessions/", params={"app_id": str(app.id)})
    assert response.status_code == 408
    assert response.text == "This could be caused by network congestion or no GPUs available. Please try again."
    assert nvcf.called


@freeze_time("2024-09-03 23:00:00")
async def test_session_sign_in(
    nvcf_server, websocket_server, cookies
):
    async def accept(websocket: ServerConnection):
        await websocket.send("SYN")
        recv = await websocket.recv()
        if recv == "ACK":
            await websocket.send("SYN ACK")

    async with nvcf_server(accept):
        async with connect(
            SESSION_URL, additional_headers={"Cookie": cookies}
        ) as ws:
            msg = await ws.recv()
            assert msg == "SYN"

            await ws.send("ACK")
            msg = await ws.recv()
            assert msg == "SYN ACK"

            session = await SessionModel.get(
                function_id=FUNCTION_ID,
                function_version_id=FUNCTION_VERSION_ID,
            )
            assert session.id is not None
            assert session.status == SessionStatus.active

            await ws.close()
            session = await SessionModel.get(
                function_id=FUNCTION_ID,
                function_version_id=FUNCTION_VERSION_ID,
            )
            assert session.status == SessionStatus.idle
            assert str(session.end_date) == "2024-09-03 23:00:00+00:00"


@freeze_time("2024-09-03 23:00:00")
async def test_session_sign_in_reconnect(
    nvcf_server, websocket_server, cookies
):
    async def accept(websocket: ServerConnection):
        await websocket.send("SYN")
        recv = await websocket.recv()
        if recv == "ACK":
            await websocket.send("SYN ACK")

    async with nvcf_server(accept):
        async with connect(
            SESSION_URL, additional_headers={"Cookie": cookies}
        ) as ws:
            msg = await ws.recv()
            assert msg == "SYN"

            await ws.send("ACK")
            msg = await ws.recv()
            assert msg == "SYN ACK"

            await ws.close()

        async with connect(
            SESSION_URL, additional_headers={"Cookie": cookies}
        ) as ws:
            msg = await ws.recv()
            assert msg == "SYN"

            await ws.send("ACK")
            msg = await ws.recv()
            assert msg == "SYN ACK"

            session = await SessionModel.get(
                function_id=FUNCTION_ID,
                function_version_id=FUNCTION_VERSION_ID,
            )
            assert session.id is not None
            assert session.status == SessionStatus.active
            assert session.end_date is None

            await ws.close()
            session = await SessionModel.get(
                function_id=FUNCTION_ID,
                function_version_id=FUNCTION_VERSION_ID,
            )
            assert session.status == SessionStatus.idle
            assert str(session.end_date) == "2024-09-03 23:00:00+00:00"


async def test_session_sign_in_reconnect_clears_error(
    nvcf_server, websocket_server, cookies
):
    """A session with a previously reported error must start the next
    streaming attempt from a clean slate."""
    active_event = asyncio.Event()

    async def listener(model, instance: SessionModel, *args):
        if instance.status == SessionStatus.active:
            active_event.set()

    SessionModel.register_listener(Signals.post_save, listener)

    async def accept(websocket: ServerConnection):
        await websocket.send("SYN")
        await websocket.recv()

    async with nvcf_server(accept):
        # First connection populates the session row.
        async with connect(
            SESSION_URL, additional_headers={"Cookie": cookies}
        ) as ws:
            assert await ws.recv() == "SYN"
            await ws.send("ACK")
            await ws.close()

        await SessionModel.filter(id=SESSION_ID).update(
            error="ICE failed; closing tab.",
        )

        active_event.clear()
        async with connect(
            SESSION_URL, additional_headers={"Cookie": cookies}
        ) as ws:
            assert await ws.recv() == "SYN"
            await asyncio.wait_for(active_event.wait(), 5)

            session = await SessionModel.get(
                function_id=FUNCTION_ID,
                function_version_id=FUNCTION_VERSION_ID,
            )
            assert session.status == SessionStatus.active
            assert session.error is None

            await ws.send("ACK")
            await ws.close()


async def test_session_sign_in_server_close_abnormally(
    nvcf_server, websocket_server, cookies
):
    async def accept(websocket: ServerConnection):
        await websocket.close(code=1011, reason="Test error.")

    async with nvcf_server(accept):
        with pytest.raises(websockets.exceptions.ConnectionClosedError):
            async with connect(
                SESSION_URL, additional_headers={"Cookie": cookies}
            ) as ws:
                await ws.recv()

        session = await SessionModel.get(
            function_id=FUNCTION_ID,
            function_version_id=FUNCTION_VERSION_ID,
        )
        assert session.status == SessionStatus.failed
        assert session.error is not None
        assert "1011" in session.error
        assert "Test error." in session.error


async def test_session_sign_in_server_close_preserves_client_reported_error(
    nvcf_server, websocket_server, cookies
):
    """Verifies that a client-reported error already persisted on the session
    (e.g. via a PATCH /sessions/{id}/ from the streaming SDK) is not
    overwritten by the upstream-close error produced when the websocket
    handler tears the session down."""

    client_error = "STUN requests timed out before NVCF closed the stream."

    async def accept(websocket: ServerConnection):
        # Wait until the server-side handler has loaded the session, then
        # simulate the PATCH /sessions/{id}/ writing a client error to the
        # row before forcibly closing the upstream connection.
        await SessionModel.filter(id=SESSION_ID).update(error=client_error)
        await websocket.close(code=1011, reason="Test error.")

    async with nvcf_server(accept):
        with pytest.raises(websockets.exceptions.ConnectionClosedError):
            async with connect(
                SESSION_URL, additional_headers={"Cookie": cookies}
            ) as ws:
                await ws.recv()

        session = await SessionModel.get(
            function_id=FUNCTION_ID,
            function_version_id=FUNCTION_VERSION_ID,
        )
        assert session.status == SessionStatus.failed
        assert session.error == client_error


async def test_session_sign_in_invalid_status(
    nvcf_server, websocket_server, cookies
):
    async def process_request(connection: ServerConnection, request: websockets.server.Request):
        return connection.respond(
            status=404,
            text="Test error.",
        )

    async def accept(websocket: ServerConnection):
        await websocket.close()

    async with nvcf_server(accept, process_request=process_request):
        with pytest.raises(websockets.exceptions.ConnectionClosedError) as err:
            async with connect(
                SESSION_URL, additional_headers={"Cookie": cookies}
            ) as ws:
                await ws.recv()

        assert err.value.rcvd.code == 3001
        assert err.value.rcvd.reason == "Test error."

        session = await SessionModel.get(
            function_id=FUNCTION_ID,
            function_version_id=FUNCTION_VERSION_ID,
        )
        assert session.status == SessionStatus.failed
        assert session.error is not None
        assert "404" in session.error
        assert "Test error." in session.error


async def test_session_sign_in_timeout(
    nvcf_server, websocket_server, cookies
):
    async def process_request(connection: ServerConnection, request: websockets.server.Request):
        await asyncio.sleep(120)

    async def accept(websocket: ServerConnection):
        await websocket.close()

    async with nvcf_server(accept, process_request=process_request):
        with pytest.raises(websockets.exceptions.ConnectionClosedError) as err:
            async with connect(
                SESSION_URL, additional_headers={"Cookie": cookies}
            ) as ws:
                await ws.recv()

        assert err.value.rcvd.code == 3008
        assert err.value.rcvd.reason == "Failed to connect a streaming session with a timeout -- try again later."

        session = await SessionModel.get(
            function_id=FUNCTION_ID,
            function_version_id=FUNCTION_VERSION_ID,
        )
        assert session.status == SessionStatus.failed
        assert session.error == (
            "Failed to connect a streaming session with a timeout -- try again later."
        )


async def test_session_sign_in_client_close_abnormally(
    nvcf_server, websocket_server, cookies
):
    stopped_event = asyncio.Event()

    async def listener(model, instance: SessionModel, *args):
        if instance.status == SessionStatus.idle:
            stopped_event.set()

    SessionModel.register_listener(Signals.post_save, listener)

    async def accept(websocket: ServerConnection):
        with pytest.raises(websockets.exceptions.ConnectionClosedError):
            await websocket.send("SYN")
            recv = await websocket.recv()
            if recv == "ACK":
                await websocket.send("SYN ACK")

    async with nvcf_server(accept):
        async with connect(
            SESSION_URL, additional_headers={"Cookie": cookies}
        ) as ws:
            msg = await ws.recv()
            assert msg == "SYN"

            await ws.send("ACK")
            await ws.close(code=1011, reason="Test error.")

    await asyncio.wait_for(stopped_event.wait(), timeout=5)

    session = await SessionModel.get(
        function_id=FUNCTION_ID,
        function_version_id=FUNCTION_VERSION_ID,
    )
    assert session.status == SessionStatus.idle


async def test_session_sign_in_connected_already(
    nvcf_server, websocket_server, cookies
):
    connected = asyncio.Event()

    async def accept(websocket: ServerConnection):
        await websocket.send("SYN")
        recv = await websocket.recv()
        if recv == "ACK":
            await websocket.send("SYN ACK")
        await websocket.recv()

    async def send():
        async with connect(
            SESSION_URL, additional_headers={"Cookie": cookies}
        ) as ws:
            recv = await ws.recv()
            assert recv == "SYN"

            await ws.send("ACK")
            recv = await ws.recv()
            assert recv == "SYN ACK"

            connected.set()
            await ws.recv()

    async with nvcf_server(accept):
        task = asyncio.create_task(send())

        await asyncio.wait_for(connected.wait(), timeout=5)
        try:
            with pytest.raises(websockets.ConnectionClosedError) as err:
                async with connect(
                    SESSION_URL, additional_headers={"Cookie": cookies}
                ) as ws:
                    await ws.recv()
            assert err.value.rcvd.code == 3005
            assert err.value.rcvd.reason == "Client is connected already."
        finally:
            task.cancel()


async def test_session_sign_in_stopped(
    nvcf_server, websocket_server, user_token, cookies
):
    async def accept(websocket: ServerConnection):
        await websocket.close(code=1011, reason="Test error.")

    async with nvcf_server(accept):
        session = await SessionModel.get(
            function_id=FUNCTION_ID,
            function_version_id=FUNCTION_VERSION_ID,
        )
        session.status = SessionStatus.stopped
        await session.save()

        with pytest.raises(websockets.exceptions.ConnectionClosedError) as err:
            async with connect(
                SESSION_URL, additional_headers={"Cookie": cookies}
            ) as ws:
                await ws.recv()

        assert err.value.rcvd.code == 3004
        assert err.value.rcvd.reason == "Session with the specified ID not found."


async def test_session_sign_in_not_found(
    nvcf_server, websocket_server, cookies
):
    async def accept(websocket: ServerConnection):
        await websocket.close(code=1011, reason="Test error.")

    session = await SessionModel.get(
        function_id=FUNCTION_ID,
        function_version_id=FUNCTION_VERSION_ID,
    )
    await PublishedAppModel.filter(id=session.app_id).delete()

    async with nvcf_server(accept):
        with pytest.raises(websockets.exceptions.ConnectionClosedError) as err:
            async with connect(
                SESSION_URL, additional_headers={"Cookie": cookies}
            ) as ws:
                await ws.recv()

        assert err.value.rcvd.code == 3006
        assert err.value.rcvd.reason == "Application associated with this session is no longer available."


async def test_session_sign_in_app_no_longer_available(nvcf_server, websocket_server, user_token, cookies):
    async def accept(websocket: ServerConnection):
        await websocket.close(code=1011, reason="Test error.")

    async with nvcf_server(accept):
        session = await SessionModel.get(
            function_id=FUNCTION_ID,
            function_version_id=FUNCTION_VERSION_ID,
        )
        session.status = SessionStatus.stopped
        await session.save()

        with pytest.raises(
            websockets.exceptions.ConnectionClosedError) as err:
            async with connect(
                SESSION_URL, additional_headers={"Cookie": cookies}
            ) as ws:
                await ws.recv()

        assert err.value.rcvd.code == 3004
        assert err.value.rcvd.reason == "Session with the specified ID not found."


async def test_session_sign_in_session_without_cookie(
    nvcf_server, websocket_server, user_token
):
    async def accept(websocket: ServerConnection):
        await websocket.close(code=1011, reason="Test error.")

    async with nvcf_server(accept):
        with pytest.raises(websockets.exceptions.ConnectionClosedError) as err:
            async with connect(
                SESSION_URL, additional_headers={"Cookie": f"id_token={user_token}"}
            ) as ws:
                await ws.recv()

        assert err.value.rcvd.code == 3000
        assert err.value.rcvd.reason == "The session has expired."


async def test_check_session(client, create_session):
    session = await create_session(session_id=SESSION_ID, status=SessionStatus.active)
    response = await client.head(SESSION_PATH)
    assert response.status_code == 200

    session_path = f"/sessions/{session.id}/sign_in"
    nvcf_request_id = response.cookies.get(
        "nvcf-request-id",
        path=session_path
    )
    assert nvcf_request_id == session.nvcf_request_id


async def test_check_session_that_does_not_exist(client):
    response = await client.head(SESSION_PATH)
    assert response.status_code == 404


async def test_check_session_that_belongs_to_another_user(client, create_session):
    await create_session(
        status=SessionStatus.active,
        user_id="test-user"
    )

    response = await client.head(SESSION_PATH)
    assert response.status_code == 404


async def test_check_stopped_session(client, create_session):
    await create_session(session_id=SESSION_ID, status=SessionStatus.stopped)

    response = await client.head(SESSION_PATH)
    assert response.status_code == 404


async def test_check_expired_session(client, create_session):
    session = await create_session(session_id=SESSION_ID, status=SessionStatus.idle)
    session.end_date = (
        datetime.datetime.now(datetime.timezone.utc) -
        datetime.timedelta(seconds=settings.session_ttl + 10)
    )
    await session.save()

    response = await client.head(SESSION_PATH)
    assert response.status_code == 404

    await session.refresh_from_db()
    assert session.status == SessionStatus.expired


async def test_check_expired_session_with_error_is_failed(client, create_session):
    session = await create_session(
        session_id=SESSION_ID,
        status=SessionStatus.idle,
        error="STUN requests timed out.",
    )
    session.end_date = (
        datetime.datetime.now(datetime.timezone.utc) -
        datetime.timedelta(seconds=settings.session_ttl + 10)
    )
    await session.save()

    response = await client.head(SESSION_PATH)
    assert response.status_code == 404

    await session.refresh_from_db()
    assert session.status == SessionStatus.failed
    assert session.error == "STUN requests timed out."


async def test_get_all_sessions(client, create_session, create_app):
    app = await create_app()

    stopped_session = await create_session(status=SessionStatus.stopped)
    active_session = await create_session(status=SessionStatus.active, app=app)
    other_user_session = await create_session(status=SessionStatus.active, app=app, user_id="other-user")
    connecting_session = await create_session(status=SessionStatus.connecting)

    response = await client.get("/sessions")
    assert response.status_code == 200

    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 4
    assert data["total_size"] == 4
    assert data["total_pages"] == 1

    for index, session in enumerate(
        [connecting_session, other_user_session, active_session, stopped_session]
    ):
        session_data = data["items"][index]
        assert session_data["id"] == session.id
        assert session_data["status"] == session.status
        if session.app is None:
            assert session_data["app"] is None
        else:
            assert session_data["app"]["id"] == session.app.id
            assert session_data["app"]["title"] == session.app.title
            assert session_data["app"]["version"] == session.app.version


async def test_get_user_sessions(user_client, create_session, create_app):
    app = await create_app()

    user_session = await create_session(status=SessionStatus.active, app=app)
    await create_session(status=SessionStatus.active, app=app, user_id="other-user")

    response = await user_client.get("/sessions")
    assert response.status_code == 200

    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert data["total_size"] == 1
    assert data["total_pages"] == 1

    session_data = data["items"][0]
    assert session_data["id"] == user_session.id
    assert session_data["status"] == user_session.status
    assert session_data["user_id"] == user_session.user_id


async def test_get_own_sessions(user_client, create_session):
    user_session = await create_session(status=SessionStatus.active)
    await create_session(status=SessionStatus.active, user_id="other-user")

    response = await user_client.get("/sessions", params={"own": "true"})
    assert response.status_code == 200

    data = response.json()
    assert data["total_size"] == 1
    assert data["items"][0]["id"] == user_session.id


async def test_get_own_sessions_excludes_other_users_for_admin(
    client, create_session
):
    admin_session = await create_session(status=SessionStatus.active)
    other_session = await create_session(
        status=SessionStatus.active, user_id="other-user"
    )

    response = await client.get("/sessions", params={"own": "true"})
    assert response.status_code == 200

    data = response.json()
    assert data["total_size"] == 1
    assert data["items"][0]["id"] == admin_session.id

    # Administrators keep seeing sessions of all users without the flag.
    response = await client.get("/sessions")
    assert response.status_code == 200

    data = response.json()
    assert data["total_size"] == 2
    assert {item["id"] for item in data["items"]} == {
        admin_session.id,
        other_session.id,
    }


async def test_get_own_alive_sessions(client, create_session):
    idle_session = await create_session(status=SessionStatus.idle)
    await create_session(status=SessionStatus.stopped)
    await create_session(status=SessionStatus.active, user_id="other-user")

    response = await client.get(
        "/sessions",
        params={"own": "true", "status": SessionStatus.alive.value},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["total_size"] == 1
    assert data["items"][0]["id"] == idle_session.id


async def test_get_own_sessions_filtered_by_app(client, create_session, create_app):
    app = await create_app()

    app_session = await create_session(status=SessionStatus.active, app=app)
    await create_session(status=SessionStatus.active)
    await create_session(
        status=SessionStatus.active, app=app, user_id="other-user"
    )

    response = await client.get(
        "/sessions",
        params={"own": "true", "app_id": str(app.id)},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["total_size"] == 1
    assert data["items"][0]["id"] == app_session.id


async def test_get_sessions_filtered_by_status(client, create_session):
    await create_session(status=SessionStatus.stopped)
    await create_session(status=SessionStatus.connecting)
    active_session = await create_session(status=SessionStatus.active)

    response = await client.get(
        "/sessions", params={"status": SessionStatus.active.value}
    )
    assert response.status_code == 200

    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert data["total_size"] == 1
    assert data["total_pages"] == 1

    assert data["items"][0]["id"] == active_session.id
    assert data["items"][0]["status"] == SessionStatus.active


async def test_get_alive_sessions(client, create_session):
    _stopped = await create_session(status=SessionStatus.stopped)
    connecting = await create_session(status=SessionStatus.connecting)
    active = await create_session(status=SessionStatus.active)
    idle = await create_session(status=SessionStatus.idle)

    response = await client.get(
        "/sessions", params={"status": SessionStatus.alive.value}
    )
    assert response.status_code == 200

    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 3

    for index, session in enumerate(
        [idle, active, connecting]
    ):
        session_data = data["items"][index]
        assert session_data["id"] == session.id
        assert session_data["status"] == session.status
        if session.app is None:
            assert session_data["app"] is None
        else:
            assert session_data["app"]["id"] == session.app.id
            assert session_data["app"]["title"] == session.app.title
            assert session_data["app"]["version"] == session.app.version


async def test_get_sessions_filtered_by_app(client, create_session, create_app):
    app1 = await create_app()
    app1_session = await create_session(status=SessionStatus.active, app=app1)

    app2 = await create_app(function_id=str(uuid.uuid4()), function_version_id=str(uuid.uuid4()))
    await create_session(status=SessionStatus.active, app=app2)

    response = await client.get(
        "/sessions", params={"app_id": app1.id}
    )
    assert response.status_code == 200

    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert data["total_size"] == 1
    assert data["total_pages"] == 1

    assert data["items"][0]["id"] == app1_session.id
    assert data["items"][0]["status"] == app1_session.status


async def test_get_sessions_pagination(client, create_session):
    pages = 3
    page_size = 5

    sessions = []
    for page_item in range(0, pages * page_size):
        session = await create_session(status=SessionStatus.stopped)
        sessions.append(session)
    sessions = list(reversed(sessions))

    page = 2
    response = await client.get(
        "/sessions", params={"page": page, "page_size": page_size}
    )
    assert response.status_code == 200

    data = response.json()
    assert data["page"] == page
    assert data["page_size"] == page_size
    assert data["total_size"] == pages * page_size
    assert data["total_pages"] == pages

    offset = (page - 1) * page_size
    for page_item_index, offset_index in enumerate(
        range(offset, offset + page_size)
    ):
        session_data = data["items"][page_item_index]
        session = sessions[offset_index]
        assert session_data["id"] == session.id


async def test_get_sessions_pagination_filtered_by_status(
    client, create_session
):
    pages = 4
    page_size = 5

    sessions = []
    for page_item in range(0, pages * page_size):
        session = await create_session(status=SessionStatus.stopped)
        sessions.append(session)

        session = await create_session(status=SessionStatus.active)
        sessions.append(session)
    sessions = list(reversed(sessions))

    page = 2
    response = await client.get(
        "/sessions",
        params={
            "page": page,
            "page_size": page_size,
            "status": SessionStatus.active.value,
        },
    )
    assert response.status_code == 200

    data = response.json()
    assert data["page"] == page
    assert data["page_size"] == page_size
    assert data["total_size"] == pages * page_size
    assert data["total_pages"] == pages

    offset = (page - 1) * page_size * 2
    for index in range(0, page_size):
        session_data = data["items"][index]
        session = sessions[offset + index * 2]
        assert session_data["id"] == session.id


async def test_get_session(client, create_session):
    session = await create_session()

    response = await client.get(f"/sessions/{session.id}")
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == session.id
    assert data["status"] == session.status
    assert data["user_id"] == session.user_id
    assert data["function_id"] == str(session.function_id)
    assert data["function_version_id"] == str(session.function_version_id)


async def test_get_session_not_found(client):
    response = await client.get(f"/sessions/test")
    assert response.status_code == 404


async def test_get_session_that_belongs_to_another_user(user_client, create_session):
    session = await create_session(user_id="other-user")

    response = await user_client.get(f"/sessions/{session.id}")
    assert response.status_code == 404


async def test_stop_session(
    nvcf_server, websocket_server, user_client, user_token,
    respx_mock, cookies
):
    connected = asyncio.Event()

    session = await SessionModel.get(id=SESSION_ID)
    assert session.status == SessionStatus.idle

    async def accept(websocket: ServerConnection):
        await websocket.send("SYN")
        await websocket.recv()

    async def send():
        async with nvcf_server(accept):
            with pytest.raises(websockets.ConnectionClosedError) as err:
                async with connect(
                    SESSION_URL,
                    additional_headers={"Cookie": f"id_token={user_token}; nvcf-request-id={SESSION_NVCF_REQUEST_ID}"}
                ) as ws:
                    syn = await ws.recv()
                    assert syn == "SYN"

                    connected.set()
                    await ws.recv()
            assert err.value.rcvd.code == 3010
            assert err.value.rcvd.reason == "Stopped by client."

    task = asyncio.create_task(send())
    await asyncio.wait_for(connected.wait(), 5)

    nvcf = respx_mock.delete(construct_nvcf_endpoint())
    response = await user_client.delete(SESSION_URL, cookies={"nvcf-request-id": SESSION_NVCF_REQUEST_ID})
    assert response.status_code == 200
    assert nvcf.called

    await session.refresh_from_db()
    assert session.status == SessionStatus.stopped

    await task


async def test_stop_session_not_found(user_client):
    response = await user_client.delete(SESSION_URL, cookies={"nvcf-request-id": "test-id"})
    assert response.status_code == 404

    assert response.text == "Session not found."


async def test_stop_session_without_cookie(user_client):
    response = await user_client.delete(SESSION_URL)
    assert response.status_code == 400

    assert response.text == "Session ID is not specified."


async def test_terminate_session(
    nvcf_server, websocket_server, client, respx_mock, cookies
):
    connected = asyncio.Event()

    async def accept(websocket: ServerConnection):
        await websocket.send("SYN")
        await websocket.recv()

    async def send():
        async with nvcf_server(accept):
            with pytest.raises(websockets.ConnectionClosedError) as err:
                async with connect(
                    SESSION_URL, additional_headers={"Cookie": cookies}
                ) as ws:
                    syn = await ws.recv()
                    assert syn == "SYN"

                    connected.set()
                    await ws.recv()
            assert err.value.rcvd.code == 3010
            assert err.value.rcvd.reason == "Terminated by the user."

    task = asyncio.create_task(send())
    await asyncio.wait_for(connected.wait(), 5)

    response = await client.get(
        "/sessions/",
        params={"status": SessionStatus.active.value}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page_size"] == 1
    session = data["items"][0]

    nvcf = respx_mock.delete(construct_nvcf_endpoint())

    response = await client.delete(f"/sessions/{session["id"]}")
    assert response.status_code == 200
    assert nvcf.called

    response = await client.get("/sessions/")
    assert response.status_code == 200
    data = response.json()
    assert data["page_size"] == 1
    session = data["items"][0]
    assert session["status"] == SessionStatus.stopped

    await task


async def test_session_timeout(
    session_timeout, nvcf_server, websocket_server, cookies, respx_mock
):
    connected = asyncio.Event()

    async def accept(websocket: ServerConnection):
        while True:
            await websocket.send("SYN")
            await websocket.recv()
            await asyncio.sleep(0.5)

    async def send():
        async with nvcf_server(accept):
            with pytest.raises(websockets.ConnectionClosedError) as err:
                async with connect(
                    SESSION_URL, additional_headers={"Cookie": cookies}
                ) as ws:
                    while True:
                        syn = await ws.recv()
                        assert syn == "SYN"

                        connected.set()
                        await ws.recv()

                        await asyncio.sleep(0.5)
            assert err.value.rcvd.code == 3010
            assert err.value.rcvd.reason == "Timed out."

    task = asyncio.create_task(send())
    await asyncio.wait_for(connected.wait(), 5)

    nvcf = respx_mock.delete(construct_nvcf_endpoint())
    await asyncio.wait_for(task, 5)
    assert nvcf.called

    timed_out = await SessionModel.get(
        function_id=FUNCTION_ID, function_version_id=FUNCTION_VERSION_ID
    )
    assert timed_out.status == SessionStatus.expired


async def test_session_stopped_after_being_idle(
    session_timeout, client, create_session
):
    expired_session = await create_session(
        status=SessionStatus.idle,
        end_date=(
            datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(seconds=session_timeout)
        ),
    )
    await asyncio.sleep(session_timeout * 2)

    await expired_session.refresh_from_db()
    assert expired_session.status == SessionStatus.expired


async def test_session_stop_orphaned_sessions(
    session_timeout, client, create_session
):
    orphaned_session = await create_session(
        status=SessionStatus.idle,
        start_date=(
            datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(seconds=session_timeout)
        ),
        end_date=None,
    )
    await asyncio.sleep(session_timeout)

    await orphaned_session.refresh_from_db()
    assert orphaned_session.status == SessionStatus.expired


async def test_idle_watcher_promotes_session_with_error_to_failed(
    session_timeout, client, create_session
):
    """Idle sessions that have an error already recorded (e.g. via PATCH
    /sessions/{id}/) must be classified as FAILED rather than EXPIRED."""
    session = await create_session(
        status=SessionStatus.idle,
        end_date=(
            datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(seconds=session_timeout)
        ),
        error="WebRTC ICE connection failed.",
    )
    await asyncio.sleep(session_timeout * 2)

    await session.refresh_from_db()
    assert session.status == SessionStatus.failed
    assert session.error == "WebRTC ICE connection failed."


async def test_idle_watcher_promotes_orphaned_session_with_error_to_failed(
    session_timeout, client, create_session
):
    session = await create_session(
        status=SessionStatus.idle,
        start_date=(
            datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(seconds=session_timeout)
        ),
        end_date=None,
        error="STUN allocation failed.",
    )
    await asyncio.sleep(session_timeout * 2)

    await session.refresh_from_db()
    assert session.status == SessionStatus.failed
    assert session.error == "STUN allocation failed."


async def test_session_timeout_with_error_is_failed(
    session_timeout, nvcf_server, websocket_server, cookies, respx_mock
):
    """The TTL watcher must classify long-running sessions as FAILED when
    the streaming SDK has already reported an error against them."""
    connected = asyncio.Event()

    async def accept(websocket: ServerConnection):
        while True:
            await websocket.send("SYN")
            await websocket.recv()
            await asyncio.sleep(0.5)

    async def send():
        async with nvcf_server(accept):
            with pytest.raises(websockets.ConnectionClosedError) as err:
                async with connect(
                    SESSION_URL, additional_headers={"Cookie": cookies}
                ) as ws:
                    while True:
                        syn = await ws.recv()
                        assert syn == "SYN"

                        connected.set()
                        await ws.recv()

                        await asyncio.sleep(0.5)
            assert err.value.rcvd.code == 3010
            assert err.value.rcvd.reason == "Timed out."

    task = asyncio.create_task(send())
    await asyncio.wait_for(connected.wait(), 5)

    # Simulate a PATCH /sessions/{id}/ from the client recording a WebRTC
    # error before the TTL watcher tears the session down.
    await SessionModel.filter(id=SESSION_ID).update(
        error="DataChannel closed unexpectedly.",
    )

    nvcf = respx_mock.delete(construct_nvcf_endpoint())
    await asyncio.wait_for(task, 5)
    assert nvcf.called

    timed_out = await SessionModel.get(
        function_id=FUNCTION_ID, function_version_id=FUNCTION_VERSION_ID
    )
    assert timed_out.status == SessionStatus.failed
    assert timed_out.error == "DataChannel closed unexpectedly."


@pytest.mark.parametrize(
    "user_id, expected_reason",
    [
        ("omniverse", "Terminated by the user."),
        ("other-user", "Terminated by a system administrator."),
    ],
)
async def test_terminate_session_reason_depends_on_ownership(
    client, create_session, mocker, user_id, expected_reason
):
    """The reason reaches users as the websocket close reason, so a session
    that users end themselves must not be reported as an administrator action.
    """
    end_session = mocker.patch("app.routers.sessions.end_session")
    end_session.return_value = Response(status_code=204)

    session = await create_session(status=SessionStatus.active, user_id=user_id)

    response = await client.delete(f"/sessions/{session.id}")
    assert response.status_code == 204

    assert end_session.await_args.kwargs["reason"] == expected_reason


async def test_terminate_session_is_unauthorized_for_anonymous_user(client):
    del client.cookies["id_token"]

    response = await client.delete("/sessions/test")
    assert response.status_code == 401


async def test_terminate_session_returns_404_if_session_belongs_to_another_user(
    user_client, user_token, create_session
):
    session = await create_session(status=SessionStatus.active, user_id="other_user")

    response = await user_client.delete(f"/sessions/{session.id}")
    assert response.status_code == 404


async def test_terminate_session_not_found(client):
    response = await client.delete("/sessions/test")
    assert response.status_code == 404


async def test_terminate_session_no_longer_available(client, create_session, respx_mock):
    nvcf = respx_mock.delete(construct_nvcf_endpoint()).respond(
        status_code=404
    )

    session = await create_session()
    response = await client.delete(f"/sessions/{session.id}")
    assert response.status_code == 204

    await session.refresh_from_db()
    assert session.status == SessionStatus.stopped

    assert nvcf.called


@pytest.mark.parametrize(
    "terminal_status",
    [SessionStatus.stopped, SessionStatus.expired, SessionStatus.failed],
)
async def test_terminate_session_already_ended(
    client, create_session, terminal_status,
):
    session = await create_session(status=terminal_status)

    response = await client.delete(f"/sessions/{session.id}")
    assert response.status_code == 204


async def test_update_session_records_error(user_client, create_session):
    session = await create_session(status=SessionStatus.active)

    response = await user_client.patch(
        f"/sessions/{session.id}/",
        json={"error": "STUN requests timed out."},
    )
    assert response.status_code == 204

    await session.refresh_from_db()
    assert session.error == "STUN requests timed out."
    assert session.status == SessionStatus.active


async def test_update_session_clears_error(user_client, create_session):
    session = await create_session(status=SessionStatus.active)
    session.error = "Previous error."
    await session.save()

    response = await user_client.patch(
        f"/sessions/{session.id}/",
        json={"error": None},
    )
    assert response.status_code == 204

    await session.refresh_from_db()
    assert session.error is None


async def test_update_session_empty_body_is_noop(user_client, create_session):
    session = await create_session(status=SessionStatus.active)
    session.error = "Previous error."
    await session.save()

    response = await user_client.patch(f"/sessions/{session.id}/", json={})
    assert response.status_code == 204

    await session.refresh_from_db()
    assert session.error == "Previous error."


async def test_update_session_rejects_oversized_error(user_client, create_session):
    session = await create_session(status=SessionStatus.active)

    response = await user_client.patch(
        f"/sessions/{session.id}/",
        json={"error": "x" * 5000},
    )
    assert response.status_code == 422

    await session.refresh_from_db()
    assert session.error is None


async def test_update_session_is_unauthorized_for_anonymous_user(client):
    del client.cookies["id_token"]

    response = await client.patch(
        "/sessions/test/",
        json={"error": "Test error."},
    )
    assert response.status_code == 401


async def test_update_session_returns_404_for_session_owned_by_another_user(
    user_client, create_session,
):
    session = await create_session(user_id="other-user", status=SessionStatus.active)

    response = await user_client.patch(
        f"/sessions/{session.id}/",
        json={"error": "Test error."},
    )
    assert response.status_code == 404

    await session.refresh_from_db()
    assert session.error is None


async def test_update_session_admin_can_update_any_session(
    client, create_session,
):
    session = await create_session(user_id="other-user", status=SessionStatus.active)

    response = await client.patch(
        f"/sessions/{session.id}/",
        json={"error": "Recorded by admin."},
    )
    assert response.status_code == 204

    await session.refresh_from_db()
    assert session.error == "Recorded by admin."


async def test_update_session_not_found(user_client):
    response = await user_client.patch(
        "/sessions/does-not-exist/",
        json={"error": "Test error."},
    )
    assert response.status_code == 404


async def test_get_session_returns_recorded_error(user_client, create_session):
    session = await create_session(status=SessionStatus.failed)
    session.error = "Persisted error."
    await session.save()

    response = await user_client.get(f"/sessions/{session.id}")
    assert response.status_code == 200
    assert response.json()["error"] == "Persisted error."


async def test_terminate_session_timeout(client, create_session, respx_mock):
    nvcf = respx_mock.delete(construct_nvcf_endpoint()).mock(side_effect=httpx.TimeoutException)

    session = await create_session()

    response = await client.delete(f"/sessions/{session.id}")
    assert response.status_code == 408
    assert response.text == "Failed to terminate session with a timeout -- try again later."
    assert nvcf.called


async def test_session_data_purge(client, create_session):
    original_retention = settings.session_retention_days
    original_watch_interval = settings.session_watch_interval
    settings.session_retention_days = 7
    settings.session_watch_interval = 1

    try:
        old_session = await create_session(
            status=SessionStatus.stopped,
            start_date=(
                datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(days=8)
            ),
        )
        recent_session = await create_session(
            status=SessionStatus.stopped,
            start_date=(
                datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(days=3)
            ),
        )
        alive_old_session = await create_session(
            status=SessionStatus.active,
            start_date=(
                datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(days=10)
            ),
        )

        await asyncio.sleep(2)

        assert await SessionModel.get_or_none(id=old_session.id) is None
        assert await SessionModel.get_or_none(id=recent_session.id) is not None
        assert await SessionModel.get_or_none(id=alive_old_session.id) is not None
    finally:
        settings.session_retention_days = original_retention
        settings.session_watch_interval = original_watch_interval


async def test_session_data_purge_disabled(client, create_session):
    original_retention = settings.session_retention_days
    original_watch_interval = settings.session_watch_interval
    settings.session_retention_days = 0
    settings.session_watch_interval = 1

    try:
        old_session = await create_session(
            status=SessionStatus.stopped,
            start_date=(
                datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(days=365)
            ),
        )

        await asyncio.sleep(2)

        assert await SessionModel.get_or_none(id=old_session.id) is not None
    finally:
        settings.session_retention_days = original_retention
        settings.session_watch_interval = original_watch_interval
