from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.llm import ChatResult, LLMUnavailable  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402
from app.tools import ToolContext, build_registry  # noqa: E402
from app.types import Chunk  # noqa: E402


class FakeLLM:
    """Scripted LLM: each chat() call returns the next message in the list."""

    model = "fake-model"

    def __init__(self, script: list[dict[str, Any] | Exception] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages, tools=None) -> ChatResult:
        self.calls.append([dict(m) for m in messages])
        if not self.script:
            raise AssertionError("FakeLLM has no more scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(message=item, eval_count=10)

    async def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:  # pragma: no cover
        pass


class FakeDB:
    def __init__(self, chunks: list[Chunk] | None = None) -> None:
        self.chunks = chunks or []
        self.tickets: list[tuple] = []
        self.fail_ticket = False

    async def hybrid_search(self, embedding, text, k):
        return self.chunks[:k]

    async def get_order(self, order_id: str, email: str):
        if (order_id, email.lower()) == ("ORD-10001", "ana@example.com"):
            return {
                "order_id": "ORD-10001", "email": "ana@example.com", "status": "in_transit", "items": "Aurora X5 Blender",
                "total_mxn": 1899.00, "ordered_at": date(2026, 10, 14), "shipped_at": date(2026, 10, 15), "delivered_at": None,
                "carrier": "Estafeta", "tracking": "EST123456789",
            }
        return None

    async def create_ticket(self, subject, description, email, order_id):
        if self.fail_ticket:
            raise RuntimeError("db down")
        self.tickets.append((subject, description, email, order_id))
        return f"TCK-{len(self.tickets):06d}"

    async def ping(self) -> bool:
        return True


RETURNS = Chunk(
    id=1, source="returns.md", heading="Return window", score=0.82,
    content="Returns and refunds policy › Return window\nYou can return any product within 30 calendar days after delivery. The refund takes 5 to 10 business days.",
)


@pytest.fixture
def settings() -> Settings:
    return Settings(confirm_secret="test-secret", internal_api_key="", rate_limit_per_min=100, _env_file=None)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB([RETURNS])


def make_ctx(db: FakeDB, llm: FakeLLM | None = None, min_score: float = 0.45) -> ToolContext:
    llm = llm or FakeLLM()
    return ToolContext(retriever=Retriever(db, llm, top_k=4, min_score=min_score), db=db)


@pytest.fixture
def registry():
    return build_registry()


def tool_call(name: str, **args: Any) -> dict[str, Any]:
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}
