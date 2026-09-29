from __future__ import annotations

from datetime import date

import httpx
import pytest

from shadowfleet import config
from shadowfleet.util import disk, net
from shadowfleet.util.ids import extract_imos, imo_valid


def test_cutoffs_24_month_window():
    w = config.Window(date(2024, 9, 17), date(2026, 9, 16))
    cuts = config.monthly_cutoffs(w, date(2026, 9, 17))
    assert cuts[0] == date(2025, 3, 31) and cuts[-1] == date(2026, 2, 28) and len(cuts) == 12
    sup = config.supervised_cutoffs(cuts)
    assert sup[0] == date(2025, 9, 30) and len(sup) == 6
    assert all(c == config.month_end(c) for c in cuts)


def test_cutoffs_15_month_window_has_no_supervised_cutoffs():
    # the reason the old 15-month fallback was removed (ADR-11 addendum)
    w = config.Window(date(2025, 6, 17), date(2026, 9, 16))
    cuts = config.monthly_cutoffs(w, date(2026, 9, 17))
    assert len(cuts) == 3 and config.supervised_cutoffs(cuts) == []


def test_cutoffs_respect_window_end():
    w = config.Window(date(2024, 1, 1), date(2025, 1, 15))
    cuts = config.monthly_cutoffs(w, date(2030, 1, 1))
    assert cuts[-1] == date(2024, 12, 31)


def test_imo_check_digit():
    assert imo_valid(9074729) and imo_valid("9176187")
    assert not imo_valid(9074728) and not imo_valid("123") and not imo_valid(None) and not imo_valid("0000000")
    assert not imo_valid("0023569")  # passes the check digit, but IMO numbers never start with 0


def test_normalize_imo_accepts_every_spelling_sources_use():
    from shadowfleet.util.ids import normalize_imo

    assert normalize_imo("IMO9402263") == 9402263      # OpenSanctions FtM
    assert normalize_imo("IMO 9402263") == 9402263
    assert normalize_imo(9402263) == 9402263           # DMA int
    assert normalize_imo("9402263") == 9402263         # OFAC advanced XML
    assert normalize_imo("0023569") is None and normalize_imo("") is None and normalize_imo(None) is None
    txt = "Vessel Registration Identification IMO 9074729; IMO 9074728; imo:9176187"
    assert extract_imos(txt) == [9074729, 9176187]


def test_disk_guard_waits_then_releases(tmp_path, monkeypatch):
    frees = iter([10.0, 12.0, 40.0])
    monkeypatch.setattr(disk, "free_gb", lambda p: next(frees))
    sleeps = []
    disk.wait_for_space(tmp_path, min_free_gb=25, resume_free_gb=30, _sleep=sleeps.append, poll_s=1)
    assert len(sleeps) == 1  # first check fails, loop: 12 < 30 -> sleep, 40 >= 30 -> release


def test_disk_guard_fails_fast_when_not_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(disk, "free_gb", lambda p: 5.0)
    with pytest.raises(disk.DiskFullError):
        disk.wait_for_space(tmp_path, min_free_gb=25, max_wait_s=0, _sleep=lambda s: None)


def test_download_resumes_with_range(tmp_path):
    payload = b"x" * 5000
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        rng = req.headers.get("Range")
        calls.append(rng)
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=payload[start:], headers={"Content-Length": str(len(payload) - start)})
        return httpx.Response(200, content=payload, headers={"Content-Length": str(len(payload))})

    dest = tmp_path / "f.zip"
    (tmp_path / "f.zip.part").write_bytes(payload[:1200])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        n = net.download(c, "http://x/f.zip", dest, _sleep=lambda s: None)
    assert n == 5000 and dest.read_bytes() == payload and calls == ["bytes=1200-"]
    assert not (tmp_path / "f.zip.part").exists()


def test_download_retries_on_503(tmp_path):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok", headers={"Content-Length": "2"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert net.download(c, "http://x/a", tmp_path / "a", _sleep=lambda s: None) == 2


def test_free_gb_is_capped_by_host_drive(tmp_path, monkeypatch):
    import shutil as _sh
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(config, "HOST_DISK_PATH", "/fake/c")
    monkeypatch.setattr(_sh, "disk_usage",
                        lambda p: Usage(0, 0, 23 * disk.GB) if str(p) == "/fake/c" else Usage(0, 0, 940 * disk.GB))
    assert round(disk.free_gb(tmp_path)) == 23
    monkeypatch.setattr(config, "HOST_DISK_PATH", "")
    assert round(disk.free_gb(tmp_path)) == 940


def test_flag_lists_are_real_iso3_codes():
    """45 codes typed off a web page; one transposition would silently disable a feature."""
    import pycountry

    for name in ("CONVENIENCE_FLAGS", "ITF_FOC_FLAGS_SENSITIVITY"):
        codes = getattr(config, name)
        assert len(codes) == len(set(codes)), f"{name} has duplicates"
        for c in codes:
            assert pycountry.countries.get(alpha_3=c) is not None, f"{name}: {c} is not an ISO3 code"
    # every shadow-fleet registry except Guyana is also ITF-declared; that gap is deliberate and documented
    assert set(config.CONVENIENCE_FLAGS) - set(config.ITF_FOC_FLAGS_SENSITIVITY) == {"GUY"}


def test_running_a_phase_out_of_order_names_the_command_you_skipped(tmp_data):
    """A fresh machine runs these in the wrong order. Each guard must say what to run, not raise
    AttributeError six frames down or a DuckDB parse error about a glob that matched nothing."""
    from datetime import date

    import pytest

    from shadowfleet.features import asof

    with pytest.raises(SystemExit) as no_window:
        config.monthly_cutoffs(config.load_window(), date.today())
    assert "make window-gate" in str(no_window.value)

    with pytest.raises(SystemExit) as no_ingest:
        asof.population(date(2025, 3, 31))
    assert "make ingest-dma" in str(no_ingest.value)


def test_the_declared_dependencies_are_the_ones_actually_imported():
    """pdfplumber was declared and unused while pypdfium2 was used and undeclared, so a clean install
    worked only because the unused one happened to pull the used one in."""
    text = (config.REPO_ROOT / "pyproject.toml").read_text()
    assert "pypdfium2" in text, "the PDF parser's actual dependency must be declared"
    for gone in ("polars", "shapely", "pyproj", "pdfplumber"):
        assert f'"{gone}' not in text, f"{gone} is declared but nothing imports it"


def test_util_never_imports_a_phase(tmp_data):
    """util/ is a leaf. The dependency arrow runs phase -> report, and it used to run both ways: report
    reached into features.asof and backtest.explain for constants, which is a cycle that only worked
    because the imports were hidden inside functions. Constants travel in the probe dict instead."""
    import ast
    from pathlib import Path

    banned = {"shadowfleet.features", "shadowfleet.backtest", "shadowfleet.detect", "shadowfleet.models",
              "shadowfleet.resolve", "shadowfleet.briefs", "shadowfleet.labels"}
    for path in (Path(config.REPO_ROOT) / "shadowfleet" / "util").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(b) for b in banned), \
                    f"{path.name} imports {node.module}: util/ must not depend on a phase"


def test_a_deferred_import_means_optional_heavy_or_platform_specific():
    """There were 93 function-local imports, almost all of them me being quick rather than a design.

    The survivors all have a reason: the module is an optional extra (lightgbm, sklearn), it is heavy or
    platform-bound (matplotlib, pypdfium2, fcntl), or it would be imported for one branch of a parser.
    cli.py is exempt on purpose: every command imports its phase lazily so `--help` is instant and a
    missing optional dependency breaks one command instead of the whole CLI.
    """
    import ast
    from pathlib import Path

    allowed = {"lightgbm", "sklearn", "matplotlib", "pypdfium2", "fcntl", "xml", "shap",
               "importlib", "subprocess", "sys"}
    offenders = []

    def probed(fn: ast.AST, node: ast.AST) -> bool:
        """An import inside `try: ... except ImportError:` is asking whether the module is installed,
        which is what `make doctor` exists to do. That is a structural reason, not a name to allowlist."""
        for t in [n for n in ast.walk(fn) if isinstance(n, ast.Try)]:
            if node in ast.walk(t) and any(
                    isinstance(h.type, ast.Name) and h.type.id == "ImportError" for h in t.handlers):
                return True
        return False

    for path in (Path(config.REPO_ROOT) / "shadowfleet").rglob("*.py"):
        if path.name == "cli.py":
            continue
        tree = ast.parse(path.read_text())
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(fn):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = (getattr(node, "module", None) or
                           (node.names[0].name if node.names else "")).split(".")[0]
                    if mod not in allowed and not probed(fn, node):
                        offenders.append(f"{path.name}:{node.lineno} {mod}")
    assert not offenders, "move these to module level, or add the module to `allowed` with a reason: " + \
                          ", ".join(sorted(offenders))


def test_no_source_file_is_silently_gitignored():
    """A `.gitignore` pattern that swallows source is invisible until someone else clones the repo.

    This happened: `data/` with no leading slash also matches `ui/src/data/`, so the console's whole data
    layer was ignored, `git add -A` reported nothing, and the branch was pushed without it. The commit
    looked clean on the machine that made it.
    """
    import subprocess

    tracked = subprocess.run(["git", "ls-files"], cwd=config.REPO_ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    source = [p for p in subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard",
         "shadowfleet", "tests", "ui/src", "ui/scripts"],
        cwd=config.REPO_ROOT, capture_output=True, text=True, check=True).stdout.split()
        if p.endswith((".py", ".ts", ".tsx", ".mjs", ".css")) and "__pycache__" not in p]
    assert not source, f"ignored but looks like source: {source[:5]} (tracked: {len(tracked)} files)"


# Real DMA destination strings (Sep 29 diagnostic): the name-only matcher missed 1,215 of 1,462.
B1_HITS = ["RUULU", "RU ULU", "RULED", "UST LUGA", "RU PRI", "PRIMORSK", "RUPRI", "RU LED", "RUMMK", "RU KGD",
           "UST-LUGA", "RUKGD", "KALININGRAD", "RUVYS", "RU MMK", "RUULU>EGPSD", "USTLUGA", "VYSOTSK",
           "ST PETERSBURG", "RU VYS", "ST.PETERSBURG", "UST_LUGA", "PRIMORSK,RUSSIA", "UST LUGA,RUSSIA",
           "primorsk russia", "MURMANSK", "SAINT PETERSBURG", "RUPRI>EGPSD", "EGPSD>RU-ULU", "NOVOROSSIYSK",
           "RUNVS", "RU TUA", "RUBLT", "RUTAM"]
B1_MISSES = ["RUARH", "RU ARH", "ARKHANGELSK", "ROTTERDAM", "FOR ORDERS", "SKAGEN", "PERU PRIMA", "RU",
             "GDANSK", "RUULUX", "TRUPRI", None, ""]


def test_russian_destination_matches_locodes_and_spellings():
    import duckdb

    from shadowfleet.features.asof import russian_destination_sql
    pred = russian_destination_sql("d")

    def hit(s):
        return duckdb.execute(f"SELECT coalesce({pred}, false) FROM (SELECT ?::VARCHAR AS d)", [s]).fetchone()[0]
    assert [s for s in B1_HITS if not hit(s)] == []
    assert [s for s in B1_MISSES if hit(s)] == []
