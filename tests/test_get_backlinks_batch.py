"""Tests for get-backlinks: single-page backward compat + batch (--name repeatable)."""
import json
from unittest.mock import MagicMock, patch
from click.testing import CliRunner

from logseq_cli.cli import cli


def _api_with_refs(refs_by_page):
    """Mock LogseqAPI whose get_page_linked_references returns per-page refs.

    refs_by_page: {page_name: [linking_page_name, ...]}
    Native API shape per ref: [page_dict, [block, ...]]; we only need originalName.
    """
    api = MagicMock()

    def _refs(page_name):
        linkers = refs_by_page.get(page_name, [])
        return [[{"originalName": ln}, []] for ln in linkers]

    api.get_page_linked_references.side_effect = _refs
    return api


def _run(args, api):
    runner = CliRunner()
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return runner.invoke(cli, args)


class TestSingleBackwardCompat:
    def test_single_text_output_unchanged(self):
        api = _api_with_refs({"Alice": ["2023-02-13, Monday", "2023-02-20, Monday"]})
        result = _run(["get-backlinks", "--name", "Alice"], api)
        assert result.exit_code == 0
        assert "Backlinks to 'Alice' (2):" in result.output
        assert "  <- 2023-02-13, Monday" in result.output

    def test_single_json_is_object_not_array(self):
        # Backward compat: a single page must stay a dict {page, backlinks, count}.
        api = _api_with_refs({"Alice": ["A", "B"]})
        result = _run(["get-backlinks", "--name", "Alice", "--json"], api)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert set(data.keys()) == {"page", "backlinks", "count"}
        assert data["count"] == 2
        assert data["backlinks"] == ["A", "B"]

    def test_single_no_backlinks_message(self):
        api = _api_with_refs({"Empty": []})
        result = _run(["get-backlinks", "--name", "Empty"], api)
        assert "No backlinks found for 'Empty'." in result.output


class TestBatch:
    def test_batch_evaluates_all_pages_not_just_last(self):
        # Regression: previously --name A --name B silently kept only B.
        api = _api_with_refs({"Alice": ["X"], "Bob": ["Y", "Z"]})
        result = _run(["get-backlinks", "--name", "Alice", "--name", "Bob"], api)
        assert result.exit_code == 0
        assert "Backlinks to 'Alice' (1):" in result.output
        assert "Backlinks to 'Bob' (2):" in result.output

    def test_batch_json_is_array_with_all_pages(self):
        api = _api_with_refs({"Alice": ["X"], "Bob": ["Y", "Z"]})
        result = _run(["get-backlinks", "--name", "Alice", "--name", "Bob", "--json"], api)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert [d["page"] for d in data] == ["Alice", "Bob"]
        assert [d["count"] for d in data] == [1, 2]
