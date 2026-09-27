"""set-todo-status --follow-refs changes the task a ref leads to.

A task pulled into a journal by reference is a block holding only
``((uuid))``; ticking it off there should change the original, not add a
second task on the reference (#106).
"""
from unittest.mock import patch

from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

# Letters in each uuid, so a ref in capitals differs from one in lower case.
TASK = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a01"
HOST = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a02"


def run(blocks, *args, placeholders=()):
    """Run set-todo-status --status DONE on a page of ``blocks``; return the
    result and a function that reads a block's content afterwards."""
    graph = PageGraph({"Page A": blocks}, placeholders=placeholders)
    api = page_graph_api(graph)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        result = split_runner().invoke(cli, ["set-todo-status", *args, "--status", "DONE"])
    return result, lambda uuid: graph.get_block(uuid, include_children=False)["content"]


class TestWhatStays:
    """Behaviour the chain fix keeps."""

    def test_a_ref_to_a_task_changes_the_task(self):
        result, content = run([{"uuid": TASK, "content": "TODO ship it"},
                               {"uuid": HOST, "content": f"(({TASK}))"}],
                              "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == "DONE ship it"
        assert content(HOST) == f"(({TASK}))"

    def test_a_ref_in_capitals_is_the_same_ref(self):
        result, content = run([{"uuid": TASK, "content": "TODO ship it"},
                               {"uuid": HOST, "content": f"(({TASK.upper()}))"}],
                              "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == "DONE ship it"
        assert content(HOST) == f"(({TASK.upper()}))"

    def test_a_block_without_a_ref_is_changed_itself(self):
        result, content = run([{"uuid": HOST, "content": "TODO ship it"}],
                              "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(HOST) == "DONE ship it"

    def test_a_labelled_ref_is_a_link_and_is_changed_itself(self):
        result, content = run([{"uuid": TASK, "content": "TODO ship it"},
                               {"uuid": HOST, "content": f"[see]((({TASK})))"}],
                              "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(HOST) == f"DONE [see]((({TASK})))"
        assert content(TASK) == "TODO ship it"
