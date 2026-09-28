"""Hull ids must be assigned as known at the cutoff, not as known when each record was written.

Found on the real store, Sep 28: none of 20 designated IMOs resolved to a single hull id, most split across
one IMO id and three or four `syn:` ids. Two consequences made it more than cosmetic:
  * rule 3 (population excludes hulls listed on or before T) only removed the IMO-keyed fragment, so the
    `syn:` fragments of an already-sanctioned vessel stayed in the population as ordinary candidates;
  * a syn hull can never carry a positive label, so fragments of later-designated vessels were scored as
    guaranteed negatives.
"""

from __future__ import annotations

from datetime import timedelta

from shadowfleet.features import asof
from shadowfleet.ingest import dma
from shadowfleet.labels import labels as lab
from shadowfleet.resolve import identity
from tests.conftest import DAY, HEADER_V1, row, valid_imos, write_zip

SMALL = {"window_days": 2, "min_imo_days": 2}
DAYS = 12
IMO_SHY, IMO_OTHER = valid_imos(2, start=9300000)
T = (DAY + timedelta(days=DAYS - 1)).date()


def _store(tmp_data):
    """Hull 555 broadcasts its IMO on day 0 and then not again until day 6, so its cumulative vote only
    reaches `min_imo_days` late: the early windows get a `syn:` id. Hull 666 is an unrelated control."""
    from shadowfleet import config

    config.DMA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for off in range(DAYS):
        d = (DAY + timedelta(days=off)).date()
        t0 = (DAY + timedelta(days=off)).replace(hour=6)
        shy_imo = IMO_SHY if off == 0 or off >= 6 else ""
        rows = [row(t0 + timedelta(minutes=m), "Class A", 555, 57.0 + m * 0.002, 10.0, sog=11.0,
                    name="DELTA", imo=shy_imo) for m in range(30)]
        rows += [row(t0 + timedelta(minutes=m), "Class A", 666, 56.0 + m * 0.002, 11.0, sog=11.0,
                     name="EPSILON", imo=IMO_OTHER) for m in range(30)]
        z = write_zip(config.DMA_RAW_DIR / f"{d}.zip", rows, HEADER_V1)
        assert not dma.ingest_zip(z, [d], z.name).days_failed
    con = dma.connect()
    identity.hull_map(con, **SMALL)
    return con


def test_a_vessel_listed_before_the_cutoff_leaves_no_fragment_in_the_population(tmp_data):
    con = _store(tmp_data)
    lab.write_actions([{"source": "EU", "action": "add", "date": (DAY + timedelta(days=3)).date(),
                        "imo": int(IMO_SHY), "name": "DELTA", "program": "EU-MARE", "via": "test", "raw": ""}])
    pop = asof.population(T, con)
    fragments = [h for h in pop if h != IMO_OTHER]
    assert not fragments, (f"hull 555 is listed as of T, yet {fragments} is still in the population: "
                           "the records from its early windows carry a syn id that rule 3 cannot see")


def test_one_transmitter_is_one_hull_at_a_given_cutoff(tmp_data):
    con = _store(tmp_data)
    pop = asof.population(T, con)
    assert sorted(pop) == sorted([IMO_SHY, IMO_OTHER]), (
        f"expected one hull per vessel at T, got {sorted(pop)}: a transmitter whose IMO resolved during "
        "the feature window appears under both its early syn id and its IMO")


def test_attribution_at_T_never_uses_a_vote_that_closed_after_T(tmp_data):
    """The point-in-time half of ADR-23: assigning at the cutoff must not borrow a later vote.

    Hull 555's IMO reaches `min_imo_days` in the window covering days 6-7, which takes effect on day 8.
    At a cutoff on day 7 it must still be a syn hull; from day 8 on it is IMO_SHY, whole.
    """
    con = _store(tmp_data)
    early = (DAY + timedelta(days=7)).date()
    pop_early = asof.population(early, con)
    assert IMO_SHY not in pop_early, "the vote that names it closed after this cutoff"
    assert any(h.startswith("syn:") for h in pop_early), "it is still observed, just not yet named"
    assert sorted(asof.population(T, con)) == sorted([IMO_SHY, IMO_OTHER])


def test_only_the_identity_module_may_attribute_a_record_by_its_own_time():
    """Record-time attribution is what produced the rule-3 leak. It survives for coverage reporting only."""
    import pathlib

    import shadowfleet

    root = pathlib.Path(shadowfleet.__file__).parent
    home = root / "resolve" / "identity.py"  # by path: features/identity.py shares the file name
    users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                   if "_as_of_record" in p.read_text() and p != home)
    assert not users, f"{users} attribute records by their own time; use resolve.identity.at_cutoff(src, T)"
