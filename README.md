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
| `data/processed/metro_estaciones_futuras.geojson` | Estaciones en construcción o proyectadas (L6, L7, L8 y L9) | 53 | OpenStreetMap + fuentes oficiales |
| `data/processed/metro_lineas_futuras.geojson` | Trazados futuros, separados de la red operativa | 4 | OpenStreetMap + fuentes oficiales |
| `data/processed/supermercados.geojson` | Puntos, clasificados por tamaño (`grande`/`mediano`/`chico`) | ~830 | OpenStreetMap |
| `data/processed/malls.geojson` | Puntos, clasificados por tamaño (`grande`/`mediano`/`chico`) | ~230 | OpenStreetMap |
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

**Metro futuro** se mantiene separado del GTFS operativo para no presentar
proyectos como servicio disponible. Las estaciones incluyen `linea`, `nombre`,
`estado` (`en_construccion` o `proyectada`), `apertura_estimada`, `osm_type` y
`osm_id`; cuando OSM informa el orden dentro de la línea también se incluye
`orden`. Los trazados son `MultiLineString` y agregan `fuente_geometria`.

La capa considera únicamente proyectos con trazado público definido:

- Extensión L6 a Lo Errázuriz: 1 estación, construcción, apertura estimada 2027.
- L7: 19 estaciones, construcción, apertura estimada 2028.
- L8: 14 estaciones, proyectada en dos etapas para 2032 y 2033.
- L9: 19 estaciones, construcción, etapas estimadas para 2030, 2032 y 2033.

Los nombres y ubicaciones todavía pueden cambiar antes de la apertura. Los
plazos y cantidades se contrastan con las publicaciones de
[extensión L6](https://www.mtt.gob.cl/extension-de-la-linea-6-inicia-obras-que-sumaran-nueva-estacion-en-cerrillos-para-90-mil-beneficiados/),
[L7](https://www.mtt.gob.cl/futura-linea-7-de-metro-sigue-avanzando-realizo-su-primer-encuentro-de-tuneles/),
[L8](https://www.gob.cl/noticias/conozca-cual-sera-el-trayecto-de-la-nueva-linea-8-de-metro/) y
[L9](https://www.gob.cl/noticias/comienzo-obras-linea-9-metro-santiago-trayecto-inicio-operaciones/).
Las geometrías y nombres consumibles se obtienen de OpenStreetMap.

**Supermercados / malls**: `categoria`, `nombre` (cae a `brand` u
`operator` de OSM si no hay tag `name`; ~99% de los supermercados y
~82% de los malls tienen alguno de los tres), `osm_type`, `osm_id` (para
volver a consultar en openstreetmap.org si hace falta más detalle).

**Supermercados** además trae `tamano` (`"grande"` / `"mediano"` /
`"chico"` / `"sin_dato"`) y `tamano_metodo`:

- `area_edificio`: el POI es un `way` de OSM (tiene polígono de edificio,
  ~57% de los casos) y `tamano` sale de su superficie real en `area_m2`
  (≥2500 m² grande, 800-2500 mediano, <800 chico).
- `marca_conocida`: el POI es solo un nodo (sin polígono) y `tamano` sale
  de una heurística por marca (Jumbo/Tottus/Líder no-Express = grande;
  Santa Isabel/Unimarc/Ekono = mediano; formato Express o marcas chicas
  conocidas = chico). Ver `BRAND_SIZE_HINTS` en
  `scripts/fetch_overpass_pois.py` — es una lista curada a mano, no un
  dato oficial, y no cubre todas las cadenas.
- `sin_dato`: ni polígono ni marca reconocida.

Cuando hay `area_edificio` disponible manda por sobre la marca (más
objetivo) — por eso vas a ver algún "Líder Express" clasificado como
`grande`: el edificio mapeado en OSM para ese local en particular mide
más de lo que el nombre sugiere.

**Malls** además trae una clasificación trazable por capacidad y cantidad
de tiendas:

- `area_m2`: superficie de la huella del polígono del mall en OSM.
- `capacidad_personas_estimada`: ocupación simultánea aproximada, calculada
  como `area_m2 / 4`. Es una señal comparable entre malls, **no un aforo
  oficial**: no incorpora pisos ni superficie arrendable que OSM no informe.
- `cantidad_tiendas`: cantidad de POI `shop=*` de OSM cuyo nodo o centro cae
  dentro del polígono. No equivale a un catastro oficial y puede subestimar
  malls con poco detalle interior en OSM.
- `tamano_por_capacidad`: chico (<750 personas), mediano (750-3749) o grande
  (≥3750).
- `tamano_por_tiendas`: chico (<15 tiendas), mediano (15-59) o grande (≥60).
- `tamano`: la mayor de las dos clasificaciones disponibles. Esto evita que
  la cobertura incompleta de tiendas en OSM rebaje un mall con gran capacidad.
  `tamano_metodo`, `capacidad_metodo` y `cantidad_tiendas_metodo` dejan
  registrado cómo se obtuvo cada valor.

Los malls representados solo por un nodo no tienen polígono para estimar
capacidad ni contar tiendas; quedan con `tamano: "sin_dato"`. Cuando existe
también un `way` con el mismo nombre, se conserva el `way` y se descarta el
nodo duplicado.

**Autopistas**: mismo esquema que supermercados/malls, `categoria` siempre
`"autopista"`. Cada feature es un tramo (way) de OSM, no la autopista
completa — para distancia-al-punto-más-cercano esto no importa, pero si
necesitás la ruta completa hay que unir tramos por nombre.

> **Nota sobre `malls.geojson`**: OSM usa el mismo tag `shop=mall` tanto
> para grandes centros comerciales como para galerías chicas. La clasificación
> permite distinguirlos de forma aproximada sin depender del nombre, pero su
> precisión sigue limitada por la geometría y el nivel de detalle disponibles
> en OSM.

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

### Metro futuro (OpenStreetMap / fuentes oficiales)

```sh
python3 scripts/fetch_metro_futuro_osm.py
```

El script valida que estén presentes los cuatro trazados y el número esperado
de estaciones por proyecto. Si OSM cambia etiquetas o agrega extensiones
tentativas, falla explícitamente en vez de publicar una capa incompleta.

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
- Conteo de supermercados/malls dentro de un radio (ej. 500m, 1km) —
  pesando quizás por `tamano` (un Jumbo a 800m puede pesar más que un
  minimarket a 200m, según el objetivo del indicador)
- Distancia a la autopista más cercana (probablemente negativo para el
  valor por ruido/tráfico, no positivo — a diferencia de las otras capas)
- Todo esto se puede precalcular por comuna (join espacial con
  Chile-GeoJSON) para no tener que hacer el cálculo geométrico en el
  cliente
