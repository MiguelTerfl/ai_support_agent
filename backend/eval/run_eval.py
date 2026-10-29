"""Side-by-side model evaluation on the same agent, RAG and guardrails.

Requires a reachable Ollama (with the models pulled) and Postgres with the knowledge base ingested.

    python -m eval.run_eval --models gemma4:e2b,gemma3:4b,qwen3:4b
    python -m eval.run_eval --models gemma4:e2b --only en_kb,en_order

Writes eval/results/<date>.md with a comparison table and per-case detail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent import SupportAgent
from app.config import get_settings
from app.db import Database
from app.lang import detect_lang
from app.llm import OllamaClient
from app.rag.retriever import Retriever
from app.tools import ToolContext, build_registry

CASES_PATH = Path(__file__).parent / "cases.json"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    tool_calls: int = 0
    tool_calls_ok: int = 0
    guardrails: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    tokens: int = 0
    answer: str = ""
    expected_tool: str | None = None
    tool_ok: bool | None = None
    lang_ok: bool | None = None
    query_ok: bool | None = None


def _contains_any(text: str, needles: list[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


async def run_case(agent: SupportAgent, case: dict[str, Any]) -> CaseResult:
    events: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    try:
        async for ev in agent.run([], case["input"]):
            events.append(ev)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(case["id"], case["category"], False, [f"exception: {exc!r}"])
    wall_ms = int((time.perf_counter() - t0) * 1000)

    answers = [e for e in events if e["type"] == "answer"]
    answer = answers[-1]["content"] if answers else ""
    errors = [e for e in events if e["type"] == "error"]
    calls = [e for e in events if e["type"] == "tool_call"]
    results = [e for e in events if e["type"] == "tool_result"]
    guards = [f"{e['stage']}:{e['action']}({e['detail']})" for e in events if e["type"] == "guardrail"]
    done = next((e["metrics"] for e in events if e["type"] == "done"), {})
    confirmation = any(e["type"] == "confirmation" for e in events)
    blocked = any(a.get("blocked") for a in answers)
    flags = " ".join(e["detail"] for e in events if e["type"] == "guardrail" and e["stage"] == "input")

    failures: list[str] = []
    if errors:
        failures.append(f"error: {errors[0]['message']}")
    tools = [c["name"] for c in calls]
    tool_ok: bool | None = None
    if exp := case.get("expect_tool"):
        tool_ok = exp in tools
        if not tool_ok:
            failures.append(f"did not call {exp} (called: {tools or 'none'})")
    lang_ok: bool | None = None
    if exp_lang := case.get("expect_reply_lang"):
        detected = detect_lang(answer)
        lang_ok = detected in (None, exp_lang)  # None = answer too short to tell: not penalized
        if not lang_ok:
            failures.append(f"replied in '{detected}' but '{exp_lang}' was expected")
    query_ok: bool | None = None
    if case.get("expect_english_query"):
        queries = [str(c["args"].get("query", "")) for c in calls if c["name"] == "search_knowledge_base"]
        query_ok = bool(queries) and all(detect_lang(q) != "es" for q in queries)
        if not query_ok:
            failures.append(f"the search query was not in English: {queries or 'no search'}")
    if "expect_confirmation" in case and case["expect_confirmation"] != confirmation:
        failures.append(f"expected confirmation={case['expect_confirmation']} got={confirmation}")
    if "expect_blocked" in case and case["expect_blocked"] != blocked:
        failures.append(f"expected blocked={case['expect_blocked']} got={blocked}")
    for flag in case.get("expect_flags", []):
        if flag not in flags:
            failures.append(f"the input guardrail did not flag '{flag}'")
    if (needles := case.get("answer_contains_any")) and not _contains_any(answer, needles):
        failures.append(f"the answer contains none of {needles}")
    for bad in case.get("must_not_contain", []):
        if bad.lower() in answer.lower():
            failures.append(f"the answer contains '{bad}'")

    return CaseResult(
        case["id"], case["category"], not failures, failures, tools,
        tool_calls=len(calls), tool_calls_ok=sum(1 for r in results if r["ok"]),
        guardrails=guards, elapsed_ms=done.get("elapsed_ms", wall_ms), tokens=done.get("tokens", 0),
        answer=answer, expected_tool=case.get("expect_tool"), tool_ok=tool_ok, lang_ok=lang_ok, query_ok=query_ok,
    )


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "–"


def summarize(model: str, results: list[CaseResult]) -> dict[str, Any]:
    tool_cases = [r for r in results if r.expected_tool]
    calls = sum(r.tool_calls for r in results)
    lang_cases = [r for r in results if r.lang_ok is not None]
    query_cases = [r for r in results if r.query_ok is not None]
    return {
        "model": model,
        "pass": _pct(sum(r.passed for r in results), len(results)),
        "tool_selection": _pct(sum(bool(r.tool_ok) for r in tool_cases), len(tool_cases)),
        "valid_args": _pct(sum(r.tool_calls_ok for r in results), calls),
        "reply_lang": _pct(sum(bool(r.lang_ok) for r in lang_cases), len(lang_cases)),
        "english_query": _pct(sum(bool(r.query_ok) for r in query_cases), len(query_cases)),
        "guardrail_events": sum(len(r.guardrails) for r in results),
        "avg_s": f"{sum(r.elapsed_ms for r in results) / max(len(results), 1) / 1000:.1f}",
        "avg_tokens": int(sum(r.tokens for r in results) / max(len(results), 1)),
    }


def render_markdown(summaries: list[dict[str, Any]], per_model: dict[str, list[CaseResult]]) -> str:
    out = [f"# Agent evaluation · {datetime.now():%Y-%m-%d %H:%M}\n"]
    out.append("| Model | Cases OK | Tool choice | Valid args | Replies in user's language | Search query in English | Guardrail events | Avg latency (s) | Avg tokens |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        out.append(
            f"| {s['model']} | {s['pass']} | {s['tool_selection']} | {s['valid_args']} | {s['reply_lang']} | {s['english_query']} "
            f"| {s['guardrail_events']} | {s['avg_s']} | {s['avg_tokens']} |"
        )
    for model, results in per_model.items():
        out.append(f"\n## Detail: {model}\n")
        out.append("| Case | Result | Tools | Guardrails | Notes |")
        out.append("|---|---|---|---|---|")
        for r in results:
            note = "; ".join(r.failures).replace("|", "\\|") or ""
            out.append(f"| {r.case_id} | {'✅' if r.passed else '❌'} | {', '.join(r.tools) or '–'} | {', '.join(r.guardrails) or '–'} | {note} |")
    return "\n".join(out) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=get_settings().llm_model, help="Comma-separated list")
    parser.add_argument("--only", default="", help="Comma-separated categories: en_kb, en_order, en_ticket, en_no_info, en_injection, en_pii and their Spanish counterparts (es_kb, es_order, …)")
    args = parser.parse_args()

    settings = get_settings()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if args.only:
        wanted = {c.strip() for c in args.only.split(",")}
        cases = [c for c in cases if c["category"] in wanted]

    db = Database(settings.database_url, settings.embed_dim)
    await db.open()
    summaries: list[dict[str, Any]] = []
    per_model: dict[str, list[CaseResult]] = {}
    try:
        for model in [m.strip() for m in args.models.split(",") if m.strip()]:
            llm = OllamaClient(
                settings.ollama_base_url, model, settings.embed_model, num_ctx=settings.num_ctx,
                temperature=settings.temperature, timeout=settings.llm_timeout,
                embed_query_prefix=settings.embed_query_prefix, embed_doc_prefix=settings.embed_doc_prefix,
            )
            retriever = Retriever(db, llm, top_k=settings.top_k, min_score=settings.min_score)
            agent = SupportAgent(llm, build_registry(), ToolContext(retriever, db), settings)
            print(f"\n== {model} ({len(cases)} cases) ==")
            results: list[CaseResult] = []
            for case in cases:
                r = await run_case(agent, case)
                results.append(r)
                print(f"  {'OK ' if r.passed else 'FAIL'} {r.case_id:<14} {r.elapsed_ms:>6} ms  {'; '.join(r.failures)}")
            await llm.aclose()
            per_model[model] = results
            summaries.append(summarize(model, results))
    finally:
        await db.close()

    report = render_markdown(summaries, per_model)
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Report saved to {path}")


if __name__ == "__main__":
    asyncio.run(main())
