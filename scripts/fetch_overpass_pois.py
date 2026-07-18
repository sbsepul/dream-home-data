#!/usr/bin/env python3
"""Descarga supermercados, malls y autopistas de la Region Metropolitana
desde OpenStreetMap via Overpass API, y los guarda como GeoJSON en
data/processed/.

El servidor publico de Overpass (overpass-api.de) tiene rate-limiting
agresivo: pedir dos queries seguidas devuelve un error "server too busy".
Por eso las categorias principales van en una sola query combinada, y solo
se hace una segunda query (secuencial, no en paralelo) para los locales
sueltos que se usan para contar tiendas dentro de cada mall. Ambas
reintentan con backoff si el servidor esta ocupado.

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


# La capacidad no es el aforo oficial: se estima conservadoramente desde la
# huella del poligono OSM, a razon de una persona por cada 4 m2. No se intenta
# convertir la huella en GLA ni adivinar pisos que OSM no siempre informa.
MALL_M2_PER_PERSON = 4
MALL_CAPACITY_MEDIUM = 750
MALL_CAPACITY_LARGE = 3_750
MALL_STORES_MEDIUM = 15
MALL_STORES_LARGE = 60
SIZE_TO_TIER = {"chico": 1, "mediano": 2, "grande": 3}


def estimate_mall_capacity(area_m2: float | None) -> int | None:
    """Estima ocupacion simultanea desde la huella OSM; no es aforo legal."""
    if area_m2 is None or area_m2 <= 0:
        return None
    return int(area_m2 / MALL_M2_PER_PERSON)


def classify_mall_capacity(capacity: int | None) -> str:
    if capacity is None:
        return "sin_dato"
    if capacity >= MALL_CAPACITY_LARGE:
        return "grande"
    if capacity >= MALL_CAPACITY_MEDIUM:
        return "mediano"
    return "chico"


def classify_mall_stores(stores: int | None) -> str:
    if stores is None:
        return "sin_dato"
    if stores >= MALL_STORES_LARGE:
        return "grande"
    if stores >= MALL_STORES_MEDIUM:
        return "mediano"
    return "chico"


def classify_mall_size(capacity: int | None, stores: int | None) -> str:
    """Combina capacidad y tiendas usando la mayor clasificacion disponible.

    OSM tiene cobertura desigual de locales interiores, por lo que un conteo
    bajo no debe rebajar un mall cuya capacidad estimada es alta. A la vez, un
    complejo con muchos locales puede ser grande aunque su poligono represente
    solo parte de la construccion. Las dos clasificaciones se publican tambien
    por separado para que consumidores con otra politica puedan recombinarlas.
    """
    classifications = (classify_mall_capacity(capacity), classify_mall_stores(stores))
    known = [classification for classification in classifications if classification != "sin_dato"]
    if not known:
        return "sin_dato"
    return max(known, key=SIZE_TO_TIER.__getitem__)


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


def point_in_polygon(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    """Ray casting estandar. `ring` es una lista cerrada de (lon, lat)."""
    inside = False
    n = len(ring)
    x, y = lon, lat
    x1, y1 = ring[0]
    for i in range(1, n):
        x2, y2 = ring[i]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
            inside = not inside
        x1, y1 = x2, y2
    return inside


def element_center(el: dict) -> tuple[float, float] | None:
    """Extrae (lon, lat) de un node o del centro calculado por Overpass."""
    if el.get("type") == "node" and "lon" in el and "lat" in el:
        return el["lon"], el["lat"]
    center = el.get("center")
    if center and "lon" in center and "lat" in center:
        return center["lon"], center["lat"]
    return None


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

# Consulta aparte para contar los POI comerciales dentro de cada mall. Incluye
# nodes, ways y relations; `center` permite representar los dos ultimos como
# punto. Se excluye shop=mall para no contar el propio centro comercial como
# una de sus tiendas.
SHOP_POINTS_QUERY = f"""
[out:json][timeout:60];
nwr["shop"]["shop"!="mall"]{RM_BBOX};
out center;
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


def element_to_feature(el: dict) -> tuple[dict, list[tuple[float, float]] | None] | None:
    """Devuelve (feature, ring) - `ring` es el poligono crudo del way (lon,
    lat) cuando aplica (solo malls, para el conteo de tiendas adentro
    despues), o None."""
    tags = el.get("tags", {})

    category = None
    if tags.get("shop") == "supermarket":
        category = "supermercado"
    elif tags.get("shop") == "mall":
        category = "mall"
    elif tags.get("highway") == "motorway":
        category = "autopista"

    area_m2 = None
    ring = None
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
                if category == "mall":
                    ring = [(n["lon"], n["lat"]) for n in nodes]
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
    elif category == "mall" and area_m2 is not None:
        properties["area_m2"] = area_m2

    return (
        {"type": "Feature", "properties": properties, "geometry": geometry},
        ring,
    )


def main() -> None:
    print("consultando Overpass API (RM: supermercados, malls, autopistas)...")
    result = fetch_overpass(QUERY)
    elements = result.get("elements", [])
    print(f"{len(elements)} elementos recibidos")

    by_category: dict[str, list] = {"supermercado": [], "mall": [], "autopista": []}
    mall_rings: dict[int, list[tuple[float, float]]] = {}
    for el in elements:
        result_pair = element_to_feature(el)
        if not result_pair:
            continue
        feature, ring = result_pair
        category = feature["properties"]["categoria"]
        if not category:
            continue
        by_category[category].append(feature)
        if category == "mall" and ring:
            mall_rings[feature["properties"]["osm_id"]] = ring

    print(f"consultando locales (shop=*) en la RM para contar tiendas por mall...")
    shops_result = fetch_overpass(SHOP_POINTS_QUERY)
    shop_points = [
        center
        for el in shops_result.get("elements", [])
        if (center := element_center(el)) is not None
    ]
    print(f"{len(shop_points)} POI comerciales encontrados")

    # OSM a veces mapea el mismo mall dos veces: un node (el "pin" generico)
    # y un way (el poligono del edificio). Si hay un way con ese nombre, el
    # node es redundante - se descarta y se deja el way, que trae area real.
    mall_names_as_way = {
        f["properties"]["nombre"]
        for f in by_category["mall"]
        if f["properties"]["osm_type"] == "way" and f["properties"]["nombre"]
    }
    by_category["mall"] = [
        f
        for f in by_category["mall"]
        if not (f["properties"]["osm_type"] == "node" and f["properties"]["nombre"] in mall_names_as_way)
    ]

    for feature in by_category["mall"]:
        osm_id = feature["properties"]["osm_id"]
        ring = mall_rings.get(osm_id)
        tiendas = None
        if ring:
            tiendas = sum(1 for lon, lat in shop_points if point_in_polygon(lon, lat, ring))
            feature["properties"]["cantidad_tiendas"] = tiendas
            feature["properties"]["cantidad_tiendas_metodo"] = "pois_osm_en_poligono"
        area_m2 = feature["properties"].get("area_m2")
        capacity = estimate_mall_capacity(area_m2)
        if capacity is not None:
            feature["properties"]["capacidad_personas_estimada"] = capacity
            feature["properties"]["capacidad_metodo"] = "huella_osm_1_persona_cada_4m2"
        feature["properties"]["tamano_por_capacidad"] = classify_mall_capacity(capacity)
        feature["properties"]["tamano_por_tiendas"] = classify_mall_stores(tiendas)
        feature["properties"]["tamano"] = classify_mall_size(capacity, tiendas)
        feature["properties"]["tamano_metodo"] = "mayor_entre_capacidad_y_tiendas"

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
