from __future__ import annotations

import json
from pathlib import Path

import httpx

from shadowfleet import config
from shadowfleet.ingest import gfw, mid, ofac, opensanctions
from shadowfleet.util.ids import imo_valid

FIX = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- OFAC
def test_change_archive_parser_on_synthetic_fixture():
    rows, st = ofac.parse_changes_text((FIX / "ofac_changes_synthetic.txt").read_text())
    vessels = [r for r in rows if r.is_vessel]
    got = [(r.date, r.action, r.name, r.imo) for r in vessels]
    assert got == [
        ("2025-01-10", "add", "ALPHA STAR", 9074729),
        ("2025-01-10", "add", "BETA WAVE", 9176187),
        ("2025-02-27", "modify", "GAMMA RAY", 9074729),
        ("2025-02-27", "remove", "DELTA SEA", 9176187),
        ("2025-06-05", "add", "EPSILON", None),  # 1234568 fails the check digit
    ]
    alpha = vessels[0]
    assert "Linked To" in alpha.raw and alpha.programs == ["RUSSIA-EO14024"]
    assert vessels[2].old_raw and "Panama" in vessels[2].old_raw
    assert st.dates == 3 and st.vessel_entries == 5 and st.vessel_entries_with_imo == 4
    assert st.entries_without_action == 1  # the fixture preamble, counted rather than silently dropped
    assert st.actions == {"add": 3, "modify": 1, "remove": 1}
    assert not any("Page 3" in r.raw for r in rows)


def test_change_parser_inline_to_marker():
    text = ("03/01/2025:\nThe following [vessels] have been changed:\n"
            "OLD NAME Tanker; IMO 9074729 (vessel) [RUSSIA-EO14024]. -to- NEW NAME Tanker; IMO 9074729 "
            "(vessel) [RUSSIA-EO14024].\n")
    rows, _ = ofac.parse_changes_text(text)
    assert len(rows) == 1 and rows[0].name.startswith("NEW NAME") and rows[0].action == "modify"


def test_sdn_csv_vessels():
    text = ('36,"AEROCARIBBEAN AIRLINES",-0- ,"CUBA",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"Havana, Cuba."\n'
            '50001,"ALPHA STAR","vessel","RUSSIA-EO14024",-0- ,"UBCD1","Crude Oil Tanker",-0- ,-0- ,"Gabon",'
            '-0- ,"Vessel Registration Identification IMO 9074729; MMSI 626000001."\n'
            '50002,"NO IMO","vessel","RUSSIA-EO14024",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n')
    rows = ofac.parse_sdn_csv(text)
    v = ofac.sdn_vessels(rows)
    assert len(rows) == 3 and [x["imo"] for x in v] == [9074729, None]
    assert rows[0]["sdn_type"] is None


def test_advanced_xml_summary(tmp_path):
    xml = ('<Sanctions xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/'
           'ADVANCED_XML"><SanctionsEntries><SanctionsEntry ID="1"><EntryEvent ID="2"><Date><Year>2025</Year>'
           '</Date></EntryEvent></SanctionsEntry></SanctionsEntries></Sanctions>')
    p = tmp_path / "a.xml"
    p.write_text(xml)
    s = ofac.advanced_xml_summary(p)
    assert s["element_counts"]["SanctionsEntry"] == 1 and "2025" in s["example_entry_event"]


# ---------------------------------------------------------------- OpenSanctions
def test_maritime_summary_and_candidates():
    csv_text = ("type,caption,imo,risk,countries,flag,mmsi,id,url,datasets,aliases\n"
                "Vessel,ALPHA,IMO9074729,sanction,ru,ga,626000001,a,u,us_ofac_sdn;eu_journal_sanctions,\n"
                "Vessel,BETA,9176187,sanction.linked,ru,cm,,b,u,gb_fcdo_sanctions,\n"
                "Vessel,BAD,1234568,sanction,ru,,,c,u,us_ofac_sdn,\n"
                "Organization,CO,,sanction,ru,,,d,u,us_ofac_sdn,\n")
    summary, cands = opensanctions.maritime_summary(csv_text)
    assert summary["rows"] == 4 and summary["rows_with_valid_imo"] == 2
    assert cands == ["9074729"]
    assert summary["dataset_counts"]["us_ofac_sdn"] == 3


def test_ftm_vessel_sanction_summary(tmp_path):
    lines = [
        {"id": "v1", "schema": "Vessel", "properties": {"imoNumber": ["9074729"]}},
        {"id": "v2", "schema": "Vessel", "properties": {}},
        {"id": "s1", "schema": "Sanction", "properties": {"entity": ["v1"], "programId": ["EU-MARE"],
                                                         "listingDate": ["2025-05-20"]}},
        {"id": "s2", "schema": "Sanction", "properties": {"entity": ["v2"], "programId": ["EU-UKR"]}},
        {"id": "s3", "schema": "Sanction", "properties": {"entity": ["x"], "programId": ["EU-MARE"]}},
    ]
    p = tmp_path / "e.ftm.json"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    s = opensanctions.vessel_sanction_summary(p, "EU-MARE")
    assert s["vessels"] == 2 and s["vessels_with_imo"] == 1
    assert s["vessel_sanctions_EU-MARE"] == 1 and s["vessel_sanctions_with_date"] == 1
    index = {"resources": [{"name": "entities.ftm.json", "url": "u"}]}
    assert opensanctions.resource_url(index, "ftm.json") == "u"


# ---------------------------------------------------------------- MID
def test_mid_parse_and_iso3():
    html = ("<table><tr><th>Digit</th><th>Allocated to</th></tr>"
            "<tr><td>273</td><td>Russian Federation</td></tr>"
            "<tr><td>626</td><td>Gabonese Republic</td></tr>"
            "<tr><td>511</td><td>Palau (Republic of)</td></tr>"
            "<tr><td>538</td><td>Marshall Islands (Republic of the)</td></tr></table>")
    rows = mid.parse_itu_html(html)
    assert rows[0] == (273, "Russian Federation")
    assert [mid.to_iso3(n) for _, n in rows] == ["RUS", "GAB", "PLW", "MHL"]


def test_flag_from_mmsi(tmp_path, monkeypatch):
    f = tmp_path / "mid.csv"
    f.write_text("# source: test\nmid,itu_name,iso3\n273,Russian Federation,RUS\n")
    monkeypatch.setattr(config, "MID_CSV", f)
    mid.load.cache_clear()
    assert mid.flag_from_mmsi(273123456) == "RUS"
    assert mid.flag_from_mmsi(111273000) is None and mid.flag_from_mmsi(12345) is None
    mid.load.cache_clear()


# ---------------------------------------------------------------- GFW
def _gfw_handler(log):
    def handler(req: httpx.Request) -> httpx.Response:
        log.append(req)
        assert req.headers["Authorization"] == "Bearer tok"
        if req.url.path.endswith("/vessels/search"):
            return httpx.Response(200, json={"entries": [{
                "selfReportedInfo": [{"id": "vid-1", "imo": "9074729", "transmissionDateFrom": "2020-01-01",
                                      "transmissionDateTo": "2025-01-01"},
                                     {"id": "vid-2", "imo": "9074729"}],
                "combinedSourcesInfo": [{"shiptypes": [{"name": "BUNKER_OR_TANKER"}]}]}]})
        if req.url.path.endswith("/events"):
            if len([r for r in log if r.url.path.endswith("/events")]) == 1:
                return httpx.Response(429, headers={"Retry-After": "0", "X-RateLimit-Remaining": "0"})
            off = int(req.url.params["offset"])
            if off == 0:
                return httpx.Response(200, json={"entries": [{"id": "e1"}, {"id": "e2"}], "nextOffset": 2})
            return httpx.Response(200, json={"entries": [{"id": "e3"}], "nextOffset": None})
        return httpx.Response(404)
    return handler


def test_gfw_client_cache_retry_pagination(tmp_data):
    log: list = []
    c = gfw.GfwClient(token="tok", transport=httpx.MockTransport(_gfw_handler(log)), sleep=lambda s: None)
    resp = c.search_vessels("9074729")
    assert gfw.vessel_ids_from_search(resp, "9074729") == ["vid-1", "vid-2"]
    assert gfw.vessel_ids_from_search(resp, "9999999") == []
    assert gfw.identity_is_dated(resp) == {"identity_records": 2, "with_transmission_dates": 1}
    assert gfw.shiptypes(resp) == ["BUNKER_OR_TANKER"]
    ev = c.events(["vid-1"], "GAP", "2025-01-01", "2025-12-31")
    assert [e["id"] for e in ev] == ["e1", "e2", "e3"]
    q = log[-1].url.params
    assert q["vessels[0]"] == "vid-1" and q["datasets[0]"] == config.GFW_DATASETS["GAP"]
    n_live = len(log)
    # second time: all from cache, and the token is not in any cache file
    c2 = gfw.GfwClient(token="tok", transport=httpx.MockTransport(_gfw_handler(log)), offline=True)
    assert len(c2.events(["vid-1"], "GAP", "2025-01-01", "2025-12-31")) == 3
    assert len(log) == n_live
    for f in config.GFW_CACHE_DIR.rglob("*.json"):
        assert "tok" not in f.read_text().replace("token", "")
    assert (config.LOG_DIR / "gfw_ratelimit.jsonl").read_text().count("x-ratelimit-remaining") >= 1


def test_gfw_cache_key_ignores_token_and_orders_params():
    a = gfw.cache_key("get", "events", {"b": "1", "a": "2"}, None)
    b = gfw.cache_key("GET", "events", {"a": "2", "b": "1"}, None)
    assert a == b and imo_valid(9074729)


def test_vessel_ids_match_imo_in_registry_with_prefix():
    resp = {"entries": [{"selfReportedInfo": [{"id": "v9"}], "registryInfo": [{"imo": "IMO 9074729"}]},
                        {"selfReportedInfo": [{"id": "other", "imo": 9176187}]}]}
    assert gfw.vessel_ids_from_search(resp, "9074729") == ["v9"]
    assert gfw.vessel_ids_from_search(resp, "9176187") == ["other"]


def test_gfw_candidates_prefer_ofac_russia_tankers(tmp_data):
    from shadowfleet import cli

    sdn = config.HTTP_CACHE_DIR / "ofac" / "sdn.csv"
    sdn.parent.mkdir(parents=True)
    sdn.write_text(
        '1,"A","vessel","RUSSIA-EO14024",-0- ,-0- ,"Crude Oil Tanker",-0- ,-0- ,-0- ,-0- ,"IMO 9074729."\n'
        '2,"B","vessel","IRAN",-0- ,-0- ,"Crude Oil Tanker",-0- ,-0- ,-0- ,-0- ,"IMO 9176187."\n'
        '3,"C","vessel","RUSSIA-EO14024",-0- ,-0- ,"General Cargo",-0- ,-0- ,-0- ,-0- ,"IMO 9176187."\n')
    assert cli._gfw_candidates() == ["9074729"]


def test_pdf_to_text_streams_pages(tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    pdf_path = tmp_path / "s.pdf"
    with PdfPages(pdf_path) as pdf:
        for line in ["01/10/2025:", "ALPHA STAR Tanker; IMO 9074729 (vessel) [RUSSIA-EO14024]."]:
            fig = plt.figure()
            fig.text(0.1, 0.5, line)
            pdf.savefig(fig)
            plt.close(fig)
    out = tmp_path / "s.txt"
    text = ofac.pdf_to_text(pdf_path, out)
    assert "01/10/2025" in text and "IMO 9074729" in text and out.exists()
    assert "IMO 9074729" in ofac.pdf_to_text(pdf_path)
