#!/usr/bin/env python3
"""
Descarga los GeoJSON oficiales de Regiones y Comunas de Chile, simplifica las
geometrías usando el algoritmo Ramer-Douglas-Peucker (RDP) para mantener el peso
optimizado (< 500 KB para regiones, < 60 KB para comunas RM), y los guarda en
data/processed/.

Archivos generados:
- data/processed/regiones.geojson  (16 regiones de Chile)
- data/processed/comunas.geojson   (345 comunas de Chile)
- data/processed/comunas_rm.geojson (52 comunas de la Región Metropolitana)
"""

import json
import math
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed"

URL_REGIONS = "https://raw.githubusercontent.com/sbsepul/Chile-GeoJSON/master/Regional.geojson"
URL_COMUNAS = "https://raw.githubusercontent.com/sbsepul/Chile-GeoJSON/master/comunas.geojson"


def point_line_distance(pt: list[float], start: list[float], end: list[float]) -> float:
    if start == end:
        return math.hypot(pt[0] - start[0], pt[1] - start[1])
    x0, y0 = pt
    x1, y1 = start
    x2, y2 = end
    num = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
    den = math.hypot(y2 - y1, x2 - x1)
    return num / den if den > 0 else 0.0


def rdp(pts: list[list[float]], epsilon: float) -> list[list[float]]:
    if len(pts) < 3:
        return pts
    dmax = 0.0
    index = 0
    end = len(pts) - 1
    for i in range(1, end):
        d = point_line_distance(pts[i], pts[0], pts[end])
        if d > dmax:
            index = i
            dmax = d
    if dmax > epsilon:
        rec1 = rdp(pts[: index + 1], epsilon)
        rec2 = rdp(pts[index:], epsilon)
        return rec1[:-1] + rec2
    return [pts[0], pts[end]]


def ring_area(ring: list[list[float]]) -> float:
    area = 0.0
    for i in range(len(ring) - 1):
        area += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(area) / 2.0


def simplify_geometry(geometry: dict, epsilon: float) -> dict:
    def simplify_ring(ring: list[list[float]]) -> list[list[float]]:
        if len(ring) < 4:
            return ring
        simplified = rdp(ring[:-1], epsilon)
        if len(simplified) < 3:
            return ring
        simplified.append(simplified[0])
        return [[round(p[0], 4), round(p[1], 4)] for p in simplified]

    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])

    if gtype == "Polygon":
        new_coords = [
            simplify_ring(r) for r in coords if len(r) >= 4 and ring_area(r) > 0.00005
        ]
        valid_coords = [c for c in new_coords if len(c) >= 4]
        return {"type": "Polygon", "coordinates": valid_coords}
    elif gtype == "MultiPolygon":
        new_polys = []
        for poly in coords:
            rings = [
                simplify_ring(r) for r in poly if len(r) >= 4 and ring_area(r) > 0.00005
            ]
            valid_rings = [r for r in rings if len(r) >= 4]
            if valid_rings:
                new_polys.append(valid_rings)
        return {"type": "MultiPolygon", "coordinates": new_polys}
    return geometry


def fetch_json(url: str) -> dict:
    print(f"Descargando {url}...")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "dream-home-data/0.1 (github.com/sbsepul/dream-home-data)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Regiones
    raw_reg = fetch_json(URL_REGIONS)
    reg_features = []
    for feat in raw_reg.get("features", []):
        props = feat.get("properties", {})
        simplified_geom = simplify_geometry(feat["geometry"], epsilon=0.008)
        if not simplified_geom.get("coordinates"):
            continue
        reg_features.append({
            "type": "Feature",
            "properties": {
                "codigo_region": str(props.get("codigo_region", "")),
                "region": str(props.get("region", "")),
                "area_km2": props.get("area_km2"),
            },
            "geometry": simplified_geom,
        })
    reg_fc = {"type": "FeatureCollection", "features": reg_features}
    reg_path = OUT_DIR / "regiones.geojson"
    reg_json = json.dumps(reg_fc, ensure_ascii=False, separators=(",", ":"))
    reg_path.write_text(reg_json, encoding="utf-8")
    print(f"✓ Guardado {reg_path.name}: {len(reg_features)} regiones ({len(reg_json)/1024:.1f} KB)")

    # 2. Comunas Chile completo
    raw_com = fetch_json(URL_COMUNAS)
    com_features = []
    rm_features = []

    for feat in raw_com.get("features", []):
        props = feat.get("properties", {})
        cod_reg = str(props.get("codigo_region", ""))
        region_name = str(props.get("region", ""))
        comuna_name = str(props.get("comuna", ""))

        simplified_geom = simplify_geometry(feat["geometry"], epsilon=0.004)
        if not simplified_geom.get("coordinates"):
            continue

        clean_props = {
            "codigo_comuna": str(props.get("codigo_comuna", "")),
            "comuna": comuna_name,
            "provincia": str(props.get("provincia", "")),
            "codigo_region": cod_reg,
            "region": region_name,
        }

        feature = {
            "type": "Feature",
            "properties": clean_props,
            "geometry": simplified_geom,
        }
        com_features.append(feature)

        if cod_reg == "13" or "metropolitana" in region_name.lower():
            rm_features.append(feature)

    com_fc = {"type": "FeatureCollection", "features": com_features}
    com_path = OUT_DIR / "comunas.geojson"
    com_json = json.dumps(com_fc, ensure_ascii=False, separators=(",", ":"))
    com_path.write_text(com_json, encoding="utf-8")
    print(f"✓ Guardado {com_path.name}: {len(com_features)} comunas ({len(com_json)/1024:.1f} KB)")

    # 3. Comunas RM
    rm_fc = {"type": "FeatureCollection", "features": rm_features}
    rm_path = OUT_DIR / "comunas_rm.geojson"
    rm_json = json.dumps(rm_fc, ensure_ascii=False, separators=(",", ":"))
    rm_path.write_text(rm_json, encoding="utf-8")
    print(f"✓ Guardado {rm_path.name}: {len(rm_features)} comunas RM ({len(rm_json)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
