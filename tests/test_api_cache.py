"""Tests for in-memory TTL cache in LogseqAPI."""

from unittest.mock import patch, MagicMock

import pytest

from logseq_cli.api import LogseqAPI


def _mock_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def _answering_writes(payload):
    """``requests.post`` for a test that updates a block: each method answers
    as Logseq does. checkEditing, which the editor gate sends before a write,
    with raw text (measured): ``false``, nobody is editing. updateBlock
    with ``null``, and getBlock with the block as last written, for the
    read that proves the update. Everything else answers ``payload``."""
    idle = MagicMock(status_code=200, text="false")
    block = {"uuid": "uuid-x", "content": ""}

    def post(url, json=None, **kwargs):
        method, args = json["method"], json["args"]
        if method == "logseq.Editor.checkEditing":
            return idle
        if method == "logseq.Editor.updateBlock":
            block["content"] = args[1]
            return _mock_response(None)
        if method == "logseq.Editor.getBlock":
            return _mock_response(dict(block))
        return _mock_response(payload)
    return post


def _requests_to(mock_post, method):
    return [c for c in mock_post.call_args_list
            if c.kwargs["json"]["method"] == f"logseq.Editor.{method}"]


@pytest.fixture(autouse=True)
def _ensure_cache_default(monkeypatch):
    """Default TTL=60s for tests, unless test overrides."""
    monkeypatch.setenv("LOGSEQ_CLI_CACHE_TTL", "60")
    yield


class TestCacheBasic:
    def test_second_read_hits_cache(self):
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", return_value=_mock_response({"ok": 1})) as mock_post:
            r1 = api.get_page("Foo")
            r2 = api.get_page("Foo")
        assert r1 == r2
        assert mock_post.call_count == 1

    def test_different_args_miss_cache(self):
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", return_value=_mock_response({"ok": 1})) as mock_post:
            api.get_page("Foo")
            api.get_page("Bar")
        assert mock_post.call_count == 2

    def test_ttl_zero_disables_cache(self, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_CACHE_TTL", "0")
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", return_value=_mock_response({"ok": 1})) as mock_post:
            api.get_page("Foo")
            api.get_page("Foo")
        assert mock_post.call_count == 2

    def test_ttl_expiry_evicts(self, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_CACHE_TTL", "60")
        api = LogseqAPI(token="x")
        # Simulate time passage
        fake_time = [1000.0]
        monkeypatch.setattr("logseq_cli.api.time.monotonic", lambda: fake_time[0])
        with patch("logseq_cli.api.requests.post", return_value=_mock_response({"ok": 1})) as mock_post:
            api.get_page("Foo")
            fake_time[0] += 30
            api.get_page("Foo")  # still in TTL
            fake_time[0] += 31  # past 60s total
            api.get_page("Foo")
        assert mock_post.call_count == 2

    def test_mutation_invalidates_cache(self):
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", side_effect=_answering_writes({"ok": 1})) as mock_post:
            api.get_page("Foo")
            api.update_block("uuid-x", "new content")
            api.get_page("Foo")
        # the read after the update is sent again, not served from the cache
        assert len(_requests_to(mock_post, "getPage")) == 2

    def test_non_cacheable_method_passthrough(self):
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", side_effect=_answering_writes({"ok": 1})) as mock_post:
            api.update_block("uuid-x", "first")
            api.update_block("uuid-x", "second")
        # mutations are never cached, and neither is the read proving each
        assert len(_requests_to(mock_post, "updateBlock")) == 2
        assert len(_requests_to(mock_post, "getBlock")) == 2

    def test_datalog_query_cacheable(self):
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", return_value=_mock_response([1, 2, 3])) as mock_post:
            api.datascript_query("[:find ?b :where [?b :block/marker]]")
            api.datascript_query("[:find ?b :where [?b :block/marker]]")
        assert mock_post.call_count == 1

    def test_no_cache_flag_bypass_via_attr(self):
        api = LogseqAPI(token="x")
        api.cache_enabled = False
        with patch("logseq_cli.api.requests.post", return_value=_mock_response({"ok": 1})) as mock_post:
            api.get_page("Foo")
            api.get_page("Foo")
        assert mock_post.call_count == 2


class TestErrorsAreNotCached:
    """A failed query must not leave a cache entry behind.

    _cache_set used to run before anything looked at the payload, so a broken
    query stayed answered as an "empty" result for up to TTL seconds, even
    after the caller fixed the input.
    """

    def test_failed_query_is_retried_not_served_from_cache(self):
        from logseq_cli.api import DatalogQueryError

        api = LogseqAPI(token="x")
        error = _mock_response({"error": "Cannot parse clause"})
        with patch("logseq_cli.api.requests.post", return_value=error) as mock_post:
            for _ in range(2):
                with pytest.raises(DatalogQueryError):
                    api.datascript_query("[:find ?x :where BROKEN]")
        assert mock_post.call_count == 2

    def test_successful_query_is_still_cached(self):
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", return_value=_mock_response([[1]])) as mock_post:
            api.datascript_query("[:find ?b :where [?b :block/marker]]")
            api.datascript_query("[:find ?b :where [?b :block/marker]]")
        assert mock_post.call_count == 1


class TestCacheableSetMatchesReality:
    """The set is a claim about which reads the tool makes. It has been wrong.

    `logseq.Editor.getPageProperties` sat in it from the initial commit and was
    never called once: the method is declared in Logseq's plugin API but the
    HTTP server answers `MethodNotExist` for it. An entry for a call that does
    not happen misleads anyone reading the set to learn what the tool does.

    That every entry is called is held in test_api_endpoint_binding.py
    (test_every_cacheable_method_has_a_wrapper), bound to a wrapper. The text
    check that stood here asked whether the name appears in api.py, which the
    registry there now guarantees: it could no longer fail.
    """

    def test_get_page_properties_stays_out(self):
        from logseq_cli.api import _CACHEABLE_METHODS

        assert "logseq.Editor.getPageProperties" not in _CACHEABLE_METHODS
