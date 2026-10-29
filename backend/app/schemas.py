from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    messages: list[Msg] = Field(min_length=1, max_length=30)
    # UI language: only used if the message is ambiguous (e.g. "ORD-10001").
    lang: Literal["es", "en"] | None = None


class ConfirmRequest(BaseModel):
    token: str = Field(min_length=10, max_length=4000)
    approve: bool
