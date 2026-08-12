#!/usr/bin/env python3
"""
Carga los GeoJSON oficiales y corregidos de Regiones y Comunas desde el repositorio
local `chile-geojson-fork` (o raw GitHub sbsepul/Chile-GeoJSON), conservando el 100%
de la topología y límites exactos entre comunas sin desalineaciones de bordes.

Archivos generados:
- data/processed/regiones.geojson   (16 regiones de Chile)
- data/processed/comunas.geojson    (345 comunas de Chile)
- data/processed/comunas_rm.geojson  (52 comunas de la Región Metropolitana)
"""

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed"

LOCAL_FORK_DIR = Path("/Users/sbsepul/repos/chile-geojson-fork")
URL_REGIONS = "https://raw.githubusercontent.com/sbsepul/Chile-GeoJSON/master/Regional.geojson"
URL_COMUNAS = "https://raw.githubusercontent.com/sbsepul/Chile-GeoJSON/master/comunas.geojson"


def load_raw_json(local_name: str, fallback_url: str) -> dict:
    local_path = LOCAL_FORK_DIR / local_name
    if local_path.exists():
        print(f"Cargando desde repositorio local corregido: {local_path}")
        return json.loads(local_path.read_text(encoding="utf-8"))

    print(f"Descargando {fallback_url}...")
    req = urllib.request.Request(
        fallback_url,
        headers={"User-Agent": "dream-home-data/0.1 (github.com/sbsepul/dream-home-data)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Regiones
    raw_reg = load_raw_json("Regional.geojson", URL_REGIONS)
    reg_clean_features = []
    for feat in raw_reg.get("features", []):
        props = feat.get("properties", {})
        reg_clean_features.append({
            "type": "Feature",
            "properties": {
                "codigo_region": str(props.get("codigo_region", "")),
                "region": str(props.get("region", "")),
                "area_km2": props.get("area_km2"),
            },
            "geometry": feat["geometry"],
        })
    reg_fc = {"type": "FeatureCollection", "features": reg_clean_features}
    reg_path = OUT_DIR / "regiones.geojson"
    reg_json = json.dumps(reg_fc, ensure_ascii=False, separators=(",", ":"))
    reg_path.write_text(reg_json, encoding="utf-8")
    print(f"✓ Guardado {reg_path.name}: {len(reg_clean_features)} regiones ({len(reg_json)/1024:.1f} KB)")

    # 2. Comunas Chile y RM
    raw_com = load_raw_json("comunas.geojson", URL_COMUNAS)
    com_clean_features = []
    rm_clean_features = []

    for feat in raw_com.get("features", []):
        props = feat.get("properties", {})
        cod_reg = str(props.get("codigo_region", ""))
        region_name = str(props.get("region", ""))
        comuna_name = str(props.get("comuna", ""))

        clean_props = {
            "codigo_comuna": str(props.get("codigo_comuna", "")),
            "comuna": comuna_name,
            "provincia": str(props.get("provincia", "")),
            "codigo_region": cod_reg,
            "region": region_name,
        }

        clean_feature = {
            "type": "Feature",
            "properties": clean_props,
            "geometry": feat["geometry"],
        }
        com_clean_features.append(clean_feature)

        if cod_reg == "13" or "metropolitana" in region_name.lower():
            rm_clean_features.append(clean_feature)

    com_fc = {"type": "FeatureCollection", "features": com_clean_features}
    com_path = OUT_DIR / "comunas.geojson"
    com_json = json.dumps(com_fc, ensure_ascii=False, separators=(",", ":"))
    com_path.write_text(com_json, encoding="utf-8")
    print(f"✓ Guardado {com_path.name}: {len(com_clean_features)} comunas ({len(com_json)/1024:.1f} KB)")

    rm_fc = {"type": "FeatureCollection", "features": rm_clean_features}
    rm_path = OUT_DIR / "comunas_rm.geojson"
    rm_json = json.dumps(rm_fc, ensure_ascii=False, separators=(",", ":"))
    rm_path.write_text(rm_json, encoding="utf-8")
    print(f"✓ Guardado {rm_path.name}: {len(rm_clean_features)} comunas RM ({len(rm_json)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
