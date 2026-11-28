# DH-040 — Aranceles publicados por los colegios particulares pagados

Actualizado: 2026-11-24.

El Directorio MINEDUC sólo informa un tramo autodeclarado de mensualidad, y su
tramo superior («Más de $100.000») agrupa a casi todos los particulares pagados.
`scripts/collect_school_fees.py` busca los montos que cada colegio publica en
su propio sitio web y en los documentos que enlaza.

## Cómo regenerarlo

Requiere `data/build/education/colegios_perfil.json` (ver
[`SCHOOL_PROFILES.md`](SCHOOL_PROFILES.md)) y `pdftotext` (poppler).

```sh
python3 scripts/collect_school_fees.py discover
python3 scripts/collect_school_fees.py discover --only-missing
python3 scripts/collect_school_fees.py crawl
python3 scripts/collect_school_fees.py extract
python3 scripts/collect_school_fees.py report
```

`--sectors particular_subvencionado` repite el proceso para subvencionados;
`--manual sitios.json` agrega sitios encontrados a mano (`{"<rbd>": {"url": …}}`).

## Método

1. **Sitio web.** Primero OpenStreetMap: la etiqueta `website` o el dominio del
   correo del colegio (se descartan Gmail, Hotmail y similares), unidos por RBD
   o por nombre a menos de 600 m. Luego dominios derivados del nombre
   (`colegiosanignacio.cl`, `sanignacio.cl`, la sigla) y, para los que siguen
   sin sitio, el nombre con la comuna y `.com`. Un dominio derivado se acepta
   sólo si su portada contiene al menos el 75 % de las palabras distintivas del
   nombre.
2. **Confirmación.** Un dominio derivado puede pertenecer a un homónimo de otra
   ciudad. El sitio cuenta como confirmado si alguna página o documento
   rastreado nombra la comuna del colegio, o si viene de la etiqueta `website`
   de OpenStreetMap con el nombre coincidente. Los montos de sitios no
   confirmados no se usan.
3. **Rastreo cortés.** Se respeta `robots.txt`, se espera 0,8 s entre pedidos a
   un mismo sitio y se visitan hasta 18 páginas por colegio, priorizando los
   enlaces con palabras como aranceles, colegiatura, matrícula, admisión o
   valores. Se descargan hasta 10 documentos (PDF, DOCX, XLSX, también desde
   Google Drive) cuyo enlace o página de origen trate esos temas. Las copias
   quedan en `data/raw/education/school_websites/<rbd>/`, fuera de Git.
4. **Extracción.** Se buscan montos en pesos (`$ 450.000`), en UF (`12,5 UF`) y
   cifras con separador de miles dentro de tablas, y se asignan a la palabra
   clave más cercana: matrícula, mensualidad o colegiatura, arancel anual,
   cuota de incorporación o postulación. Se descartan montos fuera de rangos
   plausibles y los RUT. Los montos en UF se convierten con la UF del día
   publicada por el SII. Si el colegio sólo publica un arancel anual, la
   mensualidad se deriva dividiéndolo en 10 cuotas y queda marcada como
   derivada.
5. **Resumen.** Por colegio y categoría se conserva el rango (mínimo y máximo,
   porque el precio suele variar por nivel), el año más reciente mencionado
   junto al monto, la URL de origen y el texto que lo rodea, para revisarlo.

## Productos

En `data/build/education/fees/`:

| Archivo | Contenido |
| --- | --- |
| `sitios.json` | Sitio por RBD, método, coincidencia con el nombre e intentos |
| `aranceles.json` | Montos por colegio con fuente, contexto y menciones |
| `aranceles.csv` | Una fila por colegio: matrícula, mensualidad, incorporación, año y fuente |
| `documentos.csv` | Cada documento enlazado: tipo, si se descargó, si tenía texto y montos |
| `cobertura.json`, `cobertura_embudo.csv` | Análisis de cobertura |

## Resultado del 2026-11-24

Base: 498 particulares pagados en funcionamiento con básica o media (266 en la
Región Metropolitana). UF del día según el SII: $41.398,57.

### Cobertura

| Etapa | Colegios | % de 498 |
| --- | ---: | ---: |
| Sitio web identificado | 335 | 67,3 |
| Sitio confirmado (nombre y comuna) | 239 | 48,0 |
| Publican algún monto (incluye postulación) | 155 | 31,1 |
| Publican matrícula | 100 | 20,1 |
| Publican mensualidad o arancel anual | 112 | 22,5 |
| Publican matrícula y mensualidad | 78 | 15,7 |
| Publican cuota de incorporación | 71 | 14,3 |
| Mensualidad de 2026 o 2027 | 91 | 18,3 |

- De los 335 sitios, 136 vinieron de OpenStreetMap (69 por `website` y 67 por
  el dominio del correo) y 199 de dominios derivados del nombre (31 de ellos en
  la pasada extendida).
- 90 sitios quedaron sin confirmar. En la revisión manual, la mayoría resultó
  de otro colegio homónimo (Talca en vez de Los Andes, Arica en vez de Talca) o
  de empresas sin relación; algunos pocos eran correctos pero no mencionaban la
  comuna en las páginas visitadas.
- 51 de las 112 mensualidades se derivaron de un arancel anual dividido en 10.
- Los 112 colegios con mensualidad suman 84.922 estudiantes: 24,8 % de la
  matrícula de los particulares pagados. 76 están en la Región Metropolitana.

### Documentos

En los 239 sitios confirmados se enlazaron 1.066 documentos: 918 PDF, 35 Word,
1 Excel, 101 sin extensión (en su mayoría Google Drive) y 11 páginas HTML con
nombre de documento. Se descargaron 873.

- 173 colegios (34,7 %) tienen al menos un documento descargado.
- 73 colegios (14,7 %) tienen montos dentro de un documento, y en 61 (12,2 %)
  el monto principal sale de un documento y no de la página.
- 31 PDF no tienen texto extraíble (son escaneos); necesitarían OCR.
- Por el texto de su enlace, predominan reglamentos (336: interno, de
  evaluación, de convivencia, de becas) y documentos de admisión (276); 106
  mencionan matrícula, 99 son circulares y sólo 37 se titulan «aranceles». La
  lista completa con su URL está en `documentos.csv`.

### Montos encontrados

| Monto (máximo por colegio) | Colegios | Cuartil 1 | Mediana | Cuartil 3 |
| --- | ---: | ---: | ---: | ---: |
| Mensualidad | 112 | $310.000 | $453.000 | $630.000 |
| Matrícula | 100 | $304.000 | $418.000 | $559.000 |
| Cuota de incorporación | 71 | $621.000 | $1.408.000 | $2.808.000 |

### Precisión

Una muestra aleatoria de 25 colegios con mensualidad, revisada a mano contra
el texto de origen, tuvo 19 valores correctos (76 %). Los errores fueron:

- servicios aparte (jardín de jornada completa, after school);
- tablas con varias columnas donde un valor de matrícula o incorporación quedó
  como mensualidad;
- un caso dudoso.

Por eso cada fila conserva la URL y el texto de origen.

## Aranceles revisados de los mejores PAES de la RM

`sources/school-fees-top-paes-rm.json` guarda, para 1.º básico, los cobros revisados a
mano de los 15 colegios con mejor PAES Lectora + M1 (30 o más inscritos) de cada
dependencia en la Región Metropolitana. `build_school_profiles.py` los copia a
`colegios_web.json` como `arancelesRevisados`. El campo `basis` dice de dónde sale el
monto:

- `publicado` (o ausente): particulares pagados, con los valores y documentos que publica
  cada colegio (colegiatura, matrícula, incorporación y otros cargos, con su fuente).
- `topeLegal`: subvencionados con financiamiento compartido y el Liceo Amanda Labarca.
  Es el cobro mensual máximo por alumno 2026 y su número de cuotas según la
  [Resolución Exenta N°241 del 22-01-2026](https://www.comunidadescolar.cl/wp-content/uploads/2026/01/R13-REX-No241-22-01-2026-ANEXO1.pdf)
  de la Seremi de Educación RM; si el cobro es diferenciado se usa la fila de básica que
  incluye 1.º. El colegio puede cobrar menos y la resolución no fija matrícula.
- `gratuito`: sin mensualidad según el Directorio MINEDUC 2026 y sin fila en esa
  resolución (14 de los 15 públicos, Polivalente San Rafael y San Joaquín de Renca).

La web suma el costo anual y el de primer año y marca como estimado lo que se completa con
supuestos (10 cuotas cuando el colegio no las publica, años distintos, tope legal).

## Límites y próximos pasos

- **Colegios sin sitio (163):** tienen un tamaño similar al resto (mediana de
  579 estudiantes contra 595) y están algo más en regiones (44 % en la Región
  Metropolitana contra 58 %). Se pueden agregar a mano o por búsqueda web con
  `--manual`.
- **Valores no publicados:** muchos colegios grandes dan sus aranceles sólo en
  la entrevista de admisión o en el portal de apoderados, que requiere login y
  no se rastrea.
- **PDF escaneados:** 31 documentos necesitarían OCR (por ejemplo, `tesseract`).
- **Sitios con JavaScript:** algunos cargan su contenido por JavaScript y
  muestran poco texto sin un navegador real.
- **Revisión manual:** antes de usar un monto en el producto conviene
  revisarlo contra su fuente, empezando por los extremos y por los rangos
  amplios dentro de un mismo colegio.
