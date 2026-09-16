"""Tests for in-memory TTL cache in LogseqAPI."""

import os
from unittest.mock import patch, MagicMock

import pytest

from logseq_cli.api import LogseqAPI


def _mock_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


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
        with patch("logseq_cli.api.requests.post", return_value=_mock_response({"ok": 1})) as mock_post:
            api.get_page("Foo")
            api.update_block("uuid-x", "new content")
            api.get_page("Foo")
        # 1 read + 1 update + 1 fresh read after invalidation
        assert mock_post.call_count == 3

    def test_non_cacheable_method_passthrough(self):
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", return_value=_mock_response({"ok": 1})) as mock_post:
            api.update_block("uuid-x", "first")
            api.update_block("uuid-x", "second")
        # mutations are never cached
        assert mock_post.call_count == 2

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
                    api.datascript_query("[:find ?x :where KAPUTT]")
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
    """

    def test_every_cacheable_method_is_actually_called_somewhere(self):
        from pathlib import Path
        from logseq_cli.api import _CACHEABLE_METHODS

        src = Path(__file__).resolve().parent.parent / "logseq_cli"
        api_src = (src / "api.py").read_text(encoding="utf-8")

        for method in _CACHEABLE_METHODS:
            # api.py names the method in the call() that wraps it; the rest of
            # the package reaches it through that wrapper.
            assert f'"{method}"' in api_src, (
                f"{method} is cacheable but api.py never calls it"
            )

    def test_get_page_properties_stays_out(self):
        from logseq_cli.api import _CACHEABLE_METHODS

        assert "logseq.Editor.getPageProperties" not in _CACHEABLE_METHODS
