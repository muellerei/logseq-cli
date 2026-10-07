"""--expect-hash on the four writes that act on a block the caller read:
set-block-property, remove-property --id, move-block, copy-block --remove.

A right hash lets the call through, a wrong one refuses before the first write
(copy-block --remove: before the first copy). A call with no block to read
(remove-property --page, copy-block without --remove) takes no precondition
and refuses the option as a usage error instead of ignoring it. Under
``require_preconditions`` the four owe one; the free calls do not.
"""
import json

import pytest

from logseq_cli.blocktext import block_hash
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

TASK = "00000000-0000-4000-8000-0000000000a1"
OTHER = "00000000-0000-4000-8000-0000000000a2"
READ = "TODO ship the parser\nprio:: high"
RIGHT = block_hash(READ)
WRONG = "0123456789ab"

CALLS = {
    "set-block-property": ["set-block-property", "--id", TASK, "--key", "prio", "--value", "low"],
    "remove-property": ["remove-property", "--id", TASK, "--key", "prio"],
    "move-block": ["move-block", "--id", TASK, "--under", OTHER],
    "copy-block": ["copy-block", "--id", TASK, "--to-page", "Other Page", "--remove"],
}


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {
        "Probe Page": [{"content": READ, "marker": "TODO", "uuid": TASK,
                        "children": [{"content": "a child"}]},
                       {"content": "the new parent", "uuid": OTHER}],
        "Other Page": [{"content": "already here"}]})


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", *args, "--json"])


@pytest.mark.parametrize("name", CALLS)
def test_the_hash_that_was_read_lets_the_call_through(double, name):
    result = run(*CALLS[name], "--expect-hash", RIGHT)
    assert result.exit_code == 0, result.stderr
    assert double.writes() != []


@pytest.mark.parametrize("extra", [[], ["--dry-run"]], ids=["write", "dry-run"])
@pytest.mark.parametrize("name", CALLS)
def test_another_hash_refuses_without_a_write(double, name, extra):
    before = double.snapshot()
    result = run(*CALLS[name], "--expect-hash", WRONG, *extra)
    assert result.exit_code != 0 and result.stdout == ""
    error = json.loads(result.stderr)
    assert error["reason"] == "precondition_failed"
    assert error["expected_hash"] == WRONG
    assert RIGHT not in result.stderr
    assert double.writes() == []
    assert double.snapshot() == before


def test_copy_block_with_a_wrong_hash_makes_no_copy(double):
    run(*CALLS["copy-block"], "--expect-hash", WRONG)
    assert double.sent("logseq.Editor.insertBlock") == []
    assert double.sent("logseq.Editor.appendBlockInPage") == []
    assert double.tree("Other Page") == [("already here", [])]


class TestOwedUnderTheSwitch:
    @pytest.fixture(autouse=True)
    def on(self, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_REQUIRE_PRECONDITIONS", "true")

    @pytest.mark.parametrize("name", CALLS)
    def test_the_call_refuses_without_one(self, double, name):
        result = run(*CALLS[name])
        error = json.loads(result.stderr)
        assert result.exit_code != 0
        assert error["reason"] == "precondition_required"
        assert error["options"] == ["--expect-hash"]
        assert double.writes() == []

    @pytest.mark.parametrize("name", CALLS)
    def test_the_hash_satisfies_it(self, double, name):
        assert run(*CALLS[name], "--expect-hash", RIGHT).exit_code == 0

    def test_remove_property_on_a_page_is_free(self, double):
        double.add_page("Props Page", [{"content": "prio:: high"}])
        result = run("remove-property", "--page", "Props Page", "--key", "prio")
        assert result.exit_code == 0, result.stderr

    def test_copy_block_without_remove_is_free(self, double):
        result = run("copy-block", "--id", TASK, "--to-page", "Other Page")
        assert result.exit_code == 0, result.stderr


class TestWithoutABlockToRead:
    @pytest.mark.parametrize("args", [
        ["remove-property", "--page", "Probe Page", "--key", "prio", "--expect-hash", RIGHT],
        ["copy-block", "--id", TASK, "--to-page", "Other Page", "--expect-hash", RIGHT],
    ], ids=["remove-property-page", "copy-block-without-remove"])
    def test_the_option_is_a_usage_error(self, double, args):
        before = double.snapshot()
        result = split_runner().invoke(cli, ["--token", "t", *args])
        assert result.exit_code == 2
        assert "--expect-hash" in result.stderr
        assert double.writes() == []
        assert double.snapshot() == before
