"""A connection that fails mid-call says what the call wrote before it.

A refusal of a write names the writes of the same call that landed
(``writes_landed``), since there is no rollback and a retry would write them
again. A timeout, a dropped connection or an answer that is not JSON can
come just as late, after two blocks of five landed, and said only "Logseq
did not answer in time." The write in progress, sent and not yet proven,
may have landed or not, and a retry of the whole command can write it twice;
the error names it.
"""
import json

import pytest
import requests

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

FAILURES = {
    "timeout": requests.exceptions.Timeout("read timed out (double)"),
    "connection_refused": requests.exceptions.ConnectionError("reset (double)"),
}


def _fail_on(double, monkeypatch, method, error, *, from_call=1):
    """Let the ``from_call``-th request to ``method`` raise ``error``."""
    real = double._handlers[method]
    calls = []

    def handler(args):
        calls.append(args)
        if len(calls) >= from_call:
            raise error
        return real(args)
    monkeypatch.setitem(double._handlers, method, handler)


def _add_note(*content):
    return split_runner().invoke(cli, ["--token", "t", "add-note-content", "--page",
                                       "Probe Page", "--content", "\n".join(content),
                                       "--json"])


@pytest.mark.parametrize("reason", sorted(FAILURES))
def test_a_write_without_answer_after_one_that_landed(monkeypatch, reason):
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["a block"]})
    _fail_on(double, monkeypatch, "logseq.Editor.appendBlockInPage", FAILURES[reason],
             from_call=2)
    r = _add_note("- first", "- second")
    assert r.exit_code == 1
    error = json.loads(r.stderr)
    assert error["reason"] == reason
    assert (error["writes_landed"], error["unproven_write"]) == (1, "appendBlockInPage")
    assert "Whether appendBlockInPage landed is not known." in error["error"]
    assert "1 earlier write(s) in this call landed and remain" in error["error"]
    assert double.tree("Probe Page") == [("a block", []), ("first", [])]


def test_the_first_write_without_answer_is_not_called_nothing(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["a block"]})
    _fail_on(double, monkeypatch, "logseq.Editor.appendBlockInPage", FAILURES["timeout"])
    r = _add_note("only")
    error = json.loads(r.stderr)
    assert (error["writes_landed"], error["unproven_write"]) == (0, "appendBlockInPage")
    assert "Nothing was written" not in error["error"]


def test_a_proof_without_answer_names_the_write_it_proves(monkeypatch):
    # The batch was answered, its read-back was not: the batch may have
    # landed, and the heading written before it did.
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["a block"]})
    _fail_on(double, monkeypatch, "logseq.Editor.getBlock", FAILURES["timeout"], from_call=2)
    r = split_runner().invoke(cli, ["--token", "t", "add-note-content", "--page",
                                    "Probe Page", "--content", "- first\n- second",
                                    "--under-heading", "## Log", "--json"])
    error = json.loads(r.stderr)
    assert error["reason"] == "timeout"
    assert (error["writes_landed"], error["unproven_write"]) == (1, "insertBatchBlock")
    assert "1 earlier write(s) in this call landed" in error["error"]
    assert double.sent("insertBatchBlock")


def test_a_question_after_a_proven_write_leaves_no_write_in_doubt(monkeypatch):
    # The insert is proven; the property write asks checkEditing first and
    # gets no answer, before anything of it is sent.
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["a block"]})
    double.check_editing_form = "timeout"
    r = split_runner().invoke(cli, ["--token", "t", "insert-block", "--after",
                                    double.uuid_of("a block"), "--content", "new one",
                                    "--property", "k=v", "--json"])
    error = json.loads(r.stderr)
    assert error["reason"] == "timeout"
    assert "unproven_write" not in error
    assert error["writes_landed"] == 1
    assert double.sent("upsertBlockProperty") == []


def test_a_refused_write_leaves_no_write_in_doubt(monkeypatch):
    # replace-text takes the refusal of the first block and goes on; the
    # second block's write gets no answer to its question, before it is sent.
    # The first was refused, not left unknown.
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["alpha one", "alpha two"]},
                                        modes={"updateBlock": "noop"})
    real = double._check_editing
    asked = []

    def check_editing():
        asked.append(1)
        if len(asked) >= 2:
            raise requests.exceptions.Timeout("checkEditing timed out (double)")
        return real()
    monkeypatch.setattr(double, "_check_editing", check_editing)
    r = split_runner().invoke(cli, ["--token", "t", "replace-text", "--page", "Probe Page",
                                    "--find", "alpha", "--replace", "beta", "--json"])
    error = json.loads(r.stderr)
    assert error["reason"] == "timeout"
    assert "unproven_write" not in error
    assert len(double.sent("updateBlock")) == 1


def test_a_failure_before_any_write_is_reported_as_before(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {"Probe Page": ["a block"]})
    _fail_on(double, monkeypatch, "logseq.Editor.getPage", FAILURES["timeout"])
    r = _add_note("only")
    error = json.loads(r.stderr)
    assert error == {"error": "Logseq did not answer in time.", "reason": "timeout"}
    assert double.writes() == []


@pytest.mark.parametrize("reason", sorted(FAILURES))
def test_a_later_write_leaves_the_batch_in_doubt(monkeypatch, reason):
    # --keep-ids on an empty page anchors the batch on an empty stand-in and
    # removes it when the batch fails. That removal went through the same
    # bookkeeping and landed, so it cleared the batch it followed: the
    # error named no write in doubt, and a retry wrote the text twice.
    double = LogseqHttpDouble.installed(monkeypatch, {"Empty Page": []})
    real = double._handlers["logseq.Editor.insertBatchBlock"]

    def landed_without_answer(args):
        real(args)
        raise FAILURES[reason]
    monkeypatch.setitem(double._handlers, "logseq.Editor.insertBatchBlock",
                        landed_without_answer)
    r = split_runner().invoke(cli, ["--token", "t", "add-note-content", "--page",
                                    "Empty Page", "--keep-ids", "--content", "one line",
                                    "--json"])
    assert r.exit_code == 1
    error = json.loads(r.stderr)
    assert error["reason"] == reason
    assert (error["writes_landed"], error["unproven_write"]) == (0, "insertBatchBlock")
    assert "Whether insertBatchBlock landed is not known." in error["error"]
    assert double.tree("Empty Page") == [("one line", [])]
