"""--resolve-refs reports the ((uuid)) refs whose target is gone.

The detection already existed and was thrown away: when the lookup for a ref
fails, ``_resolve_single_ref`` falls back to printing the raw ``((uuid))`` and
says nothing. So the output silently contains two different things spelled the
same way — a ref the caller chose not to resolve, and one that cannot be
resolved because the block was deleted.

Reported the way ``get-page`` reports a missing page: payload on stdout
unchanged, the notice on stderr. Not an error — a graph with deleted blocks is
still readable, and failing the read would make a whole page unavailable over
one stale ref.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


LIVE = "11111111-1111-1111-1111-111111111111"
DEAD = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def api():
    mock = MagicMock()
    mock.get_page.return_value = {"name": "page"}
    mock.get_page_linked_references.return_value = []
    mock.get_page_blocks_tree.return_value = [
        {"uuid": "b1", "content": f"see (({LIVE})) and (({DEAD}))", "children": []},
    ]

    def get_block(uuid, include_children=True):
        if uuid == LIVE:
            return {"uuid": LIVE, "content": "the live one",
                    "page": {"originalName": "Source"}}
        return None  # deleted block: Logseq answers null

    mock.get_block.side_effect = get_block
    with patch("logseq_cli.cli.LogseqAPI", return_value=mock):
        yield mock


class TestDeadRefsAreReported:
    def test_stderr_names_the_dead_ref(self, api):
        result = split_runner().invoke(
            cli, ["get-page", "--page", "Page", "--resolve-refs"])
        assert result.exit_code == 0, result.output
        assert DEAD in result.stderr, result.stderr

    def test_live_ref_is_not_reported(self, api):
        result = split_runner().invoke(
            cli, ["get-page", "--page", "Page", "--resolve-refs"])
        assert LIVE not in result.stderr, result.stderr

    def test_payload_on_stdout_is_unchanged(self, api):
        """The resolved text still goes out; only the notice is added."""
        result = split_runner().invoke(
            cli, ["get-page", "--page", "Page", "--resolve-refs"])
        assert "the live one" in result.stdout
        assert f"(({DEAD}))" in result.stdout

    def test_json_stays_parseable(self, api):
        result = split_runner().invoke(
            cli, ["get-page", "--page", "Page", "--resolve-refs", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["page"] == "Page"

    def test_json_carries_the_dead_refs(self, api):
        result = split_runner().invoke(
            cli, ["get-page", "--page", "Page", "--resolve-refs", "--json"])
        payload = json.loads(result.stdout)
        assert payload.get("dead_refs") == [DEAD]

    def test_page_without_dead_refs_says_nothing(self, api):
        api.get_page_blocks_tree.return_value = [
            {"uuid": "b1", "content": f"see (({LIVE}))", "children": []}]
        result = split_runner().invoke(
            cli, ["get-page", "--page", "Page", "--resolve-refs", "--json"])
        assert "dead" not in result.stderr.lower(), result.stderr
        assert "dead_refs" not in json.loads(result.stdout)

    def test_without_resolve_refs_nothing_is_claimed(self, api):
        """No lookups happen, so no claim about liveness can be made."""
        result = split_runner().invoke(cli, ["get-page", "--page", "Page"])
        assert "dead" not in result.stderr.lower(), result.stderr
