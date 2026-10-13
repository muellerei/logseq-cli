"""A removal deletes only the subtree the caller read.

``removeBlock`` cascades: whatever stands under the block when it runs goes
with it. A caller that read the block, counted and checked it, and then
removes it, passes the tree hash of that read; ``LogseqAPI.remove_block``
reads the block again just before the delete and refuses when the subtree is
another one. The window that stays is the requests between that read and the
delete: the API has no compare-and-swap.
"""
import json

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.blocktext import tree_hash
from logseq_cli.cli import cli
from logseq_cli.writerefused import BlockChanged, BlockNotFound
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


@pytest.fixture
def double(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", [
        {"content": "parent block", "children": ["kid block"]},
        "other block",
    ])
    return double


@pytest.fixture
def api(double):
    return LogseqAPI(token="t")


def _read_tree(api, uuid):
    return tree_hash(api.get_block(uuid, include_children=True, cached=False))


# --- The API ----------------------------------------------------------------

def test_a_changed_subtree_is_not_removed(double, api):
    parent = double.uuid_of("parent block")
    read = _read_tree(api, parent)
    double.add_child(parent)
    with pytest.raises(BlockChanged) as refused:
        api.remove_block(parent, expect_tree=read)
    assert refused.value.fields["block"] == parent
    assert "nothing was removed" in str(refused.value)
    assert "repeat" not in str(refused.value).lower()
    assert double.sent("removeBlock") == []


def test_a_changed_root_text_is_not_removed(double, api):
    parent = double.uuid_of("parent block")
    read = _read_tree(api, parent)
    double.set_content(parent, "parent edited")
    with pytest.raises(BlockChanged):
        api.remove_block(parent, expect_tree=read)
    assert double.sent("removeBlock") == []


def test_a_block_gone_before_the_delete_is_reported(double, api):
    parent = double.uuid_of("parent block")
    read = _read_tree(api, parent)
    double.delete_block(parent)
    with pytest.raises(BlockNotFound) as refused:
        api.remove_block(parent, expect_tree=read)
    assert refused.value.fields["id"] == parent
    assert double.sent("removeBlock") == []


def test_the_subtree_as_read_is_removed(double, api):
    parent = double.uuid_of("parent block")
    api.remove_block(parent, expect_tree=_read_tree(api, parent))
    assert len(double.sent("removeBlock")) == 1
    assert double.tree("Probe Page") == [("other block", [])]


def test_without_a_state_the_removal_is_as_before(double, api):
    parent = double.uuid_of("parent block")
    double.add_child(parent)
    api.remove_block(parent, expect_tree=None)
    assert double.tree("Probe Page") == [("other block", [])]


def test_every_caller_says_which_state_it_removes(double, api):
    with pytest.raises(TypeError):
        api.remove_block(double.uuid_of("parent block"))


# --- remove-block -----------------------------------------------------------

def test_remove_block_keeps_a_child_made_between_read_and_delete(double):
    parent = double.uuid_of("parent block")
    query = double._handlers["logseq.DB.datascriptQuery"]
    added = []

    def child_appears(args):
        # The command looks for refs into the block after it read the subtree.
        if not added:
            added.append(double.add_child(parent))
        return query(args)
    double._handlers["logseq.DB.datascriptQuery"] = child_appears
    r = split_runner().invoke(cli, ["--token", "t", "remove-block", "--id", parent, "--json"])
    assert r.exit_code == 1, r.stderr
    error = json.loads(r.stderr)
    assert error["reason"] == "block_changed"
    assert double.sent("removeBlock") == []
    assert double._locate(added[0]) is not None


def test_remove_block_reports_a_block_gone_before_the_delete(double):
    parent = double.uuid_of("parent block")
    query = double._handlers["logseq.DB.datascriptQuery"]

    def block_goes(args):
        if double._locate(parent):
            double.delete_block(parent)
        return query(args)
    double._handlers["logseq.DB.datascriptQuery"] = block_goes
    r = split_runner().invoke(cli, ["--token", "t", "remove-block", "--id", parent, "--json"])
    assert r.exit_code == 1, r.stderr
    assert json.loads(r.stderr)["reason"] == "block_not_found"
    assert double.sent("removeBlock") == []


# --- The page's property block ----------------------------------------------

def test_remove_property_keeps_a_property_block_that_got_a_child(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Props Page", ["kind:: a", "text"])
    block = double.uuid_of("kind:: a")
    tree = double._handlers["logseq.Editor.getPageBlocksTree"]
    added = []

    def child_appears(args):
        answer = tree(args)
        if not added:
            added.append(double.add_child(block))
        return answer
    double._handlers["logseq.Editor.getPageBlocksTree"] = child_appears
    r = split_runner().invoke(cli, ["--token", "t", "remove-property", "--name", "Props Page",
                                    "--key", "kind", "--json"])
    assert r.exit_code == 1, r.stderr
    assert json.loads(r.stderr)["reason"] == "block_changed"
    assert double.sent("removeBlock") == []
    assert double._locate(block) is not None and double._locate(added[0]) is not None
