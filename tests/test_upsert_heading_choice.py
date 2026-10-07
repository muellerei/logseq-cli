"""Which block ``--upsert-heading`` replaces is chosen once, first, and fresh.

The choice used to follow the creation of the journal page and the heading,
came from a cached read, and in a preview happened only with
``[graph] keep_empty_blocks_last`` on. A choice made later or from a stale
read can overwrite another block than the one the user saw; a preview that
chooses differently from the run shows something the run does not do.

Run through the real ``LogseqAPI`` against the HTTP double.
"""
import json

import pytest

import logseq_cli.commands.journal as journal
from logseq_cli.api import LogseqAPI
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

JOURNAL = "2099-01-05, Monday"
ENV = "LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST"
READS = ("logseq.Editor.getPageBlocksTree", "logseq.Editor.getBlock")

UPSERT = ["add-journal-block", "--date", "2099-01-05", "--under-heading", "## Log",
          "--upsert-heading", "### Carol", "--content", "### Carol\nnew"]


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", *args, "--json"])


def log_with(*children):
    return [{"content": "## Log", "children": list(children)}]


@pytest.fixture(autouse=True)
def _keep_off(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setenv("LOGSEQ_CLI_CACHE_TTL", "60")


def test_the_choice_does_not_come_from_the_cache(monkeypatch):
    double = LogseqHttpDouble.installed(
        monkeypatch, {JOURNAL: log_with("### Carol\nold")})
    real_call = LogseqAPI.call

    def stale(self, method, args=None, *, cached=True):
        answer = real_call(self, method, args, cached=cached)
        if cached and method in READS and not double.writes():
            # What the cache would still hold before the write: the block
            # under another name.
            return json.loads(json.dumps(answer).replace("Carol", "Dave"))
        return answer

    monkeypatch.setattr(LogseqAPI, "call", stale)
    r = run(*UPSERT)
    assert r.exit_code == 0, r.stderr
    assert "(updated)" in json.loads(r.stdout)["position"]
    assert double.uuid_of("### Carol\nnew")


def test_the_preview_chooses_the_way_the_run_does_with_the_setting_off(monkeypatch):
    LogseqHttpDouble.installed(monkeypatch, {JOURNAL: log_with("### Carol\nold")})
    chosen = []
    real_choice = journal.find_upsert_target

    def record(*args):
        chosen.append((args[1:], real_choice(*args)))
        return chosen[-1][1]

    monkeypatch.setattr(journal, "find_upsert_target", record)
    assert run(*UPSERT, "--dry-run").exit_code == 0
    assert run(*UPSERT).exit_code == 0
    preview, live = chosen
    assert preview == live
    assert preview[1][1] is not None


@pytest.mark.parametrize("graph", [{}, {JOURNAL: ["top"]}],
                         ids=["no journal page", "no heading"])
def test_nothing_is_written_when_the_choice_is_made(monkeypatch, graph):
    double = LogseqHttpDouble.installed(monkeypatch, graph)
    written_before = []
    real_choice = journal.find_upsert_target

    def record(*args):
        written_before.append(list(double.writes()))
        return real_choice(*args)

    monkeypatch.setattr(journal, "find_upsert_target", record)
    r = run(*UPSERT)
    assert r.exit_code == 0, r.stderr
    assert written_before == [[]]
    assert double.uuid_of("### Carol\nnew")
