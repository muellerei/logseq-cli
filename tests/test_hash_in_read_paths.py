"""The hash in every read path that returns blocks.

A caller that reads a block with get-page, get-journal-range, find-block or
get-todos must be able to write to it afterwards without a second read, so each
of them carries the same ``hash`` that ``get-block --json`` does. One function
(``block_hash``) computes it from the raw ``content`` Logseq stored; these tests
pin that every path agrees with it and that none computes it from text another
step has already rewritten.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.blocktext import block_hash
from logseq_cli.cli import cli
from logseq_cli.preconditions import Expect, check_precondition
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

REF = "11111111-2222-3333-4444-555555555555"
DONE_LINE = '* State "DONE" from "TODO" [2026-03-02 Mon 09:15]'
RAW = f"TODO write the report\nSCHEDULED: <2026-03-02 Mon>\n:LOGBOOK:\n{DONE_LINE}\n:END:\ncollapsed:: true"


def _run(api, args):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, ["--token", "t", *args])


def _page_api(tree):
    api = MagicMock()
    api.get_page.return_value = {"name": "p"}
    api.get_page_blocks_tree.return_value = tree
    api.get_page_linked_references.return_value = []
    return api


def _tree():
    return [{"uuid": "a", "content": RAW,
             "children": [{"uuid": "b", "content": "child text",
                           "children": [{"uuid": "c", "content": "grandchild"}]}]}]


# --- get-page ----------------------------------------------------------------

def test_get_page_json_has_a_hash_on_every_block_and_child():
    result = _run(_page_api(_tree()), ["get-page", "--name", "p", "--json"])
    assert result.exit_code == 0, result.output
    top = json.loads(result.stdout)["blocks"][0]
    assert top["hash"] == block_hash(RAW)
    assert top["children"][0]["hash"] == block_hash("child text")
    assert top["children"][0]["children"][0]["hash"] == block_hash("grandchild")


def test_get_page_text_shows_no_hash():
    result = _run(_page_api(_tree()), ["get-page", "--name", "p"])
    assert block_hash("child text") not in result.stdout


def test_get_page_hash_is_taken_before_resolve_refs_rewrites_content():
    raw = f"see (({REF}))"
    api = _page_api([{"uuid": "a", "content": raw}])
    api.get_block.return_value = {"uuid": REF, "content": "target text",
                                  "page": {"originalName": "Elsewhere"}}
    result = _run(api, ["get-page", "--name", "p", "--resolve-refs", "--json"])
    assert result.exit_code == 0, result.output
    block = json.loads(result.stdout)["blocks"][0]
    assert block["content"] != raw
    assert block["hash"] == block_hash(raw)


def test_get_page_hash_survives_heading_and_outline_cuts():
    tree = [{"uuid": "h", "content": "## Plan", "properties": {"heading": 2},
             "children": [{"uuid": "x", "content": "step"}]}]
    for extra in (["--heading", "## Plan"], ["--outline"]):
        result = _run(_page_api(tree), ["get-page", "--name", "p", "--json", *extra])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["blocks"][0]["hash"] == block_hash("## Plan")


def test_get_page_backlinks_carry_no_hash():
    api = _page_api(_tree())
    api.get_page_linked_references.return_value = [
        [{"originalName": "Other"}, [{"uuid": "z", "content": "points at p"}]]]
    result = _run(api, ["get-page", "--name", "p", "--json"])
    backlinks = json.loads(result.stdout)["backlinks"]
    assert backlinks and "hash" not in json.dumps(backlinks)


def test_get_page_does_not_change_what_the_client_caches():
    """The client returns the same tree on a second read; the hash is on a
    copy, so a caller of the API sees no new key."""
    tree = _tree()
    _run(_page_api(tree), ["get-page", "--name", "p", "--json"])
    assert "hash" not in tree[0]


# --- get-journal-range -------------------------------------------------------

def _journal_api(tree):
    api = MagicMock()
    api.get_all_pages.return_value = [
        {"originalName": "Aug 1st, 2026", "name": "aug 1st, 2026", "journalDay": 20260801}]
    api.get_page_blocks_tree.return_value = tree
    return api


def test_get_journal_range_json_has_a_hash_on_blocks_and_children():
    result = _run(_journal_api(_tree()),
                  ["get-journal-range", "--from", "2026-08-01", "--to", "2026-08-01", "--json"])
    assert result.exit_code == 0, result.output
    top = json.loads(result.stdout)[0]["blocks"][0]
    assert top["hash"] == block_hash(RAW)
    assert top["children"][0]["children"][0]["hash"] == block_hash("grandchild")


def test_get_journal_range_hash_is_taken_before_resolve_refs():
    raw = f"see (({REF}))"
    api = _journal_api([{"uuid": "a", "content": raw}])
    api.get_block.return_value = {"uuid": REF, "content": "target text",
                                  "page": {"originalName": "Elsewhere"}}
    result = _run(api, ["get-journal-range", "--from", "2026-08-01", "--to", "2026-08-01",
                        "--resolve-refs", "--json"])
    assert result.exit_code == 0, result.output
    block = json.loads(result.stdout)[0]["blocks"][0]
    assert block["content"] != raw
    assert block["hash"] == block_hash(raw)


# --- find-block --------------------------------------------------------------

@pytest.fixture
def double(monkeypatch):
    d = LogseqHttpDouble().install(monkeypatch)
    d.add_page("Probe Page", [{"content": "TODO parent", "children": ["child note"]}])
    return d


def test_find_block_json_has_a_hash(double):
    result = split_runner().invoke(
        cli, ["--token", "t", "find-block", "--content", "TODO parent", "--json"])
    assert result.exit_code == 0, result.output
    (found,) = json.loads(result.stdout)
    assert found["hash"] == block_hash("TODO parent")


def test_find_block_with_children_hashes_the_children_too(double):
    result = split_runner().invoke(
        cli, ["--token", "t", "find-block", "--content", "TODO parent",
              "--with-children", "--json"])
    assert result.exit_code == 0, result.output
    (found,) = json.loads(result.stdout)
    assert found["children"][0]["hash"] == block_hash("child note")


def test_find_block_text_shows_no_hash(double):
    result = split_runner().invoke(
        cli, ["--token", "t", "find-block", "--content", "TODO parent"])
    assert block_hash("TODO parent") not in result.stdout


# --- get-todos ---------------------------------------------------------------

def _todos_api(content):
    api = MagicMock()
    row = ({"content": content, "marker": "TODO", "uuid": "u1"},
           {"original-name": "Project Alpha", "name": "project alpha"})
    api.datascript_query.side_effect = lambda q: [] if ":block/refs" in q else [row]
    return api


def test_get_todos_hash_comes_from_the_stored_content_not_the_cleaned_field():
    result = _run(_todos_api(RAW), ["get-todos", "--json", "--no-follow-refs"])
    assert result.exit_code == 0, result.output
    (todo,) = json.loads(result.stdout)["todos"]
    assert todo["content"] != RAW  # the output field is cleaned of marker and timestamps
    assert todo["hash"] == block_hash(RAW)


def test_get_todos_hash_is_accepted_by_the_write_check():
    result = _run(_todos_api(RAW), ["get-todos", "--json", "--no-follow-refs"])
    (todo,) = json.loads(result.stdout)["todos"]
    check_precondition({"uuid": "u1", "content": RAW}, Expect(hash=todo["hash"]))


def test_get_todos_text_shows_no_hash():
    result = _run(_todos_api(RAW), ["get-todos", "--no-follow-refs"])
    assert block_hash(RAW) not in result.stdout


# --- one function, every source ---------------------------------------------

def test_every_read_path_gives_the_same_hash_for_the_same_block(monkeypatch):
    d = LogseqHttpDouble().install(monkeypatch)
    d.add_page("Probe Page", [{"content": RAW, "marker": "TODO"}])
    runner = split_runner()

    def invoke(*args):
        result = runner.invoke(cli, ["--token", "t", *args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    uuid = d.uuid_of(RAW)
    via_block = invoke("get-block", "--id", uuid)["hash"]
    via_find = invoke("find-block", "--content", "write the report")[0]["hash"]
    via_page = invoke("get-page", "--name", "Probe Page", "--no-backlinks")["blocks"][0]["hash"]
    assert via_block == via_find == via_page == block_hash(RAW)
    # ... and it is the hash the write check accepts for that block.
    raw_block = d.pages[0]["blocks"][0]
    for read_hash in (via_block, via_find, via_page):
        check_precondition(raw_block, Expect(hash=read_hash))


# --- --max-chars counts the hash ---------------------------------------------

def test_max_chars_measures_the_json_with_the_hash_in_it():
    """The cap is on the printed JSON, so the hash costs room: a cap that
    fitted the page without hashes withholds a block once they are on."""
    tree = [{"uuid": f"u{i}", "content": f"note {i}"} for i in range(3)]
    args = ["get-page", "--name", "p", "--json", "--no-backlinks"]
    whole = _run(_page_api(tree), args).stdout
    fits = json.loads(_run(_page_api(tree), [*args, "--max-chars", str(len(whole))]).stdout)
    assert "withheld" not in fits
    short = json.loads(_run(_page_api(tree), [*args, "--max-chars", str(len(whole) - 1)]).stdout)
    assert short.get("withheld")
