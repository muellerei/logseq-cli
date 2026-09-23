"""Replacing a block's text keeps its properties, and the new text's own win.

Block properties are lines of the block's text, so a plain ``updateBlock``
drops them. update-block passes their stored text back as ``opts.properties``
since #30. Measured against 0.10.15, the page file read again:

- ``updateBlock`` without properties: ``prio:: 1`` is gone; the ``id::`` line
  stays, Logseq writes it back itself.
- ``updateBlock`` with a key the new text also sets: the passed value wins,
  the caller's line is gone from the file (#66). A key only the text sets
  lands as written.
"""
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

BLOCK = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
FOREIGN = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"


def _run(args, api):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args)


def _graph(content):
    graph = PageGraph({"Page A": [{"uuid": BLOCK, "content": content}]})
    return graph, page_graph_api(graph)


def _content(graph):
    return graph.page_named("Page A")["blocks"][0]["content"]


class TestUpdateBlockLetsTheNewTextWin:
    def test_a_key_the_new_text_sets_is_not_passed_back(self):
        graph, api = _graph("old\nprio:: 1\nowner:: bob")
        r = _run(["update-block", "--id", BLOCK, "--content", "new\nprio:: 2"], api)
        assert r.exit_code == 0, r.output
        lines = _content(graph).split("\n")
        assert "prio:: 2" in lines and "prio:: 1" not in lines
        assert "owner:: bob" in lines

    def test_keys_compare_as_logseq_stores_them(self):
        # Logseq lower-cases a key: Prio:: sets the stored prio.
        graph, api = _graph("old\nprio:: 1")
        r = _run(["update-block", "--id", BLOCK, "--content", "new\nPrio:: 2"], api)
        assert r.exit_code == 0, r.output
        assert "prio:: 1" not in _content(graph).split("\n")

    def test_an_underscore_key_is_the_stored_one(self):
        # due_date:: is stored as due-date; passed back, the old value, written
        # last, would win (measured).
        graph, api = _graph("old\ndue_date:: 1")
        r = _run(["update-block", "--id", BLOCK, "--content", "new\ndue_date:: 2"], api)
        assert r.exit_code == 0, r.output
        assert _content(graph) == "new\ndue_date:: 2"

    @pytest.mark.parametrize("key", ["id", "custom-id"])
    def test_the_uuid_goes_back_whatever_a_code_block_says(self, key):
        # updateBlock takes an id:: line out of a code block (#68); only the
        # passed own id keeps the block's uuid from becoming that one.
        # Logseq reads custom-id:: as the uuid too.
        graph, api = _graph(f"old\n{key}:: {BLOCK}")
        r = _run(["update-block", "--id", BLOCK, "--content",
                  f"new\n```\n{key}:: {FOREIGN}\n```"], api)
        assert r.exit_code == 0, r.output
        assert api.update_block.call_args.kwargs["properties"] == {key: BLOCK}
        assert FOREIGN not in _content(graph)

    def test_a_line_in_a_code_block_sets_its_key_too(self):
        # updateBlock takes it out of the code block as a property (measured,
        # #68); passing the old value as well would make it win over the line.
        graph, api = _graph("old\nprio:: 1")
        r = _run(["update-block", "--id", BLOCK, "--content",
                  "new\n```\nprio:: 2\n```"], api)
        assert r.exit_code == 0, r.output
        assert "prio:: 1" not in _content(graph).split("\n")

    def test_the_dry_run_names_only_what_it_keeps(self):
        graph, api = _graph("old\nprio:: 1\nowner:: bob")
        r = _run(["update-block", "--id", BLOCK, "--content", "new\nprio:: 2",
                  "--dry-run"], api)
        assert r.exit_code == 0, r.output
        assert "keeps: owner::" in r.output and "prio::" not in r.output.split("keeps:")[1]
        assert _content(graph) == "old\nprio:: 1\nowner:: bob"
