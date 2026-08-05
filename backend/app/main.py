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
import traceback
from contextlib import asynccontextmanager

import aerich
import uvicorn
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from tortoise import Tortoise
from tortoise.contrib.fastapi import RegisterTortoise
from tortoise.exceptions import DoesNotExist, IntegrityError

from app.api_keys import api_keys
from app.routers import apps_router, deployment_router, sessions_router, pages_router, users_router
from app.routers.sessions import watch_session_timeout, watch_idle_sessions, watch_session_data_purge
from app.settings import settings


@asynccontextmanager
async def configure_api(app: FastAPI):
    settings.listen()
    api_keys.listen()

    session_termination_task = None
    session_idle_termination_task = None
    session_data_purge_task = None
    mcp_data_purge_task = None
    try:
        async with RegisterTortoise(
            app,
            config=settings.tortoise_orm(),
            generate_schemas=True,
        ):
            session_termination_task = asyncio.create_task(
                watch_session_timeout()
            )
            session_termination_task.add_done_callback(watcher_task_done)

            session_idle_termination_task = asyncio.create_task(
                watch_idle_sessions()
            )
            session_idle_termination_task.add_done_callback(watcher_task_done)

            session_data_purge_task = asyncio.create_task(
                watch_session_data_purge()
            )
            session_data_purge_task.add_done_callback(watcher_task_done)

            if settings.mcp_enabled:
                from app.mcp.server import get_mcp
                from app.mcp.oauth import watch_mcp_data_purge

                mcp_data_purge_task = asyncio.create_task(
                    watch_mcp_data_purge()
                )
                mcp_data_purge_task.add_done_callback(watcher_task_done)

                async with get_mcp().session_manager.run():
                    yield
            else:
                yield
    finally:
        if session_termination_task is not None:
            session_termination_task.cancel()
        if session_idle_termination_task is not None:
            session_idle_termination_task.cancel()
        if session_data_purge_task is not None:
            session_data_purge_task.cancel()
        if mcp_data_purge_task is not None:
            mcp_data_purge_task.cancel()
        settings.stop()
        api_keys.stop()


def watcher_task_done(task):
    if task.exception():
        traceback.print_exc()


api = FastAPI(
    title="Backend",
    description=(
        "The API for publishing extended metadata for container images."
    ),
    lifespan=configure_api,
    root_path=settings.root_path,
    docs_url="/",
    strict_content_type=False,
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@api.exception_handler(DoesNotExist)
async def doesnotexist_exception_handler(request: Request, exc: DoesNotExist):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@api.exception_handler(IntegrityError)
async def integrityerror_exception_handler(
    request: Request, exc: IntegrityError
):
    return JSONResponse(
        status_code=422,
        content={
            "detail": [{"loc": [], "msg": str(exc), "type": "IntegrityError"}]
        },
    )


api.include_router(apps_router, tags=["apps"])
api.include_router(deployment_router, tags=["deployment"])
api.include_router(sessions_router, tags=["sessions"])
api.include_router(pages_router, tags=["pages"])
api.include_router(users_router, tags=["users"])


if settings.mcp_enabled:
    from app.mcp.server import mount_mcp

    mount_mcp(api)


def start():
    logging_config = uvicorn.config.LOGGING_CONFIG
    logging_config["loggers"]["uvicorn"]["level"] = "DEBUG"
    logging_config["loggers"]["uvicorn.access"]["level"] = "INFO"
    logging_config["loggers"]["app"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }

    uvicorn.run(
        "app.main:api",
        host="0.0.0.0",
        port=8000,
        reload=False,
        ws_ping_interval=None,
        ws_ping_timeout=None,
        log_level="info",
        log_config=logging_config,
        h11_max_incomplete_event_size=settings.h11_max_incomplete_event_size,
    )


def migrations():
    async def migrate():
        config = settings.tortoise_orm()
        await Tortoise.init(config)

        try:
            async with aerich.Command(
                tortoise_config=config, app="models"
            ) as command:
                await command.upgrade()
        finally:
            await Tortoise.close_connections()

    asyncio.run(migrate())
