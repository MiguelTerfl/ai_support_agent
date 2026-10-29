"""Input guardrails: deterministic rules (fast, auditable, and they cost no tokens).

With small local models, asking the LLM to police itself is unreliable; these checks run
before the text reaches the model. They cover English and Spanish (the store serves both).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..lang import msg
from ..textutil import fold, normalize

__all__ = ["InputVerdict", "check_history_message", "check_input", "detect_injection", "fold", "normalize", "redact_pii"]

_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # --- Spanish ---
    ("ignore_instructions_es", re.compile(r"\b(ignora|olvida|omite|descarta|salta)\w*\s+(todas?\s+)?(las\s+|tus\s+|sus\s+)?(instrucciones|reglas|indicaciones|restricciones)")),
    ("disable_safety_es", re.compile(r"\b(desactiva|deshabilita|apaga|quita)\w*\s+(los\s+|tus\s+)?(filtros|guardrails|restricciones|seguridad|protecciones)")),
    ("role_override_es", re.compile(r"(a\s+partir\s+de\s+ahora\s+(eres|actua)|ahora\s+eres\s+(un|una)\s|finge\s+ser|haz\s+de\s+cuenta\s+que\s+eres)")),
    # --- English ---
    ("ignore_instructions_en", re.compile(r"\b(ignore|forget|disregard|override|bypass)\s+(all\s+|any\s+)?(of\s+)?(your\s+|the\s+)?(previous\s+|prior\s+|above\s+|earlier\s+)?(instructions|rules|prompts?|guidelines|restrictions)")),
    ("disable_safety_en", re.compile(r"\b(disable|turn\s+off|remove|deactivate)\s+(your\s+|the\s+|all\s+)?(filters|safety|guardrails|restrictions|protections)")),
    ("role_override_en", re.compile(r"(you\s+are\s+now\s+(a|an|in)\s|from\s+now\s+on\s+you|pretend\s+(to\s+be|you\s+are)|act\s+as\s+(if\s+you\s+have\s+no|an?\s+(unrestricted|unfiltered)))")),
    # --- both languages ---
    (
        "reveal_prompt",
        re.compile(
            r"(system\s*prompt|prompt\s+(del\s+)?sistema|prompt\s+interno|(tus|sus)\s+instrucciones\s+(internas|iniciales|originales|de\s+sistema)"
            r"|reveal\s+your\s+(system\s+)?(prompt|instructions)"
            r"|(your|the)\s+(system|hidden|initial|original|internal|secret)\s+(prompt|instructions|message)"
            r"|your\s+(prompt|instructions)\b)"
        ),
    ),
    ("jailbreak", re.compile(r"(jailbreak|do\s+anything\s+now|modo\s+(desarrollador|dios|sin\s+restricciones)|developer\s+mode|\bdan\s+mode)")),
    ("role_markers", re.compile(r"(<\s*/?\s*(system|assistant|im_start|im_end)\s*>|\[/?inst\]|<\|im_(start|end)\|>|###\s*(system|instruction))")),
]


def detect_injection(text: str) -> str | None:
    """Returns the name of the matching rule, or None."""
    folded = fold(normalize(text))
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(folded):
            return name
    return None


_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_CURP_RE = re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", re.IGNORECASE)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Masks cards (Luhn-validated) and CURP (Mexican ID). Email is NOT masked:
    the order tool needs it to verify the customer."""
    flags: list[str] = []

    def _card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            if "card" not in flags:
                flags.append("card")
            return "[CARD]"
        return m.group(0)

    text = _CARD_RE.sub(_card, text)
    if _CURP_RE.search(text):
        flags.append("curp")
        text = _CURP_RE.sub("[CURP]", text)
    return text, flags


@dataclass
class InputVerdict:
    allowed: bool
    text: str
    reason: str | None = None
    user_message: str | None = None
    flags: list[str] = field(default_factory=list)


def check_input(text: str, max_chars: int, lang: str = "en") -> InputVerdict:
    text = normalize(text).strip()
    if not text:
        return InputVerdict(False, "", "empty", msg("empty", lang))
    if len(text) > max_chars:
        return InputVerdict(False, "", "too_long", msg("too_long", lang, max=max_chars))
    rule = detect_injection(text)
    if rule:
        return InputVerdict(False, "", f"injection:{rule}", msg("blocked", lang), ["injection"])
    redacted, flags = redact_pii(text)
    return InputVerdict(True, redacted, None, None, flags)


def check_history_message(content: str) -> str | None:
    """For earlier messages sent by the client (they may have been tampered with)."""
    return detect_injection(content)
