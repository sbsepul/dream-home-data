#!/usr/bin/env python3
"""Recolecta matrículas y mensualidades publicadas en los sitios web de los colegios.

Etapas (cada una lee lo que dejó la anterior):

    discover  sitio web por RBD: OpenStreetMap (website/correo) y dominios
              candidatos derivados del nombre, aceptados sólo si la portada
              menciona al colegio.
    crawl     visita cortés: respeta robots.txt, pausa entre pedidos a un mismo
              sitio, pocas páginas por colegio y prioriza enlaces de admisión y
              aranceles. Descarga PDF/DOCX/XLSX con nombres relacionados.
    extract   busca montos en pesos y UF junto a palabras como matrícula,
              mensualidad, colegiatura, arancel o cuota de incorporación.
    report    cobertura por etapa, documentos encontrados y montos por colegio.

Las páginas descargadas son datos no confiables: se guardan en
`data/raw/education/school_websites/` (fuera de Git) y sólo se leen como texto.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.robotparser
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archive_education_source import tls_context  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROFILES_PATH = ROOT / "data" / "build" / "education" / "colegios_perfil.json"
CRAWL_ROOT = ROOT / "data" / "raw" / "education" / "school_websites"
OUTPUT_ROOT = ROOT / "data" / "build" / "education" / "fees"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
UF_URL_TEMPLATE = "https://www.sii.cl/valores_y_fechas/uf/uf{year}.htm"
USER_AGENT = "Mozilla/5.0 (compatible; dream-home-data/school-fees; +https://github.com/sbsepul/dream-home-data)"

MAX_HTML_PAGES = 18
MAX_DOCUMENTS = 10
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_HTML_BYTES = 3 * 1024 * 1024
REQUEST_DELAY_SECONDS = 0.8
TIMEOUT_SECONDS = 20

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls"}
SKIP_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".mp4", ".mp3", ".zip", ".rar", ".css", ".js", ".ico", ".woff", ".woff2", ".ttf"}
FREE_MAIL = re.compile(r"(gmail|hotmail|yahoo|outlook|live\.|icloud|mineduc|vtr\.net|entel|terra\.cl|msn\.|gmx\.|tie\.cl|ctcinternet|manquehue\.net)", re.I)
SOCIAL_HOSTS = re.compile(r"(facebook|instagram|twitter|x\.com|youtube|linkedin|tiktok|wa\.me|whatsapp|google\.|goo\.gl|bit\.ly|linktr\.ee|mineduc\.cl|supereduc|agenciaeducacion|sistemadeadmisionescolar)", re.I)

# Prioridad de enlaces: cuánto se parece el texto o la URL a una página de aranceles.
LINK_KEYWORDS = {
    "arancel": 10, "aranceles": 10, "colegiatura": 10, "mensualidad": 10, "matricula": 8,
    "valores": 7, "costos": 7, "costo": 6, "cuota": 6, "incorporacion": 7, "financ": 5,
    "admision": 6, "admisiones": 6, "postula": 5, "proceso": 3, "pagos": 5, "precios": 7,
    "tuition": 8, "fees": 8, "admissions": 6, "documentos": 3, "descargas": 3, "apoderados": 2,
}
DOCUMENT_KEYWORDS = re.compile(
    r"arancel|colegiatura|mensualidad|matricula|valor|costo|cuota|incorporacion|admision|postula|financ|pago|precio|tuition|fee|contrato|reglamento|circular",
    re.I,
)

GENERIC_NAME_WORDS = {
    "COLEGIO", "ESCUELA", "LICEO", "INSTITUTO", "SCHOOL", "COLLEGE", "ACADEMIA", "CENTRO", "EDUCACIONAL",
    "COMPLEJO", "PARTICULAR", "BASICA", "CHILE", "INTERNATIONAL", "INTERNACIONAL",
}
ARTICLES = {"DE", "DEL", "LA", "LAS", "LOS", "EL", "Y", "THE", "OF", "AND", "A", "EN"}
COMMON_FOR_MATCH = {"SAN", "SANTA", "SANTO", "NUESTRA", "SENORA", "SAGRADO", "CORAZON", "MARIA", "JESUS", "SANTIAGO"}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def fold(text: str | None) -> str:
    """Minúsculas sin tildes para comparar textos."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


def name_tokens(name: str | None) -> list[str]:
    words = re.findall(r"[A-Z0-9]+", fold(name).upper())
    return [word for word in words if word not in GENERIC_NAME_WORDS and word not in ARTICLES and len(word) > 1]


def distinctive_tokens(name: str | None) -> set[str]:
    tokens = set(name_tokens(name))
    return (tokens - COMMON_FOR_MATCH) or tokens


def domain_candidates(name: str | None, comuna: str | None = None, extended: bool = False) -> list[str]:
    """Dominios plausibles para un colegio a partir de su nombre oficial.

    La pasada extendida suma la comuna al nombre y el dominio .com, para colegios
    que no aparecieron con los patrones básicos.
    """
    tokens = [token.lower() for token in name_tokens(name)]
    if not tokens:
        return []
    joined = "".join(tokens)
    stems = [joined, "".join(tokens[:2]), "".join(tokens[:3]), "-".join(tokens)]
    if len(tokens) >= 3:
        stems.append("".join(token[0] for token in tokens))
    candidates: list[str] = []

    def add(candidate: str) -> None:
        if candidate not in candidates and len(candidate) <= 44:
            candidates.append(candidate)

    for stem in stems:
        if len(stem) < 4:
            continue
        for prefix in ("colegio", "", "c"):
            add(f"{prefix}{stem}.cl")
    basic = candidates[:12]
    if not extended:
        return basic
    place = "".join(re.findall(r"[a-z0-9]+", fold(comuna)))
    for stem in dict.fromkeys([joined, "".join(tokens[:2])]):
        if place:
            for prefix in ("colegio", ""):
                add(f"{prefix}{stem}{place}.cl")
        for prefix in ("colegio", ""):
            add(f"{prefix}{stem}.com")
        add(f"{stem}school.cl")
        add(f"{stem}college.cl")
    return [candidate for candidate in candidates if candidate not in basic][:20]


def host_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


class LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self.title = ""
        self._href: str | None = None
        self._anchor: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and attributes.get("href"):
            self._href = urljoin(self.base_url, attributes["href"].strip())
            self._anchor = []
        elif tag in {"br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "section"}:
            self.text.append("\n")
        elif tag in {"iframe", "embed"} and attributes.get("src"):
            self.links.append((urljoin(self.base_url, attributes["src"].strip()), "iframe"))

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor).strip()))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        self.text.append(data)
        if self._href is not None:
            self._anchor.append(data.strip())


def parse_html(content: str, base_url: str) -> tuple[str, str, list[tuple[str, str]]]:
    parser = LinkParser(base_url)
    try:
        parser.feed(content)
    except Exception:  # HTML roto: se usa lo leído hasta el error.
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.text))
    text = re.sub(r"\n\s*\n+", "\n", text)
    return parser.title.strip(), text, parser.links


@dataclass
class Fetched:
    url: str
    status: int
    content_type: str
    body: bytes
    error: str | None = None


class PoliteClient:
    """Cliente HTTP con robots.txt y pausa por sitio."""

    def __init__(self, delay: float = REQUEST_DELAY_SECONDS) -> None:
        self.delay = delay
        self.context = tls_context()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _wait(self, host: str) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last.get(host, 0)
            wait = self.delay - elapsed
            self._last[host] = time.monotonic() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            fetched = self.get(f"{origin}/robots.txt", check_robots=False, limit=200_000)
            if fetched.status == 200 and b"<html" not in fetched.body[:500].lower():
                parser.parse(fetched.body.decode("utf-8", errors="replace").splitlines())
                self._robots[origin] = parser
            else:
                self._robots[origin] = None
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def get(self, url: str, *, check_robots: bool = True, limit: int = MAX_HTML_BYTES) -> Fetched:
        if check_robots and not self.allowed(url):
            return Fetched(url, 0, "", b"", "robots.txt")
        self._wait(host_of(url))
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "es-CL,es;q=0.9"})
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS, context=self.context) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    return Fetched(response.geturl(), response.status, response.headers.get_content_type(), b"", "demasiado grande")
                return Fetched(response.geturl(), response.status, response.headers.get_content_type(), body)
        except HTTPError as error:
            return Fetched(url, error.code, "", b"", f"HTTP {error.code}")
        except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError, ValueError) as error:
            return Fetched(url, 0, "", b"", type(error).__name__ + ": " + str(error)[:120])


def decode_body(fetched: Fetched) -> str:
    head = fetched.body[:4000].decode("ascii", errors="ignore")
    match = re.search(r"charset=[\"']?([A-Za-z0-9_-]+)", head)
    for encoding in ([match.group(1)] if match else []) + ["utf-8", "latin-1"]:
        try:
            return fetched.body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return fetched.body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def load_targets(profiles_path: Path, sectors: set[str]) -> list[dict]:
    schools = json.loads(profiles_path.read_text(encoding="utf-8"))["colegios"]
    return [school for school in schools if school["es_escolar"] and school["dependencia"]["sector"] in sectors]


def fetch_osm_schools(cache_path: Path) -> list[dict]:
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))["elements"]
    query = '[out:json][timeout:180];area["ISO3166-1"="CL"][admin_level=2]->.cl;nwr["amenity"="school"](area.cl);out center tags;'
    request = Request(OVERPASS_URL, data=("data=" + query).encode(), headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=240, context=tls_context()) as response:
        payload = response.read()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(payload)
    return json.loads(payload)["elements"]


def osm_site(tags: dict) -> tuple[str | None, str | None]:
    for key in ("website", "contact:website", "url"):
        value = (tags.get(key) or "").split(";")[0].strip()
        if value:
            return (value if value.startswith("http") else f"https://{value}"), "osm_website"
    for key in ("contact:email", "email"):
        value = (tags.get(key) or "").split(";")[0].strip()
        if "@" in value:
            domain = value.split("@", 1)[1].lower()
            if not FREE_MAIL.search(domain):
                return f"https://{domain}", "osm_email_domain"
    return None, None


def match_osm(school: dict, by_rbd: dict[int, dict], points: list[tuple]) -> dict | None:
    if school["rbd"] in by_rbd:
        return by_rbd[school["rbd"]]
    location = school.get("ubicacion")
    tokens = set(name_tokens(school["nombre"]))
    if not location or not tokens:
        return None
    best = None
    for lat, lon, other_tokens, element in points:
        if abs(lat - location["lat"]) > 0.01 or not other_tokens:
            continue
        distance = math.hypot((lat - location["lat"]) * 111_000, (lon - location["lon"]) * 111_000 * math.cos(math.radians(lat)))
        if distance > 600:
            continue
        similarity = len(tokens & other_tokens) / len(tokens | other_tokens)
        if similarity >= 0.5 and (best is None or similarity > best[0]):
            best = (similarity, element)
    return best[1] if best else None


def homepage_mentions(text: str, title: str, school_name: str) -> float:
    """Fracción de las palabras distintivas del nombre presentes en la portada."""
    tokens = distinctive_tokens(school_name)
    if not tokens:
        return 0.0
    haystack = " ".join(re.findall(r"[a-z0-9]+", fold(title + " " + text[:30_000])))
    present = sum(1 for token in tokens if re.search(rf"\b{token.lower()}\b", haystack))
    return present / len(tokens)


def verify_site(client: PoliteClient, url: str, school_name: str) -> tuple[str | None, float, str]:
    fetched = client.get(url, check_robots=False)
    if fetched.status != 200 or not fetched.body:
        return None, 0.0, fetched.error or f"HTTP {fetched.status}"
    if SOCIAL_HOSTS.search(host_of(fetched.url)):
        return None, 0.0, "redirige a red social o portal"
    title, text, _ = parse_html(decode_body(fetched), fetched.url)
    return fetched.url, homepage_mentions(text, title, school_name), title[:120]


def discover_one(client: PoliteClient, school: dict, osm_element: dict | None, extended: bool = False) -> dict:
    record = {"rbd": school["rbd"], "nombre": school["nombre"], "comuna": school["comuna"]["nombre"], "url": None, "metodo": None, "coincidencia": None, "titulo": None, "intentos": []}
    if osm_element:
        url, method = osm_site(osm_element.get("tags", {}))
        if url and not SOCIAL_HOSTS.search(host_of(url)):
            final_url, score, title = verify_site(client, url, school["nombre"])
            record["intentos"].append({"url": url, "metodo": method, "coincidencia": round(score, 2), "nota": title})
            # El sitio viene de OSM ligado al colegio: basta con que responda.
            if final_url:
                record.update(url=final_url, metodo=method, coincidencia=round(score, 2), titulo=title)
                return record
    for domain in domain_candidates(school["nombre"], school["comuna"]["nombre"], extended):
        try:
            socket.getaddrinfo(domain, 443)
        except OSError:
            continue
        final_url, score, title = verify_site(client, f"https://{domain}", school["nombre"])
        if not final_url:
            final_url, score, title = verify_site(client, f"http://{domain}", school["nombre"])
        record["intentos"].append({"url": f"https://{domain}", "metodo": "dominio_candidato", "coincidencia": round(score, 2), "nota": title})
        if final_url and score >= 0.75 and ("colegio" in fold(title + domain) or "school" in fold(title + domain) or score == 1.0):
            record.update(url=final_url, metodo="dominio_candidato", coincidencia=round(score, 2), titulo=title)
            return record
    return record


def command_discover(args: argparse.Namespace) -> int:
    targets = load_targets(args.profiles, set(args.sectors))
    elements = fetch_osm_schools(args.crawl_root / "_osm" / "schools.json")
    by_rbd, points = {}, []
    for element in elements:
        tags = element.get("tags", {})
        reference = (tags.get("ref:rbd") or tags.get("ref:RBD") or "").strip().split("-")[0]
        if reference.isdigit():
            by_rbd[int(reference)] = element
        lat = element.get("lat") or element.get("center", {}).get("lat")
        lon = element.get("lon") or element.get("center", {}).get("lon")
        if lat:
            points.append((lat, lon, set(name_tokens(tags.get("name"))), element))
    manual = json.loads(args.manual.read_text(encoding="utf-8")) if args.manual and args.manual.is_file() else {}
    previous: dict[int, dict] = {}
    sites_path = args.output / "sitios.json"
    if args.only_missing and sites_path.is_file():
        previous = {site["rbd"]: site for site in json.loads(sites_path.read_text(encoding="utf-8"))}
    client = PoliteClient()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for school in targets:
            if str(school["rbd"]) in manual:
                entry = manual[str(school["rbd"])]
                results.append({"rbd": school["rbd"], "nombre": school["nombre"], "comuna": school["comuna"]["nombre"], "url": entry["url"], "metodo": entry.get("metodo", "busqueda_web"), "coincidencia": None, "titulo": entry.get("nota"), "intentos": []})
                continue
            if previous.get(school["rbd"], {}).get("url"):
                results.append(previous[school["rbd"]])
                continue
            element = None if args.only_missing else match_osm(school, by_rbd, points)
            futures[pool.submit(discover_one, client, school, element, args.only_missing)] = school
        for index, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            if index % 50 == 0:
                print(f"... {index}/{len(futures)}", file=sys.stderr)
    results.sort(key=lambda item: item["rbd"])
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "sitios.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    found = Counter(item["metodo"] for item in results if item["url"])
    print(f"OK   sitios {sum(found.values())}/{len(results)} {dict(found)}")
    return 0


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------


def link_score(url: str, anchor: str) -> int:
    haystack = fold(url + " " + anchor)
    return sum(weight for keyword, weight in LINK_KEYWORDS.items() if keyword in haystack)


def extension_of(url: str) -> str:
    return Path(urlparse(url).path).suffix.lower()


def same_site(url: str, root_host: str) -> bool:
    host = host_of(url)
    return host == root_host or host.endswith("." + root_host) or root_host.endswith("." + host)


def is_document_host(url: str) -> bool:
    return bool(re.search(r"(drive\.google\.com|docs\.google\.com|dropbox\.com|issuu\.com|wixstatic\.com|amazonaws\.com|sharepoint\.com|onedrive)", url, re.I))


def google_drive_download(url: str) -> str | None:
    match = re.search(r"drive\.google\.com/(?:file/d/|open\?id=)([A-Za-z0-9_-]{20,})", url)
    return f"https://drive.google.com/uc?export=download&id={match.group(1)}" if match else None


def document_text(path: Path, extension: str) -> str:
    if extension == ".pdf":
        result = subprocess.run(["pdftotext", "-layout", "-l", "40", str(path), "-"], capture_output=True, timeout=120)
        return result.stdout.decode("utf-8", errors="replace")
    if extension in {".docx", ".xlsx"}:
        import zipfile

        try:
            with zipfile.ZipFile(path) as bundle:
                parts = [name for name in bundle.namelist() if name.startswith(("word/document", "xl/sharedStrings", "xl/worksheets/sheet"))]
                xml = " ".join(bundle.read(name).decode("utf-8", errors="replace") for name in parts)
        except zipfile.BadZipFile:
            return ""
        return html.unescape(re.sub(r"<[^>]+>", " ", xml))
    return ""


def crawl_site(client: PoliteClient, site: dict, crawl_root: Path) -> dict:
    root_url = site["url"]
    root_host = host_of(root_url)
    site_dir = crawl_root / str(site["rbd"])
    (site_dir / "pages").mkdir(parents=True, exist_ok=True)
    (site_dir / "docs").mkdir(parents=True, exist_ok=True)
    queue: list[tuple[int, int, str, str]] = [(-100, 0, root_url, "inicio")]
    seen: set[str] = set()
    pages, documents, skipped = [], [], Counter()
    document_links: dict[str, str] = {}
    while queue and len(pages) < MAX_HTML_PAGES:
        queue.sort()
        _, depth, url, anchor = queue.pop(0)
        url = urldefrag(url)[0]
        if url in seen:
            continue
        seen.add(url)
        fetched = client.get(url)
        if fetched.error == "robots.txt":
            skipped["robots"] += 1
            continue
        if fetched.status != 200 or not fetched.body:
            skipped["error"] += 1
            continue
        if fetched.content_type == "application/pdf":
            document_links.setdefault(fetched.url, anchor)
            continue
        if "html" not in fetched.content_type:
            continue
        title, text, links = parse_html(decode_body(fetched), fetched.url)
        digest = hashlib.sha256(fetched.url.encode()).hexdigest()[:16]
        (site_dir / "pages" / f"{digest}.txt").write_text(text, encoding="utf-8")
        pages.append({"url": fetched.url, "titulo": title[:160], "texto": f"pages/{digest}.txt", "puntaje_enlace": link_score(url, anchor)})
        for link, link_anchor in links:
            link = urldefrag(link)[0]
            if not link.startswith("http"):
                continue
            extension = extension_of(link)
            drive = google_drive_download(link)
            if extension in DOCUMENT_EXTENSIONS or drive or (is_document_host(link) and DOCUMENT_KEYWORDS.search(fold(link + link_anchor))):
                if DOCUMENT_KEYWORDS.search(fold(link + " " + link_anchor)) or link_score(url, anchor) >= 6:
                    document_links.setdefault(drive or link, link_anchor)
                continue
            if extension in SKIP_EXTENSIONS or not same_site(link, root_host) or link in seen:
                continue
            score = link_score(link, link_anchor)
            if depth >= 2 and score == 0:
                continue
            queue.append((-score, depth + 1, link, link_anchor))
    ranked_documents = sorted(document_links.items(), key=lambda item: -link_score(item[0], item[1]))
    for url, anchor in ranked_documents[:MAX_DOCUMENTS]:
        fetched = client.get(url, limit=MAX_DOCUMENT_BYTES)
        extension = extension_of(url)
        if fetched.content_type == "application/pdf" or fetched.body[:5] == b"%PDF-":
            extension = ".pdf"
        record = {"url": url, "texto_enlace": anchor[:160], "estado": fetched.status, "error": fetched.error, "tipo": extension.lstrip(".") or fetched.content_type}
        if fetched.status == 200 and fetched.body and extension in DOCUMENT_EXTENSIONS:
            digest = hashlib.sha256(fetched.body).hexdigest()
            path = site_dir / "docs" / f"{digest[:16]}{extension}"
            path.write_bytes(fetched.body)
            try:
                text = document_text(path, extension)
            except (subprocess.SubprocessError, OSError):
                text = ""
            (site_dir / "docs" / f"{digest[:16]}.txt").write_text(text, encoding="utf-8")
            record.update(sha256=digest, bytes=len(fetched.body), archivo=f"docs/{digest[:16]}{extension}", texto=f"docs/{digest[:16]}.txt", con_texto=bool(text.strip()))
        documents.append(record)
    index = {
        "rbd": site["rbd"],
        "sitio": root_url,
        "rastreado_en": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "paginas": pages,
        "documentos": documents,
        "omitidos": dict(skipped),
    }
    (site_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return index


def command_crawl(args: argparse.Namespace) -> int:
    sites = [site for site in json.loads((args.output / "sitios.json").read_text(encoding="utf-8")) if site["url"]]
    if not args.refresh:
        sites = [site for site in sites if not (args.crawl_root / str(site["rbd"]) / "index.json").is_file()]
    client = PoliteClient()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(crawl_site, client, site, args.crawl_root): site for site in sites}
        for future in as_completed(futures):
            done += 1
            try:
                future.result()
            except Exception as error:  # Un sitio roto no detiene el recorrido.
                print(f"WARN {futures[future]['rbd']}: {type(error).__name__}: {error}", file=sys.stderr)
            if done % 25 == 0:
                print(f"... {done}/{len(sites)}", file=sys.stderr)
    print(f"OK   rastreados {done} sitios")
    return 0


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

AMOUNT = re.compile(
    r"(?P<clp>(?:\$|clp)\s*(?P<clp_value>\d{1,3}(?:[.\s]\d{3})+|\d{4,8})(?![\d.,]*\s*uf))"
    r"|(?P<uf>(?:uf\s*(?P<uf_after>\d{1,3}(?:[.,]\d{1,3})?)(?!\d|[.,]\d)|(?<![\d.,])(?P<uf_before>\d{1,3}(?:[.,]\d{1,3})?)\s*(?:uf|u\.f\.)\b))"
    # Montos sin signo, típicos de tablas; el guion final descarta RUT.
    r"|(?P<bare>(?<![\d.,$])\d{1,3}(?:\.\d{3}){1,2}(?![\d.,]|\s*-|\s*uf))",
    re.I,
)
BARE_MAX_DISTANCE = 60

CATEGORIES = {
    "matricula": re.compile(r"matr[ií]cula(?!dos|das)"),
    "mensualidad": re.compile(r"mensualidad|colegiatura mensual|cuota mensual|arancel mensual|valor mensual|pension mensual|\bmensual\b|cuotas? de colegiatura|\bcolegiatura\b"),
    "arancel_anual": re.compile(r"arancel anual|colegiatura anual|valor anual|\banual\b|arancel"),
    "incorporacion": re.compile(r"incorporaci[oó]n|cuota de ingreso|bono de ingreso|derecho de ingreso"),
    "postulacion": re.compile(r"postulaci[oó]n|examen de admisi[oó]n|proceso de admisi[oó]n|derecho a examen|evaluaci[oó]n de admisi[oó]n|cuota (?:de )?admisi[oó]n"),
}

# Conceptos que no son arancel; ganan si están junto a la palabra clave o más cerca del monto.
NOT_A_FEE = re.compile(
    r"centro (?:general )?de (?:padres|apoderados)|\bcpa\b|cgpa|materiales?|seguro|textos|uniforme|almuerzo|casino|colaci[oó]n|transporte|furg[oó]n"
    r"|ingreso (?:familiar|declarado|per)|per c[aá]pita|\brenta\b|remuneraci|sueldo|\btope\b|multa|descuento|\bbeca|deuda|adeud|inter[eé]s|reajuste|donaci"
    r"|after ?school|jornada extendida|extra ?program|taller|complementary|plan mensual|comedor|men[uú]\b|\bbus\b"
)

# Rango plausible por categoría, en pesos.
PLAUSIBLE = {
    "matricula": (20_000, 1_600_000),
    "mensualidad": (30_000, 1_600_000),
    "arancel_anual": (1_000_000, 16_000_000),
    "incorporacion": (100_000, 25_000_000),
    "postulacion": (5_000, 400_000),
}


@dataclass
class Mention:
    categoria: str
    monto_clp: int
    unidad: str
    valor_original: float
    anio: int | None
    fuente: str
    tipo_fuente: str
    contexto: str
    distancia: int = field(default=0)


def parse_number(text: str, unit: str) -> float | None:
    text = text.strip().replace(" ", "")
    if unit == "clp":
        digits = text.replace(".", "")
        return float(digits) if digits.isdigit() else None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


PERIOD_AFTER = (
    ("arancel_anual", re.compile(r"^\s*(?:anual|al a[nñ]o|por a[nñ]o)\b")),
    ("mensualidad", re.compile(r"^\s*(?:mensual(?:es)?|al mes|por mes|x\s*10\b)")),
)


def nearest_category(before: str, after: str) -> tuple[str | None, int]:
    """Categoría del monto según la palabra clave más cercana.

    Manda la etiqueta anterior; la posterior sólo cuenta pegada al monto
    («$480.000 matrícula») y cuando la anterior está lejos. «UF 108 anual»
    y «UF 10,8 x 10» fijan el período aunque antes diga colegiatura.
    """
    for category, pattern in PERIOD_AFTER:
        if pattern.search(after):
            return category, 0
    best: tuple[str | None, int] = (None, 10**6)
    for category, pattern in CATEGORIES.items():
        for match in pattern.finditer(before):
            distance = len(before) - match.end()
            if distance < best[1]:
                best = (category, distance)
    if best[1] > 25:
        for category, pattern in CATEGORIES.items():
            match = pattern.match(after.lstrip(" .-:"))
            if match:
                return category, 5
    return best


def find_mentions(text: str, source: str, source_type: str, uf_value: float, default_year: int | None = None) -> list[Mention]:
    folded = fold(text)
    mentions: list[Mention] = []
    for match in AMOUNT.finditer(folded):
        if match.group("clp"):
            unit, raw = "clp", match.group("clp_value")
        elif match.group("bare"):
            unit, raw = "clp", match.group("bare")
        else:
            unit, raw = "uf", match.group("uf_after") or match.group("uf_before")
        value = parse_number(raw, unit)
        if value is None or value <= 0:
            continue
        before = folded[max(0, match.start() - 140) : match.start()]
        after = folded[match.end() : match.end() + 60]
        category, distance = nearest_category(before, after)
        if not category or distance > (BARE_MAX_DISTANCE if match.group("bare") else 120):
            continue
        excluded = [len(before) - found.end() for found in NOT_A_FEE.finditer(before)]
        if excluded and min(excluded) <= distance + 40:
            continue
        amount = round(value if unit == "clp" else value * uf_value)
        low, high = PLAUSIBLE[category]
        if not low <= amount <= high:
            continue
        window_start = max(0, match.start() - 400)
        window = folded[window_start : match.end() + 200]
        years = [(abs(window_start + found.start() - match.start()) + (0 if window_start + found.start() < match.start() else 150), int(found.group(1)))
                 for found in re.finditer(r"\b(202[4-8])\b", window)]
        year = min(years)[1] if years else default_year
        context = re.sub(r"\s+", " ", text[max(0, match.start() - 90) : match.end() + 30]).strip()
        mentions.append(Mention(category, amount, unit, value, year, source, source_type, context, distance))
    return mentions


def robust_values(values: list[int]) -> list[int]:
    """Quita lecturas de otra columna: el precio de un mismo colegio rara vez varía más del doble entre niveles."""
    if len(values) < 3:
        return values
    middle = sorted(values)[len(values) // 2]
    kept = [value for value in values if middle / 2 <= value <= middle * 2]
    return kept or values


def summarize_school(mentions: list[Mention]) -> dict:
    """Rango por categoría, preferiendo el año más reciente informado."""
    summary: dict[str, dict] = {}
    by_category: dict[str, list[Mention]] = defaultdict(list)
    for mention in mentions:
        by_category[mention.categoria].append(mention)
    for category, items in by_category.items():
        years = [item.anio for item in items if item.anio]
        latest = max(years) if years else None
        chosen = [item for item in items if item.anio == latest] if latest else items
        values = robust_values(sorted({item.monto_clp for item in chosen}))
        summary[category] = {
            "min_clp": values[0],
            "max_clp": values[-1],
            "anio": latest,
            "menciones": len(chosen),
            "en_uf": any(item.unidad == "uf" for item in chosen),
            "fuente": chosen[0].fuente,
            "tipo_fuente": chosen[0].tipo_fuente,
            "contexto": chosen[0].contexto[:220],
        }
    monthly = summary.get("mensualidad")
    annual = summary.get("arancel_anual")
    if not monthly and annual:
        summary["mensualidad_derivada"] = {"min_clp": round(annual["min_clp"] / 10), "max_clp": round(annual["max_clp"] / 10), "anio": annual["anio"], "regla": "arancel anual / 10 cuotas"}
    return summary


MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def parse_uf(content: str, today: datetime) -> float:
    """UF del día desde la tabla anual del SII (una tabla por mes)."""
    marker = re.search(rf"id=[\"']mes_{MONTHS[today.month - 1]}[\"']", content)
    if not marker:
        raise RuntimeError("la página del SII no tiene la tabla del mes")
    section = content[marker.start() : marker.start() + 8000]
    cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", section, re.S)]
    for index, cell in enumerate(cells[:-1]):
        if cell == str(today.day) and re.fullmatch(r"\d{2}\.\d{3},\d{2}", cells[index + 1]):
            return float(cells[index + 1].replace(".", "").replace(",", "."))
    raise RuntimeError("no se encontró la UF del día en el SII")


def fetch_uf(today: datetime) -> float:
    fetched = PoliteClient(delay=0).get(UF_URL_TEMPLATE.format(year=today.year), check_robots=False)
    if fetched.status != 200:
        raise RuntimeError(f"no se pudo leer la UF del SII: {fetched.error}")
    return parse_uf(fetched.body.decode("latin-1"), today)


SITE_SIGNALS = {
    "bilingue": re.compile(r"\bbiling[uü]e|\bbilingual\b|\bbicultural\b|inmersi[oó]n en (?:ingl|alem|franc)"),
    "bachillerato_internacional": re.compile(r"bachillerato internacional|international baccalaureate|\bib world school|programa del diploma del ib|\bib diploma"),
    "cambridge": re.compile(r"cambridge (?:assessment|international|english|school)|\bigcse\b"),
}
# Un idioma cuenta sólo si aparece como enseñanza, no en una mención cualquiera.
SITE_LANGUAGES = {
    "ingles": r"ingl[eé]s",
    "aleman": r"alem[aá]n",
    "frances": r"franc[eé]s",
    "italiano": r"italiano",
    "chino_mandarin": r"mandar[ií]n|chino mandar",
    "portugues": r"portugu[eé]s",
    "japones": r"japon[eé]s",
    "hebreo": r"hebreo",
}
TEACHING_CONTEXT = r"(?:clases? de|idioma|lengua|asignatura|aprend\w*|ense[nñ]\w*|curso de|programa de|segundo idioma|tercer idioma)"


def website_language_signals(texts: list[str]) -> dict | None:
    folded = " ".join(fold(text) for text in texts)
    signals = {key: bool(pattern.search(folded)) for key, pattern in SITE_SIGNALS.items()}
    languages = sorted(
        language for language, word in SITE_LANGUAGES.items()
        if re.search(rf"{TEACHING_CONTEXT}\W+(?:\w+\W+){{0,3}}{fold(word)}|{fold(word)}\W+(?:\w+\W+){{0,2}}como (?:segundo|tercer) idioma", folded)
    )
    if not any(signals.values()) and not languages:
        return None
    return {**signals, "idiomas_mencionados": languages}


def mentions_comuna(texts: list[str], comuna: str | None) -> bool:
    if not comuna:
        return False
    target = " ".join(re.findall(r"[a-z0-9]+", fold(comuna)))
    pattern = re.compile(rf"\b{re.escape(target)}\b")
    return any(pattern.search(" ".join(re.findall(r"[a-z0-9]+", fold(text)))) for text in texts)


def site_confidence(site: dict, comuna_found: bool) -> str:
    """Un dominio adivinado puede ser de un homónimo: exige que el sitio nombre la comuna."""
    if comuna_found:
        return "alta"
    if site["metodo"] == "osm_website" and (site.get("coincidencia") or 0) >= 0.5:
        return "alta"
    return "baja"


def command_extract(args: argparse.Namespace) -> int:
    uf_value = args.uf or fetch_uf(datetime.now())
    sites = {site["rbd"]: site for site in json.loads((args.output / "sitios.json").read_text(encoding="utf-8"))}
    profiles = {school["rbd"]: school for school in json.loads(args.profiles.read_text(encoding="utf-8"))["colegios"]}
    hosts = Counter(host_of(site["url"]) for site in sites.values() if site["url"])
    schools, documents = [], []
    for rbd, site in sorted(sites.items()):
        profile = profiles.get(rbd, {})
        index_path = args.crawl_root / str(rbd) / "index.json"
        record = {
            "rbd": rbd, "nombre": site["nombre"], "comuna": site["comuna"], "region": (profile.get("region") or {}).get("codigo"),
            "sitio": site["url"], "metodo_sitio": site["metodo"], "sitio_compartido": bool(site["url"]) and hosts[host_of(site["url"])] > 1,
            "rastreado": index_path.is_file(), "paginas": 0, "documentos": 0, "comuna_en_sitio": False, "confianza_sitio": None,
            "montos": {}, "menciones": [],
        }
        if index_path.is_file():
            index = json.loads(index_path.read_text(encoding="utf-8"))
            mentions: list[Mention] = []
            texts: list[str] = []
            record["paginas"] = len(index["paginas"])
            for page in index["paginas"]:
                text = (args.crawl_root / str(rbd) / page["texto"]).read_text(encoding="utf-8")
                texts.append(text)
                mentions += find_mentions(text, page["url"], "html", uf_value)
            for document in index["documentos"]:
                has_fees = False
                if document.get("texto"):
                    text = (args.crawl_root / str(rbd) / document["texto"]).read_text(encoding="utf-8")
                    texts.append(text)
                    found = find_mentions(text, document["url"], document["tipo"], uf_value)
                    mentions += found
                    has_fees = bool(found)
                record["documentos"] += 1
                documents.append({
                    "rbd": rbd, "nombre": site["nombre"], "url": document["url"], "tipo": document["tipo"],
                    "texto_enlace": document.get("texto_enlace"), "descargado": bool(document.get("sha256")),
                    "con_texto": document.get("con_texto", False), "con_montos": has_fees, "bytes": document.get("bytes"),
                    "error": document.get("error"),
                })
            record["comuna_en_sitio"] = mentions_comuna(texts, site["comuna"])
            record["idiomas_sitio"] = website_language_signals(texts)
            record["confianza_sitio"] = site_confidence(site, record["comuna_en_sitio"]) if record["paginas"] else None
            record["montos"] = summarize_school(mentions)
            record["menciones"] = [mention.__dict__ for mention in mentions[:40]]
        schools.append(record)
    (args.output / "aranceles.json").write_text(json.dumps({"uf_clp": uf_value, "generado_en": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "colegios": schools}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with (args.output / "documentos.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rbd", "nombre", "tipo", "url", "texto_enlace", "descargado", "con_texto", "con_montos", "bytes", "error"])
        writer.writeheader()
        writer.writerows(documents)
    print(f"OK   extraídos {sum(1 for school in schools if school['montos'])} colegios con montos; UF {uf_value}")
    return 0


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

FUNNEL_LABELS = {
    "base": "Particulares pagados",
    "con_sitio_web": "Con sitio web identificado",
    "sitio_confiable": "Sitio confirmado (nombre y comuna)",
    "con_algun_monto": "Publican algún monto",
    "con_matricula": "Publican matrícula",
    "con_mensualidad_explicita_o_derivada": "Publican mensualidad o arancel anual",
    "con_matricula_y_mensualidad": "Publican matrícula y mensualidad",
    "con_cuota_incorporacion": "Publican cuota de incorporación",
    "mensualidad_2026_o_2027": "Mensualidad de 2026 o 2027",
}


def trusted_fees(school: dict) -> dict:
    return school["montos"] if school.get("confianza_sitio") == "alta" else {}


def coverage_report(data: dict, documents: list[dict]) -> dict:
    schools = data["colegios"]
    total = len(schools)

    def pct(count: int, base: int = total) -> dict:
        return {"colegios": count, "pct": round(100 * count / base, 1) if base else None}

    def has_monthly(fees: dict) -> bool:
        return "mensualidad" in fees or "mensualidad_derivada" in fees

    with_site = [school for school in schools if school["sitio"]]
    trusted = [school for school in with_site if school.get("confianza_sitio") == "alta"]
    fees = [trusted_fees(school) for school in schools]
    current = [f for f in fees if any((f.get(key) or {}).get("anio") in (2026, 2027) for key in ("mensualidad", "mensualidad_derivada"))]
    trusted_rbd = {str(school["rbd"]) for school in trusted}
    trusted_documents = [document for document in documents if str(document["rbd"]) in trusted_rbd]
    from_document = [school for school, f in zip(schools, fees) if any(isinstance(item, dict) and item.get("tipo_fuente") not in (None, "html") for item in f.values())]
    return {
        "base_colegios": total,
        "embudo": {
            "base": pct(total),
            "con_sitio_web": pct(len(with_site)),
            "sitio_confiable": pct(len(trusted)),
            "con_algun_monto": pct(sum(1 for f in fees if f)),
            "con_matricula": pct(sum(1 for f in fees if "matricula" in f)),
            "con_mensualidad_explicita_o_derivada": pct(sum(1 for f in fees if has_monthly(f))),
            "con_matricula_y_mensualidad": pct(sum(1 for f in fees if "matricula" in f and has_monthly(f))),
            "con_cuota_incorporacion": pct(sum(1 for f in fees if "incorporacion" in f)),
            "mensualidad_2026_o_2027": pct(len(current)),
        },
        "mensualidad_solo_derivada": sum(1 for f in fees if "mensualidad" not in f and "mensualidad_derivada" in f),
        "sitios_baja_confianza": sum(1 for school in with_site if school.get("confianza_sitio") == "baja"),
        "sitios_sin_paginas": sum(1 for school in with_site if not school["paginas"]),
        "sitios_compartidos": sum(1 for school in with_site if school.get("sitio_compartido")),
        "documentos": {
            "total_enlazados": len(trusted_documents),
            "por_tipo": dict(Counter(document["tipo"] for document in trusted_documents)),
            "descargados": sum(1 for document in trusted_documents if document["descargado"]),
            "colegios_con_documento": pct(len({document["rbd"] for document in trusted_documents if document["descargado"]})),
            "colegios_con_montos_en_documento": pct(len({document["rbd"] for document in trusted_documents if document["con_montos"]})),
            "pdf_sin_texto_extraible": sum(1 for document in trusted_documents if document["tipo"] == "pdf" and document["descargado"] and not document["con_texto"]),
            "colegios_cuyo_monto_viene_de_documento": pct(len(from_document)),
        },
        "metodo_sitio": dict(Counter(school["metodo_sitio"] for school in trusted)),
    }


def command_report(args: argparse.Namespace) -> int:
    data = json.loads((args.output / "aranceles.json").read_text(encoding="utf-8"))
    with (args.output / "documentos.csv").open(encoding="utf-8") as handle:
        documents = [dict(row, descargado=row["descargado"] == "True", con_texto=row["con_texto"] == "True", con_montos=row["con_montos"] == "True") for row in csv.DictReader(handle)]
    report = coverage_report(data, documents)
    report["uf_clp"] = data["uf_clp"]
    (args.output / "cobertura.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output / "cobertura_embudo.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["etapa", "etiqueta", "colegios", "pct"])
        writer.writeheader()
        for stage, value in report["embudo"].items():
            writer.writerow({"etapa": stage, "etiqueta": FUNNEL_LABELS[stage], **value})
    rows = []
    for school in data["colegios"]:
        fees = trusted_fees(school)
        monthly = fees.get("mensualidad") or fees.get("mensualidad_derivada") or {}
        rows.append({
            "rbd": school["rbd"], "nombre": school["nombre"], "comuna": school["comuna"], "region": school.get("region"), "sitio": school["sitio"],
            "confianza_sitio": school.get("confianza_sitio"), "sitio_compartido": school.get("sitio_compartido"),
            "matricula_min_clp": (fees.get("matricula") or {}).get("min_clp"), "matricula_max_clp": (fees.get("matricula") or {}).get("max_clp"),
            "mensualidad_min_clp": monthly.get("min_clp"), "mensualidad_max_clp": monthly.get("max_clp"),
            "mensualidad_derivada": "mensualidad" not in fees and "mensualidad_derivada" in fees,
            "arancel_anual_max_clp": (fees.get("arancel_anual") or {}).get("max_clp"),
            "incorporacion_max_clp": (fees.get("incorporacion") or {}).get("max_clp"),
            "anio": monthly.get("anio") or (fees.get("matricula") or {}).get("anio"),
            "fuente": (fees.get("mensualidad") or fees.get("matricula") or fees.get("arancel_anual") or {}).get("fuente"),
            "contexto": (fees.get("mensualidad") or fees.get("matricula") or fees.get("arancel_anual") or {}).get("contexto"),
        })
    with (args.output / "aranceles.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["discover", "crawl", "extract", "report"])
    parser.add_argument("--profiles", type=Path, default=PROFILES_PATH)
    parser.add_argument("--crawl-root", type=Path, default=CRAWL_ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--sectors", nargs="+", default=["particular_pagado"])
    parser.add_argument("--manual", type=Path, help="JSON {rbd: {url, metodo, nota}} con sitios encontrados a mano o por búsqueda")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--refresh", action="store_true", help="volver a rastrear sitios ya rastreados")
    parser.add_argument("--only-missing", action="store_true", help="discover: sólo colegios sin sitio, con patrones de dominio extendidos")
    parser.add_argument("--uf", type=float, help="valor de la UF en pesos; por defecto se lee del SII")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    commands = {"discover": command_discover, "crawl": command_crawl, "extract": command_extract, "report": command_report}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
