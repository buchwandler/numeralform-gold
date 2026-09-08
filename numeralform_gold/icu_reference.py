"""Optional ICU/CLDR-style reference generation."""

from __future__ import annotations

from typing import Any

from .model import make_record

CLDR_REFERENCE_REVISION = "11299982335beb974c1c63c45265184e759c0f41"
CLDR_REFERENCE_TAG = "release-48-2"


def _runtime() -> tuple[Any, str]:
    try:
        import icu_rbnf
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "ICU reference generation requires the optional dependency: "
            'pip install -e ".[icu]"'
        ) from exc
    version = str(getattr(icu_rbnf, "__version__", "unknown"))
    return icu_rbnf, version


def generate_integer_range(locale: str, start: int, end: int) -> list[dict[str, Any]]:
    if end < start:
        raise ValueError("end must be >= start")
    runtime, version = _runtime()
    language = locale.replace("-", "_").split("_", 1)[0].lower()
    bcp47_locale = locale.replace("_", "-")
    rows: list[dict[str, Any]] = []
    for value in range(start, end + 1):
        form = runtime.spellout(value, locale)
        rows.append(
            make_record(
                language=language,
                locale=bcp47_locale,
                value=str(value),
                form=str(form),
                mode="cardinal",
                grammar={"context": "citation"},
                quality="reference",
                source_observations=[
                    {
                        "benchmark": "icu_rbnf_runtime",
                        "role": "generated_reference",
                        "source_id": f"{locale}:{value}",
                        "source_version": version,
                        "source_url": "https://github.com/OHF-Voice/icu-rbnf",
                        "license": None,
                    }
                ],
                generator={
                    "name": "icu-rbnf",
                    "version": version,
                    "locale": locale,
                    "standard": "CLDR/ICU RBNF",
                    "cldr_reference_tag": CLDR_REFERENCE_TAG,
                    "cldr_reference_revision": CLDR_REFERENCE_REVISION,
                    "reproducibility_note": (
                        "Runtime ICU data is recorded separately; it is not "
                        "asserted to be "
                        "byte-identical to the pinned CLDR source checkout."
                    ),
                },
                notes="Generated reference row; not automatically canonical Gold.",
            )
        )
    return rows
