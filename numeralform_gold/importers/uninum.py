"""Import Google UniNum TSV files as citation-form Gold anchors."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ..model import make_record

UNINUM_REVISION = "b3d9064655a492e5cb71f6237383154d3ba74546"
UNINUM_URL = "https://github.com/google-research-datasets/uninum"

# ISO 639-3 -> BCP 47-ish language code for high-value MVP languages.
ISO3_TO_LANGUAGE = {
    "arb": "ar",
    "cmn": "zh",
    "deu": "de",
    "eng": "en",
    "fra": "fr",
    "ita": "it",
    "jpn": "ja",
    "lit": "lt",
    "pol": "pl",
    "por": "pt",
    "rus": "ru",
    "spa": "es",
}

CODE_TO_LOCALE = {
    "arb": "ar",
    "cmn_simplified": "zh-Hans-CN",
    "cmn_traditional": "zh-Hant-TW",
    "deu": "de-DE",
    "eng_in": "en-IN",
    "eng_us": "en-US",
    "fra_ch": "fr-CH",
    "fra_fr": "fr-FR",
    "ita": "it-IT",
    "jpn": "ja-JP",
    "lit": "lt-LT",
    "pol": "pl-PL",
    "por_br": "pt-BR",
    "por_pt": "pt-PT",
    "rus": "ru-RU",
    "spa": "es",
}


def _read_codes(root: Path) -> dict[str, dict[str, str]]:
    path = root / "codes.tsv"
    if not path.is_file():
        raise ValueError(f"missing UniNum codes.tsv: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows: dict[str, dict[str, str]] = {}
        for row in reader:
            code = (row.get("Code") or "").strip()
            if code:
                rows[code] = {key: (value or "") for key, value in row.items()}
    return rows


def _language(metadata: dict[str, str]) -> str:
    iso3 = metadata.get("ISO 639-3", "").strip()
    return ISO3_TO_LANGUAGE.get(iso3, iso3 or "und")


def _locale(code: str, metadata: dict[str, str]) -> str | None:
    if code in CODE_TO_LOCALE:
        return CODE_TO_LOCALE[code]
    language = _language(metadata)
    if language == "und":
        return None
    return language


def _number_rows(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for line_no, row in enumerate(reader, 1):
            if not row:
                continue
            if len(row) < 2:
                raise ValueError(f"{path}:{line_no}: expected value and form")
            value = row[0].strip()
            form = "\t".join(row[1:]).strip()
            if not value or not form:
                raise ValueError(f"{path}:{line_no}: empty value or form")
            rows.append((value, form))
    return rows


def import_uninum(
    root: Path,
    *,
    codes: set[str] | None = None,
    languages: set[str] | None = None,
    quality: str = "quarantine",
) -> list[dict[str, Any]]:
    """Return deterministic records from a pinned UniNum checkout."""

    metadata_by_code = _read_codes(root)
    numbers_root = root / "numbers"
    if not numbers_root.is_dir():
        raise ValueError(f"missing UniNum numbers directory: {numbers_root}")
    records: list[dict[str, Any]] = []
    for path in sorted(numbers_root.glob("*.tsv")):
        code = path.stem
        if codes is not None and code not in codes:
            continue
        metadata = metadata_by_code.get(code)
        if metadata is None:
            raise ValueError(f"UniNum code {code!r} has no codes.tsv metadata")
        language = _language(metadata)
        if languages is not None and language not in languages:
            continue
        locale = _locale(code, metadata)
        for value, form in _number_rows(path):
            source = {
                "benchmark": "uninum",
                "role": "gold_anchor",
                "source_id": f"{code}:{value}",
                "source_version": UNINUM_REVISION,
                "source_url": UNINUM_URL,
                "license": "Apache-2.0",
                "source_code": code,
                "iso_639_3": metadata.get("ISO 639-3") or None,
                "language_name": metadata.get("Language name(s)") or None,
                "script": metadata.get("Script") or None,
                "source_locale": metadata.get("Locale") or None,
                "variety": metadata.get("Variety") or None,
                "source_file": f"numbers/{path.name}",
            }
            records.append(
                make_record(
                    language=language,
                    locale=locale,
                    value=value,
                    form=form,
                    mode="cardinal",
                    grammar={"context": "citation"},
                    quality=quality,
                    source_observations=[source],
                    notes="Imported from UniNum as a citation-form Gold anchor.",
                )
            )
    return sorted(
        records,
        key=lambda row: (
            row["language"],
            row["locale"] or "",
            row["input"]["value"],
            row["id"],
        ),
    )
