"""`python -m shadowfleet.cli <command>` (also installed as `shadowfleet`)."""

from __future__ import annotations

import json
import sys
from datetime import date

import typer

from shadowfleet import config
from shadowfleet.util import logs

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Shadow Fleet pipeline")


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _print(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@app.command()
def doctor(offline: bool = typer.Option(False, help="skip network checks")) -> None:
    """Check Python, WSL, disk, GPU, tokens and network."""
    from shadowfleet.util.doctor import FAIL, checks

    res = checks(network=not offline)
    width = max(len(c.name) for c in res)
    for c in res:
        typer.echo(f"{c.status:>4}  {c.name:<{width}}  {c.detail}")
    if any(c.status == FAIL for c in res):
        raise typer.Exit(1)


@app.command()
def cutoffs(start: str = typer.Option(None), end: str = typer.Option(None), today: str = typer.Option(None)) -> None:
    """Show monthly cutoffs for a window (defaults to config/window.json)."""
    w = config.Window(_d(start), _d(end)) if start and end else config.load_window()
    if w is None:
        typer.echo("no window: pass --start/--end or run window-gate")
        raise typer.Exit(1)
    t = _d(today) or date.today()
    cuts = config.monthly_cutoffs(w, t)
    _print({"window": [w.start, w.end], "today": t, "cutoffs": cuts,
            "supervised": config.supervised_cutoffs(cuts)})


# ------------------------------------------------------------------ DMA
@app.command("dma-list")
def dma_list() -> None:
    """Show what the DMA index offers."""
    from shadowfleet.ingest import dma
    from shadowfleet.util import net

    with net.client() as c:
        base, files, unknown = dma.list_available(c)
    _print({"index": base, "files": len(files), "first": files[0].name, "last": files[-1].name,
            "unrecognised": unknown[:20]})


@app.command("probe-dma")
def probe_dma(day: str = typer.Option(None, help="YYYY-MM-DD; default 14 days ago"),
              fullres: bool = typer.Option(True)) -> None:
    """Phase 0 task 2: benchmark one DMA day through the frozen ingest."""
    from shadowfleet.ingest import dma

    logs.setup("probe_dma")
    p = dma.probe(_d(day), keep_fullres=fullres)
    _print({k: v for k, v in p.items() if k != "result"} | {"day_stats": p["result"]["day_stats"],
                                                           "failed": p["result"]["days_failed"]})
    if p["result"]["days_failed"]:
        raise typer.Exit(1)


@app.command("window-gate")
def window_gate(allow_short: bool = typer.Option(False, help="only after Adam signs off"),
                start: str = typer.Option(None, help="YYYY-MM-DD; default = first daily DMA file"),
                allow_monthly: bool = typer.Option(False, help="allow a start inside the monthly archives")) -> None:
    """Phase 0 task 3: write config/window.json."""
    from dataclasses import asdict

    from shadowfleet.ingest.window import WindowGateError, run

    try:
        _print(asdict(run(allow_short=allow_short, start=_d(start), allow_monthly=allow_monthly)))
    except WindowGateError as e:
        typer.echo(f"window gate refused: {e}", err=True)
        raise typer.Exit(2) from e


@app.command("ingest-dma")
def ingest_dma(start: str = typer.Option(None), end: str = typer.Option(None),
               newest_first: bool = typer.Option(False),
               workers: int = typer.Option(config.DMA_MAX_WORKERS),
               max_days: int = typer.Option(None),
               wait_disk: bool = typer.Option(True, help="pause (not fail) when disk is low")) -> None:
    """Ingest every not-yet-done day in the window, oldest first. Safe to interrupt and rerun."""
    from shadowfleet.ingest import dma

    logs.setup("ingest_dma")
    w = config.load_window()
    s, e = _d(start) or (w.start if w else None), _d(end) or (w.end if w else None)
    if not (s and e):
        typer.echo("no window: run `make window-gate` or pass --start/--end", err=True)
        raise typer.Exit(1)
    try:
        summary = dma.run_window(s, e, newest_first=newest_first, workers=workers, max_days=max_days,
                                 wait_disk=wait_disk)
    except dma.IngestLocked as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(4) from err
    _print({k: (v if k != "not_in_index" else len(v)) for k, v in summary.items()})
    if summary.get("stopped_early"):
        raise typer.Exit(3)


@app.command("ingest-day")
def ingest_day(day: str, fullres: bool = typer.Option(False)) -> None:
    """(Re)ingest a single day, e.g. for the Phase 1 STS-readiness check."""
    from shadowfleet.ingest import dma

    logs.setup("ingest_dma")
    d = _d(day)
    marker = dma.marker_path(d)
    if marker.exists():
        marker.unlink()
    try:
        s = dma.run_window(d, d, workers=1, keep_fullres={d} if fullres else None, wait_disk=False)
    except dma.IngestLocked as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(4) from err
    _print(s)
    if s["failed"] or not s["done"]:
        raise typer.Exit(1)


@app.command("ingest-dma-check")
def ingest_dma_check(start: str = typer.Option(None), end: str = typer.Option(None)) -> None:
    """List window days without a done marker (and why, when known)."""
    from shadowfleet.ingest import dma

    w = config.load_window()
    s, e = _d(start) or (w.start if w else None), _d(end) or (w.end if w else None)
    if not (s and e):
        typer.echo("no window", err=True)
        raise typer.Exit(1)
    c = dma.check(s, e)
    share = len(c["missing"]) / c["days"]
    _print({"days": c["days"], "missing": len(c["missing"]), "missing_share": round(share, 4),
            "failed": c["failed"], "first_missing": c["missing"][:30]})


# ------------------------------------------------------------------ other probes
@app.command("phase1")
def phase1_cmd(sts_day: str = typer.Option(None, help="day with ais_fullres; default = latest ingested"),
               report: bool = typer.Option(True, help="also write reports/phase1.md")) -> None:
    """Phase 1: population, type changes, gap evidence, STS readiness (reads Parquet only)."""
    from shadowfleet import phase1
    from shadowfleet.util import probes

    logs.setup("phase1")
    out = phase1.run_all(_d(sts_day))
    probes.write("phase1", out)
    _print(out)
    if report:
        from shadowfleet.util import report as rep

        rep.write_phase1()
        typer.echo(f"wrote {config.REPORTS_DIR / 'phase1.md'}", err=True)


@app.command("probe-opensanctions")
def probe_opensanctions() -> None:
    """Phase 0 task 7."""
    from shadowfleet.ingest import opensanctions

    logs.setup("probe_opensanctions")
    out, cands = opensanctions.probe()
    (config.PROBE_DIR / "imo_candidates.json").write_text(json.dumps(cands))
    _print({k: v for k, v in out.items() if k != "maritime"} | {"candidates": len(cands)})


@app.command("probe-gfw")
def probe_gfw(imos: list[str] = typer.Argument(None, help="default: first 15 OpenSanctions candidates"),
              start: str = "2025-01-01", end: str = "2025-12-31") -> None:
    """Phase 0 task 5: needs probe-opensanctions first unless IMOs are given."""
    from shadowfleet.ingest import gfw

    logs.setup("probe_gfw")
    if not imos:
        imos = _gfw_candidates()
        if not imos:
            typer.echo("no candidate IMOs: run `make probe-ofac` (preferred) or `make probe-opensanctions` first",
                       err=True)
            raise typer.Exit(1)
        typer.echo(f"candidate IMOs: {imos}", err=True)
    _print(gfw.probe(imos, start, end))


def _gfw_candidates(want: int = 5) -> list[str]:
    """OFAC SDN vessels typed as tankers (from the cached sdn.csv), else OpenSanctions candidates checked on GFW."""
    from shadowfleet.ingest import ofac
    from shadowfleet.util.ids import imo_valid

    sdn = config.HTTP_CACHE_DIR / "ofac" / "sdn.csv"
    if sdn.exists():
        vessels = ofac.sdn_vessels(ofac.parse_sdn_csv(sdn.read_bytes().decode("latin-1")))
        tankers = [str(v["imo"]) for v in vessels
                   if v["imo"] and "tanker" in (v.get("vess_type") or "").lower() and "RUSSIA" in (v["program"] or "")]
        # newest entries last in the file; take them from the end for a spread of recent designations
        if tankers:
            return tankers[-want:]
    f = config.PROBE_DIR / "imo_candidates.json"
    if f.exists():
        cands = [c for c in json.loads(f.read_text()) if imo_valid(c)]
        return _pick_tankers(cands, want)
    return []


def _pick_tankers(candidates: list[str], want: int = 5, max_tries: int = 25) -> list[str]:
    """First `want` candidates that GFW calls a tanker; falls back to the first `want` if none are typed."""
    from shadowfleet.ingest import gfw

    picked: list[str] = []
    with gfw.GfwClient() as c:
        for imo in candidates[:max_tries]:
            types = gfw.shiptypes(c.search_vessels(imo))
            if any("TANKER" in t.upper() for t in types):
                picked.append(imo)
            if len(picked) == want:
                break
    return picked or candidates[:want]


@app.command("probe-ofac")
def probe_ofac() -> None:
    """Phase 0 task 6."""
    from shadowfleet.ingest import ofac

    logs.setup("probe_ofac")
    out = ofac.probe()
    _print(out)
    if not out["go"]:
        raise typer.Exit(1)


@app.command("probe-mid")
def probe_mid() -> None:
    """Phase 0 task 8."""
    from shadowfleet.ingest import mid

    logs.setup("probe_mid")
    _print(mid.probe())


@app.command("llm-smoke")
def llm_smoke(url: str = typer.Option(None)) -> None:
    """Phase 0 task 9: needs llama-server running (SETUP.md step 6)."""
    from shadowfleet.briefs import llm_smoke as smoke

    out = smoke.run(url)
    _print(out)
    if not out["go"]:
        raise typer.Exit(1)


@app.command("report-phase0")
def report_phase0() -> None:
    """Render reports/phase0.md from probe files."""
    from shadowfleet.util import report

    report.write()
    typer.echo(f"wrote {config.REPORTS_DIR / 'phase0.md'}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
