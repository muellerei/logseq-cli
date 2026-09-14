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


class TestPerBlockPath:
    def test_without_keep_ids_the_uuid_is_not_requested(self):
        api = _api(["new-1"])
        insert_block_tree_with_uuids(api, [{"content": f"a\nid:: {VALID}"}], "parent")
        assert "customUUID" not in api.insert_block.call_args[0][2]

    def test_keep_ids_asks_logseq_for_the_supplied_uuid(self):
        api = _api(["new-1"])
        insert_block_tree_with_uuids(
            api, [{"content": f"a\nid:: {VALID}"}], "parent", keep_ids=True)
        assert api.insert_block.call_args[0][2]["customUUID"] == VALID

    def test_blocks_without_an_id_stay_untouched_under_keep_ids(self):
        api = _api(["new-1"])
        insert_block_tree_with_uuids(
            api, [{"content": "no id here"}], "parent", keep_ids=True)
        assert "customUUID" not in api.insert_block.call_args[0][2]

    def test_children_keep_their_ids_too(self):
        api = _api(["new-1", "new-2"])
        insert_block_tree_with_uuids(
            api,
            [{"content": f"a\nid:: {VALID}", "children": [{"content": f"b\nid:: {VALID2}"}]}],
            "parent", keep_ids=True, batch=False)
        assert api.insert_block.call_args_list[1][0][2]["customUUID"] == VALID2

    def test_siblings_keep_their_ids(self):
        api = _api(["new-1"])
        insert_block_tree_as_siblings(
            api, [{"content": f"a\nid:: {VALID}"}], "anchor", keep_ids=True)
        assert api.insert_block.call_args[0][2]["customUUID"] == VALID


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
        api = self._batch_api()
        tree = [{"content": f"a\nid:: {VALID}", "children": [{"content": "b"}]}]
        insert_block_tree_with_uuids(api, tree, "parent", keep_ids=True)
        assert api.insert_batch_block.call_args[0][2]["keepUUID"] is True


# ---------- the command ----------------------------------------------------

def _run(args, api):
    with patch("logseq_cli.cli.LogseqAPI", return_value=api):
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

    def test_keep_ids_reaches_the_api(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text(f"- a\n  id:: {VALID}\n", encoding="utf-8")
        api = _api(["new-1"])
        result = _run(
            ["insert-block", "--child-of", "anchor", "--tree-file", str(f), "--keep-ids"], api)
        assert result.exit_code == 0
        assert api.insert_block.call_args[0][2]["customUUID"] == VALID

    def test_top_level_says_it_cannot_keep_root_ids(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text(f"- a\n  id:: {VALID}\n", encoding="utf-8")
        api = MagicMock()
        api.append_block_in_page.return_value = {"uuid": "new-1"}
        result = _run(
            ["insert-block", "--page", "P", "--top-level", "--tree-file", str(f), "--keep-ids"], api)
        assert "cannot preserve ids on top-level blocks" in result.output
