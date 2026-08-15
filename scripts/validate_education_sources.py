#!/usr/bin/env python3
"""Valida el catálogo versionado de fuentes educativas de Dream Home."""

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "sources" / "education.json"
ALLOWED_STATUSES = {"ready", "review_required", "portal_only", "restricted"}
ALLOWED_SEGMENTS = {"school", "higher_education", "cross"}
REQUIRED_FIELDS = {
    "id",
    "provider",
    "segment",
    "name",
    "landing_url",
    "status",
    "access",
    "grain",
    "join_keys",
    "cadence",
    "formats",
    "license_status",
    "signals",
    "priority",
    "notes",
}


def validate_catalog(catalog: dict) -> list[str]:
    errors: list[str] = []
    if catalog.get("schema_version") != 1:
        errors.append("schema_version debe ser 1")

    sources = catalog.get("sources")
    if not isinstance(sources, list) or not sources:
        return errors + ["sources debe ser una lista no vacía"]

    seen_ids: set[str] = set()
    for index, source in enumerate(sources):
        prefix = f"sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{prefix} debe ser un objeto")
            continue

        missing = sorted(REQUIRED_FIELDS - source.keys())
        if missing:
            errors.append(f"{prefix} omite: {', '.join(missing)}")

        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"{prefix}.id debe ser texto no vacío")
        elif source_id in seen_ids:
            errors.append(f"{prefix}.id duplicado: {source_id}")
        else:
            seen_ids.add(source_id)

        if source.get("status") not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status inválido: {source.get('status')!r}")
        if source.get("segment") not in ALLOWED_SEGMENTS:
            errors.append(f"{prefix}.segment inválido: {source.get('segment')!r}")

        url = source.get("landing_url")
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or not parsed.netloc:
            errors.append(f"{prefix}.landing_url debe ser HTTPS: {url!r}")

        for field in ("join_keys", "formats", "signals"):
            value = source.get(field)
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                errors.append(f"{prefix}.{field} debe ser una lista no vacía de textos")

    return errors


def main() -> int:
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL {CATALOG_PATH}: {error}")
        return 1

    errors = validate_catalog(catalog)
    if errors:
        print(f"FAIL {CATALOG_PATH}")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"OK   {CATALOG_PATH} ({len(catalog['sources'])} fuentes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
