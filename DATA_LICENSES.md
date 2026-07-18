# Licencias de los datos

El código de este repo es MIT (ver [LICENSE](LICENSE)), pero cada archivo en
`data/processed/` conserva la licencia de su fuente original:

| Archivo | Fuente | Licencia | Notas |
|---|---|---|---|
| `supermercados.geojson` | OpenStreetMap (`shop=supermarket`) | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) | Requiere atribución a "© OpenStreetMap contributors" y compartir bajo la misma licencia si se redistribuye |
| `malls.geojson` | OpenStreetMap (`shop=mall`) | ODbL 1.0 | Incluye tanto grandes malls como galerías comerciales pequeñas — ver nota en README |
| `autopistas.geojson` | OpenStreetMap (`highway=motorway`) | ODbL 1.0 | Segmentado en muchos tramos por way, es el comportamiento normal de OSM |
| `metro_estaciones.geojson`, `metro_lineas.geojson` | [GTFS DTPM](https://www.dtpm.cl/index.php/noticias/gtfs-vigente) | **No especificada** | La página de descarga no indica términos de uso. Usar con esa reserva; si dream-home pasa a producción, conviene confirmar con DTPM antes de redistribuir. |

## Atribución OSM

Si usás `supermercados.geojson`, `malls.geojson` o `autopistas.geojson` en
una app o visualización pública, incluí algo como:

> Datos de mapas © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, ODbL.
