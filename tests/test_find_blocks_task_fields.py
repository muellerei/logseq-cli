"""find_blocks_by_content can ask for the task fields of a block.

set-todo-status decides by the marker Logseq stored, and a content search is
how it finds the block with --content. The pull holds the marker only when
asked for, so the queries of find-block and --where-content stay as they were.
"""
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.lookup import find_blocks_by_content
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble
from tests.test_datalog_quoting import QueryRecorder

PULL = "[:block/content :block/uuid {:block/page [:block/original-name :block/name]}]"
PULL_WITH_MARKER = ("[:block/content :block/uuid :block/marker "
                    "{:block/page [:block/original-name :block/name]}]")

# The four queries as they were sent before the parameter existed.
UNCHANGED = {
    ("page", "regex"): (
        f'[:find (pull ?b {PULL})'
        ' :where [?p :block/name "page a"] [?b :block/page ?p] [?b :block/content _]]'),
    ("page", "substring"): (
        f'[:find (pull ?b {PULL})'
        ' :where [?p :block/name "page a"] [?b :block/page ?p] [?b :block/content ?c]'
        ' [(clojure.string/includes? ?c "x")]]'),
    ("everywhere", "regex"): (
        f'[:find (pull ?b {PULL}) :where [?b :block/content _]]'),
    ("everywhere", "substring"): (
        f'[:find (pull ?b {PULL}) :where [?b :block/content ?c]'
        ' [(clojure.string/includes? ?c "x")]]'),
}


def _query(where, how, **kwargs):
    rec = QueryRecorder()
    find_blocks_by_content(rec, "x", page="Page A" if where == "page" else None,
                           use_regex=(how == "regex"), **kwargs)
    return rec.queries[0]


@pytest.mark.parametrize("form", UNCHANGED, ids=["-".join(f) for f in UNCHANGED])
def test_the_default_queries_are_unchanged(form):
    assert _query(*form) == UNCHANGED[form]


@pytest.mark.parametrize("form", UNCHANGED, ids=["-".join(f) for f in UNCHANGED])
def test_task_fields_add_the_marker_after_the_uuid_in_every_form(form):
    query = _query(*form, with_task_fields=True)
    assert query == UNCHANGED[form].replace(PULL, PULL_WITH_MARKER)
    assert "[:block/content :block/uuid :block/marker" in query


def test_the_marker_comes_back_in_the_row():
    double = LogseqHttpDouble()
    double.add_page("Page A", [{"content": "TODO task", "marker": "TODO"}, "plain task"])
    api = MagicMock()
    api.datascript_query.side_effect = lambda q: double._datascript_query([q])
    rows = {r["content"]: r for r in
            find_blocks_by_content(api, "task", page="Page A", with_task_fields=True)}
    assert rows["TODO task"]["marker"] == "TODO"
    assert "marker" not in rows["plain task"]
    rows = find_blocks_by_content(api, "task", page="Page A")
    assert all("marker" not in r for r in rows)


@pytest.mark.parametrize("args", [
    ["find-block", "--content", "x", "--json"],
    ["update-block", "--where-content", "x", "--page", "P", "--content", "y", "--dry-run"],
], ids=["find-block", "update-block"])
def test_find_block_and_where_content_do_not_ask_for_the_marker(args):
    api = MagicMock()
    api.datascript_query.return_value = []
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        split_runner().invoke(cli, args)
    assert api.datascript_query.call_args_list
    assert all(":block/marker" not in c.args[0] for c in api.datascript_query.call_args_list)
