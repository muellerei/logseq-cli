"""Tests for --keep-ids: id:: properties survive a tree insert, or are announced.

The defect this pins: a tree carrying ``id:: <uuid>`` was written with fresh
UUIDs and reported success, so every ``((uuid))`` elsewhere in the graph that
pointed at the original block dangled. The existing verification could not see
it — it counts new blocks, and the count was right.
"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli
from logseq_cli.helpers import (
    block_id_property,
    collect_block_ids,
    invalid_block_ids,
    insert_block_tree_with_uuids,
    insert_block_tree_as_siblings,
)

VALID = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
VALID2 = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5c"


# ---------- extraction -----------------------------------------------------

class TestIdExtraction:
    def test_reads_the_id_property_from_content(self):
        assert block_id_property(f"text\nid:: {VALID}") == VALID

    def test_content_without_an_id_yields_empty(self):
        assert block_id_property("just text\ncollapsed:: true") == ""

    def test_a_uuid_inside_prose_is_not_an_id_property(self):
        # Only a real property line counts; a ((ref)) in the text must not be
        # mistaken for the block's own id.
        assert block_id_property(f"see (({VALID})) for context") == ""

    def test_collects_ids_depth_first(self):
        tree = [{"content": f"a\nid:: {VALID}",
                 "children": [{"content": f"b\nid:: {VALID2}"}]}]
        assert collect_block_ids(tree) == [VALID, VALID2]

    def test_flags_values_that_cannot_be_block_ids(self):
        tree = [{"content": f"ok\nid:: {VALID}",
                 "children": [{"content": "bad\nid:: NOT-A-UUID"}]}]
        assert invalid_block_ids(tree) == ["NOT-A-UUID"]


# ---------- the write paths ------------------------------------------------

def _api(uuids):
    api = MagicMock()
    seq = list(uuids)
    api.insert_block.side_effect = lambda *a, **k: {"uuid": seq.pop(0)}
    return api


def _graph():
    """Page P with an anchor block and a parent with one child, answered the way
    Logseq does: a --keep-ids write goes out as one insertBatchBlock and is
    proven by reading the page back (#31)."""
    from tests.conftest import PageGraph, page_graph_api
    return page_graph_api(PageGraph({"P": [
        {"uuid": "anchor", "content": "anchor"},
        {"uuid": "parent", "content": "parent", "children": [{"content": "old child"}]}]}))


class TestPerBlockPath:
    def test_without_keep_ids_the_uuid_is_not_requested(self):
        api = _api(["new-1"])
        insert_block_tree_with_uuids(api, [{"content": f"a\nid:: {VALID}"}], "parent")
        assert "customUUID" not in api.insert_block.call_args[0][2]


class TestKeepIds:
    """With --keep-ids every block of the tree keeps its id, wherever it sits."""

    def test_the_block_gets_the_supplied_uuid(self):
        api = _graph()
        uuids = insert_block_tree_with_uuids(
            api, [{"content": f"a\nid:: {VALID}"}], "parent", keep_ids=True)
        assert uuids == [VALID]
        assert api.graph.locate(VALID)[3]["uuid"] == "parent"

    def test_blocks_without_an_id_get_a_fresh_one(self):
        api = _graph()
        uuids = insert_block_tree_with_uuids(
            api, [{"content": "no id here"}], "parent", keep_ids=True)
        assert len(uuids) == 1 and uuids[0] not in (VALID, VALID2)

    def test_children_keep_their_ids_too(self):
        api = _graph()
        uuids = insert_block_tree_with_uuids(
            api,
            [{"content": f"a\nid:: {VALID}", "children": [{"content": f"b\nid:: {VALID2}"}]}],
            "parent", keep_ids=True)
        assert uuids == [VALID, VALID2]
        assert api.graph.locate(VALID2)[3]["uuid"] == VALID

    def test_siblings_keep_their_ids(self):
        api = _graph()
        uuids = insert_block_tree_as_siblings(
            api, [{"content": f"a\nid:: {VALID}"}], "anchor", keep_ids=True)
        assert uuids == [VALID]
        _, siblings, i, _ = api.graph.locate(VALID)
        assert siblings[i - 1]["uuid"] == "anchor"


class TestBatchPath:
    def _batch_api(self):
        api = MagicMock()
        api.get_block.side_effect = [
            {"uuid": "parent", "children": []},
            {"uuid": "parent", "children": [{"uuid": "a"}, {"uuid": "b"}]},
        ]
        api.insert_batch_block.return_value = None
        return api

    def test_without_keep_ids_the_batch_does_not_ask(self):
        api = self._batch_api()
        tree = [{"content": f"a\nid:: {VALID}", "children": [{"content": "b"}]}]
        insert_block_tree_with_uuids(api, tree, "parent")
        assert "keepUUID" not in api.insert_batch_block.call_args[0][2]

    def test_keep_ids_sets_keepuuid_on_the_batch(self):
        api = _graph()
        tree = [{"content": f"a\nid:: {VALID}", "children": [{"content": "b"}]}]
        insert_block_tree_with_uuids(api, tree, "parent", keep_ids=True)
        assert api.insert_batch_block.call_args[0][2]["keepUUID"] is True


# ---------- the command ----------------------------------------------------

def _run(args, api):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return CliRunner().invoke(cli, ["--token", "T"] + args)


class TestCommand:
    def test_dropped_ids_are_announced_instead_of_silent(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text(f"- a\n  id:: {VALID}\n", encoding="utf-8")
        api = _api(["new-1"])
        result = _run(["insert-block", "--child-of", "anchor", "--tree-file", str(f)], api)
        assert result.exit_code == 0
        assert "id:: propert" in result.output
        assert "--keep-ids" in result.output

    def test_a_tree_without_ids_says_nothing(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("- plain\n", encoding="utf-8")
        api = _api(["new-1"])
        result = _run(["insert-block", "--child-of", "anchor", "--tree-file", str(f)], api)
        assert "id::" not in result.output

    def test_an_unusable_id_is_refused_before_any_write(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("- a\n  id:: NOT-A-UUID\n", encoding="utf-8")
        api = _api(["new-1"])
        result = _run(
            ["insert-block", "--child-of", "anchor", "--tree-file", str(f), "--keep-ids"], api)
        assert result.exit_code == 1
        assert "Nothing was written" in result.output
        api.insert_block.assert_not_called()

    def test_keep_ids_reaches_the_graph(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text(f"- a\n  id:: {VALID}\n", encoding="utf-8")
        api = _graph()
        result = _run(
            ["insert-block", "--child-of", "anchor", "--tree-file", str(f), "--keep-ids"], api)
        assert result.exit_code == 0, result.output
        assert api.graph.locate(VALID)[3]["uuid"] == "anchor"

    def test_top_level_roots_keep_their_ids_too(self, tmp_path):
        # This used to say the roots could not keep their ids, on the belief
        # that appendBlockInPage takes no options. Measured, a customUUID kept
        # the id at top level; since #31 the batch path does, like everywhere.
        f = tmp_path / "t.md"
        f.write_text(f"- a\n  id:: {VALID}\n", encoding="utf-8")
        api = _graph()
        result = _run(
            ["insert-block", "--page", "P", "--top-level", "--tree-file", str(f), "--keep-ids"], api)
        assert result.exit_code == 0, result.output
        _, siblings, i, parent = api.graph.locate(VALID)
        assert parent is None and i == len(siblings) - 1
        assert "cannot preserve" not in result.output


class TestIdLinesAsLogseqReadsThem:
    """An id:: line counts where Logseq reads it as the block's id (measured,
    0.10.15): a tab after the value does not stop it, a carriage return keeps
    Logseq from reading it at all."""

    def test_a_trailing_tab_still_names_the_id(self):
        assert block_id_property(f"a\nid:: {VALID}\t") == VALID

    def test_a_trailing_carriage_return_does_not(self):
        assert block_id_property(f"a\nid:: {VALID}\r") == ""

    def test_dropping_ids_removes_one_with_a_trailing_tab(self):
        from logseq_cli.helpers import without_block_ids
        assert without_block_ids(f"a\nid:: {VALID}\t") == "a"

    def test_keep_ids_refuses_a_taken_id_with_a_trailing_tab(self):
        """Flat --content is written as given; --tree and hierarchical content
        are parsed, which strips the tab, so this is the path that kept it."""
        from tests.conftest import PageGraph, page_graph_api
        api = page_graph_api(PageGraph({"P": [{"uuid": "anchor", "content": "anchor"},
                                              {"uuid": VALID, "content": "real"}]}))
        result = _run(["insert-block", "--after", "anchor", "--keep-ids",
                       "--content", f"copy\nid:: {VALID}\t"], api)
        assert result.exit_code == 1
        assert "already belong" in result.output
