from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from shadowfleet import config
from shadowfleet.labels import labels as L

ADV_XML = """<?xml version="1.0"?>
<Sanctions xmlns="urn:x">
 <DistinctParties>
  <DistinctParty FixedRef="100"><Profile><Identity><Alias><DocumentedName>
   <DocumentedNamePart><NamePartValue>ALPHA STAR</NamePartValue></DocumentedNamePart>
  </DocumentedName></Alias></Identity>
  <Feature><FeatureVersion><VersionDetail>9074729</VersionDetail>
  </FeatureVersion></Feature></Profile></DistinctParty>
  <DistinctParty FixedRef="200"><Profile><Identity><Alias><DocumentedName>
   <DocumentedNamePart><NamePartValue>NOT A VESSEL LLC</NamePartValue></DocumentedNamePart>
  </DocumentedName></Alias></Identity></Profile></DistinctParty>
 </DistinctParties>
 <SanctionsEntries>
  <SanctionsEntry ID="1" ProfileID="100">
   <SanctionsMeasure>RUSSIA-EO14024</SanctionsMeasure>
   <EntryEvent ID="9" EntryEventTypeID="1"><Date><Year>2025</Year><Month>1</Month><Day>10</Day></Date>
   </EntryEvent>
   <EntryEvent ID="10" EntryEventTypeID="2"><Date><Year>2025</Year><Month>3</Month><Day>4</Day></Date>
   </EntryEvent>
  </SanctionsEntry>
  <SanctionsEntry ID="2" ProfileID="200">
   <EntryEvent ID="11" EntryEventTypeID="1"><Date><Year>2024</Year><Month>2</Month><Day>1</Day></Date>
   </EntryEvent>
  </SanctionsEntry>
 </SanctionsEntries>
</Sanctions>"""


def test_advanced_xml_gives_dated_vessel_adds(tmp_path):
    p = tmp_path / "sdn_advanced.xml"
    p.write_text(ADV_XML)
    rows = L.parse_advanced_xml(p)
    assert [(r["imo"], r["action"], r["date"].isoformat()) for r in rows] == [
        (9074729, "add", "2025-01-10"), (9074729, "modify", "2025-03-04")]
    assert rows[0]["program"] == "RUSSIA-EO14024" and rows[0]["via"] == "advanced_xml:xml_scan"
    # the SDN CSV mapping wins when it covers the entity
    named = L.parse_advanced_xml(p, {"100": 9176187})
    assert named[0]["imo"] == 9176187 and named[0]["via"] == "advanced_xml:sdn_csv"
    assert rows[0]["name"].strip() == "ALPHA STAR"  # the non-vessel party is dropped


def test_opensanctions_rows_with_celex_fallback(tmp_path):
    lines = [
        {"id": "v1", "schema": "Vessel", "properties": {"imoNumber": ["IMO9074729"], "name": ["BETA"]}},
        {"id": "v2", "schema": "Vessel", "properties": {"imoNumber": ["IMO 9176187"]}},
        {"id": "v3", "schema": "Vessel", "properties": {"imoNumber": ["0023569"]}},  # junk IMO, dropped
        {"id": "s1", "schema": "Sanction", "properties": {"entity": ["v1"], "programId": ["EU-MARE"],
                                                          "startDate": ["2024-06-25"]}},
        {"id": "s2", "schema": "Sanction", "properties": {
            "entity": ["v2"], "programId": ["EU-MARE"],
            "sourceUrl": ["https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A32024R1745"]}},
        {"id": "s3", "schema": "Sanction", "properties": {"entity": ["v3"], "programId": ["EU-PRK"],
                                                          "startDate": ["2018-06-07"]}},
    ]
    p = tmp_path / "e.ftm.json"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    rows = L.opensanctions_rows(p, "EU", "EU-MARE", {"32024R1745": "2024-06-24"})
    assert [(r["imo"], r["date"].isoformat(), r["via"]) for r in rows] == [
        (9074729, "2024-06-25", "startDate"), (9176187, "2024-06-24", "celex:32024R1745")]


def test_opensanctions_rows_skip_undated(tmp_path):
    lines = [{"id": "v1", "schema": "Vessel", "properties": {"imoNumber": ["9074729"]}},
             {"id": "s1", "schema": "Sanction", "properties": {"entity": ["v1"], "programId": ["EU-MARE"]}}]
    p = tmp_path / "e.ftm.json"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    assert L.opensanctions_rows(p, "EU", "EU-MARE", {}) == []


@pytest.fixture()
def actions(tmp_data):
    rows = [
        {"source": "OFAC", "action": "add", "date": date(2025, 1, 10), "imo": 9074729, "name": "A",
         "program": "RUSSIA-EO14024", "via": "advanced_xml", "raw": None},
        {"source": "OFAC", "action": "add", "date": date(2024, 5, 1), "imo": 9176187, "name": "B",
         "program": "RUSSIA-EO14024", "via": "change_archive", "raw": None},
        {"source": "OFAC", "action": "remove", "date": date(2024, 12, 1), "imo": 9176187, "name": "B",
         "program": None, "via": "change_archive", "raw": None},
        {"source": "EU", "action": "add", "date": date(2024, 6, 25), "imo": 9179834, "name": "C",
         "program": "EU-MARE", "via": "startDate", "raw": None},
        {"source": "UK", "action": "add", "date": date(2025, 2, 20), "imo": 9074729, "name": "A",
         "program": "GB-RUS", "via": "startDate", "raw": None},
    ]
    out = config.PARQUET_DIR / L.ACTIONS_FILE
    L.write_actions(rows, out)
    return out


def test_listed_as_of_replays_removals(actions):
    assert set(L.listed_as_of(date(2024, 7, 1), path=actions)) == {9176187, 9179834}
    assert set(L.listed_as_of(date(2025, 1, 1), path=actions)) == {9179834}   # 9176187 was removed
    at_feb = L.listed_as_of(date(2025, 2, 1), path=actions)
    assert at_feb[9074729].date == date(2025, 1, 10) and at_feb[9074729].source == "OFAC"


def test_labels_positive_within_horizon_only(actions):
    pop = [9074729, 9176187, 9179834, 9999999]
    out = {r["imo"]: r for r in L.labels(date(2024, 12, 31), pop, path=actions)}
    assert 9179834 not in out                      # listed by the EU already: excluded from the population
    assert out[9074729]["label"] == 1 and out[9074729]["source"] == "OFAC"
    assert out[9999999]["label"] == 0
    # 9176187 was de-listed, so it is in the population and has no add in the horizon
    assert out[9176187]["label"] == 0
    far = {r["imo"]: r for r in L.labels(date(2024, 6, 30), pop, path=actions)}
    assert far[9074729]["label"] == 0              # add is 194 days later, past the 182-day horizon


def test_ofac_only_label_still_excludes_eu_uk_listed(actions):
    pop = [9179834]
    assert L.labels(date(2025, 1, 1), pop, ("OFAC",), path=actions) == []


def test_positives_table_reports_the_r12_channel(actions):
    pop = [9074729, 9176187]
    rows = L.positives_table([date(2024, 12, 31)], {date(2024, 12, 31): pop}, path=actions)
    r = rows[0]
    assert r["positives_union"] == 1 and r["positives_ofac_only"] == 1
    assert r["population_in_scope"] == 2 and r["excluded_already_listed"] == 0
    assert r["ofac_adds_in_horizon_all"] == 1 and r["ofac_adds_already_eu_uk_listed"] == 0


def test_r12_counts_ofac_adds_the_eu_had_already_listed(tmp_data):
    """The hull the EU listed first is excluded from the population, so R12 must be measured before that."""
    rows = [
        {"source": "EU", "action": "add", "date": date(2024, 6, 1), "imo": 9074729, "name": "A",
         "program": "EU-MARE", "via": "startDate", "raw": None},
        {"source": "OFAC", "action": "add", "date": date(2025, 1, 10), "imo": 9074729, "name": "A",
         "program": "RUSSIA-EO14024", "via": "advanced_xml", "raw": None},
    ]
    out = config.PARQUET_DIR / "a.parquet"
    L.write_actions(rows, out)
    T = date(2024, 12, 31)
    r = L.positives_table([T], {T: [9074729]}, path=out)[0]
    assert r["excluded_already_listed"] == 1 and r["positives_union"] == 0
    assert r["ofac_adds_already_eu_uk_listed"] == 1   # the trivial channel, now visible


def test_write_actions_drops_rows_without_imo_or_date(tmp_data):
    rows = [{"source": "EU", "action": "add", "date": None, "imo": 9074729, "name": None, "program": None,
             "via": "x", "raw": None},
            {"source": "EU", "action": "add", "date": date(2025, 1, 1), "imo": None, "name": None,
             "program": None, "via": "x", "raw": None}]
    out = L.write_actions(rows, config.PARQUET_DIR / "a.parquet")
    assert out["rows"] == 0


def test_missing_actions_file_is_a_clear_error(tmp_data):
    with pytest.raises(FileNotFoundError, match="Phase 2"):
        L.listed_as_of(date(2025, 1, 1))


def test_phase2_report_renders_from_the_probe_file(tmp_data):
    from shadowfleet.util import probes, report

    probes.write("labels", {"table": {"rows": 5031, "by_source_action": []},
                            "ofac_xml": {"rows": 1862, "imos": 1525, "by_via": {}},
                            "ofac_archive": {"rows": 1832, "imos": 1247},
                            "eu": {"rows": 674, "imos": 672, "undated": 0, "undated_celex": {}},
                            "uk": {"rows": 663, "imos": 663, "undated": 0},
                            "celex_entries": 1, "xml_vs_archive": {"imos_in_both": 900},
                            "positives_by_cutoff": [{"cutoff": "2024-12-31", "population_observed": 2681,
                                                     "population_in_scope": 2599,
                                                     "excluded_already_listed": 82, "positives_union": 164,
                                                     "positives_ofac_only": 48,
                                                     "ofac_adds_in_horizon_all": 78,
                                                     "ofac_adds_already_eu_uk_listed": 30}],
                            "positives_csv": "reports/phase2_positives.csv"})
    text = report.write_report("phase2")
    assert "| 2024-12-31 | 2681 | 2599 | 82 | 164 | 48 | 78 | 30 |" in text
    assert "R12 is real" in text


def test_celex_table_in_the_repo_is_cited_and_parseable():
    from shadowfleet.labels import labels as lab

    dates = lab.load_celex_dates()
    assert dates.get("32025R2033") == "2025-10-24"
    raw = (config.REPO_ROOT / "config" / "celex_dates.json").read_text()
    for celex in dates:
        assert f"_source_{celex}" in raw, f"{celex} has no cited source"


def test_spotcheck_sample_is_stratified_and_stable(tmp_data):
    from datetime import date as _date

    rows = ([{"source": "OFAC", "action": "add", "imo": 9000000 + i, "name": f"O{i}",
              "date": _date(2025, 1, 1), "program": "RUSSIA-EO14024"} for i in range(20)]
            + [{"source": "EU", "action": "add", "imo": 9100000 + i, "name": f"E{i}",
                "date": _date(2025, 2, 1), "program": "EU-MARE"} for i in range(20)]
            + [{"source": "UK", "action": "add", "imo": 9200000 + i, "name": f"U{i}",
                "date": _date(2025, 3, 1), "program": None} for i in range(20)]
            + [{"source": "OFAC", "action": "remove", "imo": 9300000, "name": "GONE",
                "date": _date(2025, 4, 1), "program": None}])
    first = Path(L.spotcheck_sample(rows)).read_text()
    assert first == Path(L.spotcheck_sample(rows)).read_text()  # seeded: same ten on a rerun
    lines = first.strip().splitlines()
    assert len(lines) == 11  # header + 10
    by_source = Counter(ln.split(",")[0] for ln in lines[1:])
    assert by_source == Counter({"OFAC": 4, "EU": 3, "UK": 3})
    assert "GONE" not in first  # removals are not designations
