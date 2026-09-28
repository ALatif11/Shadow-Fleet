"""Phase 4a: GFW events for the population.

Every fixture below copies the STRUCTURE of a real GFW response, captured by the Phase 0 probe on Sep 17,
with every value invented. Rule 7 keeps GFW data out of the repo; the structure is what the parser has to
survive, and three things about it were only visible in real responses: numbers arrive as strings in some
fields ("distanceKm": "1781.09"), a port visit's anchorage can have a country and no name, and each
response's metadata names the dataset version actually served (":latest" is only an alias).
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import httpx
import pytest

from shadowfleet import config
from shadowfleet.ingest import gfw

IMO = 9300001

GAP = {
    "start": "2025-02-15T06:25:17.000Z", "end": "2025-02-17T14:25:17.000Z", "id": "gap-1", "type": "gap",
    "position": {"lat": 36.32, "lon": 12.746},
    "distances": {"startDistanceFromShoreKm": 142, "endDistanceFromShoreKm": 62},
    "vessel": {"id": "vid-1", "name": "INVENTED", "ssvid": "352000001", "flag": "PAN", "type": "other"},
    "gap": {"intentionalDisabling": True, "distanceKm": "1781.0977646440463", "durationHours": 56.0,
            "impliedSpeedKnots": "0.44857893526925047",
            "offPosition": {"lat": 34.47, "lon": 22.31}, "onPosition": {"lat": "37.37", "lon": "2.82"}},
}
# a Russian port visit whose anchorage has a country and NO name: 2 of 8 in the real sample looked like this
PORT_NAMELESS = {
    "start": "2025-03-01T10:00:00.000Z", "end": "2025-03-03T12:00:00.000Z", "id": "pv-1", "type": "port_visit",
    "position": {"lat": 60.35, "lon": 28.66}, "distances": {"startDistanceFromShoreKm": 1},
    "vessel": {"id": "vid-1", "ssvid": "352000001"},
    "port_visit": {"visitId": "x", "confidence": "4", "durationHrs": 50.0,
                   "startAnchorage": {"anchorageId": "a1", "atDock": False, "flag": "RUS", "lat": 60.35,
                                      "lon": 28.66, "name": None}},
}
LOITER = {
    "start": "2025-01-21T12:53:14.000Z", "end": "2025-01-22T11:51:36.000Z", "id": "lo-1", "type": "loitering",
    "position": {"lat": 43.37, "lon": 35.59}, "distances": {"startDistanceFromShoreKm": 131},
    "vessel": {"id": "vid-2", "ssvid": "352000002"},
    "loitering": {"totalTimeHours": 22.97, "totalDistanceKm": 57.1, "averageSpeedKnots": 1.34,
                  "averageDistanceFromShoreKm": 143.46},
}
# Encounters: the documented shape. The real probe returned none for tankers (the Phase 0 finding), so this
# is the one parser path that has never met a live record, and the report says so.
ENCOUNTER = {
    "start": "2025-04-01T00:00:00.000Z", "end": "2025-04-01T06:00:00.000Z", "id": "en-1", "type": "encounter",
    "position": {"lat": 35.0, "lon": 23.0}, "vessel": {"id": "vid-1", "ssvid": "352000001"},
    "encounter": {"medianDistanceKilometers": "0.05", "medianSpeedKnots": 0.8,
                  "vessel": {"id": "partner-9", "ssvid": "273000009"}},
}


def test_flatten_turns_string_numbers_into_numbers_and_dates_the_event_by_its_end():
    r = gfw.flatten(GAP, IMO, "public-global-gaps-events:v4.0")
    assert set(r) == set(gfw.EVENT_COLUMNS), "one row, exactly the declared columns"
    assert r["imo"] == IMO and r["ssvid"] == 352000001 and r["event_type"] == "gap"
    assert r["gap_distance_km"] == pytest.approx(1781.0977) and isinstance(r["gap_distance_km"], float)
    assert r["gap_implied_speed_knots"] == pytest.approx(0.4486, abs=1e-3)
    assert r["gap_intentional"] is True
    assert r["observed_at"] == r["end"] == datetime(2025, 2, 17, 14, 25, 17), "knowable when it ended"
    assert r["duration_h"] == pytest.approx(56.0)


def test_a_port_visit_without_an_anchorage_name_keeps_its_country():
    r = gfw.flatten(PORT_NAMELESS, IMO)
    assert r["port_name"] is None and r["port_country"] == "RUS"
    assert r["port_confidence"] == 4, "sent as the string '4'"


def test_loitering_and_encounter_fields():
    lo = gfw.flatten(LOITER, IMO)
    assert lo["loitering_avg_speed_knots"] == pytest.approx(1.34)
    assert lo["loitering_distance_from_shore_km"] == pytest.approx(143.46)
    en = gfw.flatten(ENCOUNTER, IMO)
    assert en["partner_gfw_id"] == "partner-9" and en["partner_ssvid"] == 273000009
    assert en["encounter_median_distance_km"] == pytest.approx(0.05)


def test_year_chunks():
    assert gfw.year_chunks("2023-09-03", "2025-02-10") == [
        ("2023-09-03", "2023-12-31"), ("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-02-10")]
    assert gfw.year_chunks("2025-03-01", "2025-06-30") == [("2025-03-01", "2025-06-30")]


def _handler(calls: list):
    """Search finds two vessel ids for IMO, none for the second IMO. Events: one page per (type, year)."""
    by_type = {"gaps": [GAP], "port-visits": [PORT_NAMELESS], "loitering": [LOITER], "encounters": [ENCOUNTER]}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path.endswith("/vessels/search"):
            q = req.url.params["query"]
            if q != str(IMO):
                return httpx.Response(200, json={"entries": []})
            return httpx.Response(200, json={"entries": [{"selfReportedInfo": [
                {"id": "vid-1", "imo": str(IMO)}, {"id": "vid-2", "imo": str(IMO)}]}]})
        ds = req.url.params["datasets[0]"]
        kind = next(k for k in by_type if f"-{k}-" in ds)
        version = ds.replace(":latest", ":v4.0")
        # the same events in every year chunk: dedupe by event id has to collapse them
        return httpx.Response(200, json={"metadata": {"datasets": [version]}, "entries": by_type[kind],
                                         "nextOffset": None})
    return handler


def test_fetch_batches_dedupes_and_reports_misses_and_served_versions(tmp_data):
    calls: list = []
    c = gfw.GfwClient(token="t", transport=httpx.MockTransport(_handler(calls)), sleep=lambda s: None)
    res = gfw.fetch([IMO, 9300013], "2024-11-01", "2025-06-30", client=c)
    assert res["misses"] == [9300013]
    assert {r["gfw_vessel_id"] for r in res["vessel_map"]} == {"vid-1", "vid-2"}
    assert sorted(e["event_id"] for e in res["events"]) == ["en-1", "gap-1", "lo-1", "pv-1"], \
        "two year chunks returned each event twice; one row each"
    assert "public-global-gaps-events:v4.0" in res["datasets"], "the served version, not the alias"
    events_calls = [r for r in calls if r.url.path.endswith("/events")]
    assert len(events_calls) == 4 * 2, "4 types x 2 years x 1 batch of vessel ids"
    assert all(r.url.params["vessels[1]"] == "vid-2" for r in events_calls), "vessel ids travel together"


def test_write_and_coverage(tmp_data):
    calls: list = []
    c = gfw.GfwClient(token="t", transport=httpx.MockTransport(_handler(calls)), sleep=lambda s: None)
    cov = gfw.write(gfw.fetch([IMO, 9300013], "2025-01-01", "2025-12-31", client=c))
    assert cov["imos_requested"] == 2 and cov["imos_with_a_gfw_id"] == 1
    assert cov["events_by_type"] == {"gap": 1, "port_visit": 1, "loitering": 1, "encounter": 1}
    assert cov["russian_port_visits"] == {"all_rus": 1, "in_b1_regions": 1, "without_a_name": 1}
    rows = duckdb.connect().execute(
        f"SELECT imo, event_type, observed_at FROM read_parquet('{config.PARQUET_DIR}/gfw_events/*/*.parquet')"
    ).fetchall()
    assert {r[0] for r in rows} == {IMO} and len(rows) == 4


def test_gfw_features_at_a_cutoff_count_what_ended_by_then_and_nothing_after(tmp_data):
    """Real-shaped events through `gfw.write` and into `features(T)`, on the attribution store's hulls.

    T is 2025-03-15. The encounter ends 2025-04-01, so it must not exist yet. The Russian port visit has no
    anchorage name, only country RUS at Primorsk's position, and must still count toward B1.
    """
    from shadowfleet.features import asof
    from tests.test_identity_attribution import IMO_OTHER, T, _store

    con = _store(tmp_data)
    imo = int(IMO_OTHER)
    events = [gfw.flatten(e, imo) for e in (GAP, PORT_NAMELESS, LOITER, ENCOUNTER)]
    gfw.write({"vessel_map": [{"imo": imo, "gfw_vessel_id": "vid-1", "match_method": "imo"}],
               "events": events, "misses": [], "datasets": [], "client_stats": {}})
    row = next(r for r in asof.features(T, con) if r["hull_id"] == IMO_OTHER)
    assert row["n_gaps"] == 1 and row["gap_hours_total"] == pytest.approx(56.0)
    assert row["max_gap_distance_km"] == pytest.approx(1781.0977)
    assert row["n_gaps_offshore"] == 1, "142 km from shore at the start, past 50 nm"
    assert row["n_loitering"] == 1 and row["n_port_visits"] == 1
    assert row["n_russian_port_visits"] == 1, "nameless, but RUS and inside the Baltic region"
    assert row["days_since_last_russian_port_visit"] == 12
    assert row["n_encounters"] == 0, "it ends after T"
    other = next(r for r in asof.features(T, con) if r["hull_id"] != IMO_OTHER)
    assert other["n_gaps"] == 0 and other["days_since_last_russian_port_visit"] is None


def test_an_encounter_with_a_vessel_listed_by_T_counts_as_a_sanctioned_partner(tmp_data):
    """The partner is known only by its MMSI (555). At T that MMSI is hull IMO_SHY, listed before T, so the
    encounter counts. Through `hull_at(T)`, not a hull id stamped at the encounter (ADR-23)."""
    from shadowfleet.features import asof
    from shadowfleet.labels import labels as lab
    from tests.conftest import DAY
    from tests.test_identity_attribution import IMO_OTHER, IMO_SHY, T, _store

    con = _store(tmp_data)
    lab.write_actions([{"source": "UK", "action": "add", "date": DAY.date(), "imo": int(IMO_SHY),
                        "name": "DELTA", "program": "x", "via": "test", "raw": ""}])
    enc = {**ENCOUNTER, "start": "2025-03-10T00:00:00.000Z", "end": "2025-03-10T06:00:00.000Z",
           "encounter": {**ENCOUNTER["encounter"], "vessel": {"id": "unknown-to-us", "ssvid": "555"}}}
    gfw.write({"vessel_map": [], "events": [gfw.flatten(enc, int(IMO_OTHER))], "misses": [],
               "datasets": [], "client_stats": {}})
    row = next(r for r in asof.features(T, con) if r["hull_id"] == IMO_OTHER)
    assert row["n_encounters"] == 1 and row["n_encounters_with_sanctioned_partner"] == 1


def test_phase4a_report_states_coverage_and_the_encounter_finding(tmp_data):
    from shadowfleet.util import report

    calls: list = []
    c = gfw.GfwClient(token="t", transport=httpx.MockTransport(_handler(calls)), sleep=lambda s: None)
    cov = gfw.write(gfw.fetch([IMO, 9300013], "2025-01-01", "2025-12-31", client=c))
    text = report.render_phase4a({**cov, "start": "2025-01-01", "end": "2025-12-31"})
    assert "1 of 2 IMOs have a GFW vessel id" in text
    assert "| gap | 1 | 1 |" in text
    assert "1 of the RUS visits carry no anchorage name" in text
    assert "public-global-gaps-events:v4.0" in text


def test_no_events_call_sends_more_vessel_ids_than_the_live_api_parses_as_an_array(tmp_data):
    """The live API parses `vessels[k]` with Node's qs, which gives up on arrays past index 20 and returns 422.

    This mock answers exactly as GFW did on Sep 28 when a call carries an index above 20, and one IMO here
    has 45 vessel ids, so any batch size over 21 fails the way the first population run did.
    """
    calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path.endswith("/vessels/search"):
            return httpx.Response(200, json={"entries": [{"selfReportedInfo": [
                {"id": f"vid-{i}", "imo": str(IMO)} for i in range(45)]}]})
        if any(f"vessels[{k}]" in req.url.params for k in range(21, 100)):
            return httpx.Response(422, json={"statusCode": 422, "error": "Unprocessable Entity", "messages": [
                {"title": "vessels", "detail": "vessels must be an array"}]})
        return httpx.Response(200, json={"entries": [], "nextOffset": None})

    c = gfw.GfwClient(token="t", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    res = gfw.fetch([IMO], "2025-01-01", "2025-12-31", client=c)
    assert len(res["vessel_map"]) == 45
    per_call = [sum(1 for k in r.url.params if k.startswith("vessels[")) for r in calls
                if r.url.path.endswith("/events")]
    assert max(per_call) <= 20 and sum(per_call) == 45 * 4, "every id sent once per event type"
