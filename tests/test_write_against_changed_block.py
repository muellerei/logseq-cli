"""A write that replaces text checks the block still holds that text.

A caller that changes a block reads it, builds the new text from what it read
and writes the whole text back. A change made to the block in between, by
another call of the CLI or by Logseq itself, would be written over, and the
proof afterwards reads back what was just sent, so it passes. ``update_block``
reads the block again, past the cache, directly before the write when it is
told what it replaces (``replacing``), and refuses when the text is another.

Same double, same real ``LogseqAPI`` as the other write tests: the change is
made by a request the double answers, between the reads.
"""
import json

import pytest

from logseq_cli.api import BlockChanged, LogseqAPI
from logseq_cli.blocktext import SplitBlockError, block_hash
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

URL = "http://127.0.0.1:12315/api"
UUID = "00000000-0000-4000-8000-0000000000aa"
FOREIGN = "TODO x\nnote added elsewhere"


def _update(double, text):
    """A change made from outside, as a request the double answers."""
    double.post(URL, json={"method": "logseq.Editor.updateBlock", "args": [UUID, text]},
                headers={"Authorization": "Bearer t"}, timeout=30)


def _content(double):
    return double.post(
        URL, json={"method": "logseq.Editor.getBlock", "args": [UUID, {}]},
        headers={"Authorization": "Bearer t"}, timeout=30).json()["content"]


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {
        "Probe Page": [{"content": "TODO x", "marker": "TODO", "uuid": UUID}]})


@pytest.fixture
def api(double):
    api = LogseqAPI(token="t")
    api.cache_enabled = False
    return api


def test_a_block_changed_since_it_was_read_is_refused(double, api):
    _update(double, FOREIGN)
    with pytest.raises(BlockChanged) as refused:
        api.update_block(UUID, "DONE x", replacing="TODO x")
    assert refused.value.reason == "block_changed"
    assert refused.value.fields == {"block": UUID}
    assert _content(double) == FOREIGN, "the change made elsewhere must stay"
    assert len(double.sent("updateBlock")) == 1, "only the change made elsewhere was written"


def test_a_block_as_it_was_read_is_written(double, api):
    api.update_block(UUID, "DONE x", replacing="TODO x")
    assert _content(double) == "DONE x"


def test_an_empty_text_is_a_text_to_compare(double, api):
    """The property-block path replaces "", which is not "no check"."""
    with pytest.raises(BlockChanged):
        api.update_block(UUID, "DONE x", replacing="")
    assert _content(double) == "TODO x"


def test_a_write_that_replaces_nothing_is_not_compared(double, api):
    api.update_block(UUID, "something else")
    assert _content(double) == "something else"


def test_set_todo_status_does_not_write_over_a_change_between_its_reads(double, monkeypatch):
    """The command reads the block, a change lands, then it writes.

    Through the command, cache on: the second read has to go past the cache
    the first one filled.
    """
    original = double.post
    changed = []

    def post(url, json=None, **kwargs):
        answer = original(url, json=json, **kwargs)
        if json["method"] == "logseq.Editor.getBlock" and not changed:
            changed.append(True)
            _update(double, FOREIGN)
        return answer

    monkeypatch.setattr("logseq_cli.api.requests.post", post)
    result = split_runner().invoke(cli, [
        "--token", "t", "set-todo-status", "--id", UUID, "--status", "DONE", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.stderr)["reason"] == "block_changed"
    assert _content(double) == FOREIGN
    assert len(double.sent("updateBlock")) == 1


SPLIT = "TODO x\n- looks like a block of its own"


def test_expect_refuses_a_block_changed_since_the_check(double, api):
    _update(double, FOREIGN)
    with pytest.raises(BlockChanged) as refused:
        api.update_block(UUID, "DONE x", expect="TODO x")
    assert refused.value.reason == "block_changed"
    assert _content(double) == FOREIGN
    assert len(double.sent("updateBlock")) == 1, "only the change made elsewhere was written"


def test_expect_writes_a_block_as_it_was_checked(double, api):
    api.update_block(UUID, "DONE x", expect="TODO x")
    assert _content(double) == "DONE x"


def test_expect_gives_a_split_line_no_pass_even_when_the_block_holds_it(double, api):
    """Unlike ``replacing``, ``expect`` loosens nothing in the check on the text."""
    _update(double, SPLIT)
    changed = SPLIT.replace("TODO", "DONE")
    with pytest.raises(SplitBlockError):
        api.update_block(UUID, changed, expect=SPLIT)
    assert _content(double) == SPLIT
    api.update_block(UUID, changed, replacing=SPLIT)
    assert _content(double) == changed


def test_set_todo_status_with_a_precondition_still_holds_the_block_to_what_it_read(
        double, monkeypatch):
    """A change between the precondition check and the write is block_changed."""
    original = double.post
    reads = []

    def post(url, json=None, **kwargs):
        answer = original(url, json=json, **kwargs)
        if json["method"] == "logseq.Editor.getBlock":
            reads.append(True)
            # 1: the command's lookup, 2: the check's read past the cache.
            if len(reads) == 2:
                _update(double, FOREIGN)
        return answer

    monkeypatch.setattr("logseq_cli.api.requests.post", post)
    result = split_runner().invoke(cli, [
        "--token", "t", "set-todo-status", "--id", UUID, "--status", "DONE",
        "--expect-hash", block_hash("TODO x"), "--json"])
    assert result.exit_code != 0
    assert json.loads(result.stderr)["reason"] == "block_changed"
    assert _content(double) == FOREIGN
