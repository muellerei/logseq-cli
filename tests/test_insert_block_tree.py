"""Tests for insert-block --tree (batch hierarchy insertion)."""

import json as _json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

import click
import pytest

from logseq_cli.cli import cli
from logseq_cli.cliinput import parse_tree_input
from logseq_cli.outlinetext import (
    has_mixed_indentation,
    normalize_indentation,
    parse_hierarchical_content,
)
from logseq_cli.api import LogseqAPI, WriteNotVerified, _block_uuid_from_result
from logseq_cli.strictinsert import (
    insert_block_tree_as_siblings,
    insert_block_tree_batched,
    insert_block_tree_with_uuids,
)

from tests.conftest import fake_api, split_runner
from tests.logseq_http_double import LogseqHttpDouble


# ---------- helpers --------------------------------------------------------

def _null_answering(monkeypatch):
    """A Logseq answering insertBlock with null and writing nothing, behind
    the real LogseqAPI: the uuid check sits in the API method,
    which a method mock would replace."""
    return LogseqHttpDouble.installed(monkeypatch, {"P": ["anchor block"]},
                                      modes={"insertBlock": "noop"})


def _make_api_with_uuid_sequence(uuids):
    """API stand-in handing out UUIDs from ``uuids`` in order.

    Backed by :class:`tests.conftest.FakeGraph` so the batch path works too:
    ``insertBatchBlock`` returns ``null`` on success, so the helper verifies the
    write by reading the parent's children back, and a bare MagicMock would
    answer that read with a MagicMock - indistinguishable from "nothing landed".
    """
    api = fake_api(uuids)
    seq = iter(uuids)

    def _append(page, content):
        return {"uuid": next(seq)}

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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        api = fake_api(["h", "c1", "c2"])
        tree = [{"content": "header", "children": [
            {"content": "child1", "children": []},
            {"content": "child2", "children": []},
        ]}]
        uuids = insert_block_tree_as_siblings(api, tree, "anchor")
        # DFS pre-order: header, child1, child2
        assert uuids == ["h", "c1", "c2"]

    def test_aborts_on_null_result(self, monkeypatch):
        # Logseq's silent failure; the real API raises on it.
        double = _null_answering(monkeypatch)
        with pytest.raises(WriteNotVerified):
            insert_block_tree_as_siblings(LogseqAPI(token="t"), [{"content": "x", "children": []}],
                                          double.uuid_of("anchor block"))

    def test_cli_after_tree_returns_uuids(self):
        api = _make_api_with_uuid_sequence(["a1", "a2", "a3"])
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--before", "anchor-uuid",
                "--tree", "- one\n- two",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["uuids"] == ["b1", "b2"]

    @pytest.mark.parametrize("args", [
        ["--tree", "- a\n- b\n\t- b1\n- c"],
        ["--content", "- a\n- b\n\t- b1\n- c"],
    ], ids=["tree", "hierarchical content"])
    def test_before_keeps_the_order_of_several_roots(self, args):
        """Measured on 0.10.15 with the code this replaced: a, b, c came out as
        c, b, a. Each root went in before the one written just ahead of it.
        The test above could not see it: its mock hands out uuids in order
        whatever the graph would have done with them."""
        from tests.conftest import PageGraph, page_graph_api
        graph = PageGraph({"P": [{"uuid": "x", "content": "x"},
                                 {"uuid": "y", "content": "y"}]})
        with patch("logseq_cli.group.LogseqAPI", return_value=page_graph_api(graph)):
            result = CliRunner().invoke(cli, ["insert-block", "--before", "y", *args])
        assert result.exit_code == 0, result.output
        assert graph.tree("P") == [("x", []), ("a", []), ("b", [("b1", [])]),
                                   ("c", []), ("y", [])]


# ---------- insert-block --dry-run -----------------------------------------

class TestInsertBlockDryRun:
    def test_dry_run_after_tree_counts_without_writing(self):
        api = MagicMock()
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--after", "anchor",
                "--content", "H:\n\t- a\n\t- b",
                "--dry-run",
            ])
        assert result.exit_code == 0, result.output
        assert "3 block(s)" in result.output
        api.insert_block.assert_not_called()


# ---------- response validation (LogseqAPI._prove_uuid) --------------------

class TestResponseValidation:
    def test_block_uuid_from_result_variants(self):
        assert _block_uuid_from_result({"uuid": "x"}) == "x"
        assert _block_uuid_from_result("y") == "y"
        assert _block_uuid_from_result(None) is None
        assert _block_uuid_from_result({}) is None

    def test_insert_raises_on_null(self, monkeypatch):
        double = _null_answering(monkeypatch)
        api = LogseqAPI(token="t")
        anchor = double.uuid_of("anchor block")
        with pytest.raises(WriteNotVerified) as exc:
            api.insert_block(anchor, "x")
        assert exc.value.fields == {"method": "insertBlock", "target": anchor,
                                    "expected": "a new block",
                                    "got": "no block uuid in the answer"}
        assert str(exc.value) == (
            f"insertBlock on block {anchor[:8]}... did not show in Logseq: "
            "expected a new block, read no block uuid in the answer.")
        # Not proven, so not counted.
        assert api.writes_landed == 0

    def test_insert_returns_the_block(self, monkeypatch):
        double = LogseqHttpDouble.installed(monkeypatch, {"P": ["anchor block"]})
        api = LogseqAPI(token="t")
        block = api.insert_block(double.uuid_of("anchor block"), "x")
        assert block["uuid"] == double.uuid_of("x")
        assert api.writes_landed == 1

    def test_a_bare_uuid_answer_becomes_a_block(self, monkeypatch):
        """Not seen from 0.10.15, but tolerated since the first check: the
        method turns it into a dict, so callers read result["uuid"] alone."""
        double = LogseqHttpDouble.installed(monkeypatch, {"P": []})
        uuid = "6500c0de-0000-4000-8000-0000000000aa"
        monkeypatch.setitem(double._handlers, "logseq.Editor.appendBlockInPage",
                            lambda args: uuid)
        assert LogseqAPI(token="t").append_block_in_page("P", "x") == {"uuid": uuid}

    def test_cli_after_flat_null_result_exits_nonzero(self, monkeypatch):
        double = _null_answering(monkeypatch)
        result = split_runner().invoke(cli, [
            "--token", "t", "insert-block", "--after", double.uuid_of("anchor block"),
            "--content", "x", "--json",
        ])
        assert result.exit_code != 0
        assert _json.loads(result.stderr)["reason"] == "write_not_verified"


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


# ---------- --first (insert at head of child list) -------------------------

class TestInsertFirstChild:
    def _api_recording(self, uuids):
        """Like _make_api_with_uuid_sequence but records (parent, content, opts)."""
        api = MagicMock()
        seq = iter(uuids)
        calls = []

        def _insert(parent_uuid, content, opts=None):
            calls.append((parent_uuid, content, opts))
            return {"uuid": next(seq)}

        api.insert_block.side_effect = _insert
        return api, calls

    def test_single_content_uses_before_true(self):
        api, calls = self._api_recording(["f1"])
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block", "--child-of", "parent", "--first",
                "--content", "head block", "--json",
            ])
        assert result.exit_code == 0, result.output
        assert calls == [("parent", "head block", {"sibling": False, "before": True})]

    def test_without_first_appends_last(self):
        api, calls = self._api_recording(["l1"])
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block", "--child-of", "parent",
                "--content", "tail block", "--json",
            ])
        assert result.exit_code == 0, result.output
        assert calls == [("parent", "tail block", {"sibling": False})]

    def test_tree_first_root_leads_rest_chain_as_siblings(self):
        """Order must be preserved: a, b, c, not reversed by repeated before=True."""
        api, calls = self._api_recording(["u-a", "u-b", "u-c"])
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block", "--child-of", "parent", "--first",
                "--tree", "- a\n- b\n- c", "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["uuids"] == ["u-a", "u-b", "u-c"]
        # first root at head of children; the rest chained after the previous one
        assert calls[0] == ("parent", "a", {"sibling": False, "before": True})
        assert calls[1] == ("u-a", "b", {"sibling": True, "before": False})
        assert calls[2] == ("u-b", "c", {"sibling": True, "before": False})

    def test_tree_first_nests_children_under_head(self):
        api, calls = self._api_recording(["u-root", "u-kid"])
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block", "--child-of", "parent", "--first",
                "--tree", "- root\n\t- kid", "--json",
            ])
        assert result.exit_code == 0, result.output
        assert calls[0] == ("parent", "root", {"sibling": False, "before": True})
        assert calls[1] == ("u-root", "kid", {"sibling": False})

    def test_first_without_child_of_is_rejected(self):
        api, _ = self._api_recording(["nope"])
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block", "--page", "SomePage", "--first",
                "--content", "x",
            ])
        assert result.exit_code == 1
        assert "--first only applies to --child-of" in result.output
        api.insert_block.assert_not_called()

    def test_silent_write_failure_aborts(self, monkeypatch):
        """API answers 200 + null -> must fail loudly, not report success."""
        double = _null_answering(monkeypatch)
        result = CliRunner().invoke(cli, [
            "--token", "t", "insert-block", "--child-of", double.uuid_of("anchor block"),
            "--first", "--content", "vanishes",
        ])
        assert result.exit_code != 0
        assert "did not show in Logseq" in result.output
        assert "Inserted" not in result.output

    def test_dry_run_reports_first_child_and_writes_nothing(self):
        api, calls = self._api_recording(["never"])
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block", "--child-of", "parent", "--first",
                "--content", "planned", "--dry-run",
            ])
        assert result.exit_code == 0, result.output
        assert "first child" in result.output
        assert calls == []


# ---------- insertBatchBlock proves itself -----------------------------------

TREE3 = [{"content": "root", "children": [{"content": "kid"}]}, {"content": "next"}]


def _batch_double(monkeypatch, *, keep=None):
    """A page with a nested and a top-level place to write, behind the real
    LogseqAPI: the batch's proof sits in the method.

    ``keep`` writes only that many roots of every batch, the partial write a
    batch can do (a malformed node is skipped while its siblings land)."""
    double = LogseqHttpDouble()
    double.add_page("P", [{"content": "parent block", "children": ["old kid"]},
                          "last top"])
    if keep is not None:
        real = double._handlers["logseq.Editor.insertBatchBlock"]
        monkeypatch.setitem(double._handlers, "logseq.Editor.insertBatchBlock",
                            lambda args: real([args[0], args[1][:keep], *args[2:]]))
    double.install(monkeypatch)
    return double, LogseqAPI(token="t")


class TestTheBatchProvesItself:
    """insertBatchBlock answers null whether it wrote all, part or nothing;
    the method reads the place the batch lands before and after the write."""

    def test_blocks_in_another_order_are_not_verified(self, monkeypatch):
        # As many blocks as sent, each text among them, but not in the order
        # sent: the proof compares them in order.
        double, api = _batch_double(monkeypatch)
        real = double._handlers["logseq.Editor.insertBatchBlock"]
        monkeypatch.setitem(double._handlers, "logseq.Editor.insertBatchBlock",
                            lambda args: real([args[0], args[1][::-1], *args[2:]]))
        with pytest.raises(WriteNotVerified) as caught:
            api.insert_batch_block(double.uuid_of("last top"),
                                   [{"content": "first"}, {"content": "second"}],
                                   {"sibling": True})
        assert (caught.value.fields["expected"], caught.value.fields["got"]) == \
            ("'first'", "'second'")
        assert api.writes_landed == 2

    @pytest.mark.parametrize("anchor,options", [
        ("parent block", {"sibling": False}),   # head of a block's children
        ("old kid", {"sibling": True}),         # after a nested sibling
        ("last top", {"sibling": True}),        # top level: the page is the parent
    ])
    def test_answers_the_new_uuids_in_preorder(self, monkeypatch, anchor, options):
        double, api = _batch_double(monkeypatch)
        new = api.insert_batch_block(double.uuid_of(anchor), TREE3, options)
        assert new == [double.uuid_of(t) for t in ("root", "kid", "next")]
        assert api.writes_landed == 3

    def test_a_partial_write_names_what_landed(self, monkeypatch):
        double, api = _batch_double(monkeypatch, keep=1)
        anchor = double.uuid_of("parent block")
        with pytest.raises(WriteNotVerified) as exc:
            api.insert_batch_block(anchor, TREE3, {"sibling": False})
        assert exc.value.fields == {"method": "insertBatchBlock", "target": anchor,
                                    "expected": "3 blocks", "got": "2"}
        # root and kid landed and stay; the count says so.
        assert api.writes_landed == 2

    def test_a_batch_that_wrote_nothing_counts_nothing(self, monkeypatch):
        double, api = _batch_double(monkeypatch)
        double.set_mode("insertBatchBlock", "noop")
        with pytest.raises(WriteNotVerified) as exc:
            api.insert_batch_block(double.uuid_of("last top"), TREE3, {"sibling": True})
        assert (exc.value.fields["expected"], exc.value.fields["got"]) == ("3 blocks", "0")
        assert api.writes_landed == 0

    def test_a_block_with_other_text_fails(self, monkeypatch):
        """The count alone passes a batch whose text came out changed, such as
        the "* " Logseq puts in front of every node anchored before a page's
        first block (#31)."""
        double, api = _batch_double(monkeypatch)
        real = double._handlers["logseq.Editor.insertBatchBlock"]

        def starred(args):
            nodes = [{**n, "content": "* " + n["content"]} for n in args[1]]
            return real([args[0], nodes, *args[2:]])
        monkeypatch.setitem(double._handlers, "logseq.Editor.insertBatchBlock", starred)
        with pytest.raises(WriteNotVerified) as exc:
            api.insert_batch_block(double.uuid_of("last top"),
                                   [{"content": "a"}, {"content": "b"}], {"sibling": True})
        assert exc.value.fields["got"] == "'* a'"

    def test_the_proof_reads_logseq_not_the_cache(self, monkeypatch):
        """The read before the write is cached; the write clears the cache, so
        the read after it reaches Logseq."""
        double, api = _batch_double(monkeypatch)
        assert api.cache_enabled
        anchor = double.uuid_of("parent block")
        api.insert_batch_block(anchor, TREE3, {"sibling": False})
        at = [m for m, _ in double.requests].index("logseq.Editor.insertBatchBlock")
        assert [a for m, a in double.requests[at:] if m == "logseq.Editor.getBlock"
                and a[0] == anchor]

    def test_a_failed_proof_still_closes_the_editor_the_batch_opened(self, monkeypatch):
        """The batch opens its last block on a visible page (measured). A proof
        that fails must not leave it open: the agent's next write to it would
        meet open_in_editor."""
        double, api = _batch_double(monkeypatch, keep=1)
        double.show_page("P")
        with pytest.raises(WriteNotVerified):
            api.insert_batch_block(double.uuid_of("parent block"), TREE3, {"sibling": False})
        assert len(double.sent("exitEditingMode")) == 1
        assert double.editing is None


class TestTheStandInIsReported:
    """--keep-ids on a page with no blocks writes after a stand-in block and
    removes it again (#31). A removal that fails must not hide the batch's
    error, nor be swallowed when the batch landed."""

    ID = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"

    def _run(self, monkeypatch, *, batch, removal="error"):
        modes = {"insertBatchBlock": batch} if batch else {}
        double = LogseqHttpDouble.installed(monkeypatch, {"Empty": []},
                                            modes={**modes, "removeBlock": removal})
        r = split_runner().invoke(cli, [
            "--token", "t", "add-note-content", "--page", "Empty", "--content",
            f"restored\nid:: {self.ID}", "--keep-ids", "--json"])
        return double, r

    def test_stand_in_removal_failure_does_not_hide_batch_error(self, monkeypatch):
        double, r = self._run(monkeypatch, batch="noop")
        assert r.exit_code == 1
        error = _json.loads(r.stderr)
        assert (error["reason"], error["method"]) == ("write_not_verified", "insertBatchBlock")
        stand_in = double.sent("removeBlock")[0][0]
        assert f"stand-in block {stand_in[:8]}..." in error["error"]
        assert "removeBlock failed" in error["error"]
        # The stand-in landed and stays.
        assert error["writes_landed"] == 1

    def test_stand_in_removal_not_shown_does_not_hide_batch_error(self, monkeypatch):
        # removeBlock proves itself: a removal Logseq answered null for and
        # did not do is added to the batch's error like an error object.
        double, r = self._run(monkeypatch, batch="noop", removal="noop")
        error = _json.loads(r.stderr)
        assert (error["reason"], error["method"]) == ("write_not_verified", "insertBatchBlock")
        stand_in = double.sent("removeBlock")[0][0]
        assert f"stand-in block {stand_in[:8]}... written for the batch stayed too: " \
               "removeBlock on block" in error["error"]
        assert error["writes_landed"] == 1

    def test_a_stand_in_removed_does_not_count(self, monkeypatch):
        # Appended and removed again: nothing of it remains, so neither
        # write counts, or a retry would be told of writes that are gone.
        double, r = self._run(monkeypatch, batch="noop", removal="execute")
        assert r.exit_code == 1
        error = _json.loads(r.stderr)
        assert (error["reason"], error["method"]) == ("write_not_verified", "insertBatchBlock")
        assert error["writes_landed"] == 0
        assert "Nothing was written." in error["error"]
        assert double.tree("Empty") == []

    def test_a_stand_in_removed_after_a_batch_that_landed_does_not_count(self, monkeypatch):
        from logseq_cli.api import LogseqAPI
        from logseq_cli.strictinsert import insert_tree_keeping_ids
        double = LogseqHttpDouble.installed(monkeypatch, {"Empty": []})
        api = LogseqAPI(token="t")
        insert_tree_keeping_ids(api, [{"content": f"restored\nid:: {self.ID}", "children": []}],
                                "page_end", "Empty")
        assert api.writes_landed == 1
        assert double.tree("Empty") == [("restored", [])]

    def test_a_stand_in_left_after_a_batch_that_landed_is_reported(self, monkeypatch):
        double, r = self._run(monkeypatch, batch=None)
        assert r.exit_code == 1
        error = _json.loads(r.stderr)
        assert (error["reason"], error["method"]) == ("logseq_error", "removeBlock")
        assert error["writes_landed"] == 2


class TestAMissingParentSaysWhatLanded:
    """The batch path checks its parent before it writes. That check can come
    after writes of the same call: the journal or page created first, the
    heading written, a parent block written by the per-block path whose
    children then go as one batch. "Nothing was written" was said whatever
    had landed; the count is the API's."""

    TREE = [{"content": "a", "children": []}, {"content": "b", "children": []}]

    def _refusal(self, landed):
        api = MagicMock(writes_landed=landed)
        api.get_block.return_value = None
        with pytest.raises(click.ClickException) as caught:
            insert_block_tree_batched(api, self.TREE, "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b")
        api.insert_batch_block.assert_not_called()
        return caught.value.message

    def test_after_writes_that_landed(self):
        message = self._refusal(2)
        assert "2 earlier write(s) in this call landed and remain" in message
        assert "Nothing was written" not in message

    def test_before_any_write(self):
        assert self._refusal(0).endswith("Nothing was written.")
