#!/usr/bin/env python3
"""Construye el perfil nacional de colegios desde los releases archivados.

Une, por RBD, las fuentes generales que cubren a casi todos los establecimientos:
identidad y dependencia, niveles y grados impartidos, matrícula, tamaño de curso,
docentes, tramos de pago autodeclarados, copago FICOM, SEP, rendimiento y retiro,
SIMCE, IDPS (incluida la percepción de ambiente seguro), PAES agregada y
denuncias ante la Superintendencia.

Los originales se leen desde `data/raw/education/` (ver
`archive_education_source.py`). Los productos quedan en `data/build/education/`,
excluido de Git hasta confirmar la licencia de redistribución de cada fuente.
Nunca se escriben filas individuales: PAES y denuncias se agregan por RBD.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_ROOT = ROOT / "data" / "raw" / "education"
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "build" / "education"
SCHEMA_VERSION = 1

# PAES se publica por participante; bajo este umbral el agregado del colegio se suprime.
MIN_PAES_PARTICIPANTS = 10

MISSING_VALUES = {"", "NA", "N/A", "NULL", "-", "."}

DEPENDENCIES = {
    1: ("Corporación Municipal", "publico"),
    2: ("Municipal DAEM", "publico"),
    3: ("Particular Subvencionado", "particular_subvencionado"),
    4: ("Particular Pagado", "particular_pagado"),
    5: ("Corporación de Administración Delegada (DL 3166)", "administracion_delegada"),
    6: ("Servicio Local de Educación Pública", "publico"),
}

SCHOOL_STATES = {
    1: "funcionando",
    2: "en_receso",
    3: "cerrado",
    4: "autorizado_sin_matricula",
}

RELIGIOUS_ORIENTATIONS = {
    1: "laica",
    2: "catolica",
    3: "evangelica",
    4: "musulmana",
    5: "judia",
    6: "budista",
    7: "otra",
    9: None,
}

# Tramos autodeclarados del Directorio, en pesos corrientes.
PAYMENT_BANDS = {
    "GRATUITO": ("gratuito", 0, 0),
    "$1.000 A $10.000": ("1000_10000", 1_000, 10_000),
    "$10.001 A $25.000": ("10001_25000", 10_001, 25_000),
    "$25.001 A $50.000": ("25001_50000", 25_001, 50_000),
    "$50.001 A $100.000": ("50001_100000", 50_001, 100_000),
    "MAS DE $100.000": ("mas_100000", 100_001, None),
}

GRADE_ORDER = ["PK", "K"] + [f"{grade}B" for grade in range(1, 9)] + [f"{grade}M" for grade in range(1, 5)]
YOUTH_SECONDARY_CODES = {310, 410, 510, 610, 710, 810, 910}
# COD_ENSE2 de enseñanza regular de niños y jóvenes en el resumen de rendimiento.
YOUTH_REGULAR_ENSE2 = {2, 5, 7}

SIMCE_SUBJECTS = {"lect": "lectura", "mate": "matematica", "hist": "historia", "cnat": "ciencias"}
SIMCE_GRADES = {"2b": "2B", "4b": "4B", "6b": "6B", "8b": "8B", "2m": "2M", "3m": "3M"}

IDPS_INDICATORS = {
    1: "autoestima_motivacion",
    2: "clima_convivencia",
    3: "participacion_ciudadana",
    4: "habitos_vida_saludable",
}
IDPS_SAFE_ENVIRONMENT_DIMENSION = 23

COMPLAINT_TOPICS = {
    "maltrato": ("MALTRATO A PÁRVULOS Y/O ESTUDIANTES", "MALTRATO A MIEMBROS ADULTOS DE LA COMUNIDAD EDUCATIVA"),
    "connotacion_sexual": ("SITUACIONES DE CONNOTACIÓN SEXUAL",),
    "discriminacion": ("DISCRIMINACIÓN",),
}


# Idiomas por nombre de asignatura (texto en mayúsculas y sin tildes). «Franciscana»
# o «Latinoamericana» no deben contar como francés o latín.
FOREIGN_LANGUAGES = {
    "ingles": re.compile(r"INGLES|\bENGLISH\b"),
    "aleman": re.compile(r"\bALEMAN\b|DEUTSCH"),
    "frances": re.compile(r"\bFRANCES\b|FRANCAIS"),
    "italiano": re.compile(r"\bITALIANO\b"),
    "chino_mandarin": re.compile(r"MANDARIN|\bCHINO\b"),
    "japones": re.compile(r"\bJAPONES\b"),
    "hebreo": re.compile(r"\bHEBREO\b"),
    "portugues": re.compile(r"\bPORTUGUES\b"),
    "latin": re.compile(r"\bLATIN\b"),
}
INDIGENOUS_LANGUAGES = {
    "mapuzugun": re.compile(r"MAPUZUGUN|MAPUDUNGUN|MAPUCHE"),
    "aymara": re.compile(r"\bAYMARA\b"),
    "quechua": re.compile(r"\bQUECHUA\b"),
    "rapa_nui": re.compile(r"RAPA ?NUI"),
    "kawesqar": re.compile(r"KAWES"),
    "yagan": re.compile(r"\bYAGAN\b"),
    "likan_antai": re.compile(r"LIKAN|KUNZA"),
    "pueblos_originarios": re.compile(r"PUEBLOS ORIGINARIOS|PPOOAA|LENGUA INDIGENA|INTERCULTURALIDAD|EDUCACION INTERCULTURAL"),
}
# El inglés es obligatorio en el currículo nacional desde 5° básico.
ENGLISH_MANDATORY_FROM = "5B"
# Pistas en el nombre del colegio (sin tildes, mayúsculas).
NAME_LANGUAGE_HINTS = {
    "ingles": re.compile(r"\b(ENGLISH|BRITISH|AMERICAN|SCHOOL|COLLEGE|ACADEMY)\b"),
    "aleman": re.compile(r"\b(ALEMAN|DEUTSCHE|SCHULE)\b"),
    "frances": re.compile(r"\b(LYCEE|FRANCES|FRANCAIS|ALLIANCE)\b"),
    "italiano": re.compile(r"\b(SCUOLA|ITALIANA|ITALIANO)\b"),
    "hebreo": re.compile(r"\b(HEBREO|HEBREW)\b"),
}


def fold_upper(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in text if not unicodedata.combining(char)).upper()


class BuildError(RuntimeError):
    """Error explicable al construir el perfil."""


# ---------------------------------------------------------------------------
# Lectura de releases archivados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    source_id: str
    release_id: str
    directory: Path
    metadata: dict

    @property
    def archive_path(self) -> Path:
        matches = sorted(self.directory.glob("source.*"))
        if not matches:
            raise BuildError(f"{self.directory} no contiene source.*")
        return matches[0]

    def provenance(self) -> dict:
        return {
            "release_id": self.release_id,
            "upstream_release_id": self.metadata.get("upstream_release_id", self.release_id),
            "sha256": self.metadata.get("sha256"),
            "resolved_download_url": self.metadata.get("resolved_download_url"),
            "fetched_at": self.metadata.get("fetched_at"),
            "license": self.metadata.get("license"),
        }


def find_release(archive_root: Path, source_id: str, release_id: str | None = None) -> Release | None:
    """Release más reciente de una fuente; una revisión gana sobre su original."""
    source_root = archive_root / source_id
    if not source_root.is_dir():
        return None
    releases: list[tuple[tuple[int, str], Release]] = []
    for directory in source_root.iterdir():
        metadata_path = directory / "metadata.json"
        if not metadata_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        upstream = str(metadata.get("upstream_release_id") or metadata.get("release_id") or directory.name)
        if release_id and upstream != release_id:
            continue
        if not upstream.isdigit():
            continue
        release = Release(source_id, directory.name, directory, metadata)
        releases.append(((int(upstream), metadata.get("fetched_at") or ""), release))
    if not releases:
        return None
    return max(releases, key=lambda item: item[0])[1]


def _extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (destination / member.filename).resolve()
                if not str(target).startswith(str(destination.resolve())):
                    raise BuildError(f"ruta insegura en {archive}: {member.filename}")
            bundle.extractall(destination)
        return
    bsdtar = shutil.which("bsdtar") or shutil.which("tar")
    if not bsdtar:
        raise BuildError("se necesita bsdtar (libarchive) para extraer RAR")
    # Los XLSX gemelos de la Agencia pesan cientos de MB y repiten los CSV. Se usan
    # exclusiones porque bsdtar falla cuando un patrón --include no aparece.
    command = [bsdtar, "-xf", str(archive), "-C", str(destination)]
    for pattern in ("*.pdf", "*.PDF", "*Archivos XLS*"):
        command += ["--exclude", pattern]
    subprocess.run(command, check=True, capture_output=True)


def extracted_dir(release: Release, cache_root: Path) -> Path:
    """Extrae un release (y sus archivos anidados) una sola vez por checksum."""
    checksum = (release.metadata.get("sha256") or release.release_id)[:16]
    target = cache_root / release.source_id / f"{release.release_id}-{checksum}"
    if (target / ".complete").is_file():
        return target
    if target.exists():
        shutil.rmtree(target)
    archive = release.archive_path
    if archive.suffix.lower() in {".csv", ".xlsx"}:
        target.mkdir(parents=True)
        shutil.copy2(archive, target / archive.name)
    else:
        _extract_archive(archive, target)
        for _depth in range(3):
            nested = [path for path in target.rglob("*") if path.suffix.lower() in {".rar", ".zip"}]
            if not nested:
                break
            for path in nested:
                _extract_archive(path, path.with_suffix(""))
                path.unlink()
    (target / ".complete").write_text("ok\n", encoding="utf-8")
    return target


def find_files(directory: Path, pattern: str) -> list[Path]:
    regex = re.compile(pattern, re.IGNORECASE)
    return sorted(path for path in directory.rglob("*") if path.is_file() and regex.search(path.name))


def find_file(directory: Path, pattern: str) -> Path:
    matches = find_files(directory, pattern)
    if not matches:
        raise BuildError(f"no hay archivo {pattern!r} en {directory}")
    return matches[0]


# ---------------------------------------------------------------------------
# Parsers genéricos
# ---------------------------------------------------------------------------


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return None if value.upper() in MISSING_VALUES else value


def to_int(value: str | None) -> int | None:
    value = clean(value)
    if value is None:
        return None
    try:
        return int(float(value.replace(",", ".")))
    except ValueError:
        return None


def to_float(value: str | None) -> float | None:
    value = clean(value)
    if value is None:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def ratio_pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return round(100 * numerator / denominator, 1)


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _detect_encoding(path: Path) -> str:
    with path.open("rb") as handle:
        sample = handle.read(1 << 20)
    try:
        sample.decode("utf-8")
        return "utf-8-sig"
    except UnicodeDecodeError as error:
        # Un corte a mitad de un carácter multibyte al final de la muestra no es latin-1.
        if error.start >= len(sample) - 3:
            return "utf-8-sig"
        return "latin-1"


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    encoding = _detect_encoding(path)
    with path.open(encoding=encoding, newline="") as handle:
        header = handle.readline()
        delimiter = ";" if header.count(";") >= header.count(",") else ","
        handle.seek(0)
        reader = csv.reader(handle, delimiter=delimiter)
        columns = [column.strip().lstrip("﻿") for column in next(reader)]
        for row in reader:
            if len(row) == len(columns):
                yield dict(zip(columns, row))


_XLSX = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx(path: Path, header_hint: str) -> Iterator[dict[str, str]]:
    """Primera hoja de un XLSX; la cabecera es la primera fila que contiene `header_hint`."""
    with zipfile.ZipFile(path) as workbook:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            strings = ["".join(text.text or "" for text in item.iter(f"{_XLSX}t")) for item in root.findall(f"{_XLSX}si")]
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    header: list[str] | None = None
    for row in sheet.iter(f"{_XLSX}row"):
        cells: dict[int, str] = {}
        for cell in row.findall(f"{_XLSX}c"):
            reference = re.match(r"[A-Z]+", cell.get("r", ""))
            column = 0
            for letter in reference.group(0) if reference else "":
                column = column * 26 + ord(letter) - 64
            value_node = cell.find(f"{_XLSX}v")
            value = value_node.text if value_node is not None else "".join(t.text or "" for t in cell.iter(f"{_XLSX}t"))
            if cell.get("t") == "s" and value is not None:
                value = strings[int(value)]
            cells[column] = value or ""
        values = [cells.get(index, "") for index in range(1, max(cells, default=0) + 1)]
        if header is None:
            if header_hint in values:
                header = [value.strip() for value in values]
            continue
        yield dict(zip(header, values + [""] * (len(header) - len(values))))


# ---------------------------------------------------------------------------
# Bloques del perfil
# ---------------------------------------------------------------------------


def payment_band(raw: str | None) -> dict | None:
    value = clean(raw)
    if value is None or value.upper() == "SIN INFORMACION":
        return None
    band = PAYMENT_BANDS.get(value.upper())
    if not band:
        return {"tramo": "desconocido", "etiqueta": value, "min_clp": None, "max_clp": None}
    code, minimum, maximum = band
    return {"tramo": code, "etiqueta": value.title(), "min_clp": minimum, "max_clp": maximum}


def directory_profiles(rows: Iterable[dict[str, str]], include_all_states: bool = False) -> dict[int, dict]:
    """Identidad, dependencia, ubicación, niveles, matrícula y tramos de pago."""
    profiles: dict[int, dict] = {}
    for row in rows:
        rbd = to_int(row.get("RBD"))
        state = to_int(row.get("ESTADO_ESTAB"))
        if rbd is None or (not include_all_states and state != 1):
            continue
        dependency_code = to_int(row.get("COD_DEPE"))
        dependency_name, sector = DEPENDENCIES.get(dependency_code, (None, None))
        teaching_codes = sorted(
            {code for key, value in row.items() if key.startswith("ENS_") and (code := to_int(value))}
        )
        enrollment = {key: to_int(row.get(f"MAT_ENS_{index}")) or 0 for index, key in enumerate(
            ["parvularia", "basica", "basica_adultos", "especial", "media_hc", "media_hc_adultos", "media_tp", "media_tp_adultos"],
            start=1,
        )}
        latitude, longitude = to_float(row.get("LATITUD")), to_float(row.get("LONGITUD"))
        location_valid = bool(latitude and longitude and -56.5 < latitude < -17 and -110 < longitude < -66)
        profiles[rbd] = {
            "rbd": rbd,
            "dv": clean(row.get("DGV_RBD")),
            "nombre": clean(row.get("NOM_RBD")),
            "estado": SCHOOL_STATES.get(state),
            "region": {"codigo": to_int(row.get("COD_REG_RBD")), "abreviatura": clean(row.get("NOM_REG_RBD_A"))},
            "comuna": {"codigo": to_int(row.get("COD_COM_RBD")), "nombre": clean(row.get("NOM_COM_RBD"))},
            "ubicacion": {"lat": latitude, "lon": longitude} if location_valid else None,
            "rural": to_int(row.get("RURAL_RBD")) == 1,
            "dependencia": {
                "codigo_oficial": dependency_code,
                "nombre_oficial": dependency_name,
                "sector": sector,
            },
            "sostenedor": {
                "persona_juridica": to_int(row.get("P_JURIDICA")) == 1,
                "rut": clean(row.get("RUT_SOSTENEDOR")) if to_int(row.get("P_JURIDICA")) == 1 else None,
            },
            "orientacion_religiosa": RELIGIOUS_ORIENTATIONS.get(to_int(row.get("ORI_RELIGIOSA"))),
            "programas": {
                "pie": to_int(row.get("CONVENIO_PIE")) == 1,
                "pace": to_int(row.get("PACE")) == 1,
            },
            "niveles": {
                "parvularia": 10 in teaching_codes,
                "basica": 110 in teaching_codes,
                "media_hc": 310 in teaching_codes,
                "media_tp": any(code in teaching_codes for code in (410, 510, 610, 710, 810)),
                "media_artistica": 910 in teaching_codes,
                # 299 es la opción 4 del PIE, no una escuela especial.
                "especial": any(211 <= code <= 219 for code in teaching_codes),
                "adultos": any(code % 100 >= 60 for code in teaching_codes if code >= 160 and not 200 <= code < 300),
                "codigos_ensenanza": teaching_codes,
            },
            "matricula": {
                "total": to_int(row.get("MAT_TOTAL")),
                "parvularia": enrollment["parvularia"],
                "basica": enrollment["basica"],
                "media": enrollment["media_hc"] + enrollment["media_tp"],
                "especial": enrollment["especial"],
                "adultos": enrollment["basica_adultos"] + enrollment["media_hc_adultos"] + enrollment["media_tp_adultos"],
            },
            "pago": {
                "matricula": payment_band(row.get("PAGO_MATRICULA")),
                "mensualidad": payment_band(row.get("PAGO_MENSUAL")),
                "autodeclarado": True,
            },
        }
    return profiles


def enrollment_totals(rows: Iterable[dict[str, str]]) -> dict[int, int]:
    totals: dict[int, int] = {}
    for row in rows:
        rbd, total = to_int(row.get("RBD")), to_int(row.get("MAT_TOTAL"))
        if rbd is not None and total is not None:
            totals[rbd] = total
    return totals


def _grade_label(ense: int, grade: int) -> str | None:
    if ense == 10:
        return {4: "PK", 5: "K"}.get(grade)
    if ense == 110 and 1 <= grade <= 8:
        return f"{grade}B"
    if ense in YOUTH_SECONDARY_CODES and 1 <= grade <= 4:
        return f"{grade}M"
    return None


def grade_span(grades: list[str]) -> str | None:
    ordered = [grade for grade in GRADE_ORDER if grade in grades]
    if not ordered:
        return None
    first, last = GRADE_ORDER.index(ordered[0]), GRADE_ORDER.index(ordered[-1])
    contiguous = len(ordered) == last - first + 1
    return f"{ordered[0]}-{ordered[-1]}" if contiguous else ",".join(ordered)


def course_profiles(rows: Iterable[dict[str, str]]) -> dict[int, dict]:
    """Grados impartidos (incluye cursos combinados) y tamaño medio de curso escolar."""
    grades: dict[int, set[str]] = defaultdict(set)
    course_sizes: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        rbd, ense = to_int(row.get("RBD")), to_int(row.get("COD_ENSE"))
        if rbd is None or ense is None:
            continue
        students = to_int(row.get("N_ALU")) or 0
        if students <= 0:
            continue
        grade = to_int(row.get("COD_GRADO"))
        found = False
        for index in range(1, 9):
            per_grade = to_int(row.get(f"N_ALU_GRADO{index}"))
            if per_grade and (label := _grade_label(ense, index)):
                grades[rbd].add(label)
                found = True
        if not found and grade is not None and (label := _grade_label(ense, grade)):
            grades[rbd].add(label)
        if ense == 110 or ense in YOUTH_SECONDARY_CODES:
            course_sizes[rbd].append(students)
    profiles = {}
    for rbd in set(grades) | set(course_sizes):
        offered = [grade for grade in GRADE_ORDER if grade in grades[rbd]]
        sizes = course_sizes[rbd]
        profiles[rbd] = {
            "grados": offered,
            "rango": grade_span(offered),
            "imparte_basica_completa": all(f"{grade}B" in offered for grade in range(1, 9)),
            "imparte_media_completa": all(f"{grade}M" in offered for grade in range(1, 5)),
            "imparte_1b_a_4m": all(grade in offered for grade in GRADE_ORDER[2:]),
            "cursos_escolares": len(sizes),
            "alumnos_por_curso": mean([float(size) for size in sizes]),
        }
    return profiles


_PERFORMANCE_COLUMN = re.compile(r"^(APR|REP|RET|TRA)_(HOM|MUJ|SI|NB)_(\d{2})$")
_UNKNOWN_COLUMN = re.compile(r"^SI_(HOM|MUJ|NB)_(\d{2})$")


def performance_profiles(rows: Iterable[dict[str, str]]) -> dict[int, dict]:
    """Situación final de niños y jóvenes; el retiro es el indicador público de abandono."""
    totals: dict[int, Counter] = defaultdict(Counter)
    attendance: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for row in rows:
        rbd = to_int(row.get("RBD"))
        if rbd is None or to_int(row.get("COD_ENSE2")) not in YOUTH_REGULAR_ENSE2:
            continue
        row_counts = Counter()
        for column, value in row.items():
            if match := _PERFORMANCE_COLUMN.match(column):
                row_counts[match.group(1)] += to_int(value) or 0
            elif _UNKNOWN_COLUMN.match(column):
                row_counts["SI"] += to_int(value) or 0
        totals[rbd].update(row_counts)
        average = to_float(row.get("PROM_ASIS"))
        evaluated = row_counts["APR"] + row_counts["REP"]
        if average is not None and evaluated:
            attendance[rbd].append((average, evaluated))
    profiles = {}
    for rbd, counts in totals.items():
        base = counts["APR"] + counts["REP"] + counts["RET"]
        weighted = attendance.get(rbd) or []
        weight = sum(students for _, students in weighted)
        profiles[rbd] = {
            "aprobados": counts["APR"],
            "reprobados": counts["REP"],
            "retirados": counts["RET"],
            "trasladados": counts["TRA"],
            "sin_informacion": counts["SI"],
            "base_situacion_final": base,
            "tasa_aprobacion_pct": ratio_pct(counts["APR"], base),
            "tasa_reprobacion_pct": ratio_pct(counts["REP"], base),
            "tasa_retiro_pct": ratio_pct(counts["RET"], base),
            "asistencia_promedio_pct": round(sum(a * s for a, s in weighted) / weight, 1) if weight else None,
        }
    return profiles


def teacher_profiles(rows: Iterable[dict[str, str]]) -> dict[int, int]:
    counts = {}
    for row in rows:
        rbd, teachers = to_int(row.get("RBD")), to_int(row.get("DC_TOT"))
        if rbd is not None and teachers is not None:
            counts[rbd] = teachers
    return counts


def sep_profiles(rows: Iterable[dict[str, str]]) -> dict[int, dict]:
    profiles = {}
    for row in rows:
        rbd = to_int(row.get("RBD"))
        if rbd is None:
            continue
        classification = clean(row.get("CLASIFICACION_SEP"))
        profiles[rbd] = {
            "convenio_sep": to_int(row.get("CONVENIO_SEP")) == 1,
            "clasificacion_sep": classification.lower().replace(" ", "_") if classification else None,
            "gratuito": to_int(row.get("EE_GRATUITO")) == 1,
            "alumnos_prioritarios": to_int(row.get("N_PRIO")),
            "alumnos_preferentes": to_int(row.get("N_PREF")),
        }
    return profiles


def ficom_profiles(rows: Iterable[dict[str, str]]) -> dict[int, dict]:
    """Cobro promedio mensual por estudiante de los subvencionados con copago."""
    profiles = {}
    for row in rows:
        rbd = to_int(row.get("RBD"))
        charge = to_float(row.get("cobro_promedio"))
        if rbd is None or charge is None:
            continue
        profiles[rbd] = {
            "cobro_promedio_mensual_clp": round(charge),
            "tramo": to_int(row.get("tramo")),
            "periodo": to_int(row.get("periodo")),
        }
    return profiles


def _simce_grade(path: Path) -> str | None:
    match = re.search(r"simce(\d[bm])\d{4}_rbd", path.name, re.IGNORECASE)
    return SIMCE_GRADES.get(match.group(1).lower()) if match else None


def simce_profiles(files: Iterable[Path]) -> dict[int, dict]:
    """Promedios SIMCE por grado y asignatura, con su variación y comparación por GSE."""
    profiles: dict[int, dict] = defaultdict(lambda: {"gse": None, "resultados": []})
    for path in files:
        grade = _simce_grade(path)
        if not grade:
            continue
        suffix = path.name.lower().split("_rbd")[0][len("simce") : len("simce") + 2]
        for row in read_csv(path):
            rbd = to_int(row.get("rbd"))
            if rbd is None:
                continue
            profile = profiles[rbd]
            profile["gse"] = to_int(row.get("cod_grupo")) or profile["gse"]
            for code, subject in SIMCE_SUBJECTS.items():
                key = f"{code}{suffix}_rbd"
                if f"prom_{key}" not in row:
                    continue
                score = to_int(row.get(f"prom_{key}"))
                mark = clean(row.get(f"marca_{key}"))
                if score is None and mark is None:
                    continue
                profile["resultados"].append({
                    "grado": grade,
                    "asignatura": subject,
                    "puntaje": score,
                    "alumnos": to_int(row.get(f"nalu_{key}")),
                    "variacion": to_int(row.get(f"dif_{key}")),
                    "variacion_significativa": to_int(row.get(f"sigdif_{key}")),
                    "dif_mismo_gse": to_int(row.get(f"difgru_{key}")),
                    "dif_mismo_gse_significativa": to_int(row.get(f"siggru_{key}")),
                    "pct_adecuado": to_float(row.get(f"palu_eda_ade_{key}")),
                    "pct_insuficiente": to_float(row.get(f"palu_eda_ins_{key}")),
                    "marca": mark,
                })
    return dict(profiles)


def _idps_grade(path: Path) -> str | None:
    match = re.search(r"idps(\d[bm])\d{4}_rbd", path.name, re.IGNORECASE)
    return SIMCE_GRADES.get(match.group(1).lower()) if match else None


def idps_profiles(indicator_files: Iterable[Path], dimension_files: Iterable[Path]) -> dict[int, dict]:
    """IDPS por grado; agrega la dimensión «Ambiente seguro» del clima de convivencia."""
    profiles: dict[int, dict] = defaultdict(lambda: {"indicadores": {}, "ambiente_seguro": {}})
    for path in indicator_files:
        grade = _idps_grade(path)
        for row in read_csv(path):
            rbd, indicator = to_int(row.get("rbd")), to_int(row.get("id_indicador"))
            name = IDPS_INDICATORS.get(indicator)
            score = to_float(row.get("prom"))
            if rbd is None or not grade or not name or score is None:
                continue
            profiles[rbd]["indicadores"].setdefault(name, {})[grade] = {
                "puntaje": score,
                "variacion": to_float(row.get("dif")),
                "variacion_significativa": to_int(row.get("sigdif")),
                "dif_mismo_gse": to_float(row.get("difgru")),
            }
    for path in dimension_files:
        grade = _idps_grade(path)
        for row in read_csv(path):
            rbd = to_int(row.get("rbd"))
            if rbd is None or not grade or to_int(row.get("id_dimension")) != IDPS_SAFE_ENVIRONMENT_DIMENSION:
                continue
            score = to_float(row.get("prom"))
            if score is not None:
                profiles[rbd]["ambiente_seguro"][grade] = score
    return dict(profiles)


def paes_profiles(rows: Iterable[dict[str, str]], minimum: int = MIN_PAES_PARTICIPANTS) -> dict[int, dict]:
    """PAES de la cohorte recién egresada, agregada por RBD de egreso.

    NEM, ranking y notas usan a todos los inscritos de la cohorte; los puntajes
    sólo a quienes rindieron Competencia Lectora y M1 en la aplicación regular.
    """
    accumulators: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    process_year: int | None = None
    for row in rows:
        process = to_int(row.get("ANYO_PROCESO"))
        if process is None:
            continue
        process_year = process_year or process
        if to_int(row.get("ANYO_DE_EGRESO")) != process - 1:
            continue
        rbd = to_int(row.get("RBD"))
        if rbd is None:
            continue
        bucket = accumulators[rbd]
        bucket["inscritos"].append(1)
        for column, key in (("PTJE_NEM", "nem"), ("PTJE_RANKING", "ranking"), ("PROMEDIO_NOTAS", "notas")):
            value = to_float(row.get(column))
            if value:
                bucket[key].append(value)
        reading, math = to_float(row.get("CLEC_REG_ACTUAL")), to_float(row.get("MATE1_REG_ACTUAL"))
        if reading and math:
            bucket["clec"].append(reading)
            bucket["m1"].append(math)
            bucket["cl_m1"].append((reading + math) / 2)
            if (reading + math) / 2 >= 600:
                bucket["sobre_600"].append(1)
    profiles = {}
    for rbd, bucket in accumulators.items():
        registered = len(bucket["inscritos"])
        tested = len(bucket["cl_m1"])
        suppressed = registered < minimum
        scores_suppressed = tested < minimum
        profiles[rbd] = {
            "proceso": process_year,
            "cohorte_egreso": process_year - 1 if process_year else None,
            "inscritos": registered if not suppressed else None,
            "rindieron_cl_m1": tested if not scores_suppressed else None,
            "promedio_nem": None if suppressed else mean(bucket["nem"]),
            "promedio_ranking": None if suppressed else mean(bucket["ranking"]),
            "promedio_notas": None if suppressed else (round(sum(bucket["notas"]) / len(bucket["notas"]), 2) if bucket["notas"] else None),
            "promedio_lectora": None if scores_suppressed else mean(bucket["clec"]),
            "promedio_m1": None if scores_suppressed else mean(bucket["m1"]),
            "promedio_cl_m1": None if scores_suppressed else mean(bucket["cl_m1"]),
            "pct_cl_m1_sobre_600": None if scores_suppressed else ratio_pct(len(bucket["sobre_600"]), tested),
            "suprimido": suppressed,
        }
    return profiles


def language_profiles(rows: Iterable[dict[str, str]]) -> dict[int, dict]:
    """Idiomas que el colegio dicta en cursos regulares de niños y jóvenes.

    Viene de «Docentes por curso y subsector»: cada fila es una asignatura de un
    curso. Se agregan asignaturas y grados por RBD; no se guarda ningún docente.
    """
    english_grades: dict[int, set[str]] = defaultdict(set)
    english_subjects_by_grade: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    foreign: dict[int, set[str]] = defaultdict(set)
    indigenous: dict[int, set[str]] = defaultdict(set)
    own_plan: dict[int, bool] = defaultdict(bool)
    seen: set[int] = set()
    for row in rows:
        rbd, ense, grade = to_int(row.get("RBD")), to_int(row.get("COD_ENSE")), to_int(row.get("COD_GRADO"))
        if rbd is None or ense is None or grade is None:
            continue
        label = _grade_label(ense, grade)
        if not label:
            continue
        seen.add(rbd)
        if to_int(row.get("TIPO_PLAN_EST")) == 1:
            own_plan[rbd] = True
        subject = fold_upper(row.get("NOM_SUBSECTOR"))
        for language, pattern in FOREIGN_LANGUAGES.items():
            if pattern.search(subject):
                if language == "ingles":
                    english_grades[rbd].add(label)
                    english_subjects_by_grade[rbd][label].add(subject)
                else:
                    foreign[rbd].add(language)
        for language, pattern in INDIGENOUS_LANGUAGES.items():
            if pattern.search(subject):
                indigenous[rbd].add(language)
    profiles = {}
    mandatory_index = GRADE_ORDER.index(ENGLISH_MANDATORY_FROM)
    for rbd in seen:
        grades = [grade for grade in GRADE_ORDER if grade in english_grades[rbd]]
        specific = sorted(indigenous[rbd] - {"pueblos_originarios"})
        profiles[rbd] = {
            "ingles": bool(grades),
            "ingles_grados": grades,
            "ingles_desde": grades[0] if grades else None,
            "ingles_temprano": bool(grades) and GRADE_ORDER.index(grades[0]) < mandatory_index,
            "ingles_intensivo": any(len(subjects) >= 2 for subjects in english_subjects_by_grade[rbd].values()),
            "otros_idiomas": sorted(foreign[rbd]),
            "lengua_indigena": specific or (["pueblos_originarios"] if indigenous[rbd] else []),
            "plan_propio": own_plan[rbd],
        }
    return profiles


def name_language_hints(name: str | None) -> list[str]:
    """Idiomas que sugiere el nombre del colegio («Deutsche Schule», «Lycée»)."""
    folded = fold_upper(name)
    return [language for language, pattern in NAME_LANGUAGE_HINTS.items() if pattern.search(folded)]


def website_signals(path: Path) -> dict[int, dict]:
    """Señales bilingües detectadas en los sitios rastreados por collect_school_fees."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        school["rbd"]: school["idiomas_sitio"]
        for school in data.get("colegios", [])
        if school.get("confianza_sitio") == "alta" and school.get("idiomas_sitio")
    }


def complaint_profiles(rows: Iterable[dict[str, str]]) -> dict[int, dict]:
    counts: dict[int, Counter] = defaultdict(Counter)
    year = None
    for row in rows:
        rbd = to_int(row.get("RBD"))
        if rbd is None:
            continue
        year = year or to_int(row.get("AGNO"))
        scope = (clean(row.get("DEN_AMBITO")) or "").upper()
        topic = (clean(row.get("DEN_TEMA")) or "").upper()
        bucket = counts[rbd]
        bucket["total"] += 1
        if scope == "CONVIVENCIA":
            bucket["convivencia"] += 1
        if scope == "SEGURIDAD E HIGIENE":
            bucket["seguridad_higiene"] += 1
        if scope == "INFRAESTRUCTURA":
            bucket["infraestructura"] += 1
        for key, topics in COMPLAINT_TOPICS.items():
            if topic in topics:
                bucket[key] += 1
    keys = ["total", "convivencia", "maltrato", "connotacion_sexual", "discriminacion", "seguridad_higiene", "infraestructura"]
    return {rbd: {"anio": year, **{key: bucket[key] for key in keys}} for rbd, bucket in counts.items()}


# ---------------------------------------------------------------------------
# Ensamblado
# ---------------------------------------------------------------------------


def assemble(
    directory: dict[int, dict],
    *,
    courses: dict[int, dict] | None = None,
    teachers: dict[int, int] | None = None,
    teachers_year: int | None = None,
    results_enrollment: dict[int, int] | None = None,
    results_year: int | None = None,
    performance: dict[int, dict] | None = None,
    sep: dict[int, dict] | None = None,
    ficom: dict[int, dict] | None = None,
    simce: dict[int, dict] | None = None,
    simce_year: int | None = None,
    idps: dict[int, dict] | None = None,
    idps_year: int | None = None,
    paes: dict[int, dict] | None = None,
    complaints: dict[int, dict] | None = None,
    languages: dict[int, dict] | None = None,
    languages_year: int | None = None,
    websites: dict[int, dict] | None = None,
) -> list[dict]:
    schools = []
    for rbd in sorted(directory):
        school = dict(directory[rbd])
        total = (school["matricula"] or {}).get("total")
        course = (courses or {}).get(rbd)
        school["grados"] = course
        # Colegio = básica o media regular de niños y jóvenes; las escuelas especiales
        # (en su mayoría de lenguaje) y los centros de adultos quedan con su marca de nivel.
        school["es_escolar"] = bool(
            school["niveles"]["basica"]
            or school["niveles"]["media_hc"]
            or school["niveles"]["media_tp"]
            or school["niveles"]["media_artistica"]
        )
        teacher_count = (teachers or {}).get(rbd)
        school["docentes"] = (
            {"anio": teachers_year, "total": teacher_count, "alumnos_por_docente": round(total / teacher_count, 1) if total and teacher_count else None}
            if teacher_count is not None
            else None
        )
        denominator = (results_enrollment or {}).get(rbd)
        record = (performance or {}).get(rbd)
        school["rendimiento"] = {"anio": results_year, **record} if record else None
        sep_record = (sep or {}).get(rbd)
        school["sep"] = (
            {
                "anio": results_year,
                **sep_record,
                "pct_prioritarios": ratio_pct(sep_record["alumnos_prioritarios"], denominator),
            }
            if sep_record
            else None
        )
        school["copago_ficom"] = (ficom or {}).get(rbd)
        simce_record = (simce or {}).get(rbd)
        school["simce"] = {"anio": simce_year, **simce_record} if simce_record and simce_record["resultados"] else None
        idps_record = (idps or {}).get(rbd)
        school["idps"] = {"anio": idps_year, **idps_record} if idps_record else None
        paes_record = (paes or {}).get(rbd)
        school["paes"] = paes_record
        complaint = (complaints or {}).get(rbd)
        if complaint or complaints is not None:
            complaint = complaint or {"anio": results_year, "total": 0, "convivencia": 0, "maltrato": 0, "connotacion_sexual": 0, "discriminacion": 0, "seguridad_higiene": 0, "infraestructura": 0}
            complaint = {**complaint, "matricula_base": denominator, "por_100_estudiantes": ratio_pct(complaint["total"], denominator)}
        school["denuncias"] = complaint
        language = (languages or {}).get(rbd)
        hints = name_language_hints(school["nombre"])
        site = (websites or {}).get(rbd)
        school["idiomas"] = (
            {"anio": languages_year if language else None, **(language or {}), "nombre_sugiere": hints, "sitio": site}
            if language or hints or site
            else None
        )
        schools.append(school)
    return schools


def _simce_score(school: dict, grade: str, subject: str) -> int | None:
    for result in (school.get("simce") or {}).get("resultados", []):
        if result["grado"] == grade and result["asignatura"] == subject:
            return result["puntaje"]
    return None


def flat_record(school: dict) -> dict:
    """Fila plana para CSV y propiedades de GeoJSON."""
    grades = school.get("grados") or {}
    performance = school.get("rendimiento") or {}
    paes = school.get("paes") or {}
    idps = school.get("idps") or {}
    complaints = school.get("denuncias") or {}
    payment = school["pago"]
    safe = idps.get("ambiente_seguro") or {}
    climate = (idps.get("indicadores") or {}).get("clima_convivencia") or {}
    return {
        "rbd": school["rbd"],
        "nombre": school["nombre"],
        "region": school["region"]["codigo"],
        "comuna_codigo": school["comuna"]["codigo"],
        "comuna": school["comuna"]["nombre"],
        "lat": (school["ubicacion"] or {}).get("lat"),
        "lon": (school["ubicacion"] or {}).get("lon"),
        "rural": school["rural"],
        "dependencia": school["dependencia"]["nombre_oficial"],
        "sector": school["dependencia"]["sector"],
        "es_escolar": school["es_escolar"],
        "grados": grades.get("rango"),
        "imparte_1b_a_4m": grades.get("imparte_1b_a_4m"),
        "imparte_basica_completa": grades.get("imparte_basica_completa"),
        "imparte_media_completa": grades.get("imparte_media_completa"),
        "media_hc": school["niveles"]["media_hc"],
        "media_tp": school["niveles"]["media_tp"],
        "matricula": school["matricula"]["total"],
        "alumnos_por_curso": grades.get("alumnos_por_curso"),
        "alumnos_por_docente": (school.get("docentes") or {}).get("alumnos_por_docente"),
        "orientacion_religiosa": school["orientacion_religiosa"],
        "pago_matricula": (payment["matricula"] or {}).get("tramo"),
        "pago_mensualidad": (payment["mensualidad"] or {}).get("tramo"),
        "copago_promedio_mensual_clp": (school.get("copago_ficom") or {}).get("cobro_promedio_mensual_clp"),
        "sep_clasificacion": (school.get("sep") or {}).get("clasificacion_sep"),
        "pct_prioritarios": (school.get("sep") or {}).get("pct_prioritarios"),
        "tasa_aprobacion_pct": performance.get("tasa_aprobacion_pct"),
        "tasa_retiro_pct": performance.get("tasa_retiro_pct"),
        "asistencia_promedio_pct": performance.get("asistencia_promedio_pct"),
        "simce_gse": (school.get("simce") or {}).get("gse"),
        "simce_4b_lectura": _simce_score(school, "4B", "lectura"),
        "simce_4b_matematica": _simce_score(school, "4B", "matematica"),
        "simce_8b_lectura": _simce_score(school, "8B", "lectura"),
        "simce_8b_matematica": _simce_score(school, "8B", "matematica"),
        "simce_2m_lectura": _simce_score(school, "2M", "lectura"),
        "simce_2m_matematica": _simce_score(school, "2M", "matematica"),
        "idps_clima_convivencia_2m": (climate.get("2M") or {}).get("puntaje"),
        "idps_ambiente_seguro_4b": safe.get("4B"),
        "idps_ambiente_seguro_8b": safe.get("8B"),
        "idps_ambiente_seguro_2m": safe.get("2M"),
        "paes_inscritos": paes.get("inscritos"),
        "paes_promedio_nem": paes.get("promedio_nem"),
        "paes_promedio_ranking": paes.get("promedio_ranking"),
        "paes_promedio_notas": paes.get("promedio_notas"),
        "paes_promedio_cl_m1": paes.get("promedio_cl_m1"),
        "paes_pct_cl_m1_sobre_600": paes.get("pct_cl_m1_sobre_600"),
        "denuncias_total": complaints.get("total"),
        "denuncias_convivencia": complaints.get("convivencia"),
        "denuncias_por_100": complaints.get("por_100_estudiantes"),
        "pie": school["programas"]["pie"],
        "ingles": (school.get("idiomas") or {}).get("ingles"),
        "ingles_desde": (school.get("idiomas") or {}).get("ingles_desde"),
        "ingles_temprano": (school.get("idiomas") or {}).get("ingles_temprano"),
        "ingles_intensivo": (school.get("idiomas") or {}).get("ingles_intensivo"),
        "otros_idiomas": ",".join((school.get("idiomas") or {}).get("otros_idiomas") or []),
        "lengua_indigena": ",".join((school.get("idiomas") or {}).get("lengua_indigena") or []),
        "plan_propio": (school.get("idiomas") or {}).get("plan_propio"),
        "bilingue_sitio": ((school.get("idiomas") or {}).get("sitio") or {}).get("bilingue"),
        "ib_sitio": ((school.get("idiomas") or {}).get("sitio") or {}).get("bachillerato_internacional"),
    }


def coverage(schools: list[dict]) -> dict:
    scholar = [school for school in schools if school["es_escolar"]]
    with_media = [school for school in scholar if school["niveles"]["media_hc"] or school["niveles"]["media_tp"]]

    def share(items: list[dict], predicate) -> dict:
        count = sum(1 for item in items if predicate(item))
        return {"con_dato": count, "base": len(items), "pct": ratio_pct(count, len(items))}

    return {
        "establecimientos_funcionando": len(schools),
        "establecimientos_escolares": len(scholar),
        "por_sector": dict(Counter(school["dependencia"]["sector"] for school in scholar)),
        "imparte_1b_a_4m": sum(1 for school in scholar if (school.get("grados") or {}).get("imparte_1b_a_4m")),
        "cobertura_escolares": {
            "ubicacion": share(scholar, lambda s: s["ubicacion"]),
            "grados": share(scholar, lambda s: (s.get("grados") or {}).get("grados")),
            "mensualidad_tramo": share(scholar, lambda s: s["pago"]["mensualidad"]),
            "copago_ficom": share(scholar, lambda s: s.get("copago_ficom")),
            "docentes": share(scholar, lambda s: s.get("docentes")),
            "rendimiento": share(scholar, lambda s: (s.get("rendimiento") or {}).get("base_situacion_final")),
            "sep": share(scholar, lambda s: s.get("sep")),
            "simce": share(scholar, lambda s: s.get("simce")),
            "idps_ambiente_seguro": share(scholar, lambda s: (s.get("idps") or {}).get("ambiente_seguro")),
            "denuncias_al_menos_una": share(scholar, lambda s: (s.get("denuncias") or {}).get("total")),
            "asignaturas_idiomas": share(scholar, lambda s: (s.get("idiomas") or {}).get("anio")),
            "ingles": share(scholar, lambda s: (s.get("idiomas") or {}).get("ingles")),
            "ingles_temprano": share(scholar, lambda s: (s.get("idiomas") or {}).get("ingles_temprano")),
            "otro_idioma_extranjero": share(scholar, lambda s: (s.get("idiomas") or {}).get("otros_idiomas")),
            "lengua_indigena": share(scholar, lambda s: (s.get("idiomas") or {}).get("lengua_indigena")),
        },
        "cobertura_con_media": {
            "paes_no_suprimida": share(with_media, lambda s: (s.get("paes") or {}).get("promedio_nem") is not None),
            "paes_puntajes": share(with_media, lambda s: (s.get("paes") or {}).get("promedio_cl_m1") is not None),
        },
    }


def write_outputs(schools: list[dict], manifest: dict, output_root: Path) -> dict[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": output_root / "colegios_perfil.json",
        "csv": output_root / "colegios_perfil.csv",
        "geojson": output_root / "colegios.geojson",
        "manifest": output_root / "colegios_manifest.json",
    }
    paths["json"].write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "generated_at": manifest["generated_at"], "colegios": schools}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rows = [flat_record(school) for school in schools]
    with paths["csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["rbd"])
        writer.writeheader()
        writer.writerows(rows)
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]}, "properties": {k: v for k, v in row.items() if k not in {"lat", "lon"}}}
        for row, school in zip(rows, schools)
        if school["ubicacion"] and school["es_escolar"]
    ]
    paths["geojson"].write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return paths


def _require(archive_root: Path, source_id: str, release_id: str | None = None) -> Release:
    release = find_release(archive_root, source_id, release_id)
    if not release:
        suffix = f" {release_id}" if release_id else ""
        raise BuildError(
            f"falta el release{suffix} de {source_id}; ejecuta scripts/archive_education_source.py {source_id}"
            + (f" --release-id {release_id}" if release_id else "")
        )
    return release


def build(archive_root: Path, output_root: Path, cache_root: Path, fees_path: Path | None = None) -> tuple[list[dict], dict]:
    used: dict[str, Release] = {}

    def load(source_id: str, release_id: str | None = None, optional: bool = False) -> Path | None:
        release = find_release(archive_root, source_id, release_id) if optional else _require(archive_root, source_id, release_id)
        if not release:
            print(f"WARN sin {source_id}; el bloque queda vacío", file=sys.stderr)
            return None
        used[source_id if source_id not in used else f"{source_id}:{release.release_id}"] = release
        return extracted_dir(release, cache_root)

    directory_dir = load("mineduc_school_directory")
    directory_release = used["mineduc_school_directory"]
    profile_year = int(directory_release.metadata.get("upstream_release_id") or directory_release.release_id)
    results_year = profile_year - 1
    directory = directory_profiles(read_csv(find_file(directory_dir, r"Directorio.*\.csv$")))

    previous_dir = load("mineduc_school_directory", str(results_year), optional=True)
    results_enrollment = enrollment_totals(read_csv(find_file(previous_dir, r"Directorio.*\.csv$"))) if previous_dir else {}

    courses_dir = load("mineduc_enrollment_course_summary", optional=True)
    courses = course_profiles(read_csv(find_file(courses_dir, r"Curso.*\.csv$"))) if courses_dir else None

    teachers_dir = load("mineduc_teachers_summary", optional=True)
    teachers = teacher_profiles(read_csv(find_file(teachers_dir, r"\.csv$"))) if teachers_dir else None

    performance_dir = load("mineduc_performance_summary", str(results_year), optional=True)
    performance = performance_profiles(read_csv(find_file(performance_dir, r"Rendimiento.*\.csv$"))) if performance_dir else None

    sep_dir = load("mineduc_sep_priority_summary", str(results_year), optional=True)
    sep = sep_profiles(read_csv(find_file(sep_dir, r"\.csv$"))) if sep_dir else None

    ficom_dir = load("mineduc_shared_financing", optional=True)
    ficom = ficom_profiles(read_xlsx(find_file(ficom_dir, r"\.xlsx$"), "RBD")) if ficom_dir else None

    simce_dir = load("agency_simce", optional=True)
    simce = simce_profiles(find_files(simce_dir, r"^simce\d[bm]\d{4}_rbd.*\.csv$")) if simce_dir else None

    idps_dir = load("agency_idps", optional=True)
    idps = (
        idps_profiles(find_files(idps_dir, r"^idps\d[bm]\d{4}_rbd_final\.csv$"), find_files(idps_dir, r"^idps\d[bm]\d{4}_rbd_dim_final\.csv$"))
        if idps_dir
        else None
    )

    paes_dir = load("mineduc_paes_scores", optional=True)
    paes = paes_profiles(read_csv(find_file(paes_dir, r"INSCRITOS_PUNTAJES.*\.csv$"))) if paes_dir else None

    complaints_dir = load("supereduc_complaints", str(results_year), optional=True)
    complaints = complaint_profiles(read_csv(find_file(complaints_dir, r"DENUNCIAS_\d{4}.*\.csv$"))) if complaints_dir else None

    subjects_dir = load("mineduc_teachers_by_course_subject", str(results_year), optional=True)
    languages = language_profiles(read_csv(find_file(subjects_dir, r"Docentes_por_curso.*\.csv$"))) if subjects_dir else None
    websites = website_signals(fees_path) if fees_path else {}

    def year_of(source_id: str) -> int | None:
        release = used.get(source_id)
        return int(release.metadata.get("upstream_release_id") or release.release_id) if release else None

    schools = assemble(
        directory,
        courses=courses,
        teachers=teachers,
        teachers_year=year_of("mineduc_teachers_summary"),
        results_enrollment=results_enrollment,
        results_year=results_year,
        performance=performance,
        sep=sep,
        ficom=ficom,
        simce=simce,
        simce_year=year_of("agency_simce"),
        idps=idps,
        idps_year=year_of("agency_idps"),
        paes=paes,
        complaints=complaints,
        languages=languages,
        languages_year=year_of("mineduc_teachers_by_course_subject"),
        websites=websites,
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "anio_perfil": profile_year,
        "anio_resultados": results_year,
        "umbral_paes_participantes": MIN_PAES_PARTICIPANTS,
        "fuentes": {key: release.provenance() for key, release in used.items()},
        "cobertura": coverage(schools),
    }
    return schools, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cache-root", type=Path, help="dónde extraer los releases (por defecto un directorio temporal)")
    parser.add_argument("--fees", type=Path, default=DEFAULT_OUTPUT_ROOT / "fees" / "aranceles.json", help="resultado de collect_school_fees.py, para las señales de idiomas de los sitios")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="school-profiles-") as temporary:
            cache_root = args.cache_root or Path(temporary)
            schools, manifest = build(args.archive_root, args.output_root, cache_root, args.fees)
            paths = write_outputs(schools, manifest, args.output_root)
    except (BuildError, OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest["cobertura"], ensure_ascii=False, indent=2))
    for kind, path in paths.items():
        print(f"OK   {kind}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
