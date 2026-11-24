# DH-040 — Perfil nacional de colegios

Actualizado: 2026-11-24.

`scripts/build_school_profiles.py` une por RBD las fuentes públicas que cubren a
casi todos los colegios de Chile. Es la primera iteración: prioriza datos
generales y deja documentados los datos valiosos que todavía no tienen una
fuente masiva.

## Cómo regenerarlo

```sh
python3 scripts/archive_education_source.py mineduc_school_directory
python3 scripts/archive_education_source.py mineduc_school_directory --release-id 2025
python3 scripts/archive_education_source.py mineduc_enrollment_course_summary
python3 scripts/archive_education_source.py mineduc_teachers_summary
python3 scripts/archive_education_source.py mineduc_performance_summary --release-id 2025
python3 scripts/archive_education_source.py mineduc_sep_priority_summary --release-id 2025
python3 scripts/archive_education_source.py mineduc_shared_financing
python3 scripts/archive_education_source.py agency_simce
python3 scripts/archive_education_source.py agency_idps
python3 scripts/archive_education_source.py mineduc_paes_scores
python3 scripts/archive_education_source.py supereduc_complaints --release-id 2025
python3 scripts/build_school_profiles.py
```

El año del perfil es el del Directorio más reciente (2026). Rendimiento, SEP y
denuncias usan el año anterior (2025) y la matrícula de ese mismo año como
denominador. Requiere `bsdtar` (incluido en macOS) para los RAR.

Productos en `data/build/education/` (fuera de Git, ver «Licencias»):

| Archivo | Contenido |
| --- | --- |
| `colegios_perfil.json` | Perfil anidado de los 12.004 establecimientos en funcionamiento |
| `colegios_perfil.csv` | Una fila plana por establecimiento con las métricas principales |
| `colegios.geojson` | Puntos de los 8.265 colegios (`es_escolar`) para el mapa |
| `colegios_manifest.json` | Releases usados (URL, SHA-256, licencia) y cobertura |

## Qué contiene

| Bloque | Campos | Fuente y año |
| --- | --- | --- |
| Identidad | RBD, nombre, región, comuna, coordenadas, ruralidad, estado | Directorio MINEDUC 2026 |
| Dependencia | código y nombre oficial; `sector`: público, particular subvencionado, particular pagado o administración delegada | Directorio 2026 |
| Niveles | parvularia, básica, media HC/TP/artística, especial, adultos | Directorio 2026 |
| Grados | lista PK–4M, rango, `imparte_1b_a_4m`, básica/media completas | Matrícula por curso 2026 (incluye cursos combinados) |
| Tamaño | matrícula por nivel, alumnos por curso, docentes y alumnos por docente | Directorio, cursos y dotación docente 2026 |
| Precio | tramos autodeclarados de matrícula y mensualidad (mín./máx. en pesos) | Directorio 2026 |
| Copago | cobro promedio mensual por estudiante | Reliquidación FICOM 2024 |
| Contexto | convenio y clasificación SEP, gratuidad, % de alumnos prioritarios | SEP 2025 |
| Trayectoria | aprobados, reprobados, retirados, trasladados; tasas y asistencia | Rendimiento 2025 |
| Aprendizaje | SIMCE 4° y 8° básico y II medio: puntaje, alumnos, variación, diferencia con su grupo socioeconómico, % adecuado/insuficiente | Agencia de Calidad 2025 |
| Bienestar y seguridad | IDPS por grado y la dimensión «Ambiente seguro» | Agencia de Calidad 2025 |
| Egreso | NEM, ranking, notas, Competencia Lectora, M1, CL+M1 y % sobre 600 | PAES admisión 2026, cohorte egresada en 2025 |
| Denuncias | total, convivencia, maltrato, connotación sexual, discriminación, seguridad e higiene, infraestructura y tasa por 100 estudiantes | Superintendencia de Educación 2025 |

## Definiciones

- **Colegio (`es_escolar`):** imparte básica o media regular de niños y
  jóvenes. Las escuelas especiales (en su mayoría de lenguaje, 1.760) y los
  centros de adultos quedan en el JSON con su marca de nivel.
- **Tasa de retiro:** retirados / (aprobados + reprobados + retirados) de la
  enseñanza regular de niños y jóvenes. Es el indicador público más cercano a
  la deserción; los traslados a otro colegio no cuentan.
- **PAES:** NEM, ranking y notas usan a todos los inscritos de la cohorte
  recién egresada; los puntajes, sólo a quienes rindieron Competencia Lectora
  y M1 en la aplicación regular. Con menos de 10 participantes, el bloque se
  suprime. Rendir la PAES es voluntario, así que el promedio no representa a
  todo el curso.
- **Ambiente seguro:** dimensión 23 del indicador Clima de Convivencia Escolar
  (IDPS), de 0 a 100, basada en cuestionarios a estudiantes.
- **Denuncias:** se cuentan por RBD; los colegios sin denuncias quedan en 0 con
  su denominador. Un volumen mayor también puede reflejar más uso del canal.
- **SIMCE:** sólo es comparable dentro del mismo grado y asignatura. La
  variación y la diferencia por grupo socioeconómico son las publicadas por la
  Agencia (`*_significativa`: −1, 0 o 1).

## Cobertura del 2026-11-24

Base: 8.265 colegios en funcionamiento (4.512 públicos, 3.185 particulares
subvencionados, 498 particulares pagados y 70 de administración delegada).
2.219 imparten de 1° básico a 4° medio.

| Dato | Colegios con dato | % |
| --- | ---: | ---: |
| Ubicación, grados, matrícula y docentes | 8.265 | 100,0 |
| Rendimiento y retiro 2025 | 8.249 | 99,8 |
| SEP | 8.242 | 99,7 |
| Tramo de mensualidad | 8.100 | 98,0 |
| SIMCE 2025 (algún grado) | 7.784 | 94,2 |
| IDPS «Ambiente seguro» | 7.397 | 89,5 |
| Al menos una denuncia en 2025 | 4.943 | 59,8 |
| Copago FICOM (sólo subvencionados que aún cobran) | 662 | 8,0 |
| PAES no suprimida (base: 3.108 colegios con media) | 2.908 | 93,6 |

Medianas por sector:

| Sector | Retiro % | Alumnos por curso | SIMCE 4° básico lectura | NEM | PAES CL+M1 | Ambiente seguro II medio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Público | 0,4 | 19,2 | 269 | 698 | 553 | 75 |
| Particular subvencionado | 0,5 | 30,6 | 273 | 712 | 601 | 76 |
| Particular pagado | 0,5 | 25,3 | 300 | 801 | 736 | 78 |

## Límites conocidos y siguientes iteraciones

| Dato | Estado | Camino propuesto |
| --- | --- | --- |
| Mensualidad exacta de particulares pagados | El tramo superior es «Más de $100.000» (440 de 498 pagados) y no discrimina | Ninguna fuente pública masiva lo informa; evaluar una captura asistida desde el sitio de cada colegio, empezando por los cercanos a las viviendas guardadas |
| Gimnasio, estadio, piscina, canchas | Sin fuente masiva oficial: el catastro MINEDUC es de 2012–2013 y sólo municipal; la ficha MIME no expone datos en HTML | Cruzar polígonos `amenity=school` de OpenStreetMap con instalaciones `leisure` (pitch, sports_centre, sports_hall, swimming_pool, stadium) (Overpass no respondió el 2026-11-24) |
| Categoría de desempeño | El portal de la Agencia sólo publica 2016–2019 | Buscar la ordenación reciente o derivarla sólo si la Agencia publica el método |
| Evolución SIMCE/IDPS | Sólo 2025 | Archivar 2022–2024 y calcular series por grado y asignatura |
| IVE-SINAE | Pendiente de licencia y URL | Archivo JUNAEB anual por RBD |
| PAES histórica | Sólo admisión 2026 | Los procesos 2023–2025 ya se resuelven con `mineduc_paes_scores` |

## Licencias

`colegios_*` combina fuentes con licencias distintas: matrícula, SEP,
sostenedores, PAES y denuncias se publican con CC BY en datos.gob.cl; la
dotación docente, con CC BY-NC; el Directorio, el resumen de rendimiento, el
FICOM y las bases de la Agencia no declaran licencia en su portal. Por eso los
productos no se versionan ni se redistribuyen hasta confirmar esos términos. El
detalle por fuente está en `sources/education.json` y en el manifiesto.
