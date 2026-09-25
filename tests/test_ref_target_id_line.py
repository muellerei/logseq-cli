"""#95: a Block Ref written by any command gives its target an Id Line.

Measured against 0.10.15 on a throwaway page. When the text a command writes
holds ``((uuid))`` and the target has no Id Line yet, Logseq adds one to the
target itself, but in column 0 whatever the target's depth, and leaves it out
of the database: ``getBlock`` shows ``properties: {}``. A later
``upsertBlockProperty`` on the target rewrites it from the database and the
line is gone from the file; parsed again without it, the Block gets a new uuid
and the ref points at nothing.

Logseq's editor does not get there: copying a ref first runs
``set-blocks-id!``, which stores the id as a property. ``setBlocksId`` is that
step, exported to the API. Called before the write, the target gets its Id
Line under its bullet and ``id`` in its properties, and Logseq adds no second
line (measured). A ref inside a code block or inline code is no ref to Logseq
and gets no line (measured); a ref in capitals and an embed do.

Checked at LogseqAPI, like the id:: contract (#56), so no write path can go
around it.
"""
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.blocktext import SplitBlockError, block_ref_uuids
from logseq_cli.cli import cli
from tests.conftest import split_runner

BARE = "8f2a3b4c-5d6e-4f70-8a9b-0c1d2e3f4a81"      # a block without an Id Line
KEPT = "9a3b4c5d-6e7f-4081-8a9b-0c1d2e3f4a92"      # a block with one
PAGE = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"      # getBlock answers a page uuid with null
DEAD = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"      # no block has it
GHOST = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a71"     # a dead ref's placeholder
OWN = "5c9e0f1a-2b3c-4d4e-8f9a-0b1c2d3e4f50"       # the block an update writes

BLOCKS = {
    BARE: {"uuid": BARE, "content": "TODO x", "page": {"id": 1}, "properties": {}},
    KEPT: {"uuid": KEPT, "content": f"TODO y\nid:: {KEPT}", "page": {"id": 1},
           "properties": {"id": KEPT}},
    OWN: {"uuid": OWN, "content": "old", "page": {"id": 1}, "properties": {}},
    # Once the page holding a dead ref is read from its file, getBlock answers
    # the ref's uuid with a placeholder that has no page (measured, #70).
    GHOST: {"uuid": GHOST, "content": f"id:: {GHOST}", "properties": {}},
}


def _api():
    api = LogseqAPI(token="t")

    def call(method, args=None):
        if method == "logseq.Editor.getBlock":
            return BLOCKS.get(args[0].lower())
        return {"uuid": "new"}

    api.call = MagicMock(side_effect=call)
    return api


def _methods(api):
    return [c.args[0] for c in api.call.call_args_list]


def _set_ids(api):
    return [c.args[1][0] for c in api.call.call_args_list
            if c.args[0] == "logseq.Editor.setBlocksId"]


# Keyed by the Logseq method each one calls.
WRITES = {
    "insertBlock": lambda api, text: api.insert_block(OWN, text),
    "appendBlockInPage": lambda api, text: api.append_block_in_page("P", text),
    "updateBlock": lambda api, text: api.update_block(OWN, text),
    "insertBatchBlock": lambda api, text: api.insert_batch_block(
        OWN, [{"content": "parent", "children": [{"content": text}]}]),
    "upsertBlockProperty": lambda api, text: api.upsert_block_property(OWN, "see", text),
}


class TestEveryWriteStoresTheTargetsId:
    @pytest.mark.parametrize("name", WRITES)
    def test_before_the_write(self, name):
        api = _api()
        WRITES[name](api, f"(({BARE}))")
        methods = _methods(api)
        assert _set_ids(api) == [[BARE]]
        # Before: stored first, Logseq adds no column-0 line (measured).
        assert methods.index("logseq.Editor.setBlocksId") < methods.index(f"logseq.Editor.{name}")

    @pytest.mark.parametrize("name", WRITES)
    def test_not_for_a_target_that_has_its_id(self, name):
        api = _api()
        WRITES[name](api, f"(({KEPT}))")
        assert _set_ids(api) == []

    def test_a_ref_among_the_properties_an_update_carries(self):
        # update-block writes the block's properties back with its new text.
        api = _api()
        api.update_block(OWN, "new text", {"see": f"(({BARE}))"})
        assert _set_ids(api) == [[BARE]]

    def test_every_target_of_one_write_in_one_call(self):
        api = _api()
        api.insert_batch_block(OWN, [{"content": f"(({BARE}))", "children": [
            {"content": f"(({KEPT})) and (({BARE}))"}]}])
        assert _set_ids(api) == [[BARE]]


class TestWhatIsNoTarget:
    @pytest.mark.parametrize("uuid", [PAGE, DEAD, GHOST],
                             ids=["page uuid", "dead ref", "dead ref's placeholder"])
    def test_no_block_behind_the_uuid(self, uuid):
        # A dead ref stays what the caller wrote; a page is no Block.
        api = _api()
        api.insert_block(OWN, f"(({uuid}))")
        assert _set_ids(api) == []
        assert "logseq.Editor.insertBlock" in _methods(api)

    def test_an_update_does_not_store_its_own_id(self):
        # The block's own ref: the write replaces its text, the id would not
        # survive it, and the block needs no ref to itself persisted.
        api = _api()
        api.update_block(OWN, f"see (({OWN}))")
        assert _set_ids(api) == []

    def test_a_refused_write_stores_nothing(self):
        api = _api()
        with pytest.raises(SplitBlockError):
            api.insert_block(OWN, f"(({BARE}))\n- second")
        api.call.assert_not_called()


class TestBlockRefUuids:
    @pytest.mark.parametrize("text", [
        f"(({BARE}))",
        f"see (({BARE})) here",
        f"{{{{embed (({BARE}))}}}}",
        f"[label]((({BARE})))",
        f"key:: (({BARE}))",
        f"(({BARE.upper()}))",
        f"`code` then (({BARE}))",
    ], ids=["bare", "in text", "embed", "labelled", "property value", "capitals",
            "after inline code"])
    def test_a_ref_logseq_reads(self, text):
        assert block_ref_uuids(text) == [BARE]

    @pytest.mark.parametrize("text", [
        f"x\n```\n(({BARE}))\n```",
        f"x `(({BARE}))`",
        f"x ``a (({BARE})) b``",
    ], ids=["code block", "inline code", "double backticks"])
    def test_code_holds_no_ref(self, text):
        assert block_ref_uuids(text) == []

    def test_each_uuid_once_in_order(self):
        assert block_ref_uuids(f"(({KEPT})) (({BARE})) (({KEPT.upper()}))") == [KEPT, BARE]

    def test_not_a_uuid(self):
        assert block_ref_uuids("((not-a-uuid)) ((1234))") == []


class TestReplaceTextReadsBackAStoredId:
    """A ref written into one block stores the id of another the same run
    replaces (#95): Logseq keeps that id through the later update, so the
    block reads back with its Id Line after the text it was sent (measured,
    0.10.15). That is the write landing, not failing."""

    def test_the_block_that_gained_its_id_counts_as_written(self):
        api = MagicMock()
        api.get_page_blocks_tree.return_value = [
            {"uuid": BARE, "content": "draft B", "children": []},
            {"uuid": OWN, "content": f"draft A (({BARE}))", "children": []}]
        written = {}
        api.update_block.side_effect = lambda uuid, new, properties=None, replacing=None: \
            written.__setitem__(uuid, new)
        api.get_block.side_effect = lambda uuid, include_children=False: {
            "uuid": uuid,
            "content": written[uuid] + (f"\nid:: {BARE}" if uuid == BARE else "")}
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "draft", "--replace", "final"])
        assert result.exit_code == 0, result.stderr
        assert "not written" not in result.stdout
