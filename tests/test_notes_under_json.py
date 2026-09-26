"""Under --json, stderr of a failed command is one JSON object, notes included.

A note printed on stderr before the error object made stderr no JSON: an
agent parsing it failed on the note, not on the error. Every write can be
refused after its notes were printed, since the write proves itself, so
this was reachable from each note a writing command prints first.

Under --json the notes are held: a failure carries them in the error object
as ``notes``, a success prints them after its result. Without --json they
go out as before.
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

DATE = "2099-01-05"
JOURNAL = "2099-01-05, Monday"
DEPRECATED = "Note: add-journal-entry is deprecated."
FOREIGN = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
ID_NOTE = "id:: line(s) naming another block's uuid will be dropped"


def _run(*args):
    return split_runner().invoke(cli, ["--token", "t", *args])


def _error(r):
    """The error object, which must be all of stderr."""
    assert r.exit_code != 0, (r.stdout, r.stderr)
    return json.loads(r.stderr)


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {JOURNAL: ["logged"],
                                                    "Probe Page": ["alpha block"]})


def test_a_refusal_after_the_deprecation_note(double):
    # Checked after the note: text Logseq would read as two blocks.
    error = _error(_run("add-journal-entry", "--date", DATE, "--content", "one\n- two",
                        "--json"))
    assert error["reason"] == "splits_into_blocks"
    assert [n for n in error["notes"] if n.startswith(DEPRECATED)]


def test_a_write_not_done_after_the_hierarchy_note(double):
    double.set_mode("appendBlockInPage", "noop")
    error = _error(_run("add-journal-block", "--date", DATE, "--top-level",
                        "--content", "parent\n\t- kid", "--json"))
    assert error["reason"] == "write_not_verified"
    assert error["notes"] == ["Note: Hierarchical content detected, using structured insertion"]


def test_a_write_not_done_after_the_id_note(double):
    double.set_mode("updateBlock", "noop")
    uuid = double.uuid_of("alpha block")
    error = _error(_run("update-block", "--id", uuid, "--content",
                        f"new text\nid:: {FOREIGN}", "--json"))
    assert error["reason"] == "write_not_verified"
    assert len(error["notes"]) == 1 and ID_NOTE in error["notes"][0]


def test_a_success_prints_its_notes_after_the_result(double):
    r = _run("add-journal-entry", "--date", DATE, "--content", "entry", "--json")
    assert r.exit_code == 0, r.stderr
    assert json.loads(r.stdout)["blocks_added"] == 1
    assert r.stderr.startswith(DEPRECATED)


def test_an_error_without_notes_has_no_notes_field(double):
    double.set_mode("updateBlock", "noop")
    error = _error(_run("update-block", "--id", double.uuid_of("alpha block"),
                        "--content", "new text", "--json"))
    assert "notes" not in error


def test_without_json_notes_go_out_at_once(double):
    double.set_mode("updateBlock", "noop")
    r = _run("update-block", "--id", double.uuid_of("alpha block"), "--content",
             f"new text\nid:: {FOREIGN}")
    assert r.exit_code == 1
    first, *rest = r.stderr.splitlines()
    assert ID_NOTE in first
    assert rest[-1].startswith("Error: ")
