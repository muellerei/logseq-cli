"""get-backlinks --with-context: which block does the linking, not just which page.

The linking blocks already arrive in the API response — getPageLinkedReferences
answers ``[page, [block, ...]]`` pairs — and ``_extract_backlink_names`` drops
everything but the name. A caller that wants to know *why* a page links back has
to fetch and search each page again, which is the read the response had already
paid for.

Behind a flag, because the plain listing is a documented shape: the tests in
test_get_backlinks_batch pin it, and a page with many backlinks would otherwise
grow the default output by the length of every linking block.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli


def _api(refs_by_page):
    """refs_by_page: {page: [(linking_page, [block_content, ...]), ...]}"""
    api = MagicMock()

    def _refs(page_name):
        return [[{"originalName": ln},
                 [{"uuid": f"u{i}", "content": c} for i, c in enumerate(blocks)]]
                for ln, blocks in refs_by_page.get(page_name, [])]

    api.get_page_linked_references.side_effect = _refs
    return api


def _run(args, api):
    with patch("logseq_cli.cli.LogseqAPI", return_value=api):
        return CliRunner().invoke(cli, args)


class TestWithContext:
    def test_json_carries_the_linking_blocks(self):
        api = _api({"Alice": [("Journal", ["met [[Alice]] at noon"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context", "--json"], api)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        entry = data["backlinks"][0]
        assert entry["page"] == "Journal"
        assert entry["blocks"][0]["content"] == "met [[Alice]] at noon"

    def test_plain_output_shows_the_block(self):
        api = _api({"Alice": [("Journal", ["met [[Alice]] at noon"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context"], api)
        assert result.exit_code == 0, result.output
        assert "met [[Alice]] at noon" in result.output

    def test_block_uuid_is_included(self):
        """So a caller can act on the block, not just read it."""
        api = _api({"Alice": [("Journal", ["met [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context", "--json"], api)
        data = json.loads(result.output)
        assert data["backlinks"][0]["blocks"][0]["uuid"] == "u0"

    def test_several_blocks_on_one_page(self):
        api = _api({"Alice": [("Journal", ["first [[Alice]]", "second [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context", "--json"], api)
        data = json.loads(result.output)
        assert len(data["backlinks"][0]["blocks"]) == 2

    def test_output_is_bounded(self):
        """Same promise as every other read: a busy page must not blow the budget."""
        many = [f"mention {i} [[Alice]]" for i in range(50)]
        api = _api({"Alice": [("Journal", many)]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "3", "--json"], api)
        data = json.loads(result.output)
        assert len(data["backlinks"][0]["blocks"]) == 3
        assert data["backlinks"][0]["withheld"] == 47

    def test_properties_blocks_are_not_context(self):
        """A key:: value block is the page's own metadata, not a mention."""
        api = _api({"Alice": [("Journal", ["tags:: people", "met [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context", "--json"], api)
        data = json.loads(result.output)
        contents = [b["content"] for b in data["backlinks"][0]["blocks"]]
        assert "met [[Alice]]" in contents
        assert "tags:: people" not in contents


class TestWithoutContextIsUnchanged:
    """The default shape is documented and pinned elsewhere; this guards it here."""

    def test_default_json_is_a_list_of_names(self):
        api = _api({"Alice": [("Journal", ["met [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--json"], api)
        data = json.loads(result.output)
        assert data["backlinks"] == ["Journal"]

    def test_default_plain_output_has_no_block_text(self):
        api = _api({"Alice": [("Journal", ["met [[Alice]] at noon"])]})
        result = _run(["get-backlinks", "--name", "Alice"], api)
        assert "at noon" not in result.output
        assert "<- Journal" in result.output
