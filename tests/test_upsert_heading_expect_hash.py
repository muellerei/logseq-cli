"""add-journal-block --upsert-heading --expect-hash: the precondition is for the
block the upsert replaces. With no match there is no block and no hash: the
precondition has no object, and the block is created. Under
``require_preconditions`` it is owed only when a block matches.

Run through the real ``LogseqAPI`` against the HTTP double.
"""
import json

import pytest

import logseq_cli.api as api_module
from logseq_cli.blocktext import block_hash
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

JOURNAL = "2099-01-05, Monday"
OLD = "### Carol\nold"
RIGHT = block_hash(OLD)
WRONG = "0123456789ab"
ON = "[safety]\nrequire_preconditions = true\n"

UPSERT = ["add-journal-block", "--date", "2099-01-05", "--under-heading", "## Log",
          "--upsert-heading", "### Carol", "--content", "### Carol\nnew"]


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", *args, "--json"])


def log_with(*children):
    return [{"content": "## Log", "children": list(children)}]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.delenv("LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST", raising=False)
    path = tmp_path / "off.toml"
    path.write_text("[safety]\nread_only = false\n", encoding="utf-8")
    monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(path))


@pytest.fixture
def switch_on(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(ON, encoding="utf-8")
    monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(path))


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {JOURNAL: log_with(OLD)})


@pytest.mark.parametrize("extra", [[], ["--dry-run"]], ids=["write", "dry-run"])
def test_a_wrong_hash_on_a_match_writes_nothing(double, extra):
    before = double.snapshot()
    result = run(*UPSERT, "--expect-hash", WRONG, *extra)
    assert result.exit_code != 0 and result.stdout == ""
    assert json.loads(result.stderr)["reason"] == "precondition_failed"
    assert double.writes() == []
    assert double.snapshot() == before


def test_a_hash_with_no_journal_page_has_no_object(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {"Other": ["x"]})
    # No page, so no match: nothing to refuse, the page is made.
    result = run(*UPSERT, "--expect-hash", WRONG)
    assert result.exit_code == 0, result.stderr
    assert double.uuid_of("### Carol\nnew")


def test_the_right_hash_replaces_the_block(double):
    result = run(*UPSERT, "--expect-hash", RIGHT)
    assert result.exit_code == 0, result.stderr
    assert "(updated)" in json.loads(result.stdout)["position"]
    assert double.uuid_of("### Carol\nnew")


def test_no_match_creates_the_block_and_says_the_hash_has_no_object(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: log_with("### Dave\nx")})
    result = run(*UPSERT, "--expect-hash", WRONG)
    assert result.exit_code == 0, result.stderr
    assert "(created)" in json.loads(result.stdout)["position"]
    assert "no object" in result.stderr
    assert double.uuid_of("### Carol\nnew")


def test_no_match_creates_under_the_switch_without_an_option(monkeypatch, switch_on):
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: log_with("### Dave\nx")})
    result = run(*UPSERT)
    assert result.exit_code == 0, result.stderr
    assert "(created)" in json.loads(result.stdout)["position"]
    assert double.uuid_of("### Carol\nnew")


def test_no_page_creates_under_the_switch_without_an_option(monkeypatch, switch_on):
    double = LogseqHttpDouble.installed(monkeypatch, {})
    result = run(*UPSERT)
    assert result.exit_code == 0, result.stderr
    assert double.uuid_of("### Carol\nnew")


@pytest.mark.parametrize("extra", [[], ["--dry-run"]], ids=["write", "dry-run"])
def test_a_match_under_the_switch_without_an_option_is_required(double, switch_on, extra):
    before = double.snapshot()
    result = run(*UPSERT, *extra)
    assert result.exit_code != 0 and result.stdout == ""
    error = json.loads(result.stderr)
    assert error["reason"] == "precondition_required"
    assert error["options"] == ["--expect-hash"]
    assert "require_preconditions" in error["error"]
    assert double.writes() == []
    assert double.snapshot() == before


def test_a_match_under_the_switch_with_the_right_hash_writes(double, switch_on):
    result = run(*UPSERT, "--expect-hash", RIGHT)
    assert result.exit_code == 0, result.stderr
    assert double.uuid_of("### Carol\nnew")


def test_read_only_wins_over_the_missing_option(double, switch_on):
    result = run("--read-only", *UPSERT)
    assert json.loads(result.stderr)["reason"] == "read_only"


def test_a_hash_without_upsert_heading_is_a_usage_error(double):
    result = run("add-journal-block", "--date", "2099-01-05", "--content", "x",
                 "--expect-hash", RIGHT)
    assert result.exit_code == 2
    assert "--upsert-heading" in result.stderr
    assert double.writes() == []


def test_the_write_is_held_to_the_text_the_hash_was_checked_on(double, monkeypatch):
    seen = []
    original = api_module.LogseqAPI.update_block

    def spy(self, *args, **kwargs):
        seen.append(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(api_module.LogseqAPI, "update_block", spy)
    assert run(*UPSERT, "--expect-hash", RIGHT).exit_code == 0
    assert seen[0].get("expect") == OLD


def test_without_a_hash_the_write_is_not_held_to_a_text(double, monkeypatch):
    seen = []
    original = api_module.LogseqAPI.update_block

    def spy(self, *args, **kwargs):
        seen.append(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(api_module.LogseqAPI, "update_block", spy)
    assert run(*UPSERT).exit_code == 0
    assert seen and seen[0].get("expect") is None
