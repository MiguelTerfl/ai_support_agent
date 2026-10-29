"""Support agent: its own tool-calling loop (no frameworks) with guardrails.

Flow of each turn:
    input -> input guardrail -> [LLM -> tools]* -> output guardrail -> answer

It emits events (dicts) that the endpoint turns into Server-Sent Events:
    status, tool_call, tool_result, guardrail, confirmation, answer, error, done
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, AsyncIterator, Protocol

from .config import Settings
from .confirm import sign_action
from .guardrails.input import check_history_message, check_input, normalize, redact_pii
from .guardrails.output import check_output
from .lang import Lang, msg as tr, resolve_lang
from .llm import ChatResult, LLMUnavailable
from .prompts import build_system_prompt
from .schemas import Msg
from .tools import ToolContext, ToolRegistry

log = logging.getLogger("agent")

MAX_CALLS_PER_STEP = 2
MAX_TOOL_ERRORS = 3


class _LLM(Protocol):
    model: str

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> ChatResult: ...


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


class SupportAgent:
    def __init__(self, llm: _LLM, tools: ToolRegistry, ctx: ToolContext, settings: Settings, canary: str | None = None) -> None:
        self.llm = llm
        self.tools = tools
        self.ctx = ctx
        self.settings = settings
        self.canary = canary or f"CANARY-{secrets.token_hex(6)}"
        self._specs = tools.specs

    async def run(self, history: list[Msg], user_text: str, hint_lang: Lang | None = None) -> AsyncIterator[dict[str, Any]]:
        s = self.settings
        started = time.perf_counter()
        # The language is detected on the server (not left to the model): current message > earlier messages > UI hint.
        lang = resolve_lang(user_text, [m.content for m in history if m.role == "user"], hint_lang)
        system_prompt = build_system_prompt(self.canary, lang)
        metrics = {"model": getattr(self.llm, "model", "?"), "lang": lang, "llm_calls": 0, "tool_calls": 0, "tokens": 0}

        def done() -> dict[str, Any]:
            return {"type": "done", "metrics": {**metrics, "elapsed_ms": int((time.perf_counter() - started) * 1000)}}

        # ---------- 1) Input guardrail ----------
        verdict = check_input(user_text, s.max_input_chars, lang)
        if verdict.flags:
            yield {"type": "guardrail", "stage": "input", "action": "redacted" if verdict.allowed else "blocked", "detail": ", ".join(verdict.flags)}
        if not verdict.allowed:
            yield {"type": "answer", "content": verdict.user_message, "sources": [], "blocked": True}
            yield done()
            return

        # The history comes from the browser: it is treated as untrusted.
        clean_history: list[dict[str, str]] = []
        for m in history[-s.max_history :]:
            if check_history_message(m.content):
                yield {"type": "guardrail", "stage": "input", "action": "blocked", "detail": "injection_in_history"}
                yield {"type": "answer", "content": tr("blocked", lang), "sources": [], "blocked": True}
                yield done()
                return
            content = normalize(m.content)[:2000]
            if m.role == "user":
                content, _ = redact_pii(content)
            clean_history.append({"role": m.role, "content": content})

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *clean_history, {"role": "user", "content": verdict.text}]
        user_texts = [h["content"] for h in clean_history if h["role"] == "user"] + [verdict.text]
        evidence: list[str] = []
        sources: list[dict[str, str]] = []
        retries = 0
        tool_errors = 0

        # ---------- 2) Agent loop ----------
        for step in range(s.max_steps):
            yield {"type": "status", "message": "Thinking…" if step == 0 else "Working…"}
            try:
                result = await self.llm.chat(messages, tools=self._specs)
            except LLMUnavailable as exc:
                log.warning("LLM unavailable: %s", exc)
                yield {"type": "error", "code": "llm_unavailable", "message": tr("llm_unavailable", lang)}
                return
            metrics["llm_calls"] += 1
            metrics["tokens"] += result.eval_count
            msg = result.message
            calls = (msg.get("tool_calls") or [])[:MAX_CALLS_PER_STEP]

            if calls:
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for call in calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    args = _parse_args(fn.get("arguments"))
                    metrics["tool_calls"] += 1
                    yield {"type": "tool_call", "name": name, "args": args}
                    try:
                        outcome = await self.tools.execute(name, args, self.ctx)
                    except LLMUnavailable:
                        yield {"type": "error", "code": "llm_unavailable", "message": tr("llm_unavailable", lang)}
                        return
                    yield {"type": "tool_result", "name": name, "ok": outcome.ok, "summary": outcome.summary}
                    messages.append({"role": "tool", "tool_name": name, "content": outcome.content})

                    if outcome.pending:  # side-effect action: stop and ask the user for confirmation
                        token = sign_action(s.confirm_secret, outcome.pending.tool, outcome.pending.args, s.confirm_ttl_seconds, lang=lang)
                        yield {"type": "confirmation", "token": token, "tool": outcome.pending.tool, "args": outcome.pending.args, "summary": outcome.pending.summary}
                        yield {"type": "answer", "content": tr("confirm_needed", lang), "sources": [], "blocked": False}
                        yield done()
                        return
                    if outcome.ok:
                        evidence.extend(outcome.evidence)
                        for src in outcome.sources:
                            if src not in sources:
                                sources.append(src)
                    else:
                        tool_errors += 1
                if tool_errors >= MAX_TOOL_ERRORS:
                    break
                continue

            # ---------- 3) Output guardrail ----------
            out = check_output(
                msg.get("content") or "",
                evidence_texts=evidence, user_texts=user_texts,
                system_prompt=system_prompt, canary=self.canary, lang=lang,
            )
            if out.ok:
                if out.issues:
                    yield {"type": "guardrail", "stage": "output", "action": "redacted", "detail": ", ".join(out.issues)}
                yield {"type": "answer", "content": out.text, "sources": sources, "blocked": False}
                yield done()
                return

            can_retry = not out.fatal and retries < s.max_output_retries
            yield {"type": "guardrail", "stage": "output", "action": "retry" if can_retry else ("blocked" if out.fatal else "fallback"), "detail": ", ".join(out.issues)}
            if not can_retry:
                yield {"type": "answer", "content": out.text if out.fatal else tr("fallback", lang), "sources": [], "blocked": True}
                yield done()
                return
            retries += 1
            messages.append({"role": "assistant", "content": msg.get("content") or ""})
            messages.append({"role": "user", "content": f"[Automatic check] {out.feedback}"})

        yield {"type": "guardrail", "stage": "output", "action": "fallback", "detail": "step_limit_or_tool_errors"}
        yield {"type": "answer", "content": tr("fallback", lang), "sources": [], "blocked": True}
        yield done()
