/* Genera el documento técnico del pipeline (docs/tilegen-etl-reference.docx).
 *
 * Es la referencia de ETL para compartir fuera del repo: se sube a Google Drive
 * y se abre como Google Doc (los títulos y las tablas se conservan). El .docx es
 * la SALIDA; lo que se edita es este script, igual que gen_catalog.py y
 * gen_cobertura.py con los suyos.
 *
 * A diferencia de esos dos, NO lee nada de S3 ni de la config: el contenido está
 * escrito acá adentro. Cuando cambie el pipeline hay que actualizarlo a mano, y
 * sobre todo la tabla del anexo (seccion 16), que es una foto de catalog.csv.
 *
 * Formato a propósito plano: sin colores ni recuadros. Lo único que estructura el
 * documento son los títulos de nivel 1/2/3 (de ahí sale el índice automático de
 * Google Docs) y las tablas.
 *
 * Necesita la librería docx de npm, que NO es dependencia del módulo:
 *
 *     npm install docx
 *     node docs/gen_etl_doc.js
 */

const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, BorderStyle,
  LevelFormat, PageBreak,
} = require("docx");

const W = 9360;            // content width, Letter with 1in margins
const SANS = "Arial";
const MONO = "Courier New";
const BODY = 22;           // 11pt
const LINE = "999999";

/* ---------- inline markup: **bold** and `code` ---------- */
function rt(text, opts = {}) {
  const size = opts.size || BODY;
  const base = { font: SANS, size };
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(new TextRun({ ...base, text: text.slice(last, m.index) }));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(new TextRun({ ...base, text: tok.slice(2, -2), bold: true }));
    else out.push(new TextRun({ ...base, text: tok.slice(1, -1), font: MONO, size: size - 3 }));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(new TextRun({ ...base, text: text.slice(last) }));
  return out;
}

/* ---------- blocks ---------- */
function p(text, opts = {}) {
  return new Paragraph({
    children: rt(text, opts),
    spacing: { after: opts.after === undefined ? 160 : opts.after, line: 276 },
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 400, after: 160 },
    children: [new TextRun({ text, font: SANS, size: 32, bold: true, color: "000000" })],
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 300, after: 120 },
    children: [new TextRun({ text, font: SANS, size: 26, bold: true, color: "000000" })],
  });
}

function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 240, after: 100 },
    children: [new TextRun({ text, font: SANS, size: 22, bold: true, color: "000000" })],
  });
}

function code(lines) {
  return lines.map((ln, i) =>
    new Paragraph({
      children: [new TextRun({ text: ln === "" ? " " : ln, font: MONO, size: 18 })],
      spacing: { before: i === 0 ? 120 : 0, after: i === lines.length - 1 ? 180 : 0, line: 240 },
      indent: { left: 360 },
      keepLines: true,
      keepNext: i < lines.length - 1,
    })
  );
}

function bullets(items) {
  return items.map((t) =>
    new Paragraph({
      children: rt(t),
      numbering: { reference: "dot-list", level: 0 },
      spacing: { after: 100, line: 276 },
    })
  );
}

function steps(items) {
  return items.map((t) =>
    new Paragraph({
      children: rt(t),
      numbering: { reference: "num-list", level: 0 },
      spacing: { after: 100, line: 276 },
    })
  );
}

function table(headers, rows, widths, opts = {}) {
  const border = { style: BorderStyle.SINGLE, size: 2, color: LINE };
  const cell = (txt, isHead, w) =>
    new TableCell({
      width: { size: w, type: WidthType.DXA },
      margins: { top: 80, bottom: 80, left: 120, right: 120 },
      children: [
        new Paragraph({
          children: isHead
            ? [new TextRun({ text: txt, font: SANS, size: 19, bold: true })]
            : rt(txt, { size: 19 }),
          spacing: { after: 0, line: 240 },
        }),
      ],
    });
  return new Table({
    columnWidths: widths,
    width: { size: W, type: WidthType.DXA },
    borders: {
      top: border, bottom: border, left: border, right: border,
      insideHorizontal: border, insideVertical: border,
    },
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, true, widths[i])) }),
      ...rows.map((r) => new TableRow({ children: r.map((c, i) => cell(c, false, widths[i])) })),
    ],
  });
}

function spec(pairs, leftWidth) {
  const lw = leftWidth || 2800;
  return table(["Setting", "Value"], pairs, [lw, W - lw]);
}

function gap(after) {
  return new Paragraph({ children: [new TextRun({ text: "", font: SANS, size: 12 })], spacing: { after: after || 160 } });
}

/* ---------- document ---------- */
const d = [];

/* title block */
d.push(new Paragraph({
  spacing: { after: 140 },
  children: [new TextRun({ text: "Tilegen: climate data ETL to per-scene Zarr cubes", font: SANS, size: 40, bold: true })],
}));
d.push(new Paragraph({
  spacing: { after: 100 },
  children: [new TextRun({ text: "Technical reference for tilegen v0.4.3", font: SANS, size: 24, color: "444444" })],
}));
d.push(new Paragraph({
  spacing: { after: 400 },
  children: [new TextRun({ text: "Daniela Risaro, Suyana. 23 September 2026.", font: SANS, size: 20, color: "444444" })],
}));

d.push(p("How tilegen extracts daily climate grids from six upstream providers, standardizes them, and lands them as long-timeseries Zarr cubes in `s3://suyana-tiles`, plus how that pipeline is scheduled, released, and monitored in production."));

/* contents */
d.push(h2("Contents"));
[
  "1. Scope and purpose",
  "2. System context",
  "3. Data model",
  "4. Extract",
  "5. Transform",
  "6. Load",
  "7. Idempotency and provisional absence",
  "8. Orchestration",
  "9. Deployment and release",
  "10. Observability",
  "11. Backfills",
  "12. Maintenance: rechunk",
  "13. CLI reference",
  "14. Configuration reference",
  "15. Failure modes and runbook",
  "16. Appendix: dataset register",
].forEach((t) => d.push(new Paragraph({
  children: [new TextRun({ text: t, font: SANS, size: BODY })],
  spacing: { after: 60 },
  indent: { left: 300 },
})));
d.push(new Paragraph({ children: [new PageBreak()] }));

/* ---- 1 ---- */
d.push(h1("1. Scope and purpose"));
d.push(p("tilegen maintains a local, uniform copy of the daily climate variables that Suyana's parametric products depend on. It downloads gridded data from public providers, cuts it to a fixed set of geographic scenes, and writes it into Zarr cubes on S3 that are optimized for reading one point's full history."));
d.push(h3("In scope"));
d.push(...bullets([
  "Scheduled incremental ingestion of eight datasets, keeping each current with its upstream provider.",
  "Historical backfill of the same datasets to their earliest available date.",
  "Publication of a machine-readable coverage catalog describing what is actually present in the bucket.",
  "A secondary per-date COG mosaic output for map rendering (`--format cog`), which is not the primary product.",
]));
d.push(h3("Explicitly not in scope"));
d.push(...bullets([
  "**No scientific recomputation.** Values pass through unchanged from the provider. The only arithmetic performed is daily aggregation of sub-daily series where the provider publishes no daily product (see 5.2).",
  "**No trigger or payout logic.** That lives downstream and reads these cubes.",
  "**No serving layer.** Consumers open the Zarr stores directly with `xarray`.",
]));
d.push(h2("1.1 Why hold a local copy at all"));
d.push(p("ARCO-ERA5, the DestinE mirror and CHIRPS in Earth Engine all exist. The copy is justified by three properties they do not jointly provide:"));
d.push(...bullets([
  "**One uniform layout.** Every dataset lands in the same bucket, on the same scenes, with the same dimension names and one authentication path. Downstream code does not branch per provider.",
  "**Independence.** A payout calculation must not depend on a third-party mirror that requires a token, changes its schema, or goes offline.",
  "**Reproducibility.** A trigger computed today must recompute identically in two years. Frozen, versioned paths (`chirps/v2.0/...`) give that; a live service does not.",
]));
d.push(h2("1.2 Why not Airflow or Kedro"));
d.push(p("One machine, one operating user, all state in S3, cron for orchestration. The pipeline stages are pure functions of their inputs, so an orchestrator can be introduced later without restructuring. Adopting one now would add a scheduler database and a web service to operate, in exchange for scheduling that cron already performs correctly. See section 7 for why resumability is a property of the data model here, not of the orchestrator."));

/* ---- 2 ---- */
d.push(h1("2. System context"));
d.push(p("The pipeline runs in three stages for every (dataset, scene, variable, date) it is asked about:"));
d.push(table(
  ["Stage", "What happens"],
  [
    ["Extract", "`plan()` reads the ledger and computes only the pending dates. `granules()` groups pending (variable x date) into download units. `fetch()` pulls each granule from the provider, yielding assets."],
    ["Transform", "`asset_to_dataarray()` opens GeoTIFF or NetCDF into one array; casts to float32, nodata to NaN, renames dims to latitude and longitude; `clip_box()` to the scene bbox, then `align()` to the store grid."],
    ["Load", "`write_batch()` region-writes consecutive date runs into pre-allocated slots. `update_ledger()` records dates written and dates confirmed absent. The run summary is emitted as JSON to the cron log."],
  ],
  [1500, W - 1500]
));
d.push(gap());
d.push(h2("2.1 Data products"));
d.push(p("The primary product is one Zarr store per (dataset, scene) pair, addressed by a stable path:"));
d.push(...code([
  "s3://suyana-tiles/{dataset}/{version}/{scene}.zarr",
  "s3://suyana-tiles/{dataset}/{version}/_ledger/{scene}/{variable}.json",
]));
d.push(p("The version segment is the provider's product version (v2.0, daily-stats, reanalysis), not a version of this software. It is frozen: when a provider issues v3.0 it becomes a new dataset alongside the old one, so that a historical calculation keeps resolving to the bytes it was computed from. chirps v2.0 and chirps-v3 coexist for exactly this reason."));
d.push(p("A reader needs no client library beyond xarray:"));
d.push(...code([
  "import xarray as xr",
  "",
  'ds = xr.open_zarr("s3://suyana-tiles/chirps/v2.0/peru.zarr")',
  'series = ds.precip.sel(latitude=-3.75, longitude=-73.25, method="nearest")',
  'series.sel(time=slice("1981-01-01", "2025-12-31"))   # ~1 object read per year',
]));
d.push(h2("2.2 The scene model"));
d.push(p("A scene is a fixed named rectangle in EPSG:4326, defined once in `config/scenes.yaml` and shared by every dataset. There are 21. The partition rule is administrative rather than geometric: one scene per country, small countries grouped (centroamerica, antillas_mayores, guayanas), large ones split (brasil x 4, mexico x 2), plus index regions for phenomena far from any coast (nino34)."));
d.push(p("Bounding boxes are whole degrees, [W, S, E, N], with one degree of margin at land borders and three to five degrees extended offshore where there is coastline. The same scene therefore serves both land and ocean variables; each dataset fills the pixels where it has data and leaves the rest NaN. CPC, for instance, covers 20 land scenes and is absent from nino34 by design, not by failure."));
d.push(p("**Why partition by scene at all.** A single global cube per dataset would make every point read contend with every other, and would tie all 21 regions to one write lock. Per-scene stores make the daily job trivially parallel across regions and keep any one corruption or rewrite blast-radius to a single country."));
d.push(h2("2.3 Component map"));
d.push(table(
  ["Path", "Responsibility"],
  [
    ["`tilegen/cli.py`", "Click command group; argument parsing, scene resolution, run orchestration across scenes"],
    ["`tilegen/config.py`", "Typed loading of config.yaml, scenes.yaml and datasets/*.yaml"],
    ["`tilegen/zarr_pipeline.py`", "The ETL itself: plan(), run(), absence horizon, date clamping"],
    ["`tilegen/zarrstore.py`", "Store creation, region writes, ledger I/O, chunk audit and rechunk"],
    ["`tilegen/sources/base.py`", "Granule, Asset, abstract DataSource"],
    ["`tilegen/sources/*.py`", "Six provider adapters (see 4.2)"],
    ["`tilegen/assets.py`", "GeoTIFF or NetCDF to standardized DataArray"],
    ["`tilegen/grid.py`, `tiler.py`", "Fixed 10 degree tile grid and tiling for the COG output path"],
    ["`tilegen/s3io.py`", "S3 helpers"],
    ["`docs/gen_cobertura.py`", "Coverage matrix generator (scene x variable), reads ledgers only"],
    ["`docs/gen_catalog.py`", "Catalog generator to CATALOG.md, catalog.csv, catalog.yaml"],
    ["`~/tilegen-prod/bin/`", "Production wrappers: prod-run.sh, rechunk-run.sh, avisar-clickup.sh"],
  ],
  [2700, W - 2700]
));

/* ---- 3 ---- */
d.push(h1("3. Data model"));
d.push(h2("3.1 The cube"));
d.push(p("Each store is a single time x latitude x longitude cube holding the dataset's full history for one scene. The decisive property is that **the cube is created once with the dataset's complete daily time axis**, from its configured start to the present, as metadata only. No data chunks are written at creation; unwritten chunks read back as NaN."));
d.push(p("Three consequences follow, and the rest of the design depends on all three:"));
d.push(...steps([
  "**Writes are position-independent.** Every date already owns a slot, so 1994 can be written after 2025 with no restructuring.",
  "**Writes are idempotent.** Writing the same date twice overwrites the same region with the same bytes.",
  "**Structure never changes during ingestion.** A region write touches only pre-allocated chunks, so the consolidated metadata index is never rewritten on the hot path.",
]));
d.push(p("Arrays are float32 with NaN as the single nodata convention, and dimensions are always named (time, latitude, longitude) regardless of what the provider called them."));
d.push(h2("3.2 Chunking"));
d.push(spec([
  ["`time_chunk`", "365, one year per chunk"],
  ["`spatial_chunk`", "32 px per side, applied as a cap, not a fixed size"],
  ["Typical chunk", "About 1.5 MB compressed"],
]));
d.push(gap());
d.push(p("The access pattern this serves is a long series at a single point, which is what a parametric trigger evaluates. With a one-year time chunk, forty years of one pixel costs roughly forty object reads rather than tens of thousands."));
d.push(p("**Regression fixed in v0.4.0.** spatial_chunk was originally 128. Because the value is clamped with min() against the scene's own pixel dimensions, and an ERA5 scene at 0.25 degrees is only about 85x89 px, the cap swallowed the entire scene into one chunk. Reading one pixel's series pulled the whole country for every year. Lowering it to 32 splits even the coarse grids. The migration of existing cubes is the rechunk job in section 12; `tilegen chunks --stale` lists cubes still on the old geometry."));
d.push(h2("3.3 The ledger"));
d.push(p("The ledger is the pipeline's entire persistent state. It is one small JSON document per (scene, variable), stored in S3 outside the .zarr prefix so that rewriting or replacing a cube never disturbs it:"));
d.push(...code([
  "{dataset}/{version}/_ledger/{scene}/{variable}.json",
  "",
  "{",
  '  "written": ["1981-01-01", "1981-01-02", ...],',
  '  "missing": ["2003-06-14", ...]',
  "}",
]));
d.push(p("The two lists carry different meanings, and the distinction is load-bearing:"));
d.push(...bullets([
  "`written`: the date is filled in the cube. Skip it.",
  "`missing`: the date has been confirmed not to exist at the provider. Skip it, unless `--retry-missing` is passed.",
  "Anything in neither list is pending and will be requested on the next run.",
]));
d.push(p("Because state lives beside the data in S3 rather than in a database on the server, a rebuilt or replaced machine loses nothing, and any run can be started from any host with bucket access."));

/* ---- 4 ---- */
d.push(h1("4. Extract"));
d.push(h2("4.1 Source interface"));
d.push(p("Every provider is reached through one abstract class with two required methods. Adding a provider means subclassing it and registering the subclass in sources/__init__.py; nothing in the transform or load stages changes."));
d.push(...code([
  "@dataclass",
  "class Granule:          # the unit of download",
  '    key: str            # "chirps:precip:2024-01-15"',
  "    variable: str",
  "    dates: list",
  "",
  "@dataclass",
  "class Asset:            # one variable, one date, ready to tile",
  "    variable: str",
  "    date: dt.date",
  "    path: Path          # local GeoTIFF or NetCDF",
  "    band: int = 1",
  "    time_index: int | None = None",
  "",
  "class DataSource(ABC):",
  "    parallel_fetch = True      # False for queued APIs (CDS)",
  "    shared_downloads = False   # True when one file serves every scene",
  "",
  "    def granules(self, variables, dates) -> list[Granule]: ...",
  "    def fetch(self, granule) -> list[Asset]: ...",
]));
d.push(p("A granule is the smallest thing worth requesting from a given provider: one HTTP file for CHIRPS, one queued request covering a whole month for CDS. The adapter decides that grouping, which is where most provider-specific cost optimization lives."));
d.push(p("The contract for a date the provider does not have is explicit: fetch() raises FileNotFoundError. The pipeline catches it, records the granule's dates through the absence rule (section 7), and continues. It is not an error condition and does not increment the error counter."));
d.push(h2("4.2 Source adapters"));
d.push(table(
  ["Adapter", "Protocol", "Provider", "Used by", "Parallel"],
  [
    ["`http_geotiff`", "HTTPS, one GeoTIFF per day", "UCSB Climate Hazards Center", "chirps, chirps-v3, chirts", "yes, x6"],
    ["`era5_cds`", "Queued API (cdsapi)", "Copernicus CDS", "era5, era5-land", "serial"],
    ["`era5_edh`", "Remote Zarr over HTTP", "DestinE EarthDataHub", "era5 (swvl1, backfills)", "yes"],
    ["`cpc_psl`", "OPeNDAP", "NOAA PSL", "cpc", "yes"],
    ["`cfs_ncei`", "HTTPS GRIB", "NOAA NCEI", "cfs", "yes"],
    ["`cfsr_gdex`", "HTTPS, hourly series", "NCAR GDEX (d093001)", "cfsr", "yes"],
  ],
  [1560, 2000, 2100, 2500, 1200]
));
d.push(gap());
d.push(p("parallel_fetch = False on the CDS adapter is not a performance oversight. CDS is a queue: concurrent requests from one account do not complete faster, they simply occupy more queue slots. The pipeline honours the flag by setting max_workers = 1 for that source, while HTTP sources run at runtime.workers = 6."));
d.push(p("**Dual-source datasets.** era5 declares era5_cds as its default source but can be run against the EarthDataHub mirror with `--source era5_edh`. The two are not interchangeable in coverage: swvl1 (soil moisture) has no cds_variable and therefore exists only on EDH, which is why it is scheduled as its own weekly job rather than folded into the daily ERA5 lines."));
d.push(h2("4.3 Request economics"));
d.push(p("Providers charge in different currencies, and each adapter is shaped by its own. The CDS case is the most consequential."));
d.push(p("CDS costs roughly twenty minutes per request, largely independent of the request's size. Requesting a per-scene window would mean 21 requests per month of backfill, about 8,000 requests for a 380-month history. Instead era5.yaml declares a single window covering all 21 scenes at once:"));
d.push(...code([
  "cds_fetch_area: [-170, -57, -32, 35]   # W, S, E, N: the box enclosing all scenes",
]));
d.push(p("That reduces the same backfill to 380 requests. The per-scene cut is then done locally. The mechanism that makes it work is the shared_downloads = True flag: when set, the pipeline does not delete the downloaded file after finishing a granule, because the next scene will read the same file off disk instead of re-queuing it."));
d.push(p("**Operational consequence.** Shared downloads accumulate in .work/downloads/era5/ at roughly 25 MB per month fetched, or about 10 GB for a full backfill. They are not garbage-collected and must be pruned by hand once the backfill closes."));
d.push(h2("4.4 Absence at source"));
d.push(p("A fetch that finds nothing produces one of two outcomes, and choosing between them correctly is the single most important behaviour in the pipeline. It is documented on its own in section 7."));

/* ---- 5 ---- */
d.push(h1("5. Transform"));
d.push(h2("5.1 Standardization"));
d.push(p("asset_to_dataarray() is the single boundary between provider formats and the rest of the system. Whatever the adapter downloaded, it leaves the function as one DataArray in canonical form:"));
d.push(table(
  ["Step", "Action", "Reason"],
  [
    ["Open", "NetCDF via xarray under a global lock; GeoTIFF via rioxarray, band squeezed", "The NetCDF C library is not thread-safe; the lock serializes only opening, not processing"],
    ["Clip", "rio.clip_box(*bbox) to the scene", "Cut once, at the earliest point, so everything downstream is scene-sized"],
    ["Cast", "astype(\"float32\")", "One dtype across all datasets; halves storage against float64"],
    ["Nodata", "Provider sentinel to NaN, skipped when the sentinel is already NaN", "NaN is the single missing-value convention in the cubes"],
    ["Rename", "Detected x and y dims to longitude and latitude", "Providers disagree (lon, x, longitude); consumers should not have to"],
    ["Strip", "reset_coords(drop=True)", "Removes provider scalar coordinates that would otherwise pollute the cube schema"],
  ],
  [1000, 3700, W - 4700]
));
d.push(gap());
d.push(p("There is no reprojection stage. Every supported provider publishes on a regular EPSG:4326 lat/lon grid, and the cubes preserve each dataset's native resolution: 0.05 degrees for CHIRPS, 0.25 for ERA5, 0.5 for CPC. Resampling is deliberately left to the consumer, so that no interpolation decision is baked irreversibly into the archive."));
d.push(h2("5.2 Daily aggregation"));
d.push(p("Where a provider publishes a daily product, it is used as-is. Where it does not, the daily statistic is computed from the sub-daily series, and the aggregation rule is declared per variable in the dataset YAML rather than hardcoded:"));
d.push(...code([
  "t2m_max:",
  "  cds_variable: 2m_temperature",
  "  daily_statistic: daily_maximum",
  "  edh_hourly_variable: t2m        # the daily EDH store has no maxima ->",
  "  edh_hourly_statistic: max       # aggregate the hourly store locally",
  "  units: K",
]));
d.push(p("Three cases in the current configuration are worth knowing:"));
d.push(...bullets([
  "**t2m_min is aggregated, not taken from mn2t.** ERA5 publishes mn2t, but its window is \"since the last post-processing\", which does not align to the calendar day. Using it would leave the daily minimum offset relative to the maximum. The minimum over the day's 24 hourly values is computed instead.",
  "**fg10_max and the accumulated variables** (precip_sum, ssrd_sum) have the same problem: the daily EDH store carries neither maxima nor accumulations, so both are derived from the hourly store.",
  "**cfsr daily means** are taken over the window 01:00 on day D to 00:00 on D+1 UTC, which is the 24-hour block belonging to that calendar day in an hourly series stamped at hour-end.",
]));
d.push(p("**Units are stored, not converted.** Variables keep provider units and declare them in the config (K, m, J m-2, m s-1). Derivations belong downstream. Two that recur:"));
d.push(...bullets([
  "Monthly irradiation: `kWh m-2 = sum(clip(ssrd_sum, 0, None)) / 3.6e6` over the month's real days, which is exact, so no days-in-month factor is needed.",
  "Snow depth: `h(m) = sd_mean * 1000 / rsn_mean`, because sd_mean is the water equivalent of the snowpack, **not** its height. rsn_mean (snow density) exists solely to make that conversion possible.",
]));
d.push(h2("5.3 Clipping and alignment"));
d.push(p("Clipping happens twice, for different reasons. clip_box() in the transform stage cuts the provider's grid down to the scene. Then ZarrStore.align() snaps the resulting array onto the store's existing coordinate vectors before writing, so that floating-point differences in how a provider expresses the same grid cannot produce a one-pixel offset between a day written today and one written in 2027."));
d.push(p("**Fixed in commit f8f2ca4.** An earlier version of the EDH clip dropped the boundary row and column on 0.1 degree grids, silently narrowing every era5-land scene by one pixel on two edges. The class of bug to watch for when adding a provider is half-open versus closed interval conventions in the provider's own subsetting API."));

/* ---- 6 ---- */
d.push(h1("6. Load"));
d.push(h2("6.1 Store creation"));
d.push(p("On the first run for a (dataset, scene) the store is created from the first standardized array, which supplies the spatial grid, plus the dataset's configured date range, which supplies the time axis. Creation writes metadata only. New stores are pinned to Zarr format v2 so that both zarr 2.x and 3.x environments can read and write them, since consumers are not all on the same version."));
d.push(h2("6.2 Region writes"));
d.push(p("write_batch() writes many days of one variable in a single pass:"));
d.push(...code([
  'da = da.sortby("time")',
  'times  = pd.DatetimeIndex(da["time"].values)',
  'gaps   = np.diff(times.values).astype("timedelta64[D]") > np.timedelta64(1, "D")',
  "breaks = np.flatnonzero(gaps) + 1",
  "",
  "for run in np.split(np.arange(times.size), breaks):",
  "    (da.isel(time=run).to_dataset(name=variable)",
  '       .to_zarr(self.mapper(), region="auto", consolidated=False))',
]));
d.push(p("Two details carry real weight:"));
d.push(...bullets([
  "**Dates are split into consecutive runs.** region=\"auto\" requires a contiguous slice along time. A batch covering 1 to 5 March and 9 March becomes two write calls, not six.",
  "**consolidated=False on every write.** A region write fills pre-allocated chunks and never alters store structure, so the consolidated index stays valid. Re-consolidating on each write is what made the earlier day-by-day path slow, and it bought nothing.",
]));
d.push(h2("6.3 Concurrency"));
d.push(p("Downloads run in a ThreadPoolExecutor sized at runtime.workers (6) for parallel-capable sources and 1 for queued ones. **Writes are serialized** by a process-level threading.Lock, because two dates within the same year land in the same chunk and concurrent writers would lose one of them."));
d.push(p("**The write lock is intra-process only.** It protects threads inside one tilegen run. It does not protect against two separate processes writing the same cube. That guarantee is provided one layer up, by the per-job flock in prod-run.sh (section 9) and by the stricter \"no tilegen alive at all\" guard in the rechunk wrapper (section 12). Any new scheduled entry point that writes cubes must take a lock, or it will silently drop days."));
d.push(h2("6.4 Schema evolution"));
d.push(p("Stores outlive the configuration that created them, so two reconciliation steps run before every batch:"));
d.push(...bullets([
  "`ensure_variables()` adds any variable present in the YAML but absent from the store, as empty (all-NaN) arrays on the existing grid. This is how ssrd_sum, sd_mean and rsn_mean were rolled out across 21 existing scenes without rebuilding a single cube.",
  "`ensure_covers(end_date)` extends the time axis forward when the requested end date is past the axis built at creation. This is what keeps an open-ended dataset advancing year over year without intervention.",
]));
d.push(p("Both are structural operations, so both run inside the write lock and before any region write."));

/* ---- 7 ---- */
d.push(h1("7. Idempotency and provisional absence"));
d.push(p("Everything above combines into one guarantee: **every run is idempotent and resumable.** A run computes its plan from the ledger, fetches only what is pending, and records what it achieved. Killing it at any moment costs only the granules in flight. This is why cron is a sufficient orchestrator: resumability is a property of the data model, not of the scheduler."));
d.push(h2("7.1 The plan"));
d.push(...code([
  'skip = set(ledger["written"])',
  "if not retry_missing:",
  '    skip |= set(ledger["missing"])',
  "pending = [d for d in dates if str(d) not in skip]",
]));
d.push(p("Dates are first clamped: the end is capped at today minus lag_days and at the dataset's configured end where it has one, and the start is floored at the dataset's start. A closed dataset like chirts (1983-2016) therefore cannot be asked for dates that do not exist, no matter what window the cron line passes."));
d.push(h2("7.2 The failure this prevents"));
d.push(p("When a provider does not return a date, the pipeline must decide whether the date is permanently absent or merely not published yet. Recording it in missing is permanent: the planner will skip it forever."));
d.push(p("Getting this wrong produces the worst failure mode this system has, because it is completely silent. A provider running two weeks late has its unpublished days written down as permanent holes. Tomorrow's run skips them. The job exits 0. The logs are clean. No alert fires. And the dataset has silently stopped advancing."));
d.push(p("**This is not hypothetical.** swvl1 sat frozen for two months while every scheduled run reported success. The bug was found by inspecting coverage, not by any alarm, which is precisely the point: nothing was in a position to notice."));
d.push(h2("7.3 The absence horizon"));
d.push(p("An absence is recorded as definitive only once the date is old enough that the provider really should have published it. The horizon resolves by precedence:"));
d.push(...code([
  "def _horizonte(self, variable):",
  "    # per-variable override > dataset value > lag_days + 30",
  "    for valor in (vcfg.missing_after_days, self.dcfg.missing_after_days):",
  "        if valor is not None:",
  "            return valor",
  "    return self.dcfg.lag_days + 30",
]));
d.push(table(
  ["Horizon", "Meaning", "Example"],
  [
    ["lag_days + 30", "Default. Thirty days of slack beyond the provider's own declared lag.", "era5: 6 + 30 = 36 days"],
    ["explicit value", "Provider-specific knowledge overrides the default.", "per-dataset or per-variable"],
    ["-1", "**Never definitive.** Nothing is ever written to missing; every gap is retried forever.", "cfs, whose provider moved its URL rather than retiring the product"],
  ],
  [1700, 4300, W - 6000]
));
d.push(gap());
d.push(p("Days inside the horizon stay pending and are re-requested next run. The pipeline logs the count explicitly, so the condition is visible rather than inferred:"));
d.push(...code([
  "snowc_mean: 2 dia(s) sin publicar todavia, se vuelven a pedir",
  "en la proxima corrida (horizonte 150 dias)",
]));
d.push(h2("7.4 Recovering from a misclassification"));
d.push(p("`--retry-missing` drops the missing set from the skip list, so every recorded absence is re-requested. Use it when a provider has caught up on a genuine backlog, or when a horizon was configured too aggressively and froze days that did eventually appear. It is safe to run at any time; the cost is one wasted request per date that really is absent."));

/* ---- 8 ---- */
d.push(h1("8. Orchestration"));
d.push(h2("8.1 Daily schedule"));
d.push(p("All times UTC, all data jobs invoked through prod-run.sh."));
d.push(table(
  ["Time", "Job", "Dataset", "Window", "Scenes"],
  [
    ["05:00", "cpc", "cpc", "45 d", "20 land"],
    ["06:00", "chirps", "chirps v2.0", "70 d", "20 land"],
    ["06:30", "era5-1", "era5", "16 d", "7"],
    ["07:15", "era5-2", "era5", "16 d", "7"],
    ["08:00", "era5-3", "era5", "16 d", "7"],
    ["09:30", "chirps-v3", "chirps-v3", "70 d", "20 land"],
    ["10:30", "era5-land", "era5-land", "120 d", "20 land"],
    ["12:00", "cobertura", "n/a", "n/a", "catalog regeneration"],
    ["14:00", "rechunk", "one per weekday", "n/a", "migration, temporary"],
    ["Sun 09:00", "era5-swvl1", "era5 via EDH", "90 d", "21"],
    ["@reboot", "ensure_backfills", "n/a", "n/a", "relaunch tmux sessions"],
  ],
  [1300, 1900, 2100, 1100, W - 6400]
));
d.push(gap());
d.push(h2("8.2 Window sizing"));
d.push(p("**Every job looks backwards over a window, never at yesterday.** Each cron line computes its own start date as $(date -d '-N days' +%F). The window is deliberately wider than the provider's declared lag (CHIRPS lags 45 days and is asked for 70; ERA5 lags 6 and is asked for 16) so that a day the provider publishes late is still inside the window when it appears."));
d.push(p("Re-asking for days already written is free: the ledger removes them from the plan before any network call. The window's only real cost is that it must be wider than the worst realistic publication delay, and era5-land is the illustration: its 120-day window is sized for old holes in the CDS archive (gaps back in August), not for a uniform lag."));
d.push(h2("8.3 Job splitting and locks"));
d.push(p("The job name passed to prod-run.sh names its lock. Two runs of the same job cannot overlap; different jobs run concurrently by design."));
d.push(p("**ERA5 is split into three jobs of seven scenes** with independent locks era5-1, era5-2 and era5-3. Each scene is a separate CDS queue request, so the job is latency-bound, not CPU-bound; four scenes already took about seven minutes. Under a single lock all 21 scenes would serialize behind one flock. Three lock names let the groups overlap, which is safe because different scenes write different stores and never contend."));
d.push(p("**Regression fixed 2026-08-19.** The cron lines previously listed only 4 of the 21 scenes. The other 17 were frozen (ERA5 had fallen behind to 2026-07-16, CHIRPS to 2026-06-05) and nothing reported it, because the four listed scenes were succeeding. When editing these lines, verify scene coverage against `tilegen scenes`, not against the fact that the job is green."));

/* ---- 9 ---- */
d.push(h1("9. Deployment and release"));
d.push(h2("9.1 Production is a separate checkout"));
d.push(p("Nothing scheduled runs from the development repository. Production is ~/tilegen-prod:"));
d.push(...code([
  "~/tilegen-prod/",
  "  repo/                        clean checkout, pinned to a tag (detached HEAD)",
  "  venv/                        own environment, installed editable over repo/",
  "  bin/prod-run.sh              the only entry point",
  "  bin/rechunk-run.sh           maintenance wrapper, different guards",
  "  bin/avisar-clickup.sh        failure notifier",
  "  log/cron.log                 all scheduled output",
  "  locks/                       one lock file per job",
  "  repo-requirements-lock.txt   exact versions in use",
]));
d.push(p("The reason is concrete: the cron previously ran from the development tree with an editable install, so what executed at 06:00 was whatever the working tree happened to contain, including half-finished edits from the night before. Because the production venv is likewise installed editable over the pinned checkout, a clean checkout at tag X determines exactly what runs, with no site-packages copy that can drift out of sync."));
d.push(h2("9.2 The three guards"));
d.push(p("prod-run.sh aborts before executing anything if:"));
d.push(...steps([
  "**The checkout has uncommitted changes.** Production does not run unpromoted code. Editing ~/tilegen-prod/repo by hand takes the cron down until it is clean. That is the intended signal, not a side effect.",
  "**HEAD is not exactly on a tag.** Every run is attributable to a named version, never to an arbitrary commit.",
  "**Another run of the same job is alive.** flock -n -E 99; the second invocation exits 99 having done nothing, and a slow run is never trampled by the next day's.",
]));
d.push(p("Exit codes are distinguished downstream: 0 is logged as ok, 99 raises a SALTEADO notification, anything else a FALLO."));
d.push(h2("9.3 Promotion and rollback"));
d.push(...code([
  "# 1. in the development repo: commit, tag, push",
  "cd ~/tile_generation_module",
  'git tag -a v0.4.3 -m "..."',
  "git push origin main && git push origin v0.4.3",
  "",
  "# 2. in production: fetch and stand on the tag",
  "cd ~/tilegen-prod/repo",
  "git fetch --tags origin",
  "git checkout v0.4.3",
  "",
  "# 3. only if pyproject.toml dependencies changed",
  "~/tilegen-prod/venv/bin/pip install -e . && \\",
  "  ~/tilegen-prod/venv/bin/pip freeze --exclude-editable > ../repo-requirements-lock.txt",
  "",
  "# 4. verify before handing it back to cron ('smoke' touches no real lock)",
  "~/tilegen-prod/bin/prod-run.sh smoke datasets",
  "~/tilegen-prod/bin/prod-run.sh smoke plan -d chirps -x peru -s 2026-06-10 -e 2026-06-12",
]));
d.push(p("Rollback is git checkout of the previous tag and nothing else. Manual runs must use the same job name the cron uses for that dataset, so the manual run serializes against the scheduled one through the same lock."));
d.push(h2("9.4 Dependency pinning"));
d.push(p("**Resolver trap.** Before v0.2.0, pyproject.toml declared no lower bounds and a clean pip install resolved s3fs 0.4.2, a 2020 pre-async release. An unbounded boto3 pulled a botocore that conflicts with what every modern s3fs needs through aiobotocore, and pip silently walked backwards until something fit. Fixed in v0.2.0 with floors (s3fs>=2024.6.0, fsspec>=2024.6.0, boto3>=1.34, botocore>=1.34). The production venv stays pinned to the validated set recorded in repo-requirements-lock.txt."));

/* ---- 10 ---- */
d.push(h1("10. Observability"));
d.push(h2("10.1 Logs"));
d.push(p("All scheduled output appends to ~/tilegen-prod/log/cron.log. Each run brackets itself with a start line carrying the job name, the running tag and the full argument list, and closes with a machine-readable summary:"));
d.push(...code([
  "2026-09-23T10:30:01Z [era5-land] start  tilegen v0.4.3  ::  run -d era5-land ...",
  "{",
  '  "dataset": "era5-land",',
  '  "scene": "patagonia",',
  '  "store": "s3://suyana-tiles/era5-land/daily-stats/patagonia.zarr",',
  '  "variables": ["snowc_mean"],',
  '  "start": "2026-05-26", "end": "2026-09-17",',
  '  "stats": { "written": 0, "missing_at_source": 0, "errors": 0 },',
  '  "errors": [],',
  '  "duration_s": 1.0,',
  '  "finished": "2026-09-23T10:30:24+00:00"',
  "}",
  "2026-09-23T10:30:24Z [era5-land] ok",
]));
d.push(p("Grepping the job name in brackets gives that job's complete history. Note that written: 0 is the normal steady state once a dataset is current. It means nothing new was published, not that nothing happened."));
d.push(h2("10.2 Alerting"));
d.push(p("On a non-zero exit, prod-run.sh calls avisar-clickup.sh, which posts to the Devops channel in ClickUp with the job name, what happened, the UTC timestamp and where to look. States are FALLO, CORTADO, SALTEADO and FALLAS."));
d.push(p("The notifier is deliberately failure-tolerant: missing token, unreachable API or timeout all exit 0 with a single log line. A broken alert must never take down the job it was reporting on. The token is read from ~/.config/tilegen/clickup.token (mode 600) and never appears in the script, so rotation means overwriting one file."));
d.push(p("**Known gap, still open.** This covers only jobs that ran and failed. If the server is down or cron never fires, nothing runs (including the notifier) and the resulting silence is indistinguishable from a healthy day. Closing it requires a dead-man's switch outside this machine: each job pings an external watchdog on success, and the watchdog alerts when an expected ping fails to arrive. Not yet implemented."));
d.push(h2("10.3 Coverage catalog"));
d.push(p("At 12:00 UTC, cobertura-run.sh regenerates four artifacts from the ledgers and uploads them to the bucket root:"));
d.push(table(
  ["Artifact", "Content"],
  [
    ["cobertura.html", "Scene x variable coverage matrix"],
    ["CATALOG.md", "Human-readable catalog: datasets, ranges, variables, S3 paths"],
    ["catalog.csv", "One row per (dataset, variable, scene) with first and last date, day count, gaps, status"],
    ["catalog.yaml", "The same, structured for programmatic consumption"],
  ],
  [2100, W - 2100]
));
d.push(gap());
d.push(p("It is strictly read-only over the data: it lists ledgers, downloads nothing and touches no cube, so it cannot contend with the data jobs or with rechunk. If one generator fails, that file is not uploaded: the last good snapshot stays published rather than being replaced by a partial one. It runs from the development checkout because the generators are scripts under docs/ rather than tilegen subcommands, which is why it leaves docs/ modified in that working tree every day. That is expected; commit it when you want to freeze a snapshot."));
d.push(h3("Coverage as of 23 September 2026"));
d.push(spec([
  ["Datasets", "8"],
  ["Catalog rows", "354 (dataset x variable x scene)"],
  ["Combinations", "462 (142 complete, 212 partial, 108 empty)"],
  ["Day-values written", "5,464,387"],
]));
d.push(gap());
d.push(p("Partial with zero gaps is the normal state for a closed historical dataset or one that covers fewer than all 21 scenes; it is not a defect. The column that indicates a real problem is gaps."));
d.push(h2("10.4 Verification commands"));
d.push(...bullets([
  "`tilegen verify` cross-checks ledger claims against what is actually readable in the stores.",
  "`tilegen catalog` prints the catalog for a dataset or all of them.",
  "`tilegen chunks --stale` lists cubes whose chunk geometry differs from the current config.",
]));

/* ---- 11 ---- */
d.push(h1("11. Backfills"));
d.push(p("Backfills run the same code as the daily jobs over a historical window. They are long (days to weeks), so they run in tmux sessions rather than under cron, one session per backfill, each appending to its own log under .work/."));
d.push(p("ensure_backfills.sh reconciles the desired set of sessions with what is running. It is idempotent: if a session exists it is left alone; if not it is started. Because the backfills themselves are resumable, relaunching a finished one simply exits quickly. It is wired to @reboot with a 30-second delay and is equally safe to run by hand."));
d.push(p("**Why this exists.** A backfill process died and nobody noticed for a full day. It accumulated neither history nor new days in that time. Anything long-running that matters belongs in this file; a session that is not listed has nothing watching it."));
d.push(h3("Scheduling backfills against each other"));
d.push(p("The binding constraint is usually a shared provider, not this machine. The comments in ensure_backfills.sh record which backfills compete:"));
d.push(...bullets([
  "chirts and chirps-v3 both pull from data.chc.ucsb.edu, so running them together splits one server's bandwidth rather than adding throughput.",
  "ssrd (EDH), cpc (NOAA PSL) and chirts (UCSB) hit three different providers and genuinely run in parallel.",
  "fg10 goes through CDS and consumes almost no bandwidth: it waits in a queue. Its three groups run in series, taking the era5-1, era5-2 and era5-3 locks one at a time, so the daily ERA5 job is never blocked on more than one group.",
  "cfsr, the last outstanding historical gap, completed 23 September 2026 at 03:09 UTC: 32 years, 23,314 day-values, 0 errors, 0 gaps. It was CPU-bound, not bandwidth-bound.",
]));
d.push(p("A finished backfill should be commented out of ensure_backfills.sh with the completion date and the reason, rather than deleted, because the file doubles as the record of what was run and when."));

/* ---- 12 ---- */
d.push(h1("12. Maintenance: rechunk"));
d.push(p("rechunk rewrites existing cubes to the current chunk geometry (3.2). It is temporary work: when `tilegen chunks --stale` reports nothing, the cron lines and this section can be removed."));
d.push(p("It runs one dataset per weekday at 14:00 UTC (Mon era5, Tue chirps, Thu chirts, Fri chirps-v3, Sat cfs) and deliberately does not go through prod-run.sh, because that wrapper's lock serializes by job name and lets different jobs run concurrently. Concurrency is exactly the hazard here: if the daily update writes a cube that is being rewritten, days are lost."));
d.push(h3("Inverted guards"));
d.push(...steps([
  "**No tilegen process alive at all**, whatever the job (pgrep against the venv binary).",
  "**Outside 05-13h UTC**, the window the data jobs occupy.",
  "**A 13-hour ceiling** via timeout, because guard 1 only checks at start and an ERA5 run can last hours. Starting at 14:00, it is finished by 03:00.",
]));
d.push(p("Interrupting it costs nothing: it proceeds cube by cube and skips those already correct, so the next run continues where it stopped. With `--keep-old` the original is left as {scene}.zarr.old. **Those must be pruned by hand** once the rewrite is trusted, or the bucket carries two copies of everything."));

/* ---- 13 ---- */
d.push(h1("13. CLI reference"));
d.push(table(
  ["Command", "Purpose", "Notable options"],
  [
    ["datasets", "List configured datasets and their variables", "none"],
    ["scenes", "List the 21 scenes with their bounding boxes", "none"],
    ["plan", "Show what a run would fetch and write. Network-free against the ledger.", "-d -v -x -s -e --bbox --format"],
    ["run", "Execute the pipeline", "--overwrite --retry-missing --local-only --keep-local --workers --source"],
    ["verify", "Check stores against their ledgers", "-d -v -x --format"],
    ["catalog", "Print the coverage catalog", "-d --format"],
    ["chunks", "Audit chunk geometry across cubes", "--stale --local-only"],
    ["rechunk", "Rewrite cubes to the configured geometry", "--dry-run --keep-old --resume --no-verify -y"],
  ],
  [1400, 4200, W - 5600]
));
d.push(gap());
d.push(h3("Common flags"));
d.push(spec([
  ["-d, --dataset", "Dataset name, as listed by tilegen datasets"],
  ["-v, --variable", "Repeatable; defaults to every variable in the dataset"],
  ["-x, --scene", "Repeatable; scene name. The cron lines enumerate scenes explicitly"],
  ["-s, -e", "Start and end date, clamped to the dataset's range and lag"],
  ["--local-only", "Operate on the workdir instead of S3; use this when testing"],
  ["--overwrite", "Ignore the ledger entirely and rewrite the window"],
  ["--retry-missing", "Re-request dates recorded as absent at source (7.4)"],
], 2400));
d.push(gap());
d.push(p("**Testing without touching production data.** Develop in ~/tile_generation_module and run with `--local-only`, or against a test scene. Use plan liberally: it makes no network calls and shows exactly what a run would do."));

/* ---- 14 ---- */
d.push(h1("14. Configuration reference"));
d.push(h2("14.1 Global: config/config.yaml"));
d.push(spec([
  ["s3.bucket", "suyana-tiles, region us-east-1, empty prefix"],
  ["output.format", "zarr (timeseries cubes) or cog (per-date map mosaics)"],
  ["zarr.time_chunk", "365"],
  ["zarr.spatial_chunk", "32 px, a cap not a fixed size"],
  ["grid.tile_size_deg", "10, a fixed MERIT/GHSL-style grid for the COG path; tiles named by lower-left corner (s20w080)"],
  ["runtime.workdir", "~/tile_generation_module/.work"],
  ["runtime.workers", "6 parallel granule downloads (HTTP sources only)"],
  ["runtime.keep_local", "false"],
]));
d.push(gap());
d.push(h2("14.2 Per dataset: config/datasets/*.yaml"));
d.push(table(
  ["Key", "Meaning"],
  [
    ["name, version", "Together form the S3 path prefix"],
    ["source", "Adapter key, resolved through sources/__init__.py"],
    ["start, end", "Dataset date range. An absent end means open-ended"],
    ["lag_days", "The provider's own publication delay; caps the requestable end date"],
    ["missing_after_days", "Absence horizon override. -1 means never definitive (7.3)"],
    ["nodata", "Provider sentinel value, converted to NaN"],
    ["variables", "Map of variable name to per-variable config"],
    ["cds_dataset, cds_product_type, cds_fetch_area", "CDS request parameters. cds_product_type lives in YAML rather than code because ERA5-Land does not publish that field"],
    ["edh_store, edh_hourly_store", "EarthDataHub Zarr endpoints, daily and hourly"],
  ],
  [3100, W - 3100]
));
d.push(gap());
d.push(h3("Per-variable keys"));
d.push(spec([
  ["units", "Provider units, stored as an attribute; never converted"],
  ["cds_variable", "CDS name. **Absent means the variable exists only on EDH**"],
  ["daily_statistic", "daily_mean, daily_maximum, daily_minimum or daily_sum"],
  ["edh_variable", "Name in the daily EDH store"],
  ["edh_hourly_variable", "Name in the hourly EDH store, when the daily store lacks the statistic"],
  ["edh_hourly_statistic", "max, min or sum: the local aggregation to apply"],
  ["missing_after_days", "Per-variable absence horizon; wins over the dataset value"],
], 2800));
d.push(gap());
d.push(h2("14.3 Scenes: config/scenes.yaml"));
d.push(...code([
  "scenes:",
  "  mexico_norte:",
  "    num: 1",
  "    type: pais",
  "    bbox: [-122, 20, -96, 35]     # W, S, E, N: whole degrees",
]));
d.push(p("Scene bounding boxes are effectively frozen. Changing one changes the grid of every cube for that scene, which means a full rewrite of that scene's history across every dataset. Treat an edit here as a migration, not a configuration change."));

/* ---- 15 ---- */
d.push(h1("15. Failure modes and runbook"));
d.push(table(
  ["Symptom", "Likely cause", "Action"],
  [
    ["FALLO in the Devops channel", "The job ran and exited non-zero", "Grep the job name in ~/tilegen-prod/log/cron.log; the summary JSON lists up to 20 errors verbatim"],
    ["SALTEADO", "The previous run of that job is still alive", "Normal once; if it repeats, the job no longer fits its slot, so narrow the window or split the scenes"],
    ["Guard abort: uncommitted changes", "Someone edited ~/tilegen-prod/repo directly", "Restore the checkout to its tag. Develop in ~/tile_generation_module"],
    ["Guard abort: HEAD not on a tag", "A promotion was left half-done", "git -C ~/tilegen-prod/repo checkout the intended tag"],
    ["Silence. No alerts, no log lines", "Server down, cron not firing, or the crontab was replaced", "Check the last timestamp in cron.log. **Nothing detects this automatically yet** (10.2)"],
    ["A dataset stops advancing while every run reports ok", "Absences frozen by too tight a horizon, or the cron line lists a subset of scenes", "Compare catalog.csv against expectation; re-run with --retry-missing; verify the scene list"],
    ["Source returns 404 for every date", "The provider moved or retired the product", "Confirm before concluding retirement: NCEI moved the CFS product rather than withdrawing it (fix in commit e2fdd92)"],
    ["Point reads unexpectedly slow", "Cube still on the 128 px chunk geometry", "tilegen chunks --stale, then let the rechunk schedule reach it"],
    ["Bucket size roughly doubled", "{scene}.zarr.old copies left by --keep-old", "Prune them once the rewrite is verified (section 12)"],
    ["Fresh install resolves ancient s3fs", "Dependency floors missing or bypassed", "Install from repo-requirements-lock.txt (9.4)"],
  ],
  [2400, 2900, W - 5300]
));
d.push(gap());
d.push(p("**General principle.** A green job proves that the scenes and variables it was asked for succeeded. It proves nothing about the ones it was never asked for. Both historical incidents in this system (the four-scene cron lines and the frozen swvl1) were invisible to the job's own exit code and visible in the coverage catalog. Check coverage, not just status."));

/* ---- 16 ---- */
d.push(h1("16. Appendix: dataset register"));
d.push(p("State as published on 23 September 2026. Resolution is in degrees. Ranges are the dates actually present in the cubes, not the configured range."));
d.push(table(
  ["Dataset", "Source", "Res.", "Range with data", "Lag", "Variables", "Scenes"],
  [
    ["cfs (operational-analysis)", "cfs_ncei", "0.204", "2011-04-01 to 2026-03-31", "30 d", "tmp2m, u10m, v10m", "1"],
    ["cfsr (reanalysis)", "cfsr_gdex", "0.312", "1979-01-01 to 2010-12-31", "n/a", "u10m, v10m", "1"],
    ["chirps (v2.0)", "http_geotiff", "0.05", "1981-01-01 to 2026-08-09", "45 d", "precip", "20"],
    ["chirps-v3 (v3.0)", "http_geotiff", "0.05", "1981-01-01 to 2026-08-09", "45 d", "precip_rnl, precip_sat", "20"],
    ["chirts (v1.0)", "http_geotiff", "0.05", "1983-01-01 to 2016-12-31", "n/a", "tmax, tmin", "20"],
    ["cpc (v1.0)", "cpc_psl", "0.5", "1979-01-01 to 2026-09-20", "3 d", "tmax, tmin", "20"],
    ["era5 (daily-stats)", "era5_cds or era5_edh", "0.25", "1981-01-01 to 2026-09-01", "6 d", "t2m_mean, t2m_max, t2m_min, precip_sum, ssrd_sum, fg10_max, swvl1, sd_mean, rsn_mean", "21"],
    ["era5-land (daily-stats)", "era5_cds", "0.1", "1981-01-01 to 2026-09-15", "6 d", "snowc_mean", "20"],
  ],
  [1750, 1500, 700, 1700, 620, 2390, 700]
));
d.push(gap());
d.push(h3("Notes on individual datasets"));
d.push(...bullets([
  "**cfs and cfsr are one continuous wind history on two grids.** CFSR reanalysis covers 1979-2010 at about 0.31 degrees; the CFS operational analysis covers 2011 onward at about 0.20. The grids differ, so they are separate datasets rather than one cube, and a consumer joining them must handle the resolution change explicitly.",
  "**cfs carries missing_after_days: -1**, so no absence is ever recorded as definitive, because NCEI relocated the product once already.",
  "**chirps and chirps-v3 coexist deliberately.** v2.0 is retained so historical calculations keep resolving to the data they were computed from.",
  "**cpc has no ocean coverage by design.** Its absence from nino34 is expected, not a gap.",
  "**era5 configures start: 1940-01-01 but holds data from 1981.** The configured start is the provider's range; 1981 is the deliberate archive floor.",
  "**era5-land exists solely for snow cover** at 0.1 degrees, land only. Its 120-day window compensates for old holes in the CDS archive, not a uniform lag.",
]));
d.push(gap(300));
d.push(p("Reflects tilegen v0.4.3 as deployed at ~/tilegen-prod on 23 September 2026. The source of truth is the repository; where this document and the code disagree, the code is correct.", { size: 19 }));

/* ---------- assemble ---------- */
const doc = new Document({
  creator: "Daniela Risaro",
  title: "Tilegen: climate data ETL to per-scene Zarr cubes",
  description: "Technical reference for tilegen v0.4.3",
  numbering: {
    config: [
      {
        reference: "dot-list",
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 420, hanging: 220 } } },
        }],
      },
      {
        reference: "num-list",
        levels: [{
          level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 420, hanging: 220 } } },
        }],
      },
    ],
  },
  styles: { default: { document: { run: { font: SANS, size: BODY, color: "000000" } } } },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    children: d,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  const out = process.argv[2] || "docs/tilegen-etl-reference.docx";
  fs.writeFileSync(out, buf);
  console.log("escrito:", out, buf.length, "bytes");
});
