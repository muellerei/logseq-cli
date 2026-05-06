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
