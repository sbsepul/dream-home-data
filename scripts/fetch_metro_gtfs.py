#!/usr/bin/env python3
"""Extrae estaciones y trazado de lineas del Metro de Santiago desde el
feed GTFS oficial de DTPM (https://www.dtpm.cl/index.php/noticias/gtfs-vigente).

GTFS marca route_type=1 para "subway". De ahi se sacan los trip_id de esas
rutas, se cruzan con stop_times.txt para encontrar las estaciones
realmente visitadas, y con shapes.txt para el trazado de cada linea.

Nota: la pagina de DTPM no indica licencia de uso explicita.

Uso:
    python3 scripts/fetch_metro_gtfs.py /ruta/al/gtfs_extraido/
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_csv(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        yield from csv.DictReader(f)


def main(gtfs_dir: Path) -> None:
    subway_route_ids = {
        row["route_id"] for row in read_csv(gtfs_dir / "routes.txt") if row["route_type"] == "1"
    }
    print(f"lineas de metro (route_type=1): {len(subway_route_ids)}")

    trip_to_route = {}
    route_to_shape = {}
    for row in read_csv(gtfs_dir / "trips.txt"):
        if row["route_id"] in subway_route_ids:
            trip_to_route[row["trip_id"]] = row["route_id"]
            route_to_shape.setdefault(row["route_id"], row["shape_id"])

    print("escaneando stop_times.txt (archivo grande, puede tardar)...")
    station_stop_ids = set()
    with (gtfs_dir / "stop_times.txt").open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["trip_id"] in trip_to_route:
                station_stop_ids.add(row["stop_id"])

    routes_meta = {
        row["route_id"]: row
        for row in read_csv(gtfs_dir / "routes.txt")
        if row["route_id"] in subway_route_ids
    }

    # El GTFS modela cada anden/sentido como un stop_id distinto (p.ej.
    # "Tobalaba Direccion Los Dominicos" y "Tobalaba Direccion San Pablo").
    # Se agrupa por nombre de estacion (todo antes de " Direccion ") y se
    # promedian las coordenadas de sus andenes.
    stations_by_name: dict[str, list] = {}
    for row in read_csv(gtfs_dir / "stops.txt"):
        if row["stop_id"] not in station_stop_ids:
            continue
        station_name = row["stop_name"].split(" Dirección ")[0].strip()
        stations_by_name.setdefault(station_name, []).append(
            (float(row["stop_lon"]), float(row["stop_lat"]), row["stop_id"])
        )

    station_features = []
    for name, platforms in stations_by_name.items():
        lon = sum(p[0] for p in platforms) / len(platforms)
        lat = sum(p[1] for p in platforms) / len(platforms)
        station_features.append(
            {
                "type": "Feature",
                "properties": {
                    "nombre": name,
                    "stop_ids": [p[2] for p in platforms],
                },
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            }
        )

    shape_points: dict[str, list] = {}
    wanted_shapes = set(route_to_shape.values())
    for row in read_csv(gtfs_dir / "shapes.txt"):
        if row["shape_id"] in wanted_shapes:
            shape_points.setdefault(row["shape_id"], []).append(
                (int(row["shape_pt_sequence"]), float(row["shape_pt_lon"]), float(row["shape_pt_lat"]))
            )

    line_features = []
    for route_id, shape_id in route_to_shape.items():
        pts = sorted(shape_points.get(shape_id, []))
        if not pts:
            continue
        meta = routes_meta[route_id]
        line_features.append(
            {
                "type": "Feature",
                "properties": {
                    "linea": meta["route_short_name"],
                    "nombre": meta["route_long_name"],
                    "color": f"#{meta['route_color']}",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for _, lon, lat in pts],
                },
            }
        )

    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "metro_estaciones.geojson").write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": station_features},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    (out_dir / "metro_lineas.geojson").write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": line_features},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )

    print(f"data/processed/metro_estaciones.geojson: {len(station_features)} estaciones")
    print(f"data/processed/metro_lineas.geojson: {len(line_features)} lineas")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/dtpm_gtfs/extracted"))
