"""Tests for insert-block --tree (batch hierarchy insertion)."""

import json as _json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli
from logseq_cli.helpers import (
    parse_tree_input,
    insert_block_tree_with_uuids,
)


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
