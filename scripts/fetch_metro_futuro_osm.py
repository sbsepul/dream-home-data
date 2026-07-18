#!/usr/bin/env python3
"""Descarga trazados y estaciones futuras del Metro de Santiago desde OSM.

La red operativa sigue viniendo del GTFS oficial y no se mezcla con estos
datos. Esta capa incluye solo proyectos con trazado públicamente definido:
extensión L6 a Lo Errázuriz y líneas 7, 8 y 9.

Uso:
    python3 scripts/fetch_metro_futuro_osm.py
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RM_BBOX = (-34.2909, -71.7152, -32.9219, -69.7700)
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

PROJECTS = {
    "L6": {
        "nombre": "Extensión Línea 6 a Lo Errázuriz",
        "estado": "en_construccion",
        "apertura_estimada": "2027",
    },
    "L7": {
        "nombre": "Línea 7",
        "estado": "en_construccion",
        "apertura_estimada": "2028",
    },
    "L8": {
        "nombre": "Línea 8",
        "estado": "proyectada",
        "apertura_estimada": "2032-2033",
    },
    "L9": {
        "nombre": "Línea 9",
        "estado": "en_construccion",
        "apertura_estimada": "2030-2033",
    },
}
EXPECTED_STATIONS = {"L6": 1, "L7": 19, "L8": 14, "L9": 19}
NAME_CORRECTIONS = {"Isidora Goyanechea": "Isidora Goyenechea"}

QUERY = f"""
[out:json][timeout:180];
(
  way["railway"~"^(construction|proposed)$"]
     ["name"~"^(Línea 7|Línea 8|Línea 9|Extensión Línea 6)"]{RM_BBOX};
  node["network"~"^Línea (7|8|9)$"]{RM_BBOX};
  node["website"~"/linea-9$"]["subway"="yes"]{RM_BBOX};
  node["name"="Lo Errázuriz"]["subway"="yes"]{RM_BBOX};
);
out tags geom;
"""


def fetch_overpass(query: str, retries: int = 3) -> dict:
    encoded = urllib.parse.urlencode({"data": query})
    last_error = None
    for attempt in range(retries):
        url = f"{OVERPASS_URLS[attempt % len(OVERPASS_URLS)]}?{encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "dream-home-data/0.1 (github.com/sbsepul/dream-home-data)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=200) as response:
                return json.load(response)
        except (json.JSONDecodeError, TimeoutError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                wait = 10 * (attempt + 1)
                print(f"Overpass fallo ({exc}); reintentando en {wait}s...")
                time.sleep(wait)
    raise RuntimeError(f"Overpass no respondio: {last_error}")


def project_id(tags: dict) -> str | None:
    name = tags.get("name", "")
    network = tags.get("network", "")
    if name == "Lo Errázuriz" or name.startswith("Extensión Línea 6"):
        return "L6"
    for line in ("7", "8", "9"):
        if (
            f"Línea {line}" in name
            or network in (f"Línea {line}", f"Linea {line}")
            or tags.get("website", "").endswith(f"/linea-{line}")
        ):
            return f"L{line}"
    return None


def estimated_opening(tags: dict, line: str) -> str:
    return tags.get("opening_date") or tags.get("start_date") or PROJECTS[line]["apertura_estimada"]


def build_features(elements: list[dict]) -> tuple[list[dict], list[dict]]:
    segments: dict[str, list[list[list[float]]]] = {line: [] for line in PROJECTS}
    stations: dict[tuple[str, str], dict] = {}

    for element in elements:
        tags = element.get("tags", {})
        line = project_id(tags)
        if not line:
            continue

        if element.get("type") == "way" and element.get("geometry"):
            coordinates = [[node["lon"], node["lat"]] for node in element["geometry"]]
            if len(coordinates) >= 2:
                segments[line].append(coordinates)
            continue

        if element.get("type") != "node" or not tags.get("name"):
            continue
        if "lon" not in element or "lat" not in element:
            continue
        if line == "L7" and not (tags.get("ref", "").isdigit() and 1 <= int(tags["ref"]) <= 19):
            # Excluye extensiones tentativas al oriente que OSM tambien marca
            # como network=Línea 7, pero no son parte de sus 19 estaciones.
            continue

        name = NAME_CORRECTIONS.get(tags["name"], tags["name"])
        properties = {
            "linea": line,
            "nombre": name,
            "estado": PROJECTS[line]["estado"],
            "apertura_estimada": estimated_opening(tags, line),
            "osm_type": "node",
            "osm_id": element["id"],
        }
        if tags.get("ref"):
            properties["orden"] = int(tags["ref"]) if tags["ref"].isdigit() else tags["ref"]
        stations[(line, name)] = {
            "type": "Feature",
            "properties": properties,
            "geometry": {"type": "Point", "coordinates": [element["lon"], element["lat"]]},
        }

    line_features = []
    for line, project in PROJECTS.items():
        if not segments[line]:
            continue
        line_features.append(
            {
                "type": "Feature",
                "properties": {"linea": line, **project, "fuente_geometria": "OpenStreetMap"},
                "geometry": {"type": "MultiLineString", "coordinates": segments[line]},
            }
        )

    station_features = sorted(
        stations.values(),
        key=lambda feature: (
            feature["properties"]["linea"],
            str(feature["properties"].get("orden", "999")).zfill(3),
            feature["properties"]["nombre"],
        ),
    )
    return station_features, line_features


def main() -> None:
    print("consultando proyectos futuros de Metro en Overpass...")
    result = fetch_overpass(QUERY)
    stations, lines = build_features(result.get("elements", []))

    missing_lines = sorted(set(PROJECTS) - {feature["properties"]["linea"] for feature in lines})
    if missing_lines:
        raise RuntimeError(f"faltan trazados para: {', '.join(missing_lines)}")

    station_counts = {
        line: sum(feature["properties"]["linea"] == line for feature in stations) for line in PROJECTS
    }
    unexpected_counts = {
        line: (station_counts[line], expected)
        for line, expected in EXPECTED_STATIONS.items()
        if station_counts[line] != expected
    }
    if unexpected_counts:
        details = ", ".join(
            f"{line}={actual} (esperadas {expected})"
            for line, (actual, expected) in unexpected_counts.items()
        )
        raise RuntimeError(f"conteo inesperado de estaciones: {details}")

    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "metro_estaciones_futuras.geojson": stations,
        "metro_lineas_futuras.geojson": lines,
    }
    for filename, features in outputs.items():
        path = out_dir / filename
        path.write_text(
            json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":"))
        )
        print(f"{path.relative_to(ROOT)}: {len(features)} features")


if __name__ == "__main__":
    main()
