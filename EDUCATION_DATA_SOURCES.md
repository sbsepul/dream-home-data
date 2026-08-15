# DH-040 — Fuentes de datos de Educación en Chile

Actualizado: 2026-08-14.

Este documento define cómo identificar y conservar las fuentes que pueden
enriquecer la capa Educación de Dream Home. El catálogo legible por máquinas es
[`sources/education.json`](sources/education.json); este texto explica las
decisiones que no caben en una fila del registro.

La secuencia de implementación, entregables y criterios de cierre se mantiene
en [`DH-040_IMPLEMENTATION_PLAN.md`](DH-040_IMPLEMENTATION_PLAN.md).

## Alcance

El inventario incluye fuentes que permiten describir o evaluar:

- establecimientos escolares reconocidos por MINEDUC;
- dependencia pública/privada, sostenedor, niveles y matrícula;
- aprendizaje, bienestar, asistencia y trayectoria escolar;
- resultados PAES agregados por colegio;
- vulnerabilidad, fiscalización y certificaciones complementarias;
- universidades, institutos profesionales, centros de formación técnica y sus
  sedes, carreras, acreditación y resultados.

No pretende copiar datos personales ni convertir toda señal disponible en un
ranking. Denuncias, vulnerabilidad, subvenciones y selectividad, por ejemplo,
requieren contexto y denominadores antes de exponerse.

## Resultado del inventario

El catálogo registra 38 productos de datos de nueve familias oficiales:

| Familia | Qué aporta | Identificador principal |
| --- | --- | --- |
| Centro de Estudios MINEDUC | Directorio, dependencia, sostenedor, matrícula, rendimiento, asistencia, dotación, SEP, subvenciones y JEC | `RBD` |
| Agencia de Calidad | SIMCE, IDPS y categoría de desempeño | `RBD` + año/grado/asignatura |
| DEMRE | PAES agregada por colegio y bases de admisión anonimizadas | `RBD` + código de enseñanza; código de carrera |
| JUNAEB | IVE-SINAE | `RBD` + año/nivel |
| Superintendencia de Educación | Denuncias, mediaciones y sanciones | validar `RBD` en cada release |
| MINEDUC/SNED/MIME/SAE | Desempeño, ficha pública y admisión escolar | `RBD` |
| MMA/SNCAE | Certificación ambiental | `RBD`, Rol JUNJI o código Integra |
| SIES/Mi Futuro/SES | Instituciones, sedes, oferta, matrícula, titulados, personal, retención, duración, infraestructura y finanzas | código SIES + sede/carrera |
| CNA y Sistema de Acceso | Acreditación, vacantes, requisitos y ponderaciones | código de institución/carrera más crosswalk |

### Fuentes prioritarias para el primer recorrido vertical

1. Directorio de establecimientos MINEDUC.
2. Resumen de matrícula y rendimiento por establecimiento/unidad educativa.
3. SIMCE e IDPS de la Agencia de Calidad.
4. Reportes PAES agregados por unidad educativa de DEMRE.
5. Instituciones y oferta académica SIES.
6. Acreditaciones CNA.

Estas seis familias bastan para reemplazar los puntos genéricos de Mapbox por
establecimientos oficiales con identidad, ubicación, dependencia, niveles y
resultados explicables.

## Cómo conservar cada descarga

El enlace de una fuente no basta: los portales reemplazan archivos y cambian
URLs. `scripts/archive_education_source.py` resuelve el archivo publicado,
descarga por streaming, calcula su checksum y conserva cada release de forma
inmutable:

```text
data/raw/education/
  mineduc_school_directory/
    2025/
      source.rar
      metadata.json
  agency_simce/
    simce-2025-final/
      source.xlsx
      metadata.json
```

`data/raw/` ya está excluido de Git. Los originales deben persistirse en object
storage cuando exista ese servicio; Git conserva sólo catálogo, scripts,
manifiestos y productos pequeños autorizados para redistribución.

El `metadata.json` debe registrar como mínimo:

```json
{
  "source_id": "mineduc_school_directory",
  "release_id": "2026",
  "landing_url": "https://datosabiertos.mineduc.cl/directorio-de-establecimientos-educacionales/",
  "resolved_download_url": "https://.../directorio-2026.rar",
  "fetched_at": "2026-08-14T00:00:00Z",
  "upstream_published_at": "2026-07-31",
  "sha256": "...",
  "bytes": 123,
  "media_type": "application/vnd.rar",
  "license": "por confirmar",
  "terms_url": null
}
```

Una nueva descarga nunca sobrescribe la anterior, aunque el portal conserve el
mismo nombre de archivo. El `sha256` distingue correcciones silenciosas del
proveedor. Si cambia el contenido para el mismo release, el archivador crea
`<release>-revision-<sha12>`; si el checksum ya existe, informa `UNCHANGED`.

Para consultar y archivar el release vigente del Directorio MINEDUC:

```sh
python3 scripts/archive_education_source.py mineduc_school_directory --list-releases
python3 scripts/archive_education_source.py mineduc_school_directory
python3 scripts/verify_education_archive.py
```

Sólo se automatizan entradas `ready` con acceso `public_download`. Los datos
quedan en `data/raw/education/`, excluidos de Git, hasta contar con object
storage y una licencia de redistribución confirmada.

El primer recorrido ejecutado el 2026-08-14 archivó los once productos MINEDUC
marcados `ready`: directorios de establecimientos y sostenedores, tres
resúmenes de matrícula, rendimiento, docentes, asistentes, SEP, financiamiento
compartido y JEC. Son 11 releases y 9.867.612 bytes verificados; el año vigente
depende de cada producto y se conserva en su `release_id`.

## Modelo canónico

Los archivos crudos se normalizan sin perder su procedencia:

```text
education_institution
  id, kind, canonical_name, status

education_external_id
  institution_id, namespace, external_id, valid_from, valid_to

education_site
  id, institution_id, name, address, comuna_code, location

school_annual_snapshot
  institution_id, year, official_dependency, normalized_sector,
  sponsor_id, levels, enrollment, teachers, assistants

education_metric
  institution_id, site_id, source_id, metric_code, year, grade,
  subject, cohort, value, unit, sample_size, coverage_rate,
  change_value, change_significance, suppressed, release_id

higher_education_offering
  institution_id, site_id, program_code, year, name, area,
  modality, schedule, formal_duration, tuition, vacancies
```

### Dependencia escolar

No se guarda como booleano. Se conserva el valor oficial anual:

- municipal;
- Servicio Local de Educación Pública (SLEP);
- particular subvencionado;
- particular pagado;
- administración delegada.

`normalized_sector` permite filtros agrupados, pero nunca reemplaza
`official_dependency`. La dependencia vive en `school_annual_snapshot` porque
un establecimiento puede migrar desde administración municipal a un SLEP.

### Colegios versus universidades

Para colegios, el RBD identifica el establecimiento y normalmente su punto
principal. Para educación superior hay que separar:

- institución legal;
- sede o campus físico;
- carrera/programa ofrecido en esa sede;
- métricas de institución, carrera o cohorte.

Una acreditación institucional no debe copiarse como si fuera acreditación de
cada carrera, ni un resultado nacional de una carrera asignarse a una sede.

## Reglas de privacidad y presentación

- No servir registros individuales de matrícula, asistencia, SIMCE o DEMRE.
- Agregar por RBD/sede/programa y suprimir cohortes pequeñas.
- Conservar `sample_size`, `coverage_rate`, año y fuente junto con cada valor.
- No interpretar ausencia de datos como cero.
- PAES debe incluir participantes/cobertura porque la rendición es selectiva.
- Denuncias y sanciones deben mostrar hechos, periodo y denominador; no producir
  automáticamente un “colegio peligroso”.
- IVE/SEP describe contexto socioeducativo, no calidad.
- Comparar SIMCE sólo dentro de grado y asignatura compatibles, usando las
  variaciones y significancia publicadas por la Agencia.

## Estados del catálogo

- `ready`: hay descarga pública utilizable y el archivador puede intentar
  resolver sus releases.
- `review_required`: antes de automatizar hay que inspeccionar licencia,
  diccionario, granularidad o términos.
- `portal_only`: se puede consultar, pero no hay descarga masiva estable
  documentada.
- `restricted`: requiere autenticación, solicitud o condiciones incompatibles
  con una recolección automática.

El estado evita que “fuente encontrada” se confunda con “fuente autorizada y
operativa”.

El registro se valida con:

```sh
python3 scripts/validate_education_sources.py
```

## Resumen del orden de implementación

1. Respaldar releases y resolver permisos de redistribución.
2. Publicar el directorio canónico con dependencia oficial y sector derivado.
3. Añadir snapshots de matrícula, rendimiento y contexto.
4. Incorporar SIMCE/IDPS históricos y su evolución comparable.
5. Validar y agregar PAES sólo a nivel autorizado y agregado.
6. Incorporar institución/sede/oferta SIES y acreditación CNA.
7. Integrar contratos versionados en data-platform y web.

El detalle operativo y los gates están en el plan DH-040 vinculado arriba.

## Fuentes oficiales verificadas

- [Datos Abiertos MINEDUC](https://datosabiertos.mineduc.cl/)
- [Bases públicas Agencia de Calidad](https://informacionestadistica.agenciaeducacion.cl/)
- [SIMCE e IDPS](https://www.agenciaeducacion.cl/simce/)
- [Portal de datos DEMRE](https://portaldemre.demre.cl/portales/portal-bases-datos)
- [Resultados PAES por colegio](https://colegios.demre.cl/estadistica-resultados-puntajes)
- [Mi Futuro / SIES](https://www.mifuturo.cl/instituciones-de-educacion-superior-en-chile/)
- [Buscador CNA](https://www.cnachile.cl/Paginas/buscador-avanzado.aspx)
- [Sistema de Acceso](https://acceso.mineduc.cl/)
- [Superintendencia de Educación en datos.gob.cl](https://datos.gob.cl/organization/superintendencia-de-educacion)
- [SNED](https://sned.mineduc.cl/)
- [SNCAE](https://sncae.mma.gob.cl/portal/establecimientos)
