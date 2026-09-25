# Misión

Construir y ejecutar un pipeline reproducible que:
1. extraiga oportunidades de inversión publicadas por ProInversión (investinperu.pe),
2. las filtre y clasifique según su encaje con YOFC Perú (infraestructura telecom), y
3. entregue un Excel de pipeline comercial priorizado, más un reporte de cambios frente a la corrida anterior.

El usuario es el Director Comercial de la Unidad de Negocio Gobierno de YOFC Perú.
No hay meta por modalidad: OxI, APP y Proyectos en Activos (PA) valen igual.
El único corte duro es el monto: inversión > US$ 5M.

# Reglas no negociables

- NUNCA inventar datos. Si un campo no existe en la fuente, se deja vacío y se marca en `campos_faltantes`.
  Esto aplica en especial a CUI, montos, fechas y entidades.
- Todo registro conserva `fuente_url`, `portal` y `fecha_extraccion`.
- Los snapshots crudos se guardan en `data/raw/YYYY-MM-DD/` y no se modifican nunca.
- Scraping respetuoso: una sola sesión, pausa de 2–4 s entre acciones, sin paralelizar requests al mismo host.
  Si el sitio bloquea o pide captcha, DETENTE y avísame. No intentes evadirlo.
- Antes de la primera corrida completa, haz una corrida de prueba con 1 portal y muéstrame 5 registros de muestra.
  Espera mi OK.
- Si algo falla o hay ambigüedad, dilo explícitamente. Prefiero un problema detectado temprano a un Excel bonito con datos malos.

# Stack

Python 3.11+, Playwright (Chromium), pandas, openpyxl, pyyaml.
Todo corre con un solo comando: `python -m src.run --date today`.

# Fuentes (portales de investinperu.pe)

| id | Portal | Ruta de menú |
|----|--------|--------------|
| P1 | Portafolio APP / PA | Proyectos APP → Portafolio APP |
| P2 | OxI – Inversiones en búsqueda de empresa financista | Obras por impuestos → Inversiones en búsqueda de empresa financista |
| P3 | OxI – Intervenciones priorizadas Ley 29230 | Obras por impuestos → Intervenciones priorizadas según Ley N°29230 |
| P4 | OxI – Intervenciones priorizadas D.U. 010-2026 (FEN) | Obras por impuestos → Intervenciones priorizadas según D.U. 010-2026 |
| P5 | PA – Proyectos en cartera | Proyectos en Activos → Proyectos en cartera |
| P6 | VERTIX – Búsqueda por Proyectos | VERTIX → Búsqueda por Proyectos |

Las listas cargan por JavaScript. Si las pides con un request plano, devuelven "0 Proyectos".

# Estrategia de extracción (en este orden de preferencia)

1. **API interna.** Abre el portal con Playwright y registra el tráfico XHR/fetch (por ejemplo admin-ajax.php,
   /wp-json/ o cualquier endpoint que devuelva JSON). Si encuentras el endpoint que alimenta la tabla,
   úsalo directamente con los parámetros de filtro. Documenta el endpoint en `docs/endpoints.md`.
2. **Botón "Exportar a Excel".** Aplica los filtros en la UI, haz clic y captura la descarga
   con `page.expect_download()`.
3. **DOM.** Parsea la tabla o las tarjetas renderizadas y recorre toda la paginación.

Registra en el log cuál método funcionó para cada portal.

# Filtros (config/filters.yaml)

- P1, P5 y P6:
  - sector ∈ {Telecomunicaciones, Tecnología, Comunicaciones, Penitenciario}
  - fase ∈ {En Cartera}
  - año de adjudicación ∈ {2026, 2027, 2028}
  - Extraer además fase = Adjudicado (años 2023–2026) a un archivo aparte: `competencia.xlsx`.
- P2 y P3:
  - función ∈ {COMUNICACIONES, ORDEN PÚBLICO Y SEGURIDAD}
  - o entidad ∈ {MINISTERIO DE TRANSPORTES Y COMUNICACIONES, MINISTERIO DEL INTERIOR, JUSTICIA}
  - todos los estados
  - monto ≥ S/ 17,000,000
- P4: extraer todo sin filtro. El filtrado se hace en la etapa de clasificación.
- Si un filtro de la UI no existe o se llama distinto, extrae sin ese filtro y filtra en pandas. Reporta la diferencia.

# Esquema normalizado (data/processed/oportunidades.parquet)

id_interno, portal, modalidad (OxI | APP | PA | OxI-FEN), nombre, sector_funcion, entidad,
nivel_gobierno, region, provincia, distrito, cui, monto_soles, monto_usd, fase_estado,
fecha_adjudicacion_estimada, tipo_iniciativa, fuente_url, fecha_extraccion, campos_faltantes,
fit_nivel, fit_razon, timing, prioridad, notas.

- Tipo de cambio: S/ 3.40 por US$ (el que usa ProInversión para 2026).
  Si el monto ya viene en USD, no se convierte.
- Deduplicación: por CUI cuando exista. Si no hay CUI, por nombre normalizado (minúsculas, sin tildes) + entidad.
  Conservar la lista de portales donde aparece el registro.

# Clasificación

**fit_nivel**
- 3 **CORE**: fibra óptica, banda ancha, red de transporte, conectividad, internet, telecomunicaciones,
  nodos, O&M de red, red dorsal.
- 2 **ADYACENTE**: videovigilancia, cámaras, C4/C5, centro de monitoreo, radiocomunicación,
  alerta temprana, centro de datos, seguridad tecnológica.
- 1 **PERIFÉRICO**: obra de otro sector con un componente TI explícito (por ejemplo "equipamiento tecnológico").
- 0 **DESCARTAR**.

Primero se aplican reglas por palabras clave. Si hay ambigüedad, decide con juicio y escribe siempre `fit_razon` en una línea.

**timing**
- TEMPRANO: Idea, Por priorizar, En formulación/estructuración. Es el momento de influir en las bases.
- MEDIO: Priorizado, Actos previos, Informe previo.
- URGENTE: Proceso de selección o convocatoria abierta.
- COMPETENCIA: Adjudicado.

**prioridad**
- **A**: fit ≥ 2, monto_usd > 5M y timing ∈ {TEMPRANO, URGENTE}.
- **B**: fit ≥ 2, monto_usd > 5M y timing MEDIO. También fit = 3 con monto desconocido.
- **C**: el resto con fit ≥ 1.
- Excluir: fit = 0 o monto_usd ≤ 5M.

# Validación cruzada (semillas conocidas)

Verifica si estos casos aparecen. Si alguno no aparece, repórtalo. No lo agregues a mano.
- Proyectos Selva (MTC, PA): Loreto Alto Amazonas–Datem (~S/ 730.1M), Loreto Napo–Putumayo (~S/ 418.3M),
  Madre de Dios (~S/ 148.6M).
- Red Dorsal Nacional de Fibra Óptica (concesión definitiva).
- Pronatel: proyectos regionales de Cajamarca, Piura y Tumbes (reformulación).
- GORE Junín / MP Huancayo: seguridad ciudadana, central de monitoreo (~S/ 32M, OxI).

# Entregables (output/)

1. `pipeline_YYYY-MM-DD.xlsx` con estas hojas:
   - `Prioridad_A`, `Prioridad_B`, `Prioridad_C`: ordenadas por monto_usd descendente.
   - `Competencia`: adjudicados, con el nombre de la empresa si la fuente lo trae.
   - `Resumen`: conteos por modalidad, región, fit y timing.
   - `Calidad_datos`: registros con campos faltantes y método de extracción por portal.
2. `reporte_YYYY-MM-DD.md` (máximo una página):
   - qué es nuevo,
   - qué cambió de fase y qué desapareció frente a la corrida anterior,
   - los 5 principales de prioridad A con la acción sugerida,
   - las semillas no encontradas,
   - los problemas de extracción.
3. `data/processed/oportunidades.parquet`: histórico acumulado, que permite hacer el diff.

# Orquestación con subagentes

Usa los subagentes de `.claude/agents/` en este orden. Ninguno avanza si el anterior falló su checklist.
1. `scraper-proinversion` → data/raw/
2. `normalizador` → data/processed/
3. `clasificador` → agrega fit, timing y prioridad
4. `reportero` → output/

Al terminar, dame un resumen de 5 líneas: total extraído, total > US$ 5M, cuántos A/B/C, novedades y problemas.

# Fase 2 (NO ejecutar sin mi OK)

- Verificar el CUI de las prioridad A en la consulta pública de Invierte.pe (viabilidad, monto actualizado, PMI).
- Cruzar con la base OxI del MEF (empresa financista, estado de CIPRL) para mapear competidores.
- Cargar el pipeline en una tabla de Supabase para el CRM.
- Programar una corrida semanal (cron) con alerta cuando aparezca una nueva prioridad A.
