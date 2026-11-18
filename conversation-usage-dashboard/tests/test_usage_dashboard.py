import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
import usage_dashboard as ud
from fixture_api import API_KEY, FULL, ORG_ID, PARTIAL, TOTALS_ONLY, FixtureApi


@pytest.fixture
def api():
    fixture = FixtureApi().start()
    yield fixture
    fixture.stop()


@pytest.fixture
def dashboard(api):
    client = ud.UsageClient(api.url, API_KEY, ORG_ID)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ud.make_handler(client))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(url):
    try:
        with urlopen(url) as response:
            return response.status, json.loads(response.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def test_list_paginates_and_sends_bearer_get(api, dashboard):
    status, first = get(f"{dashboard}/api/conversations?per_page=20")
    assert status == 200
    assert (first["total_items"], first["total_pages"], len(first["items"])) == (
        25,
        2,
        20,
    )
    _, second = get(f"{dashboard}/api/conversations?page=2&per_page=20")
    assert [c["title"] for c in second["items"]][0] == "Synthetic run 21"
    assert {r["method"] for r in api.requests} == {"GET"}
    assert {r["auth"] for r in api.requests} == {f"Bearer {API_KEY}"}
    assert "page=2&per_page=20" in api.requests[-1]["path"]


def test_list_drops_fields_the_dashboard_does_not_need(dashboard):
    _, page = get(f"{dashboard}/api/conversations")
    assert set(page["items"][0]) == set(ud.LIST_FIELDS)


def test_search_and_status_go_to_the_api(api, dashboard):
    _, page = get(f"{dashboard}/api/conversations?search=run+1&execution_status=error")
    assert "search=run+1" in api.requests[-1]["path"]
    assert "execution_status=error" in api.requests[-1]["path"]
    assert all(c["execution_status"] == "error" for c in page["items"])


def test_tag_filter_applies_to_the_loaded_page_only(api, dashboard):
    _, page = get(f"{dashboard}/api/conversations?tag=team%3Dbeta&per_page=10")
    assert "tag" not in api.requests[-1]["path"]
    assert page["loaded"] == 10
    assert len(page["items"]) == 5
    assert all(c["tags"]["team"] == "beta" for c in page["items"])


def test_matches_tag():
    assert ud.matches_tag({"team": "a"}, "team")
    assert ud.matches_tag({"team": "a"}, "team = a")
    assert not ud.matches_tag({"team": "a"}, "team=b")
    assert not ud.matches_tag({}, "team")


def test_detail_off_returns_helpful_403(dashboard):
    status, body = get(f"{dashboard}/api/conversations/{FULL}/usage")
    assert status == 403
    assert "PATCH /api/organizations/{org_id}" in body["error"]


def test_detail_toggles_without_restart(api, dashboard):
    api.detail_enabled = True
    assert get(f"{dashboard}/api/conversations/{FULL}/usage")[0] == 200
    api.detail_enabled = False
    assert get(f"{dashboard}/api/conversations/{FULL}/usage")[0] == 403


def test_full_detail_reports_remainder_not_double_count(api, dashboard):
    api.detail_enabled = True
    _, d = get(f"{dashboard}/api/conversations/{FULL}/usage")
    assert d["totals"]["cost"] == 1.25
    assert d["unattributed"] == {
        "cost": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    }


def test_partial_detail_keeps_unknown_distinct_from_zero(api, dashboard):
    api.detail_enabled = True
    _, d = get(f"{dashboard}/api/conversations/{PARTIAL}/usage")
    assert d["completeness"] == "partial"
    assert d["incomplete_reasons"] == ["task_usage_missing", "unknown_counters"]
    assert d["unattributed"]["cache_read_tokens"] == 0
    assert d["unattributed"]["cache_write_tokens"] is None
    assert d["unattributed"]["reasoning_tokens"] is None
    assert d["unattributed"]["prompt_tokens"] == 500


def test_totals_only_has_no_remainder(api, dashboard):
    api.detail_enabled = True
    _, d = get(f"{dashboard}/api/conversations/{TOTALS_ONLY}/usage")
    assert d["completeness"] == "totals_only"
    assert d["buckets"] == [] and d["unattributed"] is None


def test_missing_and_invalid_conversation(api, dashboard):
    api.detail_enabled = True
    missing = "00000000-0000-4000-8000-999999999999"
    assert get(f"{dashboard}/api/conversations/{missing}/usage")[0] == 404
    assert get(f"{dashboard}/api/conversations/bad%2Fid/usage")[0] == 400


def test_wrong_key_gives_message_without_the_key(api):
    client = ud.UsageClient(api.url, "wrong-key", ORG_ID)
    with pytest.raises(ud.ApiError) as e:
        client.list_conversations()
    assert e.value.status == 401
    assert "wrong-key" not in e.value.message


def test_unreachable_api_message_hides_url():
    client = ud.UsageClient("http://127.0.0.1:9", API_KEY, ORG_ID, timeout=2)
    with pytest.raises(ud.ApiError) as e:
        client.list_conversations()
    assert e.value.status == 502
    assert API_KEY not in str(e.value) and "127.0.0.1" not in str(e.value)


def test_only_get_is_served(dashboard):
    from urllib.request import Request

    with pytest.raises(HTTPError) as e:
        urlopen(Request(f"{dashboard}/api/conversations", method="PATCH"))
    assert e.value.code == 501
