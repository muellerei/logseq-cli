"""remove-block --expect-tree-hash refuses a block whose subtree is not the one the caller read.

The hash of the block alone would still match after a child was added, and the
removal would take the new child along. Same double and real ``LogseqAPI`` as
the other write tests.
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {
        "Probe Page": [{"content": "parent", "children": [{"content": "child"}]},
                       {"content": "other"}]})


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", *args])


def read(double, content="parent"):
    result = run("get-block", "--id", double.uuid_of(content), "--json")
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def test_an_unchanged_subtree_is_removed(double):
    block = read(double)
    result = run("remove-block", "--id", block["uuid"], "--expect-tree-hash", block["tree_hash"])
    assert result.exit_code == 0, result.stderr
    assert double.tree("Probe Page") == [("other", [])]


def test_a_child_added_since_the_read_refuses_without_a_write(double):
    block = read(double)
    added = run("insert-block", "--child-of", block["uuid"], "--content", "new child")
    assert added.exit_code == 0, added.stderr
    before = double.snapshot()
    writes = len(double.writes())
    result = run("remove-block", "--id", block["uuid"], "--expect-tree-hash", block["tree_hash"], "--json")
    assert result.exit_code != 0 and result.stdout == ""
    error = json.loads(result.stderr)
    assert error["reason"] == "precondition_failed"
    assert error["expected_tree_hash"] == block["tree_hash"]
    assert "expected_hash" not in error
    assert "actual_tree_hash" not in error and block["tree_hash"] not in error["error"]
    assert len(double.writes()) == writes
    assert double.snapshot() == before


def test_the_hash_of_the_block_alone_does_not_stand_in_for_the_tree_hash(double):
    block = read(double)
    result = run("remove-block", "--id", block["uuid"], "--expect-tree-hash", block["hash"], "--json")
    assert result.exit_code != 0
    assert json.loads(result.stderr)["reason"] == "precondition_failed"


def test_dry_run_checks_too(double):
    block = read(double)
    result = run("remove-block", "--id", block["uuid"], "--expect-tree-hash", "0123456789ab",
                 "--dry-run", "--json")
    assert result.exit_code != 0
    assert json.loads(result.stderr)["reason"] == "precondition_failed"


def test_remove_block_has_no_expect_hash(double):
    block = read(double)
    result = run("remove-block", "--id", block["uuid"], "--expect-hash", block["hash"])
    assert result.exit_code == 2
    assert double.writes() == []


def test_delete_block_takes_the_same_option(double):
    block = read(double)
    run("insert-block", "--child-of", block["uuid"], "--content", "new child")
    result = run("delete-block", "--id", block["uuid"], "--expect-tree-hash", block["tree_hash"], "--json")
    assert result.exit_code != 0
    assert json.loads(result.stderr)["reason"] == "precondition_failed"


def test_a_missing_block_is_block_not_found(double):
    result = run("remove-block", "--id", "00000000-0000-4000-8000-0000000000ff",
                 "--expect-tree-hash", "0123456789ab", "--json")
    assert result.exit_code != 0
    assert json.loads(result.stderr)["reason"] == "block_not_found"


@pytest.mark.parametrize("name", ["remove-block", "delete-block"])
def test_the_switch_owes_the_tree_hash(double, name):
    block = read(double)
    result = run("--require-preconditions", name, "--id", block["uuid"], "--json")
    assert result.exit_code != 0
    error = json.loads(result.stderr)
    assert error["reason"] == "precondition_required"
    assert error["options"] == ["--expect-tree-hash"]
    assert double.writes() == []
