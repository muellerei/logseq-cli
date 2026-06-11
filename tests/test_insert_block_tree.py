"""Tests for insert-block --tree (batch hierarchy insertion)."""

import json as _json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

import pytest

from logseq_cli.cli import cli
from logseq_cli.helpers import (
    parse_tree_input,
    insert_block_tree_with_uuids,
    insert_block_tree_as_siblings,
    block_uuid_from_result,
    require_insert,
    has_mixed_indentation,
    normalize_indentation,
    parse_hierarchical_content,
)
import click


# ---------- helpers --------------------------------------------------------

def _make_api_with_uuid_sequence(uuids):
    """Each insert_block / append_block_in_page returns next UUID from sequence."""
    api = MagicMock()
    seq = iter(uuids)

    def _insert(parent_uuid, content, opts=None):
        return {"uuid": next(seq)}

    def _append(page, content):
        return {"uuid": next(seq)}

    api.insert_block.side_effect = _insert
    api.append_block_in_page.side_effect = _append
    return api


# ---------- parse_tree_input -----------------------------------------------

class TestParseTreeInput:
    def test_tab_indented_simple(self):
        tree = parse_tree_input("- one\n- two")
        assert len(tree) == 2
        assert tree[0]["content"] == "one"
        assert tree[1]["content"] == "two"

    def test_tab_indented_nested(self):
        tree = parse_tree_input("- root\n\t- child")
        assert len(tree) == 1
        assert tree[0]["content"] == "root"
        assert tree[0]["children"][0]["content"] == "child"

    def test_json_array_simple(self):
        raw = _json.dumps([{"content": "a"}, {"content": "b"}])
        tree = parse_tree_input(raw)
        assert len(tree) == 2
        assert tree[0]["content"] == "a"
        assert tree[1]["content"] == "b"
        assert tree[0]["children"] == []

    def test_json_array_nested(self):
        raw = _json.dumps([
            {"content": "root", "children": [
                {"content": "child", "children": [
                    {"content": "grandchild"}
                ]}
            ]}
        ])
        tree = parse_tree_input(raw)
        assert tree[0]["content"] == "root"
        assert tree[0]["children"][0]["content"] == "child"
        assert tree[0]["children"][0]["children"][0]["content"] == "grandchild"

    def test_empty_string_yields_empty(self):
        assert parse_tree_input("") == []

    def test_whitespace_only_yields_empty(self):
        assert parse_tree_input("   \n\n  ") == []

    def test_auto_detect_json_with_leading_whitespace(self):
        raw = "  \n  " + _json.dumps([{"content": "x"}])
        tree = parse_tree_input(raw)
        assert tree[0]["content"] == "x"


# ---------- insert_block_tree_with_uuids -----------------------------------

class TestInsertBlockTreeWithUuids:
    def test_returns_uuids_in_tree_order_flat(self):
        api = _make_api_with_uuid_sequence(["u1", "u2", "u3"])
        tree = [
            {"content": "a", "children": []},
            {"content": "b", "children": []},
            {"content": "c", "children": []},
        ]
        uuids = insert_block_tree_with_uuids(api, tree, "parent")
        assert uuids == ["u1", "u2", "u3"]

    def test_returns_uuids_in_tree_order_nested(self):
        api = _make_api_with_uuid_sequence(["u1", "u2", "u3"])
        tree = [
            {"content": "root", "children": [
                {"content": "child", "children": [
                    {"content": "grand", "children": []}
                ]}
            ]}
        ]
        uuids = insert_block_tree_with_uuids(api, tree, "parent")
        # DFS pre-order: root, child, grand
        assert uuids == ["u1", "u2", "u3"]

    def test_mixed_siblings_and_children(self):
        api = _make_api_with_uuid_sequence(["A", "B", "C", "D", "E"])
        tree = [
            {"content": "p1", "children": [
                {"content": "p1c1", "children": []},
                {"content": "p1c2", "children": []},
            ]},
            {"content": "p2", "children": [
                {"content": "p2c1", "children": []},
            ]},
        ]
        uuids = insert_block_tree_with_uuids(api, tree, "parent")
        assert uuids == ["A", "B", "C", "D", "E"]


# ---------- CLI integration ------------------------------------------------

class TestInsertBlockCLITreeFlag:
    def test_tree_under_child_of_returns_uuids(self):
        api = _make_api_with_uuid_sequence(["u1", "u2", "u3"])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--child-of", "parent-uuid",
                "--tree", "- a\n- b\n\t- c",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["uuids"] == ["u1", "u2", "u3"]
        assert data["blocks_added"] == 3

    def test_tree_with_json_input(self):
        api = _make_api_with_uuid_sequence(["x1", "x2"])
        tree_json = _json.dumps([
            {"content": "alpha", "children": [{"content": "beta"}]},
        ])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--child-of", "parent",
                "--tree", tree_json,
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["uuids"] == ["x1", "x2"]

    def test_tree_with_page_top_level(self):
        api = _make_api_with_uuid_sequence(["p1", "p2"])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--page", "MyPage",
                "--top-level",
                "--tree", "- one\n- two",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["uuids"] == ["p1", "p2"]

    def test_tree_only_root_block(self):
        api = _make_api_with_uuid_sequence(["solo"])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--child-of", "p",
                "--tree", "- only",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["uuids"] == ["solo"]

    def test_tree_empty_input_is_error(self):
        api = _make_api_with_uuid_sequence([])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--child-of", "p",
                "--tree", "",
            ])
        # Empty tree should fail with non-zero exit and a clear message
        assert result.exit_code != 0

    def test_tree_plain_text_output_lists_uuids(self):
        api = _make_api_with_uuid_sequence(["u1", "u2"])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--child-of", "parent",
                "--tree", "- a\n- b",
            ])
        assert result.exit_code == 0, result.output
        assert "u1" in result.output
        assert "u2" in result.output


# ---------- insert_block_tree_as_siblings ----------------------------------

class TestInsertBlockTreeAsSiblings:
    def test_siblings_after_anchor_flat(self):
        api = _make_api_with_uuid_sequence(["s1", "s2"])
        calls = []
        api.insert_block.side_effect = lambda anchor, content, opts: (
            calls.append((anchor, content, opts)) or {"uuid": f"s{len(calls)}"}
        )
        tree = [{"content": "a", "children": []}, {"content": "b", "children": []}]
        uuids = insert_block_tree_as_siblings(api, tree, "anchor", before=False)
        assert uuids == ["s1", "s2"]
        # first node sibling of anchor; second node sibling of first
        assert calls[0][0] == "anchor" and calls[0][2] == {"sibling": True, "before": False}
        assert calls[1][0] == "s1" and calls[1][2] == {"sibling": True, "before": False}

    def test_siblings_with_children_nest_under_each_top_node(self):
        seq = iter(["h", "c1", "c2"])
        api = MagicMock()
        api.insert_block.side_effect = lambda anchor, content, opts: {"uuid": next(seq)}
        tree = [{"content": "header", "children": [
            {"content": "child1", "children": []},
            {"content": "child2", "children": []},
        ]}]
        uuids = insert_block_tree_as_siblings(api, tree, "anchor")
        # DFS pre-order: header, child1, child2
        assert uuids == ["h", "c1", "c2"]

    def test_strict_aborts_on_null_result(self):
        api = MagicMock()
        api.insert_block.return_value = None  # silent API failure
        with pytest.raises(click.ClickException):
            insert_block_tree_as_siblings(api, [{"content": "x", "children": []}], "anchor")

    def test_cli_after_tree_returns_uuids(self):
        api = _make_api_with_uuid_sequence(["a1", "a2", "a3"])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--after", "anchor-uuid",
                "--tree", "- header\n\t- c1\n\t- c2",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["blocks_added"] == 3
        assert data["uuids"] == ["a1", "a2", "a3"]

    def test_cli_before_tree_works(self):
        api = _make_api_with_uuid_sequence(["b1", "b2"])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--before", "anchor-uuid",
                "--tree", "- one\n- two",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["uuids"] == ["b1", "b2"]


# ---------- insert-block --dry-run -----------------------------------------

class TestInsertBlockDryRun:
    def test_dry_run_after_tree_counts_without_writing(self):
        api = MagicMock()
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--after", "anchor",
                "--tree", "- H\n\t- c1\n\t- c2",
                "--dry-run", "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["dry_run"] is True
        assert data["blocks"] == 3
        # nothing was written
        api.insert_block.assert_not_called()
        api.append_block_in_page.assert_not_called()

    def test_dry_run_child_of_flat_content(self):
        api = MagicMock()
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--child-of", "parent",
                "--content", "single block",
                "--dry-run", "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["blocks"] == 1
        api.insert_block.assert_not_called()

    def test_dry_run_after_hierarchical_content(self):
        api = MagicMock()
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--after", "anchor",
                "--content", "H:\n\t- a\n\t- b",
                "--dry-run",
            ])
        assert result.exit_code == 0, result.output
        assert "3 block(s)" in result.output
        api.insert_block.assert_not_called()


# ---------- response validation (require_insert) ---------------------------

class TestResponseValidation:
    def test_block_uuid_from_result_variants(self):
        assert block_uuid_from_result({"uuid": "x"}) == "x"
        assert block_uuid_from_result("y") == "y"
        assert block_uuid_from_result(None) is None
        assert block_uuid_from_result({}) is None

    def test_require_insert_raises_on_null(self):
        with pytest.raises(click.ClickException):
            require_insert(None, "a block")

    def test_require_insert_returns_uuid(self):
        assert require_insert({"uuid": "ok"}, "a block") == "ok"

    def test_cli_after_flat_null_result_exits_nonzero(self):
        api = MagicMock()
        api.insert_block.return_value = None  # Logseq returns null for bad anchor
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--after", "00000000-0000-0000-0000-000000000000",
                "--content", "x",
            ])
        assert result.exit_code != 0
        assert "no block UUID" in result.output or "did not create" in result.output


# ---------- mixed-indentation guard ----------------------------------------

class TestMixedIndentation:
    def test_has_mixed_indentation(self):
        assert has_mixed_indentation("a\n\t  \t- b") is True
        assert has_mixed_indentation("a\n\t\t- b") is False
        assert has_mixed_indentation("a\n    - b") is False

    def test_normalize_indentation_tabs(self):
        assert normalize_indentation("\t  \t- x") == "\t\t\t- x"
        assert normalize_indentation("    - y") == "\t\t- y"
        assert normalize_indentation("- z") == "- z"

    def test_parse_hierarchical_normalizes_mixed(self):
        tree = parse_hierarchical_content("### H:\n\t  \t- a\n\t  \t- b")
        assert len(tree) == 1
        kids = [c["content"] for c in tree[0]["children"]]
        assert kids == ["a", "b"]
