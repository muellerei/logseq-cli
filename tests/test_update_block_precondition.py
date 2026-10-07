"""update-block --expect-hash refuses a block that is not what the caller read.

Same double and real ``LogseqAPI`` as the other write tests.
"""
import json

import pytest

from logseq_cli.blocktext import block_hash
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

URL = "http://127.0.0.1:12315/api"
TARGET = "00000000-0000-4000-8000-0000000000a1"
OTHER = "00000000-0000-4000-8000-0000000000b2"
READ = "plain note"
RIGHT = block_hash(READ)
WRONG = "0123456789ab"


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {
        "Probe Page": [{"content": READ, "uuid": TARGET},
                       {"content": "referenced elsewhere", "uuid": OTHER}]})


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", "update-block", *args, "--json"])


def content(double, uuid=TARGET):
    return double.post(URL, json={"method": "logseq.Editor.getBlock", "args": [uuid, {}]},
                       headers={"Authorization": "Bearer t"}, timeout=30).json()["content"]


@pytest.mark.parametrize("extra", [[], ["--dry-run"]], ids=["write", "dry-run"])
def test_a_wrong_hash_writes_nothing(double, extra):
    before = double.snapshot()
    result = run("--id", TARGET, "--content", "new", "--expect-hash", WRONG, *extra)
    assert result.exit_code != 0 and result.stdout == ""
    assert json.loads(result.stderr)["reason"] == "precondition_failed"
    assert double.writes() == []
    assert double.snapshot() == before


def test_a_wrong_hash_with_a_ref_gives_the_target_no_id(double):
    """The refusal comes before the write that would store the ref target's id."""
    before = double.snapshot()
    result = run("--id", TARGET, "--content", f"see (({OTHER}))", "--expect-hash", WRONG)
    assert json.loads(result.stderr)["reason"] == "precondition_failed"
    assert double.sent("setBlocksId") == []
    assert double.snapshot() == before


def test_a_wrong_hash_is_refused_with_where_content(double):
    result = run("--where-content", READ, "--content", "new", "--expect-hash", WRONG)
    assert json.loads(result.stderr)["reason"] == "precondition_failed"
    assert double.writes() == []


def test_the_right_hash_writes(double):
    result = run("--id", TARGET, "--content", "new", "--expect-hash", RIGHT)
    assert result.exit_code == 0, result.stderr
    assert content(double) == "new"


def test_the_right_hash_with_where_content_writes(double):
    result = run("--where-content", READ, "--content", "new", "--expect-hash", RIGHT)
    assert result.exit_code == 0, result.stderr
    assert content(double) == "new"


def test_a_change_after_the_check_is_block_changed(double, monkeypatch):
    original = double.post
    reads = []

    def post(url, json=None, **kwargs):
        answer = original(url, json=json, **kwargs)
        if json["method"] == "logseq.Editor.getBlock":
            reads.append(True)
            if len(reads) == 2:  # the check's read past the cache
                original(url, json={"method": "logseq.Editor.updateBlock",
                                    "args": [TARGET, "changed elsewhere"]},
                         headers={"Authorization": "Bearer t"}, timeout=30)
        return answer

    monkeypatch.setattr("logseq_cli.api.requests.post", post)
    result = run("--id", TARGET, "--content", "new", "--expect-hash", RIGHT)
    assert json.loads(result.stderr)["reason"] == "block_changed"
    assert content(double) == "changed elsewhere"


def test_without_a_precondition_the_write_is_not_held_to_a_text(double, monkeypatch):
    """Behaviour unchanged: update_block gets no text to compare."""
    import logseq_cli.api as api_module
    seen = []
    original = api_module.LogseqAPI.update_block

    def spy(self, *args, **kwargs):
        seen.append(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(api_module.LogseqAPI, "update_block", spy)
    assert run("--id", TARGET, "--content", "new").exit_code == 0
    assert seen and seen[0].get("expect") is None and seen[0].get("replacing") is None
