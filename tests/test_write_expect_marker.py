"""set-todo-status --expect-marker refuses a block whose marker is not the one the caller read.

Same double and real ``LogseqAPI`` as the other write tests.
"""
import json

import pytest

from logseq_cli.blocktext import block_hash
from logseq_cli.cli import cli
from logseq_cli.tasks import ORDER
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

URL = "http://127.0.0.1:12315/api"
TASK = "00000000-0000-4000-8000-0000000000a1"
PLAIN = "00000000-0000-4000-8000-0000000000a3"
READ = "TODO ship the parser"
RIGHT_HASH = block_hash(READ)
WRONG_HASH = "0123456789ab"


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {
        "Probe Page": [{"content": READ, "marker": "TODO", "uuid": TASK},
                       {"content": "just a note", "uuid": PLAIN}]})


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", "set-todo-status", *args, "--json"])


def content(double, uuid=TASK):
    return double.post(URL, json={"method": "logseq.Editor.getBlock", "args": [uuid, {}]},
                       headers={"Authorization": "Bearer t"}, timeout=30).json()["content"]


def refusal(result):
    assert result.exit_code != 0
    assert result.stdout == ""
    return json.loads(result.stderr)


@pytest.mark.parametrize("given", ["TODO", "todo", "Todo"])
def test_the_marker_it_has_writes_whatever_the_case(double, given):
    result = run("--id", TASK, "--status", "DONE", "--expect-marker", given)
    assert result.exit_code == 0, result.stderr
    assert content(double) == "DONE ship the parser"


@pytest.mark.parametrize("extra", [[], ["--dry-run"]], ids=["write", "dry-run"])
def test_another_marker_writes_nothing(double, extra):
    before = double.snapshot()
    error = refusal(run("--id", TASK, "--status", "DONE", "--expect-marker", "DOING", *extra))
    assert error["reason"] == "precondition_failed"
    assert error["expected_marker"] == "DOING"
    assert error["actual_marker"] == "TODO"
    assert "expected_hash" not in error
    assert double.writes() == []
    assert double.snapshot() == before


def test_another_marker_is_refused_where_nothing_would_change(double):
    error = refusal(run("--id", TASK, "--status", "TODO", "--expect-marker", "DONE"))
    assert error["reason"] == "precondition_failed"


def test_none_on_a_block_with_a_marker_is_refused(double):
    error = refusal(run("--id", TASK, "--status", "DONE", "--expect-marker", "none"))
    assert error["reason"] == "precondition_failed"
    assert error["expected_marker"] == "NONE"
    assert error["actual_marker"] == "TODO"
    assert double.writes() == []


def test_a_marker_on_a_block_without_one_reports_null(double):
    error = refusal(run("--id", PLAIN, "--status", "DONE", "--expect-marker", "TODO"))
    assert error["reason"] == "precondition_failed"
    assert error["actual_marker"] is None
    assert "marker is none" in error["error"]


def test_none_on_a_block_without_a_marker_passes_the_check(double):
    """The check passes; the command then refuses the block as no task, as it always does."""
    error = refusal(run("--id", PLAIN, "--status", "DONE", "--expect-marker", "none"))
    assert error["reason"] != "precondition_failed"


def test_the_marker_and_the_hash_must_both_match(double):
    ok = run("--id", TASK, "--status", "DONE", "--expect-marker", "TODO",
             "--expect-hash", RIGHT_HASH, "--dry-run")
    assert ok.exit_code == 0, ok.stderr

    bad_hash = refusal(run("--id", TASK, "--status", "DONE", "--expect-marker", "TODO",
                           "--expect-hash", WRONG_HASH))
    assert bad_hash["expected_hash"] == WRONG_HASH
    assert bad_hash["expected_marker"] == "TODO"

    bad_marker = refusal(run("--id", TASK, "--status", "DONE", "--expect-marker", "DOING",
                             "--expect-hash", RIGHT_HASH))
    assert bad_marker["expected_marker"] == "DOING"
    assert double.writes() == []


def test_the_marker_is_read_past_the_cache(double, monkeypatch):
    original = double.post
    seen = []

    def post(url, json=None, **kwargs):
        answer = original(url, json=json, **kwargs)
        if json["method"] == "logseq.Editor.getBlock" and not seen:
            seen.append(True)
            original(url, json={"method": "logseq.Editor.updateBlock",
                                "args": [TASK, "CANCELED ship the parser"]}, **kwargs)
        return answer

    monkeypatch.setattr("logseq_cli.api.requests.post", post)
    error = refusal(run("--id", TASK, "--status", "DONE", "--expect-marker", "TODO"))
    assert error["reason"] == "precondition_failed"
    assert error["actual_marker"] == "CANCELED"
    assert content(double) == "CANCELED ship the parser"


@pytest.mark.parametrize("marker", ORDER)
def test_every_marker_of_the_task_model_is_accepted(double, marker):
    result = run("--id", TASK, "--status", "DONE", "--expect-marker", marker, "--dry-run")
    if result.exit_code:
        assert json.loads(result.stderr)["reason"] == "precondition_failed"


def test_an_unknown_marker_is_a_usage_error_naming_the_known_ones(double):
    result = run("--id", TASK, "--status", "DONE", "--expect-marker", "MAYBE")
    assert result.exit_code == 2
    assert double.writes() == []
    for marker in ORDER:
        assert marker in result.stderr
    assert "none" in result.stderr
