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
MALL_SIZES = {"chico", "mediano", "grande", "sin_dato"}
MALL_SIZE_TIER = {"sin_dato": 0, "chico": 1, "mediano": 2, "grande": 3}
FUTURE_METRO_LINES = {"L6", "L7", "L8", "L9"}
FUTURE_METRO_STATES = {"en_construccion", "proyectada"}


def validate_mall_properties(properties: dict, index: int) -> list[str]:
    errors = []
    prefix = f"features[{index}].properties"
    for field in ("tamano", "tamano_por_capacidad", "tamano_por_tiendas"):
        if properties.get(field) not in MALL_SIZES:
            errors.append(f"{prefix}.{field} invalido: {properties.get(field)!r}")

    capacity_size = properties.get("tamano_por_capacidad")
    stores_size = properties.get("tamano_por_tiendas")
    final_size = properties.get("tamano")
    if capacity_size in MALL_SIZES and stores_size in MALL_SIZES and final_size in MALL_SIZES:
        expected = max((capacity_size, stores_size), key=MALL_SIZE_TIER.__getitem__)
        if final_size != expected:
            errors.append(f"{prefix}.tamano debe ser {expected!r}, es {final_size!r}")

    if "area_m2" in properties:
        for field in (
            "capacidad_personas_estimada",
            "capacidad_metodo",
            "cantidad_tiendas",
            "cantidad_tiendas_metodo",
        ):
            if field not in properties:
                errors.append(f"{prefix}.{field} requerido cuando existe area_m2")
    return errors


def validate_future_metro_properties(properties: dict, index: int) -> list[str]:
    errors = []
    prefix = f"features[{index}].properties"
    if properties.get("linea") not in FUTURE_METRO_LINES:
        errors.append(f"{prefix}.linea invalida: {properties.get('linea')!r}")
    if properties.get("estado") not in FUTURE_METRO_STATES:
        errors.append(f"{prefix}.estado invalido: {properties.get('estado')!r}")
    if not properties.get("apertura_estimada"):
        errors.append(f"{prefix}.apertura_estimada requerida")
    return errors


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
        elif path.name == "malls.geojson":
            errors.extend(validate_mall_properties(f["properties"], i))
        elif path.name in ("metro_estaciones_futuras.geojson", "metro_lineas_futuras.geojson"):
            errors.extend(validate_future_metro_properties(f["properties"], i))

        if path.name == "metro_estaciones_futuras.geojson" and geom and geom.get("type") != "Point":
            errors.append(f"features[{i}].geometry debe ser Point")
        if path.name == "metro_lineas_futuras.geojson" and geom and geom.get("type") != "MultiLineString":
            errors.append(f"features[{i}].geometry debe ser MultiLineString")
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
