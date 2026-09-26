"""A page a write creates is named once, and every step uses that name.

Logseq's ``create!`` cleans a name in one pass: ``#[[X]]`` becomes ``[[X]]``,
since the brackets are unwrapped only around the whole name, before the ``#``
goes (handler/page.cljs ``create!``; measured, 0.10.15). The cleaning is not
idempotent: ``[[X]]`` sent again becomes ``X``. The commands cleaned the name
and ``LogseqAPI.create_page`` cleaned it once more, so the page came out as
``X`` while the write, the check and the output named ``[[X]]``: the block was
not written (write_not_verified), and create-page without --content reported
a page that did not exist.
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

ASKED = "#[[Alpha Beta]]"
CREATED = "[[Alpha Beta]]"


def _run(monkeypatch, args):
    double = LogseqHttpDouble.installed(monkeypatch, {"Source": ["the source"]})
    args = [a.replace("SOURCE", double.uuid_of("the source")) for a in args]
    result = split_runner().invoke(cli, ["--token", "t", *args, "--json"])
    return double, result


def _page_names(double):
    return {p["name"] for p in double.pages}


WRITES = {
    "add-note-content": ["add-note-content", "--page", ASKED, "--content", "hello"],
    "insert-block": ["insert-block", "--page", ASKED, "--content", "hello"],
    "insert-block keep-ids": ["insert-block", "--page", ASKED, "--content", "hello",
                              "--keep-ids"],
    "add-block-ref": ["add-block-ref", "--source-id", "SOURCE", "--page", ASKED],
    "copy-block": ["copy-block", "--id", "SOURCE", "--to-page", ASKED],
}


@pytest.mark.parametrize("args", WRITES.values(), ids=WRITES.keys())
def test_a_write_lands_on_the_page_it_created(monkeypatch, args):
    double, result = _run(monkeypatch, args)
    assert result.exit_code == 0, result.stderr
    assert _page_names(double) == {"Source", CREATED}
    assert len([b for b in double.tree(CREATED) if b[0]]) == 1


def test_create_page_with_content_writes_to_the_page_it_created(monkeypatch):
    double, result = _run(monkeypatch, ["create-page", "--page", ASKED, "--content", "hello"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["created"] == CREATED
    assert double.tree(CREATED) == [("hello", [])]
    assert _page_names(double) == {"Source", CREATED}


def test_create_page_reports_the_page_it_created(monkeypatch):
    double, result = _run(monkeypatch, ["create-page", "--page", ASKED])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["created"] == CREATED
    assert _page_names(double) == {"Source", CREATED}


def test_create_page_preview_names_the_page_the_run_creates(monkeypatch):
    double, result = _run(monkeypatch, ["create-page", "--page", ASKED, "--dry-run"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["page"] == CREATED
    assert double.writes() == []


@pytest.mark.parametrize("args", [["create-page", "--page", "\ufeffAlpha Beta"],
                                  ["add-note-content", "--page", "\ufeffAlpha Beta",
                                   "--content", "hello"]],
                         ids=["create-page", "add-note-content"])
def test_a_name_is_trimmed_as_logseq_trims_it(monkeypatch, args):
    # create! trims with JavaScript's trim, which takes a byte order mark
    # off and str.strip() does not: the page was made as "Alpha Beta" and
    # looked for under the name with the mark, a write that landed reported
    # as write_not_verified.
    double, result = _run(monkeypatch, args)
    assert result.exit_code == 0, result.stderr
    assert _page_names(double) == {"Source", "Alpha Beta"}
