"""Lightweight language detection (Spanish/English) and localized user-facing messages.

Neither a library nor the LLM is used: guardrails and error messages must work even when
the model is off, and with only two languages, counting function words is enough.
"""

from __future__ import annotations

import re
from typing import Literal

from .textutil import fold

Lang = Literal["es", "en"]

_EN = set(
    """the is are was were what whats how when where which who why do does did can could would should will
    i im ive my you your to for of and in on with have has had it its this that these those need want please hello
    hi thanks thank order return refund warranty shipping invoice payment open create help not there any about if
    at from be been get got long much many days after before still already since she he we they their our
    delivery arrive arrived cost price cancel""".split()
)
_ES = set(
    """el la los las de del que como cuando donde cual cuanto cuantos cuantas es son esta estan un una unos unas mi
    mis tu tus por para con y en se al lo le hola gracias quiero necesito tengo puedo pedido devolver garantia
    envio factura pago tarjeta porque su sus mas pero ya si ayuda favor puede pueden hay tienen tiene dias
    cuesta llego llegar cancelar""".split()
)

_ES_CHARS = re.compile(r"[¿¡ñáéíóúü]", re.IGNORECASE)


def detect_lang(text: str) -> Lang | None:
    """Returns 'es' or 'en', or None if there isn't enough signal (e.g. just 'ORD-10001')."""
    folded = fold(text)
    words = re.findall(r"[a-z]+", folded)
    es = sum(w in _ES for w in words)
    en = sum(w in _EN for w in words)
    if _ES_CHARS.search(text):
        es += 3
    if en > es:
        return "en"
    if es > en:
        return "es"
    return None


def resolve_lang(user_text: str, previous_user_texts: list[str], hint: Lang | None = None) -> Lang:
    """Language of the turn: the message's own; if ambiguous, that of earlier messages; then the UI hint."""
    found = detect_lang(user_text)
    if found:
        return found
    for text in reversed(previous_user_texts):
        found = detect_lang(text)
        if found:
            return found
    return hint or "en"


MESSAGES: dict[str, dict[str, str]] = {
    "blocked": {
        "es": "No puedo ayudar con esa solicitud. Sí puedo orientarte con devoluciones, garantías, envíos, facturación, pagos y el estado de tus pedidos.",
        "en": "I can't help with that request. I can help you with returns, warranties, shipping, invoicing, payments and the status of your orders.",
    },
    "empty": {
        "es": "Escribe tu pregunta para poder ayudarte.",
        "en": "Type your question so I can help you.",
    },
    "too_long": {
        "es": "Tu mensaje es demasiado largo (máximo {max} caracteres). Resúmelo, por favor.",
        "en": "Your message is too long (maximum {max} characters). Please shorten it.",
    },
    "fallback": {
        "es": "No pude verificar esa respuesta con la información disponible, así que prefiero no darte datos que podrían ser incorrectos. ¿Quieres que abra un ticket para que una persona del equipo te ayude?",
        "en": "I couldn't verify that answer with the information I have, so I'd rather not give you details that might be wrong. Would you like me to open a ticket so a person on the team can help you?",
    },
    "leak": {
        "es": "No puedo compartir información interna de configuración. ¿En qué más puedo ayudarte con tu pedido o una duda de la tienda?",
        "en": "I can't share internal configuration details. How else can I help with your order or a question about the store?",
    },
    "confirm_needed": {
        "es": "Antes de crear el ticket necesito tu confirmación. Revisa los datos y confirma si todo está bien.",
        "en": "I need your confirmation before creating the ticket. Please review the details and confirm if everything looks right.",
    },
    "ticket_created": {
        "es": "Listo, creé el ticket {code}. Te responderemos al correo {email}; el tiempo de primera respuesta es de 24 horas hábiles.",
        "en": "Done, I created ticket {code}. We'll reply to {email}; first response time is 24 business hours.",
    },
    "ticket_cancelled": {
        "es": "Listo, no creé el ticket. ¿Necesitas algo más?",
        "en": "Okay, I didn't create the ticket. Is there anything else you need?",
    },
    "llm_unavailable": {
        "es": "El modelo no está disponible en este momento. Intenta de nuevo en unos minutos.",
        "en": "The model isn't available right now. Please try again in a few minutes.",
    },
    "busy": {
        "es": "Hay muchas consultas en este momento. Intenta de nuevo en unos segundos.",
        "en": "There are a lot of requests right now. Please try again in a few seconds.",
    },
    "internal": {
        "es": "Ocurrió un error inesperado. Intenta de nuevo.",
        "en": "Something went wrong. Please try again.",
    },
}

# Instruction for the model when it is asked to correct an answer (the output language is fixed here).
REPLY_IN: dict[str, str] = {"es": "Reply in Spanish.", "en": "Reply in English."}


def msg(key: str, lang: str = "en", **fmt: object) -> str:
    text = MESSAGES[key].get(lang) or MESSAGES[key]["en"]
    return text.format(**fmt) if fmt else text
