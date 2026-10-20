"""``add-journal-block --upsert-heading`` with children replaces the matched
block's children, and a second run of the same call writes nothing.

The upsert replaced only the text (``updateBlock``) and appended the children
(``insertBlock``), so the same call run twice left ``### Carol`` with its
children twice (reproduced against the HTTP double). Replacing children
deletes blocks, which an upsert could not do before, so a call that would
delete any owes ``--expect-tree-hash``, whatever ``require_preconditions``
says: ``--expect-hash`` sees the block alone, not one child.

Run through the real ``LogseqAPI`` against the HTTP double.
"""
import json

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.blocktext import block_hash, tree_hash
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

JOURNAL = "2099-01-05, Monday"
UPSERT = ["add-journal-block", "--date", "2099-01-05", "--under-heading", "## Log",
          "--upsert-heading", "### Carol"]
KIDS = "### Carol\n\t- one\n\t- two"
MUTATING = {"insertBlock", "insertBatchBlock", "updateBlock", "removeBlock",
            "appendBlockInPage", "createPage", "upsertBlockProperty"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST", raising=False)
    monkeypatch.delenv("LOGSEQ_CLI_REQUIRE_PRECONDITIONS", raising=False)


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", *UPSERT, *args, "--json"])


def ok(r):
    assert r.exit_code == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def refused(r):
    assert r.exit_code == 1, r.stdout + r.stderr
    return json.loads(r.stderr)


def carol(*children):
    return {JOURNAL: [{"content": "## Log", "children": [
        {"content": "### Carol", "children": list(children)}]}]}


def under_carol(double):
    return next(kids for content, kids in double.tree(JOURNAL)[0][1] if content == "### Carol")


def written(double):
    return [m.rsplit(".", 1)[-1] for m, _ in double.writes()
            if m.rsplit(".", 1)[-1] in MUTATING]


def the_tree_hash(double):
    block = LogseqAPI(token="t").get_block(double.uuid_of("### Carol"), include_children=True, cached=False)
    return tree_hash(block)


# --- D: twice the same call ------------------------------------------------

def test_the_same_call_twice_leaves_one_subtree(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol())
    first = ok(run("--content", KIDS))
    assert first["status"] == "updated"
    second = ok(run("--content", KIDS, "--expect-tree-hash", first["tree_hash"]))
    assert under_carol(double) == [("one", []), ("two", [])]
    assert (second["status"], second["blocks"], second["children_removed"]) == ("unchanged", 0, 0)
    assert second["tree_hash"] == first["tree_hash"]


def test_an_unchanged_run_writes_nothing_and_owes_nothing(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol("one", "two"))
    before = len(double.writes())
    p = ok(run("--content", KIDS))
    assert p["status"] == "unchanged"
    assert written(double)[before:] == []


def test_a_property_the_block_only_keeps_is_no_change(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: [{"content": "## Log", "children": [
        "### Carol\nprio:: 1"]}]})
    assert ok(run("--content", "### Carol"))["status"] == "unchanged"
    assert written(double) == []


def test_a_property_the_content_sets_anew_is_a_change(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: [{"content": "## Log", "children": [
        "### Carol\nprio:: 1"]}]})
    assert ok(run("--content", "### Carol\nprio:: 2"))["status"] == "updated"
    assert "updateBlock" in written(double)


# --- E4: what an upsert replaces ------------------------------------------

def test_flat_content_keeps_the_children(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol("note by hand"))
    p = ok(run("--content", "### Carol\nfresh"))
    assert (p["status"], p["children_removed"]) == ("updated", 0)
    assert under_carol(double) == [("note by hand", [])]


def test_children_on_a_block_without_any_are_added(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol())
    p = ok(run("--content", KIDS))
    assert p["children_removed"] == 0
    assert under_carol(double) == [("one", []), ("two", [])]


def test_children_replace_the_children_with_the_tree_hash(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a", {"content": "old b", "children": ["deep"]}))
    read = the_tree_hash(double)      # get-block --json carries it as tree_hash
    preview = ok(run("--content", KIDS, "--expect-tree-hash", read, "--dry-run"))
    assert (preview["action"], preview["children_removed"], preview["tree_hash"]) == ("replace", 2, read)
    assert preview["would_replace"] == double.uuid_of("### Carol")
    assert written(double) == []
    p = ok(run("--content", KIDS, "--expect-tree-hash", preview["tree_hash"]))
    assert (p["status"], p["children_removed"], p["blocks"]) == ("updated", 2, 3)
    assert under_carol(double) == [("one", []), ("two", [])]
    assert p["tree_hash"] == the_tree_hash(double)


# --- E5: the precondition when children go ---------------------------------

@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]], ids=["run", "dry-run"])
@pytest.mark.parametrize("given", [[], ["--expect-hash", "HASH"]], ids=["none", "expect-hash"])
def test_removing_children_owes_the_tree_hash(monkeypatch, dry_run, given):
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a"))
    given = [block_hash(double.content_of(double.uuid_of("### Carol"))) if g == "HASH" else g
             for g in given]
    error = refused(run("--content", KIDS, *given, *dry_run))
    assert error["reason"] == "precondition_required"
    assert error["options"] == ["--expect-tree-hash"]
    assert "--expect-hash covers the block alone" in error["error"]
    assert written(double) == []
    assert under_carol(double) == [("old a", [])]


def test_a_stale_tree_hash_is_refused(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a"))
    stale = the_tree_hash(double)
    double.add_child(double.uuid_of("old a"), "added since")
    error = refused(run("--content", KIDS, "--expect-tree-hash", stale))
    assert error["reason"] == "precondition_failed"
    assert written(double) == []


def test_keeping_the_children_owes_only_under_the_switch(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a"))
    assert ok(run("--content", "### Carol\nfresh"))["status"] == "updated"
    monkeypatch.setenv("LOGSEQ_CLI_REQUIRE_PRECONDITIONS", "true")
    error = refused(run("--content", "### Carol\nfresher"))
    assert (error["reason"], error["options"]) == ("precondition_required", ["--expect-hash"])
    assert double.uuid_of("### Carol\nfresh")


def test_the_tree_hash_without_a_match_has_no_object(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: [{"content": "## Log", "children": []}]})
    r = run("--content", KIDS, "--expect-tree-hash", "0123456789ab")
    assert ok(r)["status"] == "created"
    assert "--expect-tree-hash has no object" in r.stderr
    assert under_carol(double) == [("one", []), ("two", [])]


def test_the_tree_hash_is_refused_without_upsert(monkeypatch):
    LogseqHttpDouble.installed(monkeypatch, carol())
    r = split_runner().invoke(cli, ["--token", "t", "add-journal-block", "--date", "2099-01-05",
                                    "--content", "x", "--expect-tree-hash", "0123456789ab"])
    assert r.exit_code == 2
    assert "--upsert-heading" in r.stderr


# --- E7: refs into the old children ---------------------------------------

@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]], ids=["run", "dry-run"])
def test_a_ref_into_an_old_child_is_refused(monkeypatch, dry_run):
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a"))
    target = double.uuid_of("old a")
    double.add_page("Other Page", [f"see (({target}))"])
    error = refused(run("--content", KIDS, "--expect-tree-hash", the_tree_hash(double), *dry_run))
    assert error["reason"] == "block_refs"
    assert [r["target"] for r in error["refs"]] == [target]
    assert "remove-block --ignore-refs" in error["error"]
    assert written(double) == []


# --- E9: the chain ----------------------------------------------------------

def test_three_upserts_chain_on_the_tree_hash_without_a_read(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a"))
    h = the_tree_hash(double)
    for kids in ("### Carol\n\t- x", "### Carol\n\t- y\n\t- z", "### Carol\n\t- w"):
        h = ok(run("--content", kids, "--expect-tree-hash", h))["tree_hash"]
    assert under_carol(double) == [("w", [])]
    assert h == the_tree_hash(double)


# --- E10: a run cut off ------------------------------------------------------

@pytest.mark.parametrize("method", ["removeBlock", "insertBatchBlock"])
def test_a_run_cut_off_keeps_the_old_children_and_a_rerun_replaces_both(monkeypatch, method):
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a", "old b"))
    double.set_mode(method, "error")
    r = run("--content", KIDS, "--expect-tree-hash", the_tree_hash(double))
    assert r.exit_code != 0
    error = json.loads(r.stderr)
    assert "written_uuids" in error
    assert isinstance(error["writes_landed"], int)
    assert "get-block --json" in error["error"]
    kids = [c for c, _ in under_carol(double)]
    assert {"old a", "old b"} <= set(kids)
    if method == "removeBlock":
        assert error["written_uuids"] == [double.uuid_of("one"), double.uuid_of("two")]
    double.set_mode(method, "execute")
    fresh = the_tree_hash(double)
    assert ok(run("--content", KIDS, "--expect-tree-hash", fresh, "--dry-run"))["action"] == "replace"
    ok(run("--content", KIDS, "--expect-tree-hash", fresh))
    assert under_carol(double) == [("one", []), ("two", [])]


@pytest.mark.parametrize("where", ["child", "root"])
def test_a_ref_in_the_content_to_a_child_it_removes_is_refused(monkeypatch, where):
    # A caller that read the tree and writes it back with refs to the old
    # uuids: the graph holds no such ref before the write, yet it would
    # dangle after it (found in review against the HTTP double).
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a", "old b"))
    target = double.uuid_of("old b")
    content = (f"### Carol\n\t- see (({target}))\n\t- b2" if where == "child"
               else f"### Carol (({target}))\n\t- x")
    error = refused(run("--content", content, "--expect-tree-hash", the_tree_hash(double)))
    assert error["reason"] == "block_refs"
    assert [r["target"] for r in error["refs"]] == [target]
    assert written(double) == []


def test_children_with_other_text_are_a_change(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol("one", "old"))
    p = ok(run("--content", KIDS, "--expect-tree-hash", the_tree_hash(double)))
    assert (p["status"], p["children_removed"]) == ("updated", 2)
    assert under_carol(double) == [("one", []), ("two", [])]


def test_an_unchanged_run_says_so_in_text(monkeypatch):
    LogseqHttpDouble.installed(monkeypatch, carol("one", "two"))
    r = split_runner().invoke(cli, ["--token", "t", *UPSERT, "--content", KIDS])
    assert r.exit_code == 0, r.stderr
    assert r.stdout.startswith("Unchanged, nothing written")


def test_a_refused_text_write_does_not_speak_of_a_rerun(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, carol("old a"))
    double.set_mode("updateBlock", "error")
    r = run("--content", KIDS, "--expect-tree-hash", the_tree_hash(double))
    error = json.loads(r.stderr)
    assert "written_uuids" not in error and "get-block --json" not in error["error"]
