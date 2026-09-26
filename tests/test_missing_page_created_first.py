"""A write to a page that does not exist creates it first, without a block.

``appendBlockInPage`` creates a missing page itself, and the page then starts
with an empty block before the one written (measured, 0.10.15). The writers
that name a page create it first with ``createFirstBlock: false``, as
``add-note-content`` does, so the file holds only what was written:
``insert-block --page``, ``add-block-ref --page`` and ``copy-block --to-page``.
A page that exists is not created, and a preview creates nothing. That a
``--keep-ids`` write asks whether a block is open before it creates the page
is tested in test_editor_gate against the real LogseqAPI, where the question
is asked: a mock of it cannot see that.
"""
import pytest

from logseq_cli.cli import cli
from tests.conftest import mock_api, split_runner

MISSING = "Reading List"
SOURCE = "abcdef12-3456-7890-abcd-ef1234567890"


def _api(monkeypatch, *, page_exists=False):
    api = mock_api()
    order = []
    pages = {}
    if page_exists:
        pages[MISSING.lower()] = {"name": MISSING.lower(), "originalName": MISSING,
                                  "uuid": "p1", "file": {"path": "pages/x.md"}}

    def get_page(name):
        return pages.get(str(name).lower())

    def create_page(name, properties=None, *, first_block=True):
        order.append(("create", name, first_block))
        pages[name.lower()] = {"name": name.lower(), "originalName": name, "uuid": "p1"}
        return pages[name.lower()]

    counter = iter(f"u{i}" for i in range(1, 100))

    def append(name, content, options=None):
        order.append(("append", name))
        return {"uuid": next(counter)}

    def insert(target, content, options=None):
        order.append(("insert", target))
        return {"uuid": next(counter)}

    api.get_page.side_effect = get_page
    api.create_page.side_effect = create_page
    api.append_block_in_page.side_effect = append
    api.insert_block.side_effect = insert
    api.get_page_blocks_tree.return_value = []
    api.datascript_query.return_value = []
    api.order = order
    monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
    monkeypatch.delenv("LOGSEQ_JOURNAL_HEADING", raising=False)
    return api


def _run(*args):
    return split_runner().invoke(cli, ["--token", "t", *args])


WRITES = {
    "insert-block flat": ["insert-block", "--page", MISSING, "--content", "a note"],
    "insert-block outline": ["insert-block", "--page", MISSING,
                             "--content", "- parent\n  - child"],
    "insert-block tree": ["insert-block", "--page", MISSING, "--top-level",
                          "--tree", '[{"content": "a note"}]'],
    "add-block-ref": ["add-block-ref", "--source-id", SOURCE, "--page", MISSING],
    "copy-block": ["copy-block", "--id", SOURCE, "--to-page", MISSING],
}


def _source(api):
    api.get_block.return_value = {"uuid": SOURCE, "content": "the source",
                                  "page": {"id": 7}, "children": []}


@pytest.mark.parametrize("args", WRITES.values(), ids=WRITES.keys())
def test_missing_page_is_created_before_the_first_write(monkeypatch, args):
    api = _api(monkeypatch)
    _source(api)
    result = _run(*args)
    assert result.exit_code == 0, result.stderr
    assert api.order[0] == ("create", MISSING, False), api.order
    assert len(api.order) > 1 and api.order[1][0] in ("append", "insert")


@pytest.mark.parametrize("args", WRITES.values(), ids=WRITES.keys())
def test_page_that_exists_is_not_created(monkeypatch, args):
    api = _api(monkeypatch, page_exists=True)
    _source(api)
    result = _run(*args)
    assert result.exit_code == 0, result.stderr
    api.create_page.assert_not_called()


@pytest.mark.parametrize("args", WRITES.values(), ids=WRITES.keys())
def test_preview_creates_nothing(monkeypatch, args):
    api = _api(monkeypatch)
    _source(api)
    result = _run(*args, "--dry-run")
    assert result.exit_code == 0, result.stderr
    api.create_page.assert_not_called()
    api.append_block_in_page.assert_not_called()


def test_add_block_ref_preview_says_the_page_would_be_created(monkeypatch):
    api = _api(monkeypatch)
    _source(api)
    result = _run(*WRITES["add-block-ref"], "--dry-run", "--json")
    assert result.exit_code == 0, result.stderr
    assert '"would_create_page": true' in result.stdout


def test_add_block_ref_under_heading_creates_the_page_before_the_heading(monkeypatch):
    api = _api(monkeypatch)
    _source(api)
    result = _run(*WRITES["add-block-ref"], "--under-heading", "## Refs")
    assert result.exit_code == 0, result.stderr
    assert api.order[0] == ("create", MISSING, False), api.order
