"""Canonical identity and record construction."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

SCHEMA_VERSION = "1.0.0"


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def semantic_identity(
    *,
    language: str,
    locale: str | None,
    input_value: dict[str, str],
    mode: str,
    grammar: dict[str, Any],
) -> dict[str, Any]:
    return {
        "language": language,
        "locale": locale,
        "input": input_value,
        "mode": mode,
        "grammar": grammar,
    }


def record_id(
    *,
    language: str,
    locale: str | None,
    input_value: dict[str, str],
    mode: str,
    grammar: dict[str, Any],
) -> str:
    identity = semantic_identity(
        language=language,
        locale=locale,
        input_value=input_value,
        mode=mode,
        grammar=grammar,
    )
    payload = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:20]
    safe_language = "".join(ch if ch.isalnum() else "-" for ch in language.lower())
    return f"nfg-{safe_language}-{digest}"


def make_record(
    *,
    language: str,
    locale: str | None,
    value: str,
    form: str,
    mode: str = "cardinal",
    grammar: dict[str, Any] | None = None,
    quality: str,
    source_observations: list[dict[str, Any]],
    kind: str = "integer",
    accepted: list[str] | None = None,
    rejected: list[str] | None = None,
    notes: str = "",
    generator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    grammar_value = dict(grammar or {})
    input_value = {"kind": kind, "value": str(value)}
    canonical = nfc(form)
    accepted_values = [nfc(item) for item in (accepted or [canonical])]
    if canonical not in accepted_values:
        accepted_values.insert(0, canonical)
    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": record_id(
            language=language,
            locale=locale,
            input_value=input_value,
            mode=mode,
            grammar=grammar_value,
        ),
        "language": language,
        "locale": locale,
        "input": input_value,
        "mode": mode,
        "grammar": grammar_value,
        "oracle": {
            "canonical": canonical,
            "accepted": list(dict.fromkeys(accepted_values)),
            "rejected": list(dict.fromkeys(nfc(item) for item in (rejected or []))),
        },
        "quality": quality,
        "source_observations": source_observations,
    }
    if notes:
        row["notes"] = notes
    if generator is not None:
        row["generator"] = generator
    return row
