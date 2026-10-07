"""set-todo-status --expect-hash refuses a block that is not what the caller read.

Same double and real ``LogseqAPI`` as the other write tests, so the cache, the
re-read and the write proof run as they do for a user.
"""
import json

import pytest

from logseq_cli.blocktext import block_hash
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

URL = "http://127.0.0.1:12315/api"
TASK = "00000000-0000-4000-8000-0000000000a1"
HOST = "11111111-0000-4000-8000-0000000000a2"
READ = "TODO ship the parser"
RIGHT = block_hash(READ)
WRONG = "0123456789ab"
NOTHING = "Nothing was written."


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {
        "Probe Page": [{"content": READ, "marker": "TODO", "uuid": TASK},
                       {"content": f"(({TASK}))", "uuid": HOST}]})


def _request(double, method, *args):
    return double.post(URL, json={"method": method, "args": list(args)},
                       headers={"Authorization": "Bearer t"}, timeout=30)


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", "set-todo-status", *args, "--json"])


def content(double, uuid=TASK):
    return _request(double, "logseq.Editor.getBlock", uuid, {}).json()["content"]


def refusal(result):
    assert result.exit_code != 0
    assert result.stdout == ""
    return json.loads(result.stderr)


@pytest.mark.parametrize("extra", [[], ["--dry-run"]], ids=["write", "dry-run"])
def test_a_wrong_hash_writes_nothing(double, extra):
    before = double.snapshot()
    error = refusal(run("--id", TASK, "--status", "DONE", "--expect-hash", WRONG, *extra))
    assert error["reason"] == "precondition_failed"
    assert double.writes() == []
    assert double.snapshot() == before


def test_a_wrong_hash_is_refused_where_nothing_would_change(double):
    """The status the block already has is a no-op; the check comes first."""
    error = refusal(run("--id", TASK, "--status", "TODO", "--expect-hash", WRONG))
    assert error["reason"] == "precondition_failed"


def test_the_right_hash_writes(double):
    result = run("--id", TASK, "--status", "DONE", "--expect-hash", RIGHT)
    assert result.exit_code == 0, result.stderr
    assert content(double) == "DONE ship the parser"


def test_the_right_hash_keeps_dry_run_and_no_op(double):
    dry = run("--id", TASK, "--status", "DONE", "--expect-hash", RIGHT, "--dry-run")
    assert dry.exit_code == 0 and json.loads(dry.stdout)["dry_run"] is True
    same = run("--id", TASK, "--status", "TODO", "--expect-hash", RIGHT)
    assert same.exit_code == 0 and json.loads(same.stdout)["status"] == "unchanged"
    assert double.writes() == []


def test_the_hash_may_be_typed_in_capitals(double):
    assert run("--id", TASK, "--status", "DONE", "--expect-hash", RIGHT.upper()).exit_code == 0


def test_a_selection_by_text_with_a_wrong_hash_is_refused_though_the_text_fits(double):
    error = refusal(run("--content", "ship the parser", "--page", "Probe Page",
                        "--status", "DONE", "--expect-hash", WRONG))
    assert error["reason"] == "precondition_failed"
    assert content(double) == READ


def test_a_selection_by_text_with_the_right_hash_writes(double):
    result = run("--content", "ship the parser", "--page", "Probe Page",
                 "--status", "DONE", "--expect-hash", RIGHT)
    assert result.exit_code == 0, result.stderr
    assert content(double) == "DONE ship the parser"


def test_follow_refs_checks_the_target_not_the_host(double):
    error = refusal(run("--id", HOST, "--status", "DONE", "--follow-refs",
                        "--expect-hash", block_hash(f"(({TASK}))")))
    assert error["reason"] == "precondition_failed"
    assert TASK[:8] in error["error"], "the message names the target"
    assert HOST[:8] not in error["error"]
    assert double.writes() == []


def test_follow_refs_with_the_hash_of_the_target_writes(double):
    result = run("--id", HOST, "--status", "DONE", "--follow-refs", "--expect-hash", RIGHT)
    assert result.exit_code == 0, result.stderr
    assert content(double) == "DONE ship the parser"
    assert content(double, HOST) == f"(({TASK}))"


def test_the_refusal_names_marker_and_first_line_and_never_the_current_hash(double):
    _request(double, "logseq.Editor.updateBlock", TASK, "DOING ship it\nnote")
    result = run("--id", TASK, "--status", "DONE", "--expect-hash", RIGHT)
    error = refusal(result)
    assert error["expected_hash"] == RIGHT
    assert error["actual_marker"] == "DOING"
    assert error["first_line"] == "DOING ship it"
    assert "DOING" in error["error"] and "ship it" in error["error"]
    assert "actual_hash" not in error
    assert block_hash(content(double)) not in result.stderr
    assert error["error"].count(NOTHING) == 1
    assert error["writes_landed"] == 0


def _after_first_block_read(double, monkeypatch, change):
    """Run ``change`` right after the command's first getBlock answered."""
    original = double.post
    seen = []

    def post(url, json=None, **kwargs):
        answer = original(url, json=json, **kwargs)
        if json["method"] == "logseq.Editor.getBlock" and not seen:
            seen.append(True)
            change()
        return answer

    monkeypatch.setattr("logseq_cli.api.requests.post", post)


def test_the_check_reads_past_the_cache(double, monkeypatch):
    """The first read fills the cache; a change lands; the check has to see it.
    Read from the cache it would pass, and only the re-read right before the
    write (block_changed) would stop the command."""
    _after_first_block_read(double, monkeypatch, lambda: _request(
        double, "logseq.Editor.updateBlock", TASK, "TODO ship the parser, changed"))
    error = refusal(run("--id", TASK, "--status", "DONE", "--expect-hash", RIGHT))
    assert error["reason"] == "precondition_failed"
    assert content(double) == "TODO ship the parser, changed"


def test_a_block_gone_since_it_was_found_is_block_not_found(double, monkeypatch):
    _after_first_block_read(double, monkeypatch, lambda: _request(
        double, "logseq.Editor.removeBlock", TASK))
    error = refusal(run("--id", TASK, "--status", "DONE", "--expect-hash", RIGHT))
    assert error["reason"] == "block_not_found"
    assert error["id"] == TASK
    assert error["error"].count(NOTHING) == 1
    assert double.sent("updateBlock") == []
