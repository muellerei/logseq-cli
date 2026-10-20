"""--content becomes a tree by the rule its --help states, and nothing else.

The help of add-journal-block promised "tab-indented hierarchy" in --content.
contains_hierarchical_content reads a value as a tree only when a line
after the first is indented and starts with "- ": "Parent\\n\\tChild" was
written as one block with a tab in its text, exit 0, with no sign (#146,
reproduced against the HTTP double). The detection stays; these tests pin
it to the rule the help now states, and the Note on stderr that names the
indented lines a value without a tree keeps as text.

Run through the real LogseqAPI against the HTTP double.
"""
import json

import pytest

from logseq_cli.cli import cli
from logseq_cli.outlinetext import TREE_CONTENT_EXAMPLE
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

DATE = "2099-01-05"
JOURNAL = "2099-01-05, Monday"
FLAT = "Parent\n\tChild"
NOTE = 'Note: indented lines without "- " are text in one block, not children'


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST", raising=False)
    monkeypatch.delenv("LOGSEQ_CLI_REQUIRE_PRECONDITIONS", raising=False)
    monkeypatch.delenv("LOGSEQ_JOURNAL_HEADING", raising=False)


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", *args])


def journal_block(*args):
    return run("add-journal-block", "--date", DATE, "--top-level", *args)


def ok(r):
    assert r.exit_code == 0, r.stdout + r.stderr
    return r


def notes(r):
    return [line for line in r.stderr.splitlines() if line.startswith(NOTE)]


# --- the rule the help states --------------------------------------------------

def test_the_example_of_the_help_is_a_tree(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"]})
    ok(journal_block("--content", TREE_CONTENT_EXAMPLE))
    assert double.tree(JOURNAL) == [("logged", []), ("Parent", [("Child", [])])]


def test_indentation_alone_is_one_block(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"]})
    ok(journal_block("--content", FLAT))
    assert double.tree(JOURNAL) == [("logged", []), ("Parent", [])]
    assert double.content_of(double.uuid_of(FLAT)) == FLAT


def test_every_flush_line_of_a_tree_is_a_block(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"]})
    ok(journal_block("--content", "A\nB\n\t- c"))
    assert double.tree(JOURNAL) == [("logged", []), ("A", []), ("B", [("c", [])])]


def test_a_property_line_stays_with_the_block_above_indented_or_not(monkeypatch):
    # A "- key:: value" deeper than its block is that block's property, the
    # shape agents write for one; so is an indented SCHEDULED line.
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"]})
    ok(journal_block("--content", "TODO Parent\n\t- owner:: bob\n\tSCHEDULED: <2099-01-05 Mon>\n\t- Child"))
    assert double.tree(JOURNAL) == [("logged", []), ("TODO Parent", [("Child", [])])]
    parent = double.uuid_of("TODO Parent\nowner:: bob\nSCHEDULED: <2099-01-05 Mon>")
    assert double.content_of(parent).splitlines()[1:] == ["owner:: bob", "SCHEDULED: <2099-01-05 Mon>"]


@pytest.mark.parametrize("target", ["--page", "--after"])
def test_insert_block_reads_the_tree_for_every_target(monkeypatch, target):
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["alpha block"]})
    where = "Probe Page" if target == "--page" else double.uuid_of("alpha block")
    ok(run("insert-block", target, where, "--content", TREE_CONTENT_EXAMPLE))
    assert double.tree("Probe Page") == [("alpha block", []), ("Parent", [("Child", [])])]


def test_insert_block_keeps_indentation_alone_in_one_block(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["alpha block"]})
    ok(run("insert-block", "--page", "Probe Page", "--content", FLAT))
    assert double.content_of(double.uuid_of(FLAT)) == FLAT


# --- the Note (F9) ------------------------------------------------------------

def test_indented_lines_without_a_marker_get_a_note_naming_content_file(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"]})
    r = ok(journal_block("--content", FLAT, "--json"))
    json.loads(r.stdout)
    [note] = notes(r)
    assert "--content-file" in note
    assert double.tree(JOURNAL) == [("logged", []), ("Parent", [])]


@pytest.mark.parametrize("source", ["--content", "--content-file"])
def test_insert_block_names_tree_for_the_same_lines(monkeypatch, tmp_path, source):
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["alpha block"]})
    value = FLAT
    if source == "--content-file":
        (tmp_path / "flat.md").write_text(FLAT)
        value = str(tmp_path / "flat.md")
    r = ok(run("insert-block", "--page", "Probe Page", source, value, "--json"))
    json.loads(r.stdout)
    [note] = notes(r)
    assert note.endswith('use --tree.')
    assert double.tree("Probe Page") == [("alpha block", []), ("Parent", [])]


def test_the_note_comes_under_dry_run_too_and_claims_no_write(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"],
                                                      "Probe Page": ["alpha block"]})
    before = len(double.writes())
    journal = ok(journal_block("--content", FLAT, "--dry-run"))
    insert = ok(run("insert-block", "--page", "Probe Page", "--content", FLAT, "--dry-run"))
    assert len(double.writes()) == before
    for r in (journal, insert):
        [note] = notes(r)
        assert "written" not in note


def test_an_upsert_gets_the_note(monkeypatch):
    LogseqHttpDouble.installed(monkeypatch, {JOURNAL: [{"content": "## Log", "children": [
        {"content": "### Carol", "children": []}]}]})
    r = ok(run("add-journal-block", "--date", DATE, "--under-heading", "## Log",
               "--upsert-heading", "### Carol", "--content", "### Carol\n\tChild", "--json"))
    assert json.loads(r.stdout)["status"] == "updated"
    [note] = notes(r)
    assert "--content-file" in note


def test_a_refusal_under_json_carries_the_note_in_its_error(monkeypatch):
    LogseqHttpDouble.installed(monkeypatch, {JOURNAL: [{"content": "## Log", "children": [
        {"content": "### Carol", "children": []}, {"content": "### Carol", "children": []}]}]})
    r = run("add-journal-block", "--date", DATE, "--under-heading", "## Log",
            "--upsert-heading", "### Carol", "--content", "### Carol\n\tChild", "--json")
    assert r.exit_code == 1, r.stdout + r.stderr
    error = json.loads(r.stderr)
    assert error["reason"] == "ambiguous"
    assert [n for n in error["notes"] if n.startswith(NOTE)]


@pytest.mark.parametrize("args", [
    ["--content", TREE_CONTENT_EXAMPLE],
    ["--content", "Notes\n```\n\tcode\n```\nDone."],
    ["--content", "Notes\n\t```\n\tcode\n\t```"],
    ["--content", "Parent\n\tstatus:: done"],
    ["--content", "TODO Parent\n\tSCHEDULED: <2099-01-05 Mon>"],
    ["--content", "Notes\n#+BEGIN_QUOTE\n\tquoted\n#+END_QUOTE"],
    ["--content", "Parent\nChild"],
    ["--no-preserve", "--content", FLAT],
    ["--no-preserve", "--content", FLAT, "--content", "Other\n\tline"],
], ids=["tree", "code-block", "indented-fences", "property", "planning", "org-block", "flush", "no-preserve", "no-preserve-batch"])
def test_no_note(monkeypatch, args):
    LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"]})
    assert notes(ok(journal_block(*args))) == []


def test_one_note_for_a_batch(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"]})
    r = ok(journal_block("--content", "Plain", "--content", FLAT))
    assert len(notes(r)) == 1
    assert double.tree(JOURNAL) == [("logged", []), ("Plain", []), ("Parent", [])]


def test_no_note_from_add_journal_content(monkeypatch):
    LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"]})
    r = ok(run("add-journal-content", "--date", DATE, "--top-level", "--content", FLAT))
    assert notes(r) == []
