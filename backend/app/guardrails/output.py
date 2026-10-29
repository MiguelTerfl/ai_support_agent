"""Output guardrails: validate the answer BEFORE it is shown.

Checks (all deterministic):
  1. System prompt leak (canary + n-gram match)                      -> fatal
  2. PII in the answer (cards)                                       -> masked
  3. Ungrounded figures: numbers that appear neither in the retrieved
     context, nor in tool results, nor in what the user said
                                                                      -> retry
  4. Invented actions: claiming a ticket was created (that only happens
     after the user's confirmation, outside the LLM)                  -> retry
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..lang import REPLY_IN, msg
from ..textutil import fold, normalize
from .input import redact_pii

_NUM_RE = re.compile(r"(?<![\w.,])\d[\d.,]*\d|\d")
_LIST_MARKER_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+")

_FAKE_ACTION_RE = re.compile(
    # Spanish
    r"((he|hemos|ya)\s+(creado|abierto|generado|registrado)\s+(el|un|tu|su)?\s*ticket"
    r"|ticket\s+(ya\s+)?(fue|ha\s+sido|quedo|queda)\s+(creado|abierto|generado|registrado)"
    # English
    r"|(i\W?ve|i\s+have|we\W?ve|we\s+have|i|we)\s+(just\s+|already\s+)?(created|opened|submitted|filed|generated|registered)\s+(a|an|the|your)\s+(support\s+)?ticket"
    r"|ticket\s+(has\s+been|was|is\s+now|got)\s+(created|opened|submitted|filed|generated|registered)"
    # both
    r"|tck-\d+)"
)


def canon_number(raw: str) -> str | None:
    """Normalizes '1,899.00', '1.899', '0042' -> '1899', '1899', '42' for comparison."""
    s = raw.strip(".,")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        s = s.replace(",", "") if all(len(p) == 3 for p in parts[1:]) else s.replace(",", ".")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2 or len(parts[-1]) == 3:
            s = s.replace(".", "")
    try:
        value = float(s)
    except ValueError:
        return None
    return str(int(value)) if value == int(value) else str(round(value, 2))


def extract_numbers(text: str) -> set[str]:
    text = _LIST_MARKER_RE.sub("", text)
    out: set[str] = set()
    for m in _NUM_RE.finditer(text):
        c = canon_number(m.group(0))
        if c is not None:
            out.add(c)
    return out


def _ngrams(text: str, n: int = 8) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", fold(text))
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


@dataclass
class OutputVerdict:
    ok: bool
    text: str
    issues: list[str] = field(default_factory=list)
    fatal: bool = False
    feedback: str | None = None  # message for the model to correct itself (retry)


def check_output(
    answer: str,
    *,
    evidence_texts: list[str],
    user_texts: list[str],
    system_prompt: str,
    canary: str,
    lang: str = "en",
) -> OutputVerdict:
    reply_in = REPLY_IN.get(lang, REPLY_IN["en"])
    text = normalize(answer).strip()
    if not text:
        return OutputVerdict(False, "", ["empty"], feedback=f"Your answer was empty. Answer the customer's question. {reply_in}")

    # 1) Prompt leak
    if canary in text or (_ngrams(system_prompt) & _ngrams(text)):
        return OutputVerdict(False, msg("leak", lang), ["system_prompt_leak"], fatal=True)

    # 2) PII
    text, pii = redact_pii(text)
    issues: list[str] = [f"pii:{p}" for p in pii]

    # 3) Invented actions (checked before figures: it is the most serious error)
    if _FAKE_ACTION_RE.search(fold(text)):
        return OutputVerdict(
            False, text, issues + ["fake_action"],
            feedback=(
                "You cannot claim that you created a ticket. If the customer wants one, call the "
                f"create_ticket tool with their details; the system will ask the customer to confirm. {reply_in}"
            ),
        )

    # 4) Ungrounded figures
    # Figures in the prompt itself (e.g. the example format ORD-12345) come from the operator and are trusted.
    allowed: set[str] = extract_numbers(system_prompt)
    for t in evidence_texts + user_texts:
        allowed |= extract_numbers(t)
    unsupported = sorted(extract_numbers(text) - allowed)
    if unsupported:
        shown = ", ".join(unsupported[:6])
        return OutputVerdict(
            False, text, issues + ["ungrounded_numbers"],
            feedback=(
                f"Your answer mentions figures that do not appear in the information consulted ({shown}). "
                "If you need data, call search_knowledge_base or get_order_status and use only figures from their results. "
                f"If you do not have the data, say you do not have that information. {reply_in}"
            ),
        )

    return OutputVerdict(True, text, issues)
