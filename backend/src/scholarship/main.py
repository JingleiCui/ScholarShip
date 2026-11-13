import asyncio
import json
import logging
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import sqlalchemy as sa

from scholarship.api.auth import router as auth_router, Identity, identity, mutation_identity
from scholarship.config import settings
from scholarship.domains.runs import create_probe, get_run, public_run, read_events
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.health import dependency_health, public_meta
from scholarship.infrastructure.schema import tables


class ProbeRequest(BaseModel):
    project_id: UUID


def create_app():
    app = FastAPI(title="ScholarShip", version="0.1.0")
    app.include_router(auth_router, prefix="/api/v1")
    from scholarship.api.library import router as library_router

    app.include_router(library_router, prefix="/api/v1")

    @app.middleware("http")
    async def request_id(request, call_next):
        rid = str(uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        # Never log exception arguments: upstream libraries can include DSNs.
        logging.getLogger("scholarship").error(
            json.dumps(
                {
                    "event": "request_failed",
                    "type": type(exc).__name__,
                    "request_id": getattr(request.state, "request_id", None),
                }
            )
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "request_id": getattr(request.state, "request_id", None)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            status_code=422, content={"error": "invalid_request", "fields": [list(e["loc"]) for e in exc.errors()]}
        )

    @app.get("/api/v1/health/live")
    def live():
        return {"alive": True}

    @app.get("/api/v1/health/ready")
    def ready():
        result = dependency_health()
        return JSONResponse(status_code=200 if result["ready"] else 503, content=result)

    @app.get("/api/v1/meta")
    def meta():
        return public_meta()

    @app.get("/api/v1/projects")
    def projects(user: Identity = Depends(identity)):
        with tenant_transaction(user.user_id) as conn:
            return [
                dict(r)
                for r in conn.execute(
                    sa.select(
                        tables["projects"].c.id, tables["projects"].c.name, tables["projects"].c.description
                    ).where(tables["projects"].c.deleted_at.is_(None))
                ).mappings()
            ]

    @app.post("/api/v1/runs", status_code=202)
    def new_probe(
        body: ProbeRequest,
        user: Identity = Depends(mutation_identity),
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return create_probe(user.user_id, body.project_id, idempotency_key)

    @app.get("/api/v1/runs/{run_id}")
    def run(run_id: UUID, user: Identity = Depends(identity)):
        return public_run(get_run(user.user_id, run_id))

    @app.get("/api/v1/runs/{run_id}/events")
    async def events(run_id: UUID, request: Request, after: int = 0, user: Identity = Depends(identity)):
        try:
            cursor = int(request.headers.get("last-event-id", after))
            if cursor < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(400, "invalid_event_cursor")
        await asyncio.to_thread(read_events, user.user_id, run_id, cursor)

        async def stream():
            nonlocal cursor
            deadline = time.monotonic() + settings().stream_timeout_seconds
            while time.monotonic() < deadline and not await request.is_disconnected():
                batch, terminal = await asyncio.to_thread(read_events, user.user_id, run_id, cursor)
                for item in batch:
                    cursor = item["seq"]
                    yield f"id: {cursor}\nevent: {item['event_type']}\ndata: {json.dumps(item['public_payload'], ensure_ascii=False)}\n\n"
                if terminal and not batch:
                    return
                if not batch:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    return app
