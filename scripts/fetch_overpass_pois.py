#!/usr/bin/env python3
"""Descarga supermercados, malls y autopistas de la Region Metropolitana
desde OpenStreetMap via Overpass API, y los guarda como GeoJSON en
data/processed/.

El servidor publico de Overpass (overpass-api.de) tiene rate-limiting
agresivo: pedir dos queries seguidas devuelve un error "server too busy".
Por eso este script hace una sola query combinada (todas las categorias en
un solo request) y reintenta con backoff si el servidor esta ocupado.

Uso:
    python3 scripts/fetch_overpass_pois.py
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Bounding box de la Region Metropolitana (south, west, north, east),
# calculado desde Regional.geojson de sbsepul/Chile-GeoJSON.
RM_BBOX = (-34.2909, -71.7152, -32.9219, -69.7700)

# Metros por grado en la latitud de Santiago (~-33.45), para pasar el area
# del poligono (shoelace en grados) a m2 sin depender de shapely/pyproj.
# Aproximacion equirectangular, suficiente para este uso (no es para medir
# terrenos, es para clasificar "chico/mediano/grande").
METERS_PER_DEG_LAT = 111_320
METERS_PER_DEG_LON = 111_320 * 0.8348  # cos(33.45 deg)

# Heuristica de marca -> tamano tipico, usada SOLO cuando el POI es un nodo
# sin poligono de edificio (no hay area real que medir). No es un dato
# oficial de superficie, es una clasificacion aproximada por formato de
# tienda conocido en Chile. Cuando hay poligono, el area manda.
BRAND_SIZE_HINTS = {
    "grande": ["jumbo", "tottus", "lider", "líder"],
    "mediano": ["santa isabel", "unimarc", "ekono", "alvi"],
    "chico": ["ok market", "almac", "acuenta", "a cuenta", "economax", "supermercado 10"],
}


def classify_size_by_brand(name: str | None) -> str | None:
    if not name:
        return None
    n = name.lower()
    # el formato "Express" es chico sea cual sea la cadena (Lider Express,
    # Unimarc Express, etc.) - se chequea antes que las listas de marca
    # completa para no confundirlo con el formato grande/mediano de la
    # misma cadena.
    if "express" in n:
        return "chico"
    for size, brands in BRAND_SIZE_HINTS.items():
        if any(b in n for b in brands):
            return size
    return None


def classify_size_by_area(area_m2: float) -> str:
    if area_m2 >= 2500:
        return "grande"
    if area_m2 >= 800:
        return "mediano"
    return "chico"


def polygon_area_m2(nodes: list[dict]) -> float:
    """Formula del shoelace sobre un anillo cerrado, en grados, convertida
    a m2 con la aproximacion equirectangular de arriba."""
    coords = [(n["lon"] * METERS_PER_DEG_LON, n["lat"] * METERS_PER_DEG_LAT) for n in nodes]
    area = 0.0
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2

QUERY = f"""
[out:json][timeout:90];
(
  node["shop"="supermarket"]{RM_BBOX};
  way["shop"="supermarket"]{RM_BBOX};
  node["shop"="mall"]{RM_BBOX};
  way["shop"="mall"]{RM_BBOX};
  way["highway"="motorway"]{RM_BBOX};
);
out tags geom;
"""


def fetch_overpass(query: str, retries: int = 5) -> dict:
    url = f"{OVERPASS_URL}?{urllib.parse.urlencode({'data': query})}"
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "dream-home-data/0.1 (github.com/sbsepul/dream-home-data)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=100) as resp:
                body = resp.read()
            return json.loads(body)
        except (json.JSONDecodeError, TimeoutError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            wait = 15 * attempt
            print(f"intento {attempt}/{retries} fallo ({exc}), esperando {wait}s...")
            time.sleep(wait)
    raise RuntimeError("Overpass no respondio tras varios reintentos")


def element_to_feature(el: dict) -> dict | None:
    tags = el.get("tags", {})

    category = None
    if tags.get("shop") == "supermarket":
        category = "supermercado"
    elif tags.get("shop") == "mall":
        category = "mall"
    elif tags.get("highway") == "motorway":
        category = "autopista"

    area_m2 = None
    if el["type"] == "node":
        geometry = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
    elif el["type"] == "way" and "geometry" in el:
        nodes = el["geometry"]
        if category == "autopista":
            # las autopistas son lineas reales, no su centroide
            geometry = {
                "type": "LineString",
                "coordinates": [[n["lon"], n["lat"]] for n in nodes],
            }
        else:
            # para supermercados/malls (poligono del edificio) alcanza con
            # el centroide simple de los nodos del way
            lon = sum(n["lon"] for n in nodes) / len(nodes)
            lat = sum(n["lat"] for n in nodes) / len(nodes)
            geometry = {"type": "Point", "coordinates": [lon, lat]}
            if len(nodes) >= 4 and nodes[0] == nodes[-1]:
                area_m2 = round(polygon_area_m2(nodes))
    else:
        return None  # way sin geometria resuelta (no deberia pasar con "out geom")

    name = tags.get("name") or tags.get("brand") or tags.get("operator")

    properties = {
        "categoria": category,
        "nombre": name,
        "osm_type": el["type"],
        "osm_id": el["id"],
    }

    if category == "supermercado":
        if area_m2 is not None:
            properties["area_m2"] = area_m2
            properties["tamano"] = classify_size_by_area(area_m2)
            properties["tamano_metodo"] = "area_edificio"
        else:
            by_brand = classify_size_by_brand(name)
            properties["tamano"] = by_brand or "sin_dato"
            properties["tamano_metodo"] = "marca_conocida" if by_brand else "sin_dato"

    return {
        "type": "Feature",
        "properties": properties,
        "geometry": geometry,
    }


def main() -> None:
    print("consultando Overpass API (RM: supermercados, malls, autopistas)...")
    result = fetch_overpass(QUERY)
    elements = result.get("elements", [])
    print(f"{len(elements)} elementos recibidos")

    by_category: dict[str, list] = {"supermercado": [], "mall": [], "autopista": []}
    for el in elements:
        feature = element_to_feature(el)
        if feature and feature["properties"]["categoria"]:
            by_category[feature["properties"]["categoria"]].append(feature)

    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)
    name_map = {"supermercado": "supermercados", "mall": "malls", "autopista": "autopistas"}
    for category, features in by_category.items():
        out_path = out_dir / f"{name_map[category]}.geojson"
        fc = {"type": "FeatureCollection", "features": features}
        out_path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
        print(f"{out_path.relative_to(ROOT)}: {len(features)} features")


if __name__ == "__main__":
    main()
