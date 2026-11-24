#!/usr/bin/env python3
"""Resuelve y archiva releases públicos del catálogo educativo DH-040."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "sources" / "education.json"
DEFAULT_ARCHIVE_ROOT = ROOT / "data" / "raw" / "education"
ARCHIVE_EXTENSIONS = {".csv", ".rar", ".xlsx", ".zip"}
YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
USER_AGENT = "dream-home-data/DH-040 (+https://github.com/sbsepul/dream-home-data)"


class ArchiveError(RuntimeError):
    """Error seguro y explicable del proceso de archivado."""


@dataclass(frozen=True)
class ReleaseCandidate:
    url: str
    release_id: str
    extension: str


class DownloadLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(urljoin(self.base_url, href))


MACOS_ROOT_KEYCHAIN = Path("/System/Library/Keychains/SystemRootCertificates.keychain")


def _macos_root_certificates() -> str:
    """Raíces del llavero de macOS; incluyen CA recientes ausentes de /etc/ssl/cert.pem."""
    if not MACOS_ROOT_KEYCHAIN.is_file():
        return ""
    try:
        result = subprocess.run(
            ["security", "find-certificate", "-a", "-p", str(MACOS_ROOT_KEYCHAIN)],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout


def tls_context() -> ssl.SSLContext:
    """Usa raíces del sistema si el Python de macOS no instaló su bundle."""
    paths = ssl.get_default_verify_paths()
    if paths.cafile:
        return ssl.create_default_context()
    system_bundle = Path("/etc/ssl/cert.pem")
    context = (
        ssl.create_default_context(cafile=system_bundle)
        if system_bundle.is_file()
        else ssl.create_default_context()
    )
    if macos_roots := _macos_root_certificates():
        context.load_verify_locations(cadata=macos_roots)
    return context


def load_source(source_id: str, catalog_path: Path = CATALOG_PATH) -> dict:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    matches = [source for source in catalog["sources"] if source["id"] == source_id]
    if not matches:
        raise ArchiveError(f"fuente desconocida: {source_id}")
    source = matches[0]
    if source["status"] != "ready":
        raise ArchiveError(
            f"{source_id} tiene estado {source['status']!r}; sólo se archivan fuentes 'ready'"
        )
    if source["access"] != "public_download":
        raise ArchiveError(f"{source_id} no declara acceso public_download")
    return source


def release_candidates(
    html: str, landing_url: str, link_pattern: str | None = None
) -> list[ReleaseCandidate]:
    """Enlaces descargables con año; `link_pattern` filtra el nombre del archivo."""
    parser = DownloadLinkParser(landing_url)
    parser.feed(html)
    name_filter = re.compile(link_pattern, re.IGNORECASE) if link_pattern else None
    candidates: dict[str, ReleaseCandidate] = {}
    for url in parser.links:
        path = unquote(urlparse(url).path)
        extension = Path(path).suffix.lower()
        if extension not in ARCHIVE_EXTENSIONS:
            continue
        if name_filter and not name_filter.search(Path(path).name):
            continue
        years = YEAR_PATTERN.findall(Path(path).name)
        if not years:
            continue
        candidate = ReleaseCandidate(url=url, release_id=years[-1], extension=extension)
        candidates.setdefault(url, candidate)
    return sorted(
        candidates.values(),
        key=lambda candidate: (int(candidate.release_id), candidate.url),
        reverse=True,
    )


def _fetch_text(url: str) -> tuple[str, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60, context=tls_context()) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace"), response.geturl()


def agency_candidates(listing: dict, api_url: str, name_pattern: str) -> list[ReleaseCandidate]:
    """Releases del portal de la Agencia de Calidad, que expone su catálogo por REST.

    `name_pattern` debe capturar el año en el grupo `year`.
    """
    pattern = re.compile(name_pattern, re.IGNORECASE)
    candidates: dict[str, ReleaseCandidate] = {}
    for item in listing.get("data") or []:
        match = pattern.search((item.get("nombre") or "").strip())
        uuid = item.get("uuid")
        if not match or not uuid:
            continue
        name = item["nombre"].strip()
        suffix = Path(name).suffix.lower()
        extension = suffix if suffix in ARCHIVE_EXTENSIONS else ".rar"
        url = f"{api_url.rstrip('/')}/archivo/obtener?uuid={uuid}"
        candidates.setdefault(match.group("year"), ReleaseCandidate(url, match.group("year"), extension))
    return sorted(candidates.values(), key=lambda candidate: int(candidate.release_id), reverse=True)


def list_candidates(source: dict) -> list[ReleaseCandidate]:
    resolver = source.get("release_resolver") or {}
    if resolver.get("kind") == "agency_rest":
        api_url = resolver["api_url"]
        text, _ = _fetch_text(f"{api_url.rstrip('/')}/archivo/getAllByTipo/0")
        return agency_candidates(json.loads(text), api_url, resolver["name_pattern"])
    html, effective_url = _fetch_text(source["landing_url"])
    return release_candidates(html, effective_url, resolver.get("link_pattern"))


def resolve_release(source: dict, requested_release: str | None = None) -> ReleaseCandidate:
    candidates = list_candidates(source)
    if requested_release:
        candidates = [candidate for candidate in candidates if candidate.release_id == requested_release]
    if not candidates:
        suffix = f" para el release {requested_release}" if requested_release else ""
        raise ArchiveError(f"no se encontraron archivos descargables{suffix}")
    return candidates[0]


def _published_at(last_modified: str | None) -> str | None:
    if not last_modified:
        return None
    try:
        return parsedate_to_datetime(last_modified).astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (TypeError, ValueError):
        return None


def _existing_checksum(release_dir: Path) -> str | None:
    metadata_path = release_dir / "metadata.json"
    if not release_dir.exists():
        return None
    if not metadata_path.is_file():
        raise ArchiveError(f"release incompleto; falta {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return metadata.get("sha256")


def archive_release(source: dict, candidate: ReleaseCandidate, archive_root: Path) -> tuple[Path, bool]:
    source_root = archive_root / source["id"]
    source_root.mkdir(parents=True, exist_ok=True)
    request = Request(candidate.url, headers={"User-Agent": USER_AGENT})

    digest = hashlib.sha256()
    byte_count = 0
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".download-", dir=source_root, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            with urlopen(request, timeout=180, context=tls_context()) as response:
                resolved_url = response.geturl()
                media_type = response.headers.get_content_type()
                upstream_published_at = _published_at(response.headers.get("Last-Modified"))
                while chunk := response.read(1024 * 1024):
                    temporary.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())

        checksum = digest.hexdigest()
        archive_release_id = candidate.release_id
        release_dir = source_root / archive_release_id
        existing_checksum = _existing_checksum(release_dir)
        if existing_checksum == checksum:
            temporary_path.unlink()
            return release_dir, False
        if existing_checksum:
            archive_release_id = f"{candidate.release_id}-revision-{checksum[:12]}"
            release_dir = source_root / archive_release_id
            revision_checksum = _existing_checksum(release_dir)
            if revision_checksum == checksum:
                temporary_path.unlink()
                return release_dir, False
            if revision_checksum:
                raise ArchiveError(f"colisión de checksum en {release_dir}")

        release_dir.mkdir(parents=False, exist_ok=False)
        archive_path = release_dir / f"source{candidate.extension}"
        os.replace(temporary_path, archive_path)
        temporary_path = None
        metadata = {
            "source_id": source["id"],
            "release_id": archive_release_id,
            "upstream_release_id": candidate.release_id,
            "landing_url": source["landing_url"],
            "resolved_download_url": resolved_url,
            "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "upstream_published_at": upstream_published_at,
            "sha256": checksum,
            "bytes": byte_count,
            "media_type": media_type,
            "license": source.get("license") or "por confirmar",
            "terms_url": source.get("terms_url"),
        }
        metadata_path = release_dir / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return release_dir, True
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_id", help="id registrado en sources/education.json")
    parser.add_argument("--release-id", help="año a archivar; por defecto usa el más reciente")
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--list-releases", action="store_true", help="listar releases sin descargar")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        source = load_source(args.source_id)
        if args.list_releases:
            for candidate in list_candidates(source):
                print(f"{candidate.release_id}\t{candidate.url}")
            return 0

        candidate = resolve_release(source, args.release_id)
        release_dir, created = archive_release(source, candidate, args.archive_root)
        action = "ARCHIVED" if created else "UNCHANGED"
        print(f"{action} {source['id']} {candidate.release_id} -> {release_dir}")
        return 0
    except (ArchiveError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
