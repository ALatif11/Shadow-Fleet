"""Danish Maritime Authority AIS: index, download, frozen per-day ingest (ADR-14), bulk run, probe.

Per source file:
  stage 1  stream the zipped CSV through pyarrow into a temporary all-vessel Parquet (strings only,
           canonical column names, malformed rows counted and skipped); never unzip to disk.
  stage 2  for each day in the file, DuckDB writes the ADR-14 tables; then temp and zip are deleted.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import shutil
import time
import zipfile
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from shadowfleet import config
from shadowfleet.util import disk, net, probes

log = logging.getLogger(__name__)

FULLRES_TABLE = "ais_fullres"
CANONICAL = list(config.DMA_COLUMN_ALIASES)


# ============================================================================ index
@dataclass(frozen=True)
class DmaFile:
    name: str
    url: str
    kind: str  # "daily" | "monthly"
    period: str  # YYYY-MM-DD or YYYY-MM
    size: int | None = None  # bytes, when the listing says

    def dates(self) -> list[date]:
        if self.kind == "daily":
            return [date.fromisoformat(self.period)]
        y, m = map(int, self.period.split("-"))
        d = date(y, m, 1)
        out = []
        while d.month == m:
            out.append(d)
            d += timedelta(days=1)
        return out


def parse_index(html: str, base_url: str) -> tuple[list[DmaFile], list[str]]:
    """Parse an HTML directory listing. Returns (recognised files, unrecognised archive names)."""
    hrefs = re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE)
    return _classify(((h, None) for h in hrefs), base_url)


def _classify(names_sizes: Iterable[tuple[str, int | None]], base_url: str) -> tuple[list[DmaFile], list[str]]:
    files: dict[str, DmaFile] = {}
    unknown: set[str] = set()
    for key, size in names_sizes:
        name = key.rstrip("/").split("/")[-1]
        if not re.search(r"\.(zip|rar|7z|gz)$", name, re.IGNORECASE):
            continue
        url = urljoin(base_url, key)
        if m := re.fullmatch(config.DMA_DAILY_RE, name):
            files[name] = DmaFile(name, url, "daily", m.group(1), size)
        elif m := re.fullmatch(config.DMA_MONTHLY_RE, name):
            files[name] = DmaFile(name, url, "monthly", m.group(1), size)
        else:
            unknown.add(name)
    return sorted(files.values(), key=lambda f: (f.period, f.kind)), sorted(unknown)


_S3_NS = re.compile(r"\{.*?\}")


def parse_s3_listing(xml_text: str) -> tuple[list[tuple[str, int | None]], bool, str | None]:
    """One page of an S3 ListObjectsV2 response: ([(key, size)], is_truncated, next_token)."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    root = ET.fromstring(xml_text)

    def tag(el):
        return _S3_NS.sub("", el.tag)

    items, truncated, token = [], False, None
    for el in root:
        t = tag(el)
        if t == "Contents":
            fields = {tag(x): x.text for x in el}
            size = fields.get("Size")
            items.append((fields.get("Key") or "", int(size) if size and size.isdigit() else None))
        elif t == "IsTruncated":
            truncated = (el.text or "").strip().lower() == "true"
        elif t == "NextContinuationToken":
            token = el.text
    return items, truncated, token


def list_s3(c, base: str, max_pages: int = 50) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = []
    token = None
    for _ in range(max_pages):
        params = {"list-type": "2"}
        if token:
            params["continuation-token"] = token
        r = net.get(c, base, params=params)
        r.raise_for_status()
        items, truncated, token = parse_s3_listing(r.text)
        out.extend(items)
        if not truncated or not token:
            break
    return out


def list_available(c) -> tuple[str, list[DmaFile], list[str]]:
    errors = []
    for base in config.DMA_INDEX_URLS:
        try:
            r = net.get(c, base, retries=1)
        except Exception as e:  # noqa: BLE001 - record and try the next mirror
            errors.append(f"{base}: {e!r}")
            continue
        if r.status_code != 200:
            errors.append(f"{base}: HTTP {r.status_code}")
            continue
        if "<ListBucketResult" in r.text[:2000]:
            try:
                files, unknown = _classify(list_s3(c, base), base)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{base}: S3 listing failed {e!r}")
                continue
        else:
            files, unknown = parse_index(r.text, base)
        if files:
            return base, files, unknown
        errors.append(f"{base}: no aisdk files recognised ({len(unknown)} other archives)")
    raise RuntimeError("DMA index unavailable: " + "; ".join(errors))


def files_for_dates(files: Iterable[DmaFile], start: date, end: date) -> dict[date, DmaFile]:
    """Map each date in [start, end] to its source file; a daily file wins over a monthly one."""
    out: dict[date, DmaFile] = {}
    for f in sorted(files, key=lambda f: f.kind != "daily"):
        for d in f.dates():
            if start <= d <= end and d not in out:
                out[d] = f
    return dict(sorted(out.items()))


# ============================================================================ stage 1: CSV -> temp parquet
def normalize_header(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("﻿", "").strip().lstrip("#").strip().lower())


_ALIAS_LOOKUP = {normalize_header(a): canon for canon, al in config.DMA_COLUMN_ALIASES.items() for a in al}


def map_header(columns: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (names for each position, unknown headers, missing canonical columns)."""
    names, unknown, seen = [], [], set()
    for i, col in enumerate(columns):
        canon = _ALIAS_LOOKUP.get(normalize_header(col))
        if canon is None or canon in seen:
            names.append(f"_extra_{i}")
            unknown.append(col)
        else:
            names.append(canon)
            seen.add(canon)
    missing = [c for c in CANONICAL if c not in seen]
    return names, unknown, missing


@dataclass
class StageStats:
    members: list[str] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    encoding: str = "utf8"
    rows_in: int = 0
    malformed_rows: int = 0
    seconds: float = 0.0
    temp_bytes: int = 0


def _sniff(raw: zipfile.ZipExtFile) -> tuple[str, str]:
    head = raw.read(1 << 20)
    try:
        head.decode("utf-8")
        enc = "utf8"
    except UnicodeDecodeError as e:
        # a multi-byte character cut by the 1 MB read is not evidence of latin-1
        enc = "utf8" if e.start >= len(head) - 3 else "latin1"
    first_line = head.split(b"\n", 1)[0].decode(enc, errors="replace").rstrip("\r")
    return enc, first_line


def _delimiter(line: str) -> str:
    return max([",", ";", "\t"], key=line.count)


def csv_zip_to_parquet(zip_path: Path, out_path: Path) -> StageStats:
    """Stream every CSV member of `zip_path` into one Parquet file of canonical string columns."""
    t0 = time.monotonic()
    st = StageStats()
    schema = pa.schema([(c, pa.string()) for c in CANONICAL])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".writing")
    bad = [0]

    def on_invalid(_row) -> str:
        bad[0] += 1
        return "skip"

    with zipfile.ZipFile(zip_path) as zf, pq.ParquetWriter(tmp, schema, compression="zstd") as w:
        members = [m for m in zf.infolist() if m.filename.lower().endswith(".csv")]
        if not members:
            raise ValueError(f"{zip_path.name}: no CSV member")
        for m in members:
            st.members.append(m.filename)
            with zf.open(m) as raw:
                enc, first = _sniff(raw)
            delim = _delimiter(first)
            header = next(csv.reader([first], delimiter=delim))
            names, unknown, missing = map_header(header)
            if not st.header:
                st.header, st.encoding = header, enc
            st.unknown_columns = sorted(set(st.unknown_columns) | set(unknown))
            st.missing_columns = sorted(set(st.missing_columns) | set(missing))
            req_missing = [c for c in config.DMA_REQUIRED_COLUMNS if c in missing]
            if req_missing:
                raise ValueError(f"{zip_path.name}/{m.filename}: required columns missing {req_missing}")
            with zf.open(m) as raw:
                reader = pacsv.open_csv(
                    raw,
                    read_options=pacsv.ReadOptions(
                        column_names=names, skip_rows=1, block_size=config.DMA_CSV_BLOCK_BYTES, encoding=enc
                    ),
                    parse_options=pacsv.ParseOptions(delimiter=delim, invalid_row_handler=on_invalid),
                    convert_options=pacsv.ConvertOptions(
                        column_types={n: pa.string() for n in names}, strings_can_be_null=True,
                        null_values=[""], quoted_strings_can_be_null=True,
                    ),
                )
                for batch in reader:
                    cols = {n: batch.column(i) for i, n in enumerate(batch.schema.names)}
                    arrays = [cols.get(c, pa.nulls(batch.num_rows, pa.string())) for c in CANONICAL]
                    w.write_batch(pa.RecordBatch.from_arrays(arrays, schema=schema))
                    st.rows_in += batch.num_rows
    tmp.replace(out_path)
    st.malformed_rows = bad[0]
    st.seconds = round(time.monotonic() - t0, 2)
    st.temp_bytes = out_path.stat().st_size
    return st


# ============================================================================ stage 2: per-day tables
def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _sql_list(values: Iterable[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _hazard_expr(col: str) -> str:
    parts = [f"lower({col}) LIKE '%{s.replace(chr(39), chr(39) * 2)}%'" for s in config.HAZARDOUS_CARGO_SUBSTRINGS]
    return "(" + " OR ".join(parts) + ")"


def _haversine_km(lat1: str, lon1: str, lat2: str, lon2: str) -> str:
    return (
        f"2 * 6371.0088 * asin(sqrt(pow(sin(radians({lat2} - {lat1}) / 2), 2) + "
        f"cos(radians({lat1})) * cos(radians({lat2})) * pow(sin(radians({lon2} - {lon1}) / 2), 2)))"
    )


def connect() -> duckdb.DuckDBPyConnection:
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{config.DUCKDB_MEMORY_LIMIT}'")
    con.execute(f"SET threads={config.DUCKDB_THREADS}")
    con.execute(f"SET temp_directory='{_sql_path(config.TMP_DIR / 'duckdb')}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def _write(con, sql: str, table: str, day: date, root: Path) -> tuple[int, int]:
    """COPY a query into <root>/<table>/dt=<day>/part-0.parquet atomically. Returns (rows, bytes)."""
    part_dir = root / table / f"dt={day.isoformat()}"
    staging = root / table / f".dt={day.isoformat()}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    target = staging / "part-0.parquet"
    con.execute(f"COPY ({sql}) TO '{_sql_path(target)}' (FORMAT parquet, COMPRESSION zstd)")
    rows = pq.ParquetFile(target).metadata.num_rows
    size = target.stat().st_size
    if part_dir.exists():
        shutil.rmtree(part_dir)
    staging.rename(part_dir)
    return rows, size


@dataclass
class DayStats:
    day: str
    rows_day: int = 0
    rows_bad_timestamp: int = 0
    rows_dedup: int = 0
    mmsi_all: int = 0
    mmsi_tanker_today: int = 0
    mmsi_kept: int = 0
    mmsi_kept_from_registry: int = 0
    mmsi_unknown_type_class_a_100m: int = 0
    rows_out: dict[str, int] = field(default_factory=dict)
    bytes_out: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    diagnostics: dict = field(default_factory=dict)


def process_day(
    con: duckdb.DuckDBPyConnection,
    temp_parquet: Path,
    day: date,
    registry: set[int],
    out_root: Path | None = None,
    keep_fullres: bool = False,
    diagnostics: bool = False,
) -> tuple[DayStats, set[int]]:
    """Write the ADR-14 tables for one day. Returns stats and the MMSIs that were tanker-class today."""
    t0 = time.monotonic()
    root = out_root or config.PARQUET_DIR
    ds = DayStats(day=day.isoformat())
    fmts = "[" + _sql_list(config.DMA_TIMESTAMP_FORMATS) + "]"
    lat0, lat1 = config.BBOX_LAT
    lon0, lon1 = config.BBOX_LON
    eod = f"TIMESTAMP '{day.isoformat()} 23:59:59'"

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE r AS
        SELECT
          try_strptime(ts_raw, {fmts}) AS observed_at,
          TRY_CAST(mmsi AS BIGINT) AS mmsi,
          TRY_CAST(lat AS DOUBLE) AS lat, TRY_CAST(lon AS DOUBLE) AS lon,
          TRY_CAST(sog AS DOUBLE) AS sog, TRY_CAST(cog AS DOUBLE) AS cog,
          TRY_CAST(heading AS DOUBLE) AS heading, TRY_CAST(rot AS DOUBLE) AS rot,
          nullif(trim(nav_status), '') AS nav_status, nullif(trim(mobile_type), '') AS mobile_type,
          nullif(trim(pos_fix_type), '') AS pos_fix_type, nullif(trim(data_source), '') AS data_source,
          TRY_CAST(imo AS BIGINT) AS imo, nullif(trim(callsign), '') AS callsign,
          nullif(trim(name), '') AS name, nullif(trim(ship_type), '') AS ship_type,
          nullif(trim(cargo_type), '') AS cargo_type,
          TRY_CAST(length AS DOUBLE) AS length, TRY_CAST(width AS DOUBLE) AS width,
          TRY_CAST(draught AS DOUBLE) AS draught, nullif(trim(destination), '') AS destination,
          nullif(trim(eta), '') AS eta,
          TRY_CAST(dim_a AS DOUBLE) AS dim_a, TRY_CAST(dim_b AS DOUBLE) AS dim_b,
          TRY_CAST(dim_c AS DOUBLE) AS dim_c, TRY_CAST(dim_d AS DOUBLE) AS dim_d
        FROM read_parquet('{_sql_path(temp_parquet)}')
    """)
    ds.rows_bad_timestamp = con.execute(
        "SELECT count(*) FROM r WHERE observed_at IS NULL OR mmsi IS NULL"
    ).fetchone()[0]
    ds.rows_day = con.execute(
        f"SELECT count(*) FROM r WHERE CAST(observed_at AS DATE) = DATE '{day}' AND mmsi IS NOT NULL"
    ).fetchone()[0]

    non_vessel = _sql_list(config.NON_VESSEL_MOBILE_TYPES)
    # Exact duplicates apart from the receiving source are dropped (same message heard by several stations).
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE d AS
        SELECT *,
          (lat IS NOT NULL AND lon IS NOT NULL AND NOT (lat = 0 AND lon = 0)
             AND lat BETWEEN {lat0} AND {lat1} AND lon BETWEEN {lon0} AND {lon1}) AS pos_ok,
          (lat IS NOT NULL OR lon IS NOT NULL) AS has_pos,
          lower(coalesce(mobile_type, '')) NOT IN ({non_vessel}) AS is_vessel
        FROM r
        WHERE CAST(observed_at AS DATE) = DATE '{day}' AND mmsi IS NOT NULL
        QUALIFY row_number() OVER (
          PARTITION BY mmsi, observed_at, lat, lon, sog, cog, heading, rot, nav_status, mobile_type,
                       pos_fix_type, imo, callsign, name, ship_type, cargo_type, length, width, draught,
                       destination, eta, dim_a, dim_b, dim_c, dim_d
          ORDER BY data_source NULLS LAST) = 1
    """)
    con.execute("DROP TABLE r")
    ds.rows_dedup = con.execute("SELECT count(*) FROM d").fetchone()[0]

    tanker_types = _sql_list(config.TANKER_SHIP_TYPES)
    cargo_types = _sql_list(config.CARGO_SHIP_TYPES)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE mm AS
        SELECT mmsi,
          coalesce(bool_or(coalesce(
              lower(ship_type) IN ({tanker_types})
              OR (lower(ship_type) IN ({cargo_types}) AND {_hazard_expr('cargo_type')}), false)), false)
            AS tanker_row_any,
          mode(length) FILTER (WHERE length > 0) AS modal_length,
          mode(width) FILTER (WHERE width > 0) AS modal_width,
          mode(ship_type) AS modal_ship_type,
          mode(cargo_type) AS modal_cargo_type,
          mode(mobile_type) AS modal_mobile_type,
          mode(imo) FILTER (WHERE imo > 0) AS modal_imo,
          mode(name) AS modal_name,
          bool_or(is_vessel) AS is_vessel,
          count(*) AS n_rows,
          count(*) FILTER (WHERE pos_ok) AS n_pos_ok
        FROM d GROUP BY mmsi
    """)
    reg = sorted(registry | set(config.EXTRA_MMSI_ALLOWLIST))
    con.register("registry_df", pa.table({"mmsi": pa.array(reg, pa.int64())}))
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE kept AS
        SELECT mmsi,
          (tanker_row_any AND (modal_length IS NULL OR modal_length >= {config.MIN_TANKER_LENGTH_M}))
            AS tanker_today,
          mmsi IN (SELECT mmsi FROM registry_df) AS in_registry
        FROM mm
        WHERE (tanker_row_any AND (modal_length IS NULL OR modal_length >= {config.MIN_TANKER_LENGTH_M}))
           OR mmsi IN (SELECT mmsi FROM registry_df)
    """)
    ds.mmsi_all = con.execute("SELECT count(*) FROM mm WHERE is_vessel").fetchone()[0]
    ds.mmsi_tanker_today, ds.mmsi_kept, ds.mmsi_kept_from_registry = con.execute(
        "SELECT count(*) FILTER (WHERE tanker_today), count(*), "
        "count(*) FILTER (WHERE in_registry AND NOT tanker_today) FROM kept"
    ).fetchone()
    ds.mmsi_unknown_type_class_a_100m = con.execute(f"""
        SELECT count(*) FROM mm
        WHERE lower(coalesce(modal_mobile_type, '')) = 'class a' AND modal_length >= {config.MIN_TANKER_LENGTH_M}
          AND (modal_ship_type IS NULL OR lower(modal_ship_type) IN ('undefined', 'unknown', 'other'))
    """).fetchone()[0]

    # ---- jumps over all vessels at full resolution (needed for jump_baseline; ADR-14)
    dist = _haversine_km("plat", "plon", "lat", "lon")
    step = config.JUMP_CELL_DEG
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE j AS
        WITH p AS (
          SELECT mmsi, observed_at, lat, lon,
            lag(lat) OVER w AS plat, lag(lon) OVER w AS plon, lag(observed_at) OVER w AS pts
          FROM d WHERE pos_ok AND is_vessel
          WINDOW w AS (PARTITION BY mmsi ORDER BY observed_at, lat, lon)
        ), q AS (
          SELECT *, CASE WHEN plat IS NULL THEN NULL ELSE {dist} END AS dist_km,
            epoch(observed_at) - epoch(pts) AS dt_s
          FROM p
        )
        SELECT mmsi, observed_at,
          CAST(floor(lat / {step}) AS INTEGER) || '_' || CAST(floor(lon / {step}) AS INTEGER) AS cell_id,
          coalesce(dist_km > {config.JUMP_MIN_DIST_KM}
                   AND (dt_s <= 0 OR dist_km / (dt_s / 3600.0) / 1.852 > {config.JUMP_SPEED_KN}), false)
            AS is_jump
        FROM q
    """)

    written: dict[str, tuple[int, int]] = {}
    downsample_bucket = (
        f"CASE WHEN sog < {config.SLOW_SOG_KN} THEN {config.SLOW_DOWNSAMPLE_S} ELSE {config.DOWNSAMPLE_S} END"
    )
    written["ais_dynamic"] = _write(con, f"""
        SELECT observed_at, mmsi, lat, lon, sog, cog, heading, rot, nav_status, mobile_type,
               pos_fix_type, data_source
        FROM d JOIN kept USING (mmsi)
        WHERE pos_ok
        QUALIFY row_number() OVER (
          PARTITION BY mmsi, {downsample_bucket},
                       floor(epoch(observed_at) / ({downsample_bucket}))
          ORDER BY observed_at, data_source NULLS LAST) = 1
        ORDER BY mmsi, observed_at
    """, "ais_dynamic", day, root)

    static_cols = ", ".join(config.DMA_STATIC_COLUMNS)
    any_static = " OR ".join(f"{c} IS NOT NULL" for c in config.DMA_STATIC_COLUMNS)
    # Change-point compression: a row is kept when its static tuple differs from the previous one for
    # that MMSI, which preserves every distinct consecutive static state.
    written["ais_static"] = _write(con, f"""
        WITH s AS (
          SELECT observed_at, mmsi, mobile_type, {static_cols}, hash({static_cols}) AS h
          FROM d JOIN kept USING (mmsi)
          WHERE {any_static}
        ), c AS (
          SELECT *, lag(h) OVER (PARTITION BY mmsi ORDER BY observed_at, h) AS prev_h FROM s
        )
        SELECT observed_at, mmsi, mobile_type, {static_cols}
        FROM c WHERE prev_h IS NULL OR prev_h <> h
        ORDER BY mmsi, observed_at
    """, "ais_static", day, root)

    written["ais_artifacts"] = _write(con, f"""
        WITH jr AS (
          SELECT mmsi, cell_id, count(*) AS n_rows, count(*) FILTER (WHERE is_jump) AS n_jumps
          FROM j WHERE mmsi IN (SELECT mmsi FROM kept) GROUP BY mmsi, cell_id
        ), ja AS (
          SELECT mmsi, sum(n_jumps)::BIGINT AS n_jumps,
            list(cell_id) FILTER (WHERE n_jumps > 0) AS jump_cells,
            list({{'cell_id': cell_id, 'n_rows': n_rows, 'n_jumps': n_jumps}}) AS cell_rows
          FROM jr GROUP BY mmsi
        ), bp AS (
          SELECT mmsi, count(*) FILTER (WHERE has_pos AND NOT pos_ok) AS n_bad_positions,
                 count(*) AS n_rows_fullres, count(*) FILTER (WHERE pos_ok) AS n_pos_ok
          FROM d WHERE mmsi IN (SELECT mmsi FROM kept) GROUP BY mmsi
        )
        SELECT DATE '{day}' AS day, {eod} AS observed_at, bp.mmsi, bp.n_rows_fullres, bp.n_pos_ok,
               bp.n_bad_positions, coalesce(ja.n_jumps, 0) AS n_jumps, ja.jump_cells, ja.cell_rows
        FROM bp LEFT JOIN ja USING (mmsi)
        ORDER BY mmsi
    """, "ais_artifacts", day, root)

    written["jump_baseline"] = _write(con, f"""
        SELECT DATE '{day}' AS day, {eod} AS observed_at, cell_id,
          count(DISTINCT mmsi) AS n_mmsi_observed,
          count(DISTINCT mmsi) FILTER (WHERE is_jump) AS n_mmsi_jumped,
          count(DISTINCT mmsi) FILTER (WHERE is_jump) / count(DISTINCT mmsi) AS frac_jumped
        FROM j GROUP BY cell_id ORDER BY cell_id
    """, "jump_baseline", day, root)

    written["vessel_day"] = _write(con, f"""
        SELECT DATE '{day}' AS day, {eod} AS observed_at, mm.mmsi, n_rows, n_pos_ok,
          modal_mobile_type AS mobile_type, modal_ship_type AS ship_type, modal_cargo_type AS cargo_type,
          modal_length AS length, modal_width AS width, modal_imo AS imo, modal_name AS name,
          coalesce(kept.tanker_today, false) AS tanker_today, kept.mmsi IS NOT NULL AS kept
        FROM mm LEFT JOIN kept USING (mmsi)
        WHERE mm.is_vessel
        ORDER BY mm.mmsi
    """, "vessel_day", day, root)

    if keep_fullres:
        written[FULLRES_TABLE] = _write(con, """
            SELECT observed_at, mmsi, lat, lon, sog, cog, heading, rot, nav_status, mobile_type,
                   pos_fix_type, data_source, draught
            FROM d JOIN kept USING (mmsi) WHERE pos_ok ORDER BY mmsi, observed_at
        """, FULLRES_TABLE, day, root)

    if diagnostics:
        def top(col: str, n: int = 40) -> list:
            return con.execute(
                f"SELECT {col}, count(*) AS n FROM d GROUP BY 1 ORDER BY n DESC LIMIT {n}"
            ).fetchall()

        ds.diagnostics = {
            "ship_type_values": top("ship_type"),
            "cargo_type_values": top("cargo_type"),
            "mobile_type_values": top("mobile_type"),
            "data_source_values": top("data_source"),
            "static_rows_per_dynamic_row": None,
            "jumps_total": con.execute("SELECT count(*) FILTER (WHERE is_jump) FROM j").fetchone()[0],
            "skagen_bbox_kept_mmsi": con.execute(f"""
                SELECT count(DISTINCT mmsi) FROM d JOIN kept USING (mmsi)
                WHERE pos_ok AND lat BETWEEN {config.SKAGEN_ANCHORAGE_BBOX['lat'][0]}
                                     AND {config.SKAGEN_ANCHORAGE_BBOX['lat'][1]}
                  AND lon BETWEEN {config.SKAGEN_ANCHORAGE_BBOX['lon'][0]}
                                     AND {config.SKAGEN_ANCHORAGE_BBOX['lon'][1]}
            """).fetchone()[0],
        }
        dyn = written["ais_dynamic"][0]
        ds.diagnostics["static_rows_per_dynamic_row"] = round(written["ais_static"][0] / dyn, 4) if dyn else None

    tanker_today = {
        int(x[0]) for x in con.execute("SELECT mmsi FROM kept WHERE tanker_today").fetchall()
    }
    for t in ("d", "mm", "kept", "j"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.unregister("registry_df")
    ds.rows_out = {k: v[0] for k, v in written.items()}
    ds.bytes_out = {k: v[1] for k, v in written.items()}
    ds.seconds = round(time.monotonic() - t0, 2)
    return ds, tanker_today


# ============================================================================ state
def _state(sub: str) -> Path:
    p = config.STATE_DIR / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def marker_path(day: date) -> Path:
    return _state("dma_done") / f"{day.isoformat()}.json"


def failed_path(day: date) -> Path:
    return _state("dma_failed") / f"{day.isoformat()}.json"


def is_done(day: date) -> bool:
    return marker_path(day).exists()


REGISTRY_FILE_NAME = "tanker_registry.parquet"


def load_registry() -> dict[int, str]:
    p = config.STATE_DIR / REGISTRY_FILE_NAME
    if not p.exists():
        return {}
    t = pq.read_table(p)
    return dict(zip(t.column("mmsi").to_pylist(), t.column("first_day").to_pylist(), strict=True))


def save_registry(reg: dict[int, str]) -> None:
    p = config.STATE_DIR / REGISTRY_FILE_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(reg.items())
    t = pa.table({"mmsi": pa.array([k for k, _ in items], pa.int64()),
                  "first_day": pa.array([v for _, v in items], pa.string())})
    tmp = p.with_suffix(".tmp")
    pq.write_table(t, tmp)
    tmp.replace(p)


TIMING_FIELDS = ["day", "source_file", "zipped_bytes", "rows_in_file", "rows_day", "rows_dynamic", "rows_static",
                 "mmsi_kept", "seconds_download", "seconds_stage1", "seconds_stage2", "parquet_bytes",
                 "malformed_rows", "finished_at"]


def _append_timing(row: dict) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    p = config.LOG_DIR / "dma_ingest.csv"
    new = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TIMING_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k) for k in TIMING_FIELDS})


# ============================================================================ file-level ingest
@dataclass
class FileResult:
    source_file: str
    days_done: list[str] = field(default_factory=list)
    days_failed: dict[str, str] = field(default_factory=dict)
    stage: dict = field(default_factory=dict)
    day_stats: list[dict] = field(default_factory=list)


def ingest_zip(
    zip_path: Path,
    days: list[date],
    source_file: str,
    seconds_download: float = 0.0,
    keep_fullres: set[date] | None = None,
    diagnostics: bool = False,
    delete_zip: bool = True,
    out_root: Path | None = None,
) -> FileResult:
    """Stage 1 once for the file, stage 2 for each requested day, then markers, registry, cleanup."""
    keep_fullres = keep_fullres or set()
    res = FileResult(source_file=source_file)
    temp = config.TMP_DIR / f"{zip_path.stem}.all.parquet"
    zipped_bytes = zip_path.stat().st_size
    try:
        try:
            st = csv_zip_to_parquet(zip_path, temp)
        except Exception as e:  # noqa: BLE001 - corrupt or drifted file: record, keep the zip, move on
            log.exception("stage 1 failed", extra={"file": source_file})
            for day in days:
                res.days_failed[day.isoformat()] = f"stage1: {e!r}"
                failed_path(day).write_text(json.dumps({"source_file": source_file, "error": repr(e)}))
            return res
        res.stage = asdict(st)
        if st.unknown_columns or st.missing_columns:
            log.warning("DMA header drift", extra={"file": source_file, "unknown": st.unknown_columns,
                                                    "missing": st.missing_columns})
        registry = load_registry()
        con = connect()
        try:
            for day in sorted(days):
                try:
                    ds, tanker_today = process_day(
                        con, temp, day, set(registry), out_root=out_root,
                        keep_fullres=day in keep_fullres, diagnostics=diagnostics,
                    )
                except Exception as e:  # noqa: BLE001 - one bad day must not stop the file
                    log.exception("day failed", extra={"day": day.isoformat(), "file": source_file})
                    res.days_failed[day.isoformat()] = repr(e)
                    failed_path(day).write_text(json.dumps({"source_file": source_file, "error": repr(e)}))
                    continue
                for m in tanker_today:  # keep the earliest day, even when days are ingested out of order
                    if m not in registry or day.isoformat() < registry[m]:
                        registry[m] = day.isoformat()
                save_registry(registry)
                malformed_share = st.malformed_rows / max(st.rows_in + st.malformed_rows, 1)
                marker = {
                    "source_file": source_file, "stage1": asdict(st), "day": asdict(ds),
                    "registry_size_after": len(registry),
                    "malformed_share_file": round(malformed_share, 5),
                    "malformed_flag": malformed_share > config.DMA_MALFORMED_FLAG_SHARE,
                    "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                marker_path(day).write_text(json.dumps(marker, indent=1, default=str))
                failed_path(day).unlink(missing_ok=True)
                _append_timing({
                    "day": day.isoformat(), "source_file": source_file, "zipped_bytes": zipped_bytes,
                    "rows_in_file": st.rows_in, "rows_day": ds.rows_day,
                    "rows_dynamic": ds.rows_out.get("ais_dynamic"), "rows_static": ds.rows_out.get("ais_static"),
                    "mmsi_kept": ds.mmsi_kept, "seconds_download": round(seconds_download, 2),
                    "seconds_stage1": st.seconds, "seconds_stage2": ds.seconds,
                    "parquet_bytes": sum(ds.bytes_out.values()), "malformed_rows": st.malformed_rows,
                    "finished_at": marker["finished_at"],
                })
                res.days_done.append(day.isoformat())
                res.day_stats.append(asdict(ds))
        finally:
            con.close()
    finally:
        temp.unlink(missing_ok=True)
        if delete_zip and not res.days_failed:
            zip_path.unlink(missing_ok=True)
    return res


# ============================================================================ bulk run
def zip_looks_complete(path: Path, expected_size: int | None) -> bool:
    """Cheap integrity check before reusing a zip left on disk: listed size and a readable central directory."""
    if not path.exists():
        return False
    if expected_size is not None and path.stat().st_size != expected_size:
        return False
    return zipfile.is_zipfile(path)


class IngestLocked(RuntimeError):
    pass


@contextmanager
def ingest_lock():
    """One DMA ingest at a time: two runners share raw/.part files and corrupt each other (Sep 17 2026)."""
    import fcntl  # noqa: PLC0415 - POSIX only; the runtime is WSL2

    path = _state("locks") / "ingest_dma.lock"
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        fh.seek(0)
        holder = fh.read().strip()
        fh.close()
        raise IngestLocked(f"another DMA ingest is running ({holder or 'unknown pid'}); "
                           "check with `pgrep -af shadowfleet.cli`") from e
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid {os.getpid()} since {datetime.now(UTC).isoformat(timespec='seconds')}")
    fh.flush()
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _download_file(c, f: DmaFile, size_hint: int | None, wait_disk: bool) -> tuple[Path, float]:
    dest = config.DMA_RAW_DIR / f.name
    if dest.exists():
        if zip_looks_complete(dest, f.size):
            return dest, 0.0
        log.warning("discarding incomplete or corrupt zip", extra={"file": f.name,
                                                                    "bytes": dest.stat().st_size,
                                                                    "expected": f.size})
        dest.unlink()
        dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
    need = int((size_hint or 1 << 30) * 2.5)  # zip + temp parquet + outputs, generous
    disk.wait_for_space(config.DATA_DIR, need_bytes=need, max_wait_s=None if wait_disk else 0)
    t0 = time.monotonic()
    net.download(c, f.url, dest)
    return dest, time.monotonic() - t0


def run_window(
    start: date,
    end: date,
    newest_first: bool = False,
    workers: int = config.DMA_MAX_WORKERS,
    keep_fullres: set[date] | None = None,
    wait_disk: bool = True,
    max_days: int | None = None,
    max_consecutive_failures: int = 3,
) -> dict:
    """Ingest every not-yet-done day in [start, end]. Downloads are prefetched by up to `workers`
    threads; processing is sequential in date order so the tanker registry grows in time order."""
    workers = max(1, min(workers, config.DMA_MAX_WORKERS))
    with ingest_lock(), net.client(timeout=config.DMA_HTTP_TIMEOUT_S) as c:
        base, files, _ = list_available(c)
        by_day = files_for_dates(files, start, end)
        pending = [d for d in by_day if not is_done(d)]
        if newest_first:
            pending.reverse()
        if max_days is not None:
            pending = pending[:max_days]
        groups: list[tuple[DmaFile, list[date]]] = []
        for d in pending:
            f = by_day[d]
            if groups and groups[-1][0] == f:
                groups[-1][1].append(d)
            else:
                groups.append((f, [d]))
        missing = [d.isoformat() for d in _date_range(start, end) if d not in by_day]
        log.info("DMA run planned", extra={"index": base, "days_pending": len(pending), "files": len(groups),
                                           "days_not_in_index": len(missing)})
        summary = {"index": base, "planned_days": len(pending), "done": 0, "failed": {},
                   "not_in_index": missing}
        consecutive_fail = 0
        t_start = time.monotonic()
        futures: dict[int, Future] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, (f, days) in enumerate(groups):
                for k in range(i, min(i + workers, len(groups))):
                    if k not in futures:
                        fk = groups[k][0]
                        futures[k] = pool.submit(_download_file, c, fk, fk.size or net.head_size(c, fk.url),
                                                 wait_disk)
                try:
                    zip_path, secs = futures.pop(i).result()
                except Exception as e:  # noqa: BLE001
                    log.error("download failed", extra={"file": f.name, "err": repr(e)})
                    for d in days:
                        summary["failed"][d.isoformat()] = f"download: {e!r}"
                        failed_path(d).write_text(json.dumps({"source_file": f.name, "error": repr(e)}))
                    consecutive_fail += 1
                else:
                    res = ingest_zip(zip_path, days, f.name, seconds_download=secs, keep_fullres=keep_fullres)
                    summary["done"] += len(res.days_done)
                    summary["failed"].update(res.days_failed)
                    consecutive_fail = consecutive_fail + 1 if res.days_failed else 0
                if consecutive_fail >= max_consecutive_failures:
                    log.error("stopping: consecutive failures look systematic",
                              extra={"consecutive": consecutive_fail})
                    summary["stopped_early"] = True
                    break
                n_done = summary["done"]
                if n_done and (n_done % 10 == 0 or i == len(groups) - 1):
                    rate = (time.monotonic() - t_start) / n_done
                    left = len(pending) - n_done - len(summary["failed"])
                    log.info("progress", extra={"done": n_done, "left": left,
                                                "eta_h": round(rate * left / 3600, 1),
                                                "free_gb": round(disk.free_gb(config.DATA_DIR), 1)})
            for fut in futures.values():
                fut.cancel()
    return summary


def _date_range(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def check(start: date, end: date) -> dict:
    """Days in [start, end] without a done marker, and which of those failed with what."""
    missing, failed = [], {}
    for d in _date_range(start, end):
        if not is_done(d):
            missing.append(d.isoformat())
            if failed_path(d).exists():
                failed[d.isoformat()] = json.loads(failed_path(d).read_text()).get("error")
    return {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days + 1,
            "missing": missing, "failed": failed}


# ============================================================================ probe (Phase 0 task 2)
def probe(day: date | None = None, keep_fullres: bool = True) -> dict:
    with net.client(timeout=config.DMA_HTTP_TIMEOUT_S) as c:
        base, files, unknown = list_available(c)
        daily = [f for f in files if f.kind == "daily"]
        monthly = [f for f in files if f.kind == "monthly"]
        all_days = sorted({d for f in files for d in f.dates()})
        sizes = {f.name: f.size or net.head_size(c, f.url) for f in (files[:2] + files[-2:])}
        target = day or (date.today() - timedelta(days=14))
        by_day = files_for_dates(files, target, target)
        if target not in by_day:
            if day is not None:
                raise ValueError(f"{day} is not in the DMA index")
            target = date.fromisoformat(daily[-1].period) if daily else all_days[-1]
            by_day = files_for_dates(files, target, target)
        f = by_day[target]
        size_hint = f.size or net.head_size(c, f.url)
        t0 = time.monotonic()
        dest, secs = _download_file(c, f, size_hint, wait_disk=False)
        secs = secs or (time.monotonic() - t0)
    zipped = dest.stat().st_size
    res = ingest_zip(dest, [target], f.name, seconds_download=secs,
                     keep_fullres={target} if keep_fullres else set(), diagnostics=True)
    payload = {
        "index_url": base,
        "n_daily_files": len(daily), "n_monthly_files": len(monthly),
        "earliest_day": all_days[0].isoformat() if all_days else None,
        "latest_day": all_days[-1].isoformat() if all_days else None,
        "earliest_daily": daily[0].period if daily else None,
        "earliest_monthly": monthly[0].period if monthly else None,
        "unrecognised_archives": unknown[:50],
        "sample_sizes_bytes": sizes,
        "benchmark_day": target.isoformat(), "benchmark_file": f.name, "benchmark_kind": f.kind,
        "zipped_bytes": zipped, "seconds_download": round(secs, 2),
        "result": asdict(res),
        "free_gb_after": round(disk.free_gb(config.DATA_DIR), 1),
    }
    probes.write("dma", payload)
    return payload
