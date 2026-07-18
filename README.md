# dream-home-data

Datos de amenities urbanos de la Región Metropolitana (metro, supermercados,
malls, autopistas), recolectados desde fuentes públicas para calcular
indicadores de ubicación en [dream-home](https://github.com/sbsepul/dream-home).

Este repo **solo recolecta y normaliza datos**. El cálculo del indicador de
valor de vivienda (ej. score por cercanía a amenities) queda para
dream-home u otro repo — acá se deja la materia prima lista para consumir.

## Datos disponibles

| Archivo | Contenido | Features | Fuente |
|---|---|---|---|
| `data/processed/metro_estaciones.geojson` | Puntos, una por estación física (ya deduplicadas por andén/sentido) | 126 | GTFS DTPM |
| `data/processed/metro_lineas.geojson` | Líneas (trazado), una por línea de Metro | 7 | GTFS DTPM |
| `data/processed/supermercados.geojson` | Puntos | ~830 | OpenStreetMap |
| `data/processed/malls.geojson` | Puntos | ~230 | OpenStreetMap |
| `data/processed/autopistas.geojson` | Líneas, segmentadas por tramo (comportamiento normal de OSM) | ~4000 | OpenStreetMap |

Para límites de comuna/región (no amenities, pero útil para agregación
espacial), ver el repo hermano
[Chile-GeoJSON](https://github.com/sbsepul/Chile-GeoJSON) — no se duplica
acá.

### Esquema de propiedades

**Metro estaciones**: `nombre` (string), `stop_ids` (lista de los stop_id
GTFS de cada andén que se promedió para llegar a esta estación).

**Metro líneas**: `linea` (ej. "L1"), `nombre` (ej. "Línea 1"), `color`
(hex, del GTFS oficial).

**Supermercados / malls**: `categoria`, `nombre` (puede venir vacío si el
POI no tiene tag `name` en OSM), `osm_type`, `osm_id` (para volver a
consultar en openstreetmap.org si hace falta más detalle).

**Autopistas**: mismo esquema que supermercados/malls, `categoria` siempre
`"autopista"`. Cada feature es un tramo (way) de OSM, no la autopista
completa — para distancia-al-punto-más-cercano esto no importa, pero si
necesitás la ruta completa hay que unir tramos por nombre.

> **Nota sobre `malls.geojson`**: OSM usa el mismo tag `shop=mall` tanto
> para grandes centros comerciales (Mall Plaza Tobalaba, Costanera Center)
> como para galerías comerciales chicas (Galería San Antonio). Si el
> indicador de valor necesita distinguir "mall grande" de "galería", hay
> que filtrar por otro criterio (nombre, o cruzar con una lista curada) —
> no viene resuelto en el dato crudo.

## Licencias

Ver [DATA_LICENSES.md](DATA_LICENSES.md) — OSM es ODbL (requiere
atribución), el GTFS de DTPM no especifica licencia.

## Cómo regenerar los datos

Sin dependencias externas — solo Python 3 estándar (`csv`, `json`,
`urllib`).

### Metro (GTFS DTPM)

```sh
curl -sL -o /tmp/gtfs.zip "https://www.dtpm.cl/descargas/gtfs/GTFS_20260704.zip"
unzip /tmp/gtfs.zip -d /tmp/gtfs_extracted
python3 scripts/fetch_metro_gtfs.py /tmp/gtfs_extracted
```

Revisá [dtpm.cl/index.php/noticias/gtfs-vigente](https://www.dtpm.cl/index.php/noticias/gtfs-vigente)
por la URL del GTFS vigente — cambia de nombre en cada actualización.

### Supermercados, malls, autopistas (OpenStreetMap / Overpass)

```sh
python3 scripts/fetch_overpass_pois.py
```

El servidor público de Overpass (`overpass-api.de`) tiene rate-limiting
agresivo: pedir dos queries seguidas puede devolver "server too busy". El
script ya hace una sola query combinada con reintentos; si falla igual,
esperá un par de minutos y reintentá, o usá un mirror como
`https://overpass.kumi.systems/api/interpreter`.

## Ideas para el indicador de valor (no implementado acá)

Notas para cuando se aborde el cálculo del score, en este repo o en
dream-home:

- Distancia a la estación de metro más cercana (ya hay líneas y
  estaciones — se puede calcular sobre `metro_estaciones.geojson`, o sobre
  `metro_lineas.geojson` si importa la cercanía a la vía y no solo al
  andén)
- Conteo de supermercados/malls dentro de un radio (ej. 500m, 1km)
- Distancia a la autopista más cercana (probablemente negativo para el
  valor por ruido/tráfico, no positivo — a diferencia de las otras capas)
- Todo esto se puede precalcular por comuna (join espacial con
  Chile-GeoJSON) para no tener que hacer el cálculo geométrico en el
  cliente
