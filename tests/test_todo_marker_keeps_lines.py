"""set-todo-status changes the marker and nothing else.

It split the whole content at its first whitespace to find the marker, and a
line break is whitespace: "TODO\\nnotes" came out as "DONE notes", the two lines
joined, and a code block under a bare "TODO" lost the line break before its
fence. Written that way, "DONE ```js" leaves the closing fence without an
opener, and Logseq lets such a fence swallow the blocks after it when it reads
the page file again (#47). The marker is now changed on the first line only.
"""
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

UUID = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"


@pytest.mark.parametrize("old,new", [
    ("TODO\nnotes", "DONE\nnotes"),
    ("TODO\n```js\nx()\n```", "DONE\n```js\nx()\n```"),
    ("TODO write it\nnotes:: more", "DONE write it\nnotes:: more"),
    ("TODO  spaced", "DONE spaced"),
    ("no marker\nsecond", "DONE no marker\nsecond"),
], ids=["bare marker", "code under a bare marker", "property line", "extra space",
        "no marker"])
def test_only_the_marker_changes(old, new):
    api = page_graph_api(PageGraph({"Page A": [{"uuid": UUID, "content": old}]}))
    api.update_block.side_effect = lambda u, c, properties=None, replacing=None: \
        api.graph.locate(u)[1][api.graph.locate(u)[2]].update(content=c)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, ["set-todo-status", "--id", UUID, "--status", "DONE"])
    assert r.exit_code == 0, r.stderr
    assert api.update_block.call_args.args[1] == new
