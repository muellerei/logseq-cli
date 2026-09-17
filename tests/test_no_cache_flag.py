"""``--no-cache`` must reach the client, not just exist as a flag.

The group callback translates the flag into ``api.cache_enabled = False``.
Replacing that line with ``pass`` left all 842 tests green: the one test on the
subject sets the attribute itself and never runs the wiring, so the flag could
stop working without a single failure.

What the user loses is specific. ``--no-cache`` is what you reach for after
changing something in Logseq — the flag exists to get past a stale read. If it
does nothing, the tool answers from the cache and reports the state from before
the change, with no indication that it did.

These tests drive the real CLI and count outgoing requests, because the claim
is about what goes to the network, not about an attribute.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner


@pytest.fixture(autouse=True)
def _cache_on(monkeypatch):
    """The cache is only meaningful with a TTL; make it explicit here."""
    monkeypatch.setenv("LOGSEQ_CLI_CACHE_TTL", "60")


def _response(payload):
    resp = MagicMock(status_code=200)
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def _run(args):
    """Invoke the CLI for real, counting the HTTP calls it makes."""
    blocks = [{"content": "some text", "uuid": "u1", "children": []}]
    with patch("logseq_cli.api.requests.post",
               return_value=_response(blocks)) as post:
        result = split_runner().invoke(cli, ["--token", "t"] + args)
    return result, post


class TestFlagReachesTheClient:
    def test_flag_sets_cache_enabled_false(self):
        """The wiring itself, read off the context the group builds."""
        seen = {}

        class _Spy:
            def __init__(self, **kwargs):
                self.cache_enabled = True
                seen["api"] = self

        with patch("logseq_cli.group.LogseqAPI", _Spy):
            split_runner().invoke(cli, ["--token", "t", "--no-cache", "get-page",
                                        "--name", "Foo", "--no-backlinks"])
        assert seen["api"].cache_enabled is False

    def test_without_the_flag_the_cache_stays_on(self):
        seen = {}

        class _Spy:
            def __init__(self, **kwargs):
                self.cache_enabled = True
                seen["api"] = self

        with patch("logseq_cli.group.LogseqAPI", _Spy):
            split_runner().invoke(cli, ["--token", "t", "get-page",
                                        "--name", "Foo", "--no-backlinks"])
        assert seen["api"].cache_enabled is True


class TestRepeatedReadsGoOutAgain:
    """The user-visible half: a second read must not be served from memory."""

    def test_same_page_twice_without_flag_hits_the_cache(self):
        """Baseline — without the flag the second read is served locally."""
        from logseq_cli.api import LogseqAPI

        api = LogseqAPI(token="t")
        with patch("logseq_cli.api.requests.post",
                   return_value=_response([{"content": "x"}])) as post:
            api.get_page("Foo")
            api.get_page("Foo")
        assert post.call_count == 1

    def test_same_page_twice_with_flag_goes_out_twice(self):
        """With --no-cache both reads reach Logseq, so a change is visible."""
        from logseq_cli.api import LogseqAPI

        seen = {}

        class _Spy(LogseqAPI):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                seen["api"] = self

        with patch("logseq_cli.group.LogseqAPI", _Spy):
            with patch("logseq_cli.api.requests.post",
                       return_value=_response([{"content": "x"}])) as post:
                split_runner().invoke(cli, ["--token", "t", "--no-cache",
                                            "get-page", "--name", "Foo",
                                            "--no-backlinks"])
                before = post.call_count
                api = seen["api"]
                api.get_page("Foo")
                api.get_page("Foo")
                assert post.call_count == before + 2, (
                    "--no-cache left the cache on: the second read was served "
                    "from memory"
                )


class TestFlagIsDocumented:
    """A flag that works but is not in --help cannot be reached on purpose."""

    def test_no_cache_appears_in_help(self):
        result = split_runner().invoke(cli, ["--help"])
        assert "--no-cache" in result.output
