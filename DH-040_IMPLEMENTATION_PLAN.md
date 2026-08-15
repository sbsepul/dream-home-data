# DH-040 — Plan de implementación de Educación

Actualizado: 2026-08-14.

## Resultado esperado

Dream Home debe poder identificar colegios e instituciones de educación
superior mediante fuentes oficiales, mostrar su tipo de administración y
presentar resultados educativos históricos sin ocultar año, cobertura ni
procedencia.

Para colegios, la interfaz debe responder al menos:

- cuál es el establecimiento y dónde está;
- si es municipal, SLEP, particular subvencionado, particular pagado o de
  administración delegada;
- cuál es su agrupación pública/privada derivada;
- cuáles son sus resultados SIMCE y PAES disponibles;
- si el resultado mejoró, bajó o no es comparable frente al periodo anterior;
- cuántos estudiantes o qué cobertura respaldan el valor, cuando la fuente lo
  informa.

## Decisiones que no deben perderse

1. `official_dependency` conserva la categoría anual publicada por MINEDUC.
2. `normalized_sector` es un campo derivado para filtrar; nunca sustituye la
   dependencia oficial.
3. Un dato ausente no equivale a cero.
4. SIMCE se compara sólo dentro del mismo grado y asignatura.
5. PAES se publica con participantes/cobertura; no se interpreta como resultado
   de todo el alumnado del colegio.
6. No se ingieren ni publican registros individuales de estudiantes.
7. Cada métrica conserva `source_id`, `release_id`, año, granularidad y reglas
   de supresión.

## Secuencia de trabajo

### Incremento 1 — Persistencia y permisos de las fuentes

Objetivo: que los archivos ya encontrados no dependan del disco de una sola
máquina y que descarga no se confunda con permiso de redistribución.

Tareas:

- generar un manifiesto versionado desde los `metadata.json` locales;
- registrar checksum, tamaño, release, URL, fecha y nombre del objeto remoto;
- definir object storage y copiar allí los 11 releases MINEDUC ya verificados;
- documentar retención, acceso y recuperación del archivo crudo;
- completar una matriz de licencia/términos para las 38 fuentes;
- impedir publicación de binarios con licencia `review_required`.

Criterio de cierre:

- una máquina limpia puede recuperar y verificar cada release autorizado usando
  sólo el manifiesto y credenciales configuradas;
- una descarga corregida por el proveedor crea una revisión y no sobrescribe el
  archivo anterior.

Dependencia externa: elegir/configurar object storage. Hasta entonces, el
archivo local sigue siendo útil pero no constituye respaldo durable.

### Incremento 2 — Directorio canónico de colegios

Objetivo: reemplazar puntos educativos genéricos por establecimientos MINEDUC
con RBD, ubicación y dependencia oficial.

Tareas:

- extraer y perfilar el CSV del Directorio MINEDUC 2025;
- validar diccionario, codificación, coordenadas, duplicados y establecimientos
  vigentes;
- crear el modelo canónico de institución, identificadores externos, sede y
  snapshot escolar anual;
- conservar el texto/código oficial de dependencia;
- derivar `normalized_sector` mediante una tabla explícita y probada;
- producir una capa nacional y una vista RM para el mapa;
- incluir RBD, nombre, dirección, comuna, ruralidad, niveles y procedencia.

Agrupación inicial propuesta:

| Dependencia oficial | Agrupación para filtro |
| --- | --- |
| Municipal | Pública |
| SLEP | Pública |
| Particular subvencionado | Privada subvencionada |
| Particular pagado | Privada pagada |
| Administración delegada | Delegada, sin forzar pública/privada |

Criterio de cierre:

- cada punto publicado tiene RBD único, coordenada válida, dependencia oficial,
  sector derivado y release trazable;
- los conteos por región/comuna/dependencia cuadran con las frecuencias del
  proveedor o las diferencias quedan explicadas.

### Incremento 3 — Matrícula, rendimiento y contexto escolar

Objetivo: enriquecer cada colegio con tamaño, trayectoria y contexto antes de
mostrar resultados de pruebas.

Tareas:

- normalizar matrícula por establecimiento/unidad/curso;
- agregar rendimiento, docentes, asistentes, SEP y JEC por RBD y año;
- definir métricas, unidades, denominadores y reglas para cohortes pequeñas;
- medir cobertura del join contra el Directorio;
- mantener snapshots anuales en vez de sobrescribir el último valor.

Criterio de cierre:

- todas las tablas publicables pasan validaciones de llaves, año, rango,
  duplicados y cobertura;
- ningún porcentaje se presenta sin su denominador cuando esté disponible.

### Incremento 4 — SIMCE e IDPS con evolución

Objetivo: mostrar resultados comparables y la tendencia de cada colegio.

Tareas:

- implementar el adaptador del portal de la Agencia de Calidad;
- archivar releases SIMCE e IDPS autorizados y sus diccionarios;
- normalizar por RBD, año, grado, asignatura, dimensión y tipo de indicador;
- conservar promedio, distribución por estándares, variación y significancia;
- calcular `previous_comparable_value` y `change_value` sólo entre observaciones
  compatibles;
- exponer estados `mejora`, `baja`, `estable`, `sin comparación` y
  `dato suprimido` sin inventar conclusiones.

Criterio de cierre:

- la ficha puede mostrar el último SIMCE disponible y su serie histórica por
  grado/asignatura;
- la tendencia coincide con la variación/significancia oficial cuando exista;
- años no comparables o suspendidos no se unen como una serie continua falsa.

### Incremento 5 — PAES agregada por establecimiento

Objetivo: incorporar PAES sin usar datos personales ni convertir participación
selectiva en una medida absoluta de calidad.

Tareas:

- confirmar términos de uso de los reportes agregados DEMRE;
- documentar si existe descarga masiva estable o si requiere un adaptador de
  consulta con límites;
- conservar participantes, competencia lectora, M1, M2, ciencias, historia,
  promedio obligatorio, NEM y ranking cuando estén publicados;
- unir por RBD/código de enseñanza y mantener el año de admisión/proceso;
- excluir del producto toda base individual, incluso anonimizada.

Criterio de cierre:

- cada resultado PAES visible identifica proceso, prueba, participantes,
  cobertura disponible y fuente;
- el sistema diferencia explícitamente `sin datos`, `sin participantes` y
  `suprimido`.

### Incremento 6 — Instituciones de educación superior

Objetivo: representar correctamente institución, sede y carrera, sin copiar
métricas entre granularidades distintas.

Tareas:

- archivar instituciones y oferta SIES/Mi Futuro autorizadas;
- crear crosswalk de códigos SIES, CNA y Sistema de Acceso;
- separar institución legal, sede/campus y programa impartido;
- añadir acreditación, matrícula, titulados, retención, duración, vacantes y
  arancel sólo en su granularidad oficial;
- geocodificar sedes únicamente cuando no haya coordenada oficial, conservando
  método y confianza.

Criterio de cierre:

- una acreditación institucional no aparece como acreditación de cada carrera;
- toda oferta publicada se asocia a una institución, sede, año y fuente.

### Incremento 7 — Publicación e integración en el producto

Objetivo: servir la información en el mapa y la ficha educativa con contratos
estables.

Tareas:

- definir GeoJSON/API versionados para colegios y sedes;
- añadir filtros por dependencia oficial y sector normalizado;
- diseñar la ficha con último resultado, evolución, cobertura y fuente;
- mantener SIMCE y PAES separados en la presentación;
- integrar el dataset curado en data-platform y luego en web;
- realizar smoke visual en RM y pruebas de accesibilidad/responsive;
- evaluar cualquier score compuesto como un trabajo posterior y versionado,
  nunca como parte implícita de la ingesta.

Criterio de cierre:

- el usuario puede encontrar un colegio, saber si es municipal o privado y ver
  cómo evolucionan sus resultados disponibles;
- la interfaz explica año, prueba, cobertura y ausencia de información;
- fallos de una fuente no eliminan el último release válido.

## Orden inmediato recomendado

1. Crear el manifiesto versionado de los 11 releases existentes.
2. Resolver el respaldo durable en object storage.
3. Extraer el Directorio 2025 y publicar la primera capa de colegios con
   dependencia.
4. Normalizar matrícula y rendimiento ya archivados.
5. Implementar el adaptador SIMCE/IDPS y sus series comparables.
6. Resolver términos y mecanismo de PAES agregada.
7. Incorporar SIES/CNA y, finalmente, integrar data-platform/web.

## Gates comunes

Cada incremento debe incluir:

- pruebas unitarias del adaptador y transformación;
- checksum y metadata válidos para cada release;
- conteos, nulos, duplicados, rangos y cobertura de joins documentados;
- protección de privacidad y licencia;
- actualización de `EDUCATION_DATA_SOURCES.md` y `STATUS.md`;
- ausencia de cambios no relacionados en los repositorios compartidos.
