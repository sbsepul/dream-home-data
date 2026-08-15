#!/usr/bin/env python3
"""Verifica archivos educativos crudos contra su metadata de procedencia."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_ROOT = ROOT / "data" / "raw" / "education"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(archive_root: Path) -> tuple[list[str], int, int]:
    errors: list[str] = []
    releases = 0
    total_bytes = 0
    metadata_paths = sorted(archive_root.glob("*/*/metadata.json"))
    if not metadata_paths:
        return [f"no hay releases en {archive_root}"], 0, 0

    for metadata_path in metadata_paths:
        release_dir = metadata_path.parent
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{metadata_path}: metadata inválida: {error}")
            continue

        source_files = [path for path in release_dir.glob("source.*") if path.is_file()]
        if len(source_files) != 1:
            errors.append(f"{release_dir}: se esperaba exactamente un source.*")
            continue
        source_path = source_files[0]
        actual_bytes = source_path.stat().st_size
        actual_checksum = sha256(source_path)
        if metadata.get("bytes") != actual_bytes:
            errors.append(f"{source_path}: tamaño no coincide")
        if metadata.get("sha256") != actual_checksum:
            errors.append(f"{source_path}: SHA-256 no coincide")
        if metadata.get("source_id") != release_dir.parent.name:
            errors.append(f"{metadata_path}: source_id no coincide con la ruta")
        if metadata.get("release_id") != release_dir.name:
            errors.append(f"{metadata_path}: release_id no coincide con la ruta")
        releases += 1
        total_bytes += actual_bytes

    return errors, releases, total_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    args = parser.parse_args()
    errors, releases, total_bytes = verify_archive(args.archive_root)
    if errors:
        print("FAIL archivo educativo")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"OK   {releases} releases, {total_bytes:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
