#!/usr/bin/env python3
"""Descarga establecimientos de salud de la Region Metropolitana desde dos
fuentes complementarias y los guarda como GeoJSON en data/processed/salud.geojson:

1. MINSAL/DEIS ("Establecimientos de Salud Vigentes", datos.gob.cl, licencia
   CC0): fuente primaria y autoritativa para hospitales, clinicas,
   consultorios, SAPU/SAR, COSAM, postas, etc. Incluye una clasificacion
   oficial por complejidad que se usa como "tamano" en vez de inventar un
   area de edificio que MINSAL no publica.
2. OpenStreetMap / Overpass API (shop/amenity=pharmacy): complemento para
   farmacias, que MINSAL NO incluye en su registro de establecimientos de
   salud (son un rubro retail con autorizacion sanitaria propia, fuera de
   este catastro). Reusa el mismo patron de fetch_overpass_pois.py.

No se intenta deduplicar entre ambas fuentes: cada feature declara su
"procedencia" (`minsal_deis` u `osm`) para que quien consuma el dato decida
como tratarlas. No se agregan aqui `amenity=hospital|clinic|doctors` de OSM
porque MINSAL ya es la fuente autoritativa para esos tipos y sumarlos
duplicaria datos con distinta calidad sin aportar cobertura real.

Uso:
    python3 scripts/fetch_health_facilities.py
"""
import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Mismo bounding box de la Region Metropolitana que fetch_overpass_pois.py
# (south, west, north, east), calculado desde Regional.geojson de
# sbsepul/Chile-GeoJSON.
RM_BBOX = (-34.2909, -71.7152, -32.9219, -69.7700)

USER_AGENT = "dream-home-data/0.1 (github.com/sbsepul/dream-home-data)"

# --- MINSAL / DEIS -----------------------------------------------------

MINSAL_PACKAGE_ID = "establecimientos-de-salud-vigentes"
MINSAL_API_URL = f"https://datos.gob.cl/api/3/action/package_show?id={MINSAL_PACKAGE_ID}"
MINSAL_RM_REGION_CODE = "13"

# El campo NivelComplejidadEstabGlosa es la propia clasificacion oficial de
# MINSAL para la envergadura de un establecimiento (no un area de edificio,
# que este catastro no publica). Se usa tal cual en vez de estimar m2, que
# seria menos confiable que el dato oficial disponible.
NIVEL_COMPLEJIDAD_TO_SIZE = {
    "alta complejidad": "grande",
    "mediana complejidad": "mediano",
    "baja complejidad": "chico",
}


def classify_size_by_nivel_complejidad(nivel: str | None) -> str | None:
    if not nivel:
        return None
    return NIVEL_COMPLEJIDAD_TO_SIZE.get(nivel.strip().lower())


def resolve_minsal_resource_url(retries: int = 3) -> str:
    """Resuelve la URL de descarga vigente vía la API CKAN de datos.gob.cl en
    vez de asumir un nombre de archivo con fecha embebida (el recurso se
    reemplaza periodicamente, ej. `establecimientos_20260811.csv`)."""
    req = urllib.request.Request(
        MINSAL_API_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            resources = payload["result"]["resources"]
            csv_resources = [r for r in resources if r.get("format", "").upper() == "CSV"]
            if not csv_resources:
                raise RuntimeError("MINSAL/DEIS no publica un recurso CSV en este momento")
            csv_resources.sort(key=lambda r: r.get("last_modified") or r.get("created") or "")
            return csv_resources[-1]["url"]
        except (json.JSONDecodeError, KeyError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            wait = 10 * attempt
            print(f"MINSAL package_show intento {attempt}/{retries} fallo ({exc}), esperando {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"No fue posible resolver el recurso CSV de MINSAL/DEIS: {last_error}")


def fetch_minsal_rows(retries: int = 3) -> list[dict]:
    resource_url = resolve_minsal_resource_url()
    req = urllib.request.Request(resource_url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw_bytes = resp.read()
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = raw_bytes.decode("latin-1")
            reader = csv.DictReader(io.StringIO(text), delimiter=";")
            return list(reader)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            wait = 10 * attempt
            print(f"MINSAL CSV intento {attempt}/{retries} fallo ({exc}), esperando {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"No fue posible descargar el CSV de MINSAL/DEIS: {last_error}")


def _clean(row: dict, key: str) -> str | None:
    value = (row.get(key) or "").strip()
    return value or None


def is_vigente(row: dict) -> bool:
    # El propio dataset mezcla mayusculas/minusculas para el mismo estado
    # ("Vigente en Operación Habitual" y "Vigente en operación habitual"),
    # por eso se compara en minusculas y solo por el prefijo estable.
    estado = (row.get("EstadoFuncionamiento") or "").strip().lower()
    return estado.startswith("vigente")


def is_in_rm(row: dict) -> bool:
    return (row.get("RegionCodigo") or "").strip() == MINSAL_RM_REGION_CODE


def minsal_row_to_feature(row: dict) -> dict | None:
    lat_raw = (row.get("Latitud") or "").strip()
    lon_raw = (row.get("Longitud") or "").strip()
    if not lat_raw or not lon_raw:
        return None
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except ValueError:
        return None
    if lat == 0 or lon == 0:
        return None

    nivel_complejidad = _clean(row, "NivelComplejidadEstabGlosa")
    tamano = classify_size_by_nivel_complejidad(nivel_complejidad)

    direccion_partes = [
        _clean(row, "TipoViaGlosa"),
        _clean(row, "NombreVia"),
        _clean(row, "Numero"),
    ]
    direccion = " ".join(p for p in direccion_partes if p) or None

    urgencia_raw = (row.get("TieneServicioUrgencia") or "").strip().upper()

    properties = {
        "categoria": "salud",
        "procedencia": "minsal_deis",
        "nombre": _clean(row, "EstablecimientoGlosa"),
        "codigo_establecimiento": _clean(row, "EstablecimientoCodigo"),
        "tipo_establecimiento": _clean(row, "TipoEstablecimientoGlosa"),
        "nivel_atencion": _clean(row, "NivelAtencionEstabglosa"),
        "nivel_complejidad": nivel_complejidad,
        "tiene_urgencia": urgencia_raw == "SI",
        "dependencia_administrativa": _clean(row, "DependenciaAdministrativa"),
        "sistema_salud": _clean(row, "TipoSistemaSaludGlosa"),
        "region": _clean(row, "RegionGlosa"),
        "comuna": _clean(row, "ComunaGlosa"),
        "direccion": direccion,
        "tamano": tamano or "sin_dato",
        "tamano_metodo": "nivel_complejidad_minsal" if tamano else "sin_dato",
    }
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def fetch_minsal_features() -> tuple[list[dict], int]:
    """Devuelve (features, filas_sin_coordenadas_omitidas)."""
    rows = fetch_minsal_rows()
    rm_vigentes = [r for r in rows if is_in_rm(r) and is_vigente(r)]
    features: list[dict] = []
    skipped = 0
    for row in rm_vigentes:
        feature = minsal_row_to_feature(row)
        if feature is None:
            skipped += 1
            continue
        features.append(feature)
    return features, skipped


# --- OpenStreetMap / Overpass (farmacias) -------------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

PHARMACY_QUERY = f"""
[out:json][timeout:60];
(
  node["amenity"="pharmacy"]{RM_BBOX};
  way["amenity"="pharmacy"]{RM_BBOX};
);
out tags geom;
"""

# Cadenas de farmacia conocidas en Chile: se usan solo para confirmar que un
# nodo sin poligono corresponde a un formato de local pequeno (retail de
# farmacia estandar), igual criterio que BRAND_SIZE_HINTS en
# fetch_overpass_pois.py. No es un dato oficial de superficie.
KNOWN_PHARMACY_CHAINS = [
    "cruz verde",
    "salcobrand",
    "ahumada",
    "farmacias del dr. simi",
    "dr simi",
    "doctor simi",
    "farmalider",
    "redfarmacia",
]


def classify_pharmacy_size_by_brand(name: str | None) -> str | None:
    if not name:
        return None
    normalized = name.lower()
    if any(chain in normalized for chain in KNOWN_PHARMACY_CHAINS):
        return "chico"
    return None


def fetch_overpass(query: str, retries: int = 5) -> dict:
    url = f"{OVERPASS_URL}?{urllib.parse.urlencode({'data': query})}"
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read()
            return json.loads(body)
        except (json.JSONDecodeError, TimeoutError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            wait = 15 * attempt
            print(f"Overpass intento {attempt}/{retries} fallo ({exc}), esperando {wait}s...")
            time.sleep(wait)
    raise RuntimeError("Overpass no respondio tras varios reintentos")


def polygon_area_m2(nodes: list[dict]) -> float:
    """Shoelace en grados, convertido a m2 con la misma aproximacion
    equirectangular de fetch_overpass_pois.py (suficiente para clasificar
    tamano, no para medir terrenos con precision)."""
    meters_per_deg_lat = 111_320
    meters_per_deg_lon = 111_320 * 0.8348  # cos(33.45 deg), latitud de Santiago
    coords = [(n["lon"] * meters_per_deg_lon, n["lat"] * meters_per_deg_lat) for n in nodes]
    area = 0.0
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2


def pharmacy_element_to_feature(el: dict) -> dict | None:
    tags = el.get("tags", {})
    name = tags.get("name") or tags.get("brand") or tags.get("operator")

    area_m2 = None
    if el["type"] == "node":
        geometry = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
    elif el["type"] == "way" and "geometry" in el:
        nodes = el["geometry"]
        lon = sum(n["lon"] for n in nodes) / len(nodes)
        lat = sum(n["lat"] for n in nodes) / len(nodes)
        geometry = {"type": "Point", "coordinates": [lon, lat]}
        if len(nodes) >= 4 and nodes[0] == nodes[-1]:
            area_m2 = round(polygon_area_m2(nodes))
    else:
        return None

    properties = {
        "categoria": "salud",
        "procedencia": "osm",
        "tipo_establecimiento": "Farmacia",
        "nombre": name,
        "osm_type": el["type"],
        "osm_id": el["id"],
    }
    if area_m2 is not None:
        # umbral mas chico que retail general: una farmacia estandar rara
        # vez supera los 300-400 m2 de sala de venta.
        properties["area_m2"] = area_m2
        properties["tamano"] = "grande" if area_m2 >= 400 else ("mediano" if area_m2 >= 150 else "chico")
        properties["tamano_metodo"] = "area_edificio"
    else:
        by_brand = classify_pharmacy_size_by_brand(name)
        properties["tamano"] = by_brand or "sin_dato"
        properties["tamano_metodo"] = "marca_conocida" if by_brand else "sin_dato"

    return {"type": "Feature", "properties": properties, "geometry": geometry}


def fetch_osm_pharmacy_features() -> list[dict]:
    result = fetch_overpass(PHARMACY_QUERY)
    elements = result.get("elements", [])
    features = []
    for el in elements:
        feature = pharmacy_element_to_feature(el)
        if feature is not None:
            features.append(feature)
    return features


# --- Manifiesto de dataset ----------------------------------------------


def write_dataset_manifest(out_dir: Path, minsal_resource_url: str) -> None:
    manifest_path = out_dir / "dataset-manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            manifest = {}
    manifest.setdefault("datasets", {})

    geojson_path = out_dir / "salud.geojson"
    checksum = hashlib.sha256(geojson_path.read_bytes()).hexdigest()
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    manifest["datasets"]["salud.geojson"] = {
        "fuentes": [
            {
                "nombre": "MINSAL/DEIS - Establecimientos de Salud Vigentes",
                "url_api": MINSAL_API_URL,
                "url_recurso_usado_en_esta_corrida": minsal_resource_url,
                "licencia": "CC0 1.0 (Creative Commons CCZero) via datos.gob.cl",
                "cobertura": "Todo Chile, filtrado aqui a Región Metropolitana (RegionCodigo=13) y estado vigente",
            },
            {
                "nombre": "OpenStreetMap (Overpass API) - amenity=pharmacy",
                "url_api": OVERPASS_URL,
                "licencia": "ODbL 1.0",
                "atribucion": "© OpenStreetMap contributors",
                "cobertura": "Región Metropolitana (bbox fijo, ver RM_BBOX en el script)",
                "nota": "MINSAL no incluye farmacias en su registro de establecimientos de salud; este complemento cubre ese vacío.",
            },
        ],
        "version": f"minsal-deis+osm-overpass:{run_date}",
        "checksum_sha256": checksum,
        "generado_en": datetime.now(timezone.utc).isoformat(),
        "limitaciones": [
            "No se deduplican establecimientos entre MINSAL y OSM: ambas fuentes conviven con su campo 'procedencia'.",
            "No se incluyen aún amenity=hospital|clinic|doctors de OSM: MINSAL ya es la fuente autoritativa para esos tipos.",
            "No hay dato de afluencia diaria, capacidad de personas ni cantidad de pacientes: ninguna fuente gratuita y sostenible lo provee (ver docs/implementation-plans/16-health-banks-commerce-poi-plan.md).",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"{manifest_path.relative_to(ROOT)}: manifiesto actualizado")


def main() -> None:
    print("descargando establecimientos de salud MINSAL/DEIS (Región Metropolitana)...")
    minsal_resource_url = resolve_minsal_resource_url()
    minsal_features, minsal_skipped = fetch_minsal_features()
    print(
        f"{len(minsal_features)} establecimientos MINSAL con coordenadas "
        f"({minsal_skipped} omitidos por no tener Latitud/Longitud)"
    )

    print("consultando Overpass API (RM: farmacias)...")
    pharmacy_features = fetch_osm_pharmacy_features()
    print(f"{len(pharmacy_features)} farmacias OSM encontradas")

    features = minsal_features + pharmacy_features

    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "salud.geojson"
    fc = {"type": "FeatureCollection", "features": features}
    out_path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    print(f"{out_path.relative_to(ROOT)}: {len(features)} features")

    write_dataset_manifest(out_dir, minsal_resource_url)


if __name__ == "__main__":
    main()
