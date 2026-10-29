"""FastAPI app: chat over SSE, action confirmation and health."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .agent import SupportAgent
from .config import Settings, get_settings
from .confirm import InvalidToken, UsedTokens, verify_action
from .lang import msg, resolve_lang
from .db import Database
from .llm import OllamaClient
from .rag.ingest import ingest
from .rag.retriever import Retriever
from .ratelimit import SlidingWindowLimiter
from .schemas import ChatRequest, ConfirmRequest
from .tools import ToolContext, build_registry, run_confirmed

log = logging.getLogger("api")


def sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def with_heartbeat(source: AsyncIterator[str], interval: float = 10.0) -> AsyncIterator[str]:
    """Sends SSE comments while the model is thinking, so proxies (Traefik, etc.) don't close the connection."""
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for item in source:
                await queue.put(item)
        except Exception as exc:  # noqa: BLE001
            await queue.put(exc)
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                log.error("Stream error: %r", item)
                yield sse({"type": "error", "code": "internal", "message": msg("internal", "en")})
                break
            yield item
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def create_app(settings: Settings | None = None, *, llm: Any = None, db: Any = None) -> FastAPI:
    settings = settings or get_settings()
    own_llm, own_db = llm is None, db is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        _llm = llm or OllamaClient(
            settings.ollama_base_url, settings.llm_model, settings.embed_model,
            num_ctx=settings.num_ctx, temperature=settings.temperature, timeout=settings.llm_timeout,
            embed_query_prefix=settings.embed_query_prefix, embed_doc_prefix=settings.embed_doc_prefix,
        )
        _db = db or Database(settings.database_url, settings.embed_dim)
        if own_db:
            await _db.open()
            await _db.init_schema()
            if settings.auto_ingest:
                try:
                    log.info("Ingest: %s", await ingest(_db, _llm, settings))
                except Exception as exc:  # the model may be off: the app starts anyway
                    log.warning("Could not ingest on startup (is Ollama off?): %r", exc)

        retriever = Retriever(_db, _llm, top_k=settings.top_k, min_score=settings.min_score)
        ctx = ToolContext(retriever=retriever, db=_db)
        app.state.llm, app.state.db, app.state.ctx = _llm, _db, ctx
        app.state.agent = SupportAgent(_llm, build_registry(), ctx, settings)
        app.state.sem = asyncio.Semaphore(settings.max_concurrent_llm)
        app.state.limiter = SlidingWindowLimiter(settings.rate_limit_per_min)
        app.state.used_tokens = UsedTokens()
        try:
            yield
        finally:
            if own_llm:
                await _llm.aclose()
            if own_db:
                await _db.close()

    app = FastAPI(title="Support assistant · Aurora Electro", lifespan=lifespan, docs_url=None, redoc_url=None)

    async def guard(request: Request) -> str:
        """Authenticates the frontend (internal key) and returns the client's real IP."""
        key = settings.internal_api_key
        if key and not hmac.compare_digest(request.headers.get("x-internal-key", ""), key):
            raise HTTPException(status_code=401, detail="Unauthorized")
        # x-client-ip is only trusted if the request came authenticated from the frontend.
        ip = request.headers.get("x-client-ip") if key else None
        return ip or (request.client.host if request.client else "unknown")

    def rate_limit(request: Request, ip: str) -> None:
        ok, retry = request.app.state.limiter.check(ip)
        if not ok:
            raise HTTPException(status_code=429, detail=f"Too many requests. Try again in {retry} s.", headers={"Retry-After": str(retry)})

    @app.get("/api/health")
    async def health(request: Request, _ip: str = Depends(guard)):
        llm_ok, db_ok = await asyncio.gather(request.app.state.llm.ping(), request.app.state.db.ping())
        return {"ok": llm_ok and db_ok, "llm": llm_ok, "db": db_ok, "model": request.app.state.llm.model}

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request, ip: str = Depends(guard)):
        rate_limit(request, ip)
        if body.messages[-1].role != "user":
            raise HTTPException(status_code=422, detail="The last message must be from the user")
        history, user_text = body.messages[:-1], body.messages[-1].content
        lang = resolve_lang(user_text, [m.content for m in history if m.role == "user"], body.lang)
        agent: SupportAgent = request.app.state.agent
        sem: asyncio.Semaphore = request.app.state.sem

        async def stream() -> AsyncIterator[str]:
            try:
                await asyncio.wait_for(sem.acquire(), timeout=settings.queue_timeout)
            except asyncio.TimeoutError:
                yield sse({"type": "error", "code": "busy", "message": msg("busy", lang)})
                return
            try:
                async for event in agent.run(history, user_text, body.lang):
                    if event["type"] == "done":
                        log.info("chat ok %s", event["metrics"])
                    yield sse(event)
            finally:
                sem.release()

        return StreamingResponse(
            with_heartbeat(stream()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/confirm")
    async def confirm(body: ConfirmRequest, request: Request, ip: str = Depends(guard)):
        rate_limit(request, ip)
        try:
            action = verify_action(settings.confirm_secret, body.token)
        except InvalidToken:
            raise HTTPException(status_code=400, detail="The confirmation is invalid or has expired. Ask me for the ticket again.")
        if not body.approve:
            return {"ok": True, "message": msg("ticket_cancelled", action.lang)}
        used: UsedTokens = request.app.state.used_tokens
        if not used.consume(action.sig, action.exp):
            raise HTTPException(status_code=409, detail="This ticket was already confirmed.")
        try:
            message = await run_confirmed(action.tool, action.args, request.app.state.ctx, action.lang)
        except Exception:
            used.release(action.sig)
            log.exception("The confirmed action failed")
            raise HTTPException(status_code=500, detail="I couldn't create the ticket. Please try again in a moment.")
        return {"ok": True, "message": message}

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    return app


def app_factory() -> FastAPI:
    """Entry point for uvicorn: `uvicorn app.main:app_factory --factory`."""
    return create_app()
