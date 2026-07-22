"""pipeline/ferc_client.py — paging, retries, payload shapes (mocked HTTP)."""
from unittest.mock import MagicMock

import pytest
import requests

from pipeline.ferc_client import FercClient, FercClientError, docinfo_url


def make_client() -> tuple[FercClient, MagicMock]:
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    client = FercClient(session=session, pace_seconds=0)
    return client, session


def json_response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def hit(acc: str) -> dict:
    return {"acesssionNumber": acc, "description": f"filing {acc}"}


def test_search_single_page():
    client, session = make_client()
    session.request.return_value = json_response(
        {"success": True, "totalHits": 2, "searchHits": [hit("a"), hit("b")]})
    hits = client.search_docket("ER26-925")
    assert [h["acesssionNumber"] for h in hits] == ["a", "b"]
    assert session.request.call_count == 1
    body = session.request.call_args.kwargs["json"]
    assert body["docketSearches"] == [{"docketNumber": "ER26-925",
                                       "subDocketNumbers": []}]
    assert body["curPage"] == 1


def test_search_pages_until_total_hits():
    client, session = make_client()
    page1 = json_response({"success": True, "totalHits": 150,
                           "searchHits": [hit(f"p1-{i}") for i in range(100)]})
    page2 = json_response({"success": True, "totalHits": 150,
                           "searchHits": [hit(f"p2-{i}") for i in range(50)]})
    session.request.side_effect = [page1, page2]
    hits = client.search_docket("ER26-925")
    assert len(hits) == 150
    assert session.request.call_count == 2
    assert session.request.call_args_list[1].kwargs["json"]["curPage"] == 2


def test_search_stops_on_empty_batch_even_if_total_lies():
    """Defensive: a totalHits larger than what the API actually returns
    must not loop forever."""
    client, session = make_client()
    page1 = json_response({"success": True, "totalHits": 999,
                           "searchHits": [hit("only")]})
    page2 = json_response({"success": True, "totalHits": 999,
                           "searchHits": []})
    session.request.side_effect = [page1, page2]
    assert len(client.search_docket("ER26-925")) == 1


def test_search_api_error_raises():
    client, session = make_client()
    session.request.return_value = json_response(
        {"success": False, "errorMessage": "boom", "searchHits": None})
    with pytest.raises(FercClientError, match="boom"):
        client.search_docket("ER26-925")


def test_5xx_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("pipeline.ferc_client.time.sleep", lambda s: None)
    client, session = make_client()
    session.request.side_effect = [
        json_response({}, status=520),
        json_response({}, status=502),
        json_response({"DataList": [{"Accession_Number": "x"}]}),
    ]
    assert client.get_doc_info("20260101-0001") == {"Accession_Number": "x"}
    assert session.request.call_count == 3


def test_retries_exhausted_raises(monkeypatch):
    monkeypatch.setattr("pipeline.ferc_client.time.sleep", lambda s: None)
    client, session = make_client()
    session.request.side_effect = requests.ConnectionError("dead")
    with pytest.raises(FercClientError, match="failed after 5 attempts"):
        client.get_file_list("20260101-0001")
    assert session.request.call_count == 5


def test_download_payload_matches_spa():
    """The payload mirrors what elibrary.ferc.gov's own UI sends: only
    fileidLst populated."""
    client, session = make_client()
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"%PDF-1.5 fake"
    resp.raise_for_status.return_value = None
    session.request.return_value = resp
    out = client.download_file("ABC-GUID", "20260101-0001", file_type="PDF")
    assert out.startswith(b"%PDF")
    body = session.request.call_args.kwargs["json"]
    assert body == {
        "FileType": "",
        "accession": "",
        "fileid": 0,
        "FileIDAll": "",
        "fileidLst": ["ABC-GUID"],
        "Islegacy": False,
    }


def test_download_gets_deeper_retry_budget(monkeypatch):
    """FERC's origin 520s intermittently on big files; their own SPA
    retries through it. Downloads must survive a 520 streak longer than
    the metadata retry budget."""
    monkeypatch.setattr("pipeline.ferc_client.time.sleep", lambda s: None)
    client, session = make_client()
    ok = MagicMock()
    ok.status_code = 200
    ok.content = b"%PDF ok"
    ok.raise_for_status.return_value = None
    session.request.side_effect = [json_response({}, status=520)] * 5 + [ok]
    assert client.download_file("GUID") == b"%PDF ok"
    assert session.request.call_count == 6


def test_docinfo_url():
    assert docinfo_url("20260101-0001") == (
        "https://elibrary.ferc.gov/eLibrary/docinfo?"
        "accession_number=20260101-0001")
