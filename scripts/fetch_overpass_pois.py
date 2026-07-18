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

QUERY = f"""
[out:json][timeout:90];
(
  node["shop"="supermarket"]{RM_BBOX};
  way["shop"="supermarket"]{RM_BBOX};
  node["shop"="mall"]{RM_BBOX};
  way["shop"="mall"]{RM_BBOX};
  way["highway"="motorway"]{RM_BBOX};
);
out center tags;
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
    if el["type"] == "node":
        lon, lat = el["lon"], el["lat"]
        geometry = {"type": "Point", "coordinates": [lon, lat]}
    elif "center" in el:
        geometry = {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}
    else:
        return None  # way sin geometria resuelta (no deberia pasar con "out center")

    category = None
    if tags.get("shop") == "supermarket":
        category = "supermercado"
    elif tags.get("shop") == "mall":
        category = "mall"
    elif tags.get("highway") == "motorway":
        category = "autopista"

    return {
        "type": "Feature",
        "properties": {
            "categoria": category,
            "nombre": tags.get("name"),
            "osm_type": el["type"],
            "osm_id": el["id"],
        },
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
