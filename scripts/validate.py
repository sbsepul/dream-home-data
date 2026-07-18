#!/usr/bin/env python3
"""Valida estructura basica de los geojson en data/processed/. Sin
dependencias externas (a proposito, para que este repo no necesite mas
que Python estandar) — no chequea validez geometrica de poligonos porque
todas las capas actuales son puntos o lineas.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def validate(path: Path) -> list[str]:
    errors = []
    data = json.loads(path.read_text())
    if data.get("type") != "FeatureCollection":
        return [f"type debe ser FeatureCollection, es {data.get('type')!r}"]
    features = data.get("features")
    if not isinstance(features, list):
        return ["'features' debe ser una lista"]
    for i, f in enumerate(features):
        if f.get("type") != "Feature":
            errors.append(f"features[{i}].type invalido")
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("Point", "LineString", "MultiLineString", "Polygon", "MultiPolygon"):
            errors.append(f"features[{i}].geometry.type invalido: {geom}")
        if not isinstance(f.get("properties"), dict):
            errors.append(f"features[{i}].properties debe ser un objeto")
    return errors


def main() -> int:
    files = sorted((ROOT / "data" / "processed").glob("*.geojson"))
    if not files:
        print("no hay archivos en data/processed/ (corre los scripts de fetch primero)")
        return 1
    had_errors = False
    for path in files:
        errors = validate(path)
        if errors:
            had_errors = True
            print(f"FAIL {path.name}")
            for e in errors[:10]:
                print(f"  - {e}")
        else:
            data = json.loads(path.read_text())
            print(f"OK   {path.name} ({len(data['features'])} features)")
    return 1 if had_errors else 0


if __name__ == "__main__":
    sys.exit(main())
