"""#43: an id:: line inside a code block is code, not the block's id.

Measured against Logseq 0.10.15: between two fence lines (``` or ~~~) an
``id::`` line is text. ``insertBlock`` and ``appendBlockInPage`` write it as
given, and ``insertBatchBlock`` with ``keepUUID`` leaves it in place and gives
the block a fresh uuid. The CLI still read it as the block's id: without
--keep-ids it removed the line from the example, with --keep-ids it refused an
example quoting an existing block's uuid as a copy, and sent a fresh one with
keepUUID, where the read-back then failed after the write.

What made this more than a pattern change: the id check, the removal and the
write did not see the same blocks (see test_dropped_ids_leave_no_line.py). Now
they do, so the code-block rule can apply to all three.
"""
import json
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.helpers import block_id_property, blocks_with_several_ids, without_block_ids
from tests.conftest import PageGraph, page_graph_api, split_runner

EXISTING = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"   # a block in the graph has it
FRESH = "5c4b3a29-1807-4f6e-9d5c-4b3a29180716"      # nothing has it
ANCHOR = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"
EXAMPLE = f"Example:\n```\nid:: {EXISTING}\n```"
DATE = ["--date", "2026-01-05"]


def _graph():
    return page_graph_api(PageGraph({"Page A": [
        {"uuid": ANCHOR, "content": "## Log", "children": [{"content": "entry"}]},
        {"uuid": EXISTING, "content": "keep me"}]}))


def _run(args, api, input=None):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args, input=input)


def _blocks(graph):
    def walk(blocks):
        for b in blocks:
            yield b
            yield from walk(b["children"])
    return [b for p in graph.pages for b in walk(p["blocks"])]


def _the_example_is_written_as_is(api):
    written = [b for b in _blocks(api.graph) if b["content"] == EXAMPLE]
    assert len(written) == 1
    assert written[0]["uuid"] != EXISTING
    page, siblings, i, _ = api.graph.locate(EXISTING)
    assert siblings[i]["content"] == "keep me"


# Flat content is one block, so the fences stay together in the unit that is
# checked and written.
FLAT = {
    "insert-block --page": ["insert-block", "--page", "Page A", "--content", EXAMPLE],
    "insert-block --after": ["insert-block", "--after", ANCHOR, "--content", EXAMPLE],
    "insert-block --child-of": ["insert-block", "--child-of", ANCHOR, "--content", EXAMPLE],
    "add-journal-block": ["add-journal-block", "--top-level", *DATE, "--content", EXAMPLE],
    "add-journal-block, under heading": ["add-journal-block", "--under-heading", "## Log",
                                         *DATE, "--content", EXAMPLE],
    "add-journal-block, batch": ["add-journal-block", "--top-level", *DATE,
                                 "--content", EXAMPLE, "--content", "second"],
}


@pytest.mark.parametrize("name", FLAT)
class TestACodeExampleInFlatContent:
    def test_without_keep_ids_it_is_written_as_is(self, name):
        api = _graph()
        r = _run(FLAT[name], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" not in r.stderr
        _the_example_is_written_as_is(api)

    def test_with_keep_ids_an_existing_uuid_in_it_is_no_copy(self, name):
        api = _graph()
        r = _run(FLAT[name] + ["--keep-ids"], api)
        assert r.exit_code == 0, r.stderr
        _the_example_is_written_as_is(api)

    def test_with_keep_ids_a_fresh_uuid_in_it_is_not_asked_for(self, name):
        # Sent with keepUUID, Logseq gives the block a new uuid, and the
        # read-back failed the command after the write.
        api = _graph()
        args = [a.replace(EXISTING, FRESH) for a in FLAT[name]] + ["--keep-ids"]
        r = _run(args, api)
        assert r.exit_code == 0, r.stderr
        assert api.graph.locate(FRESH) is None


SPLIT = {
    # A code block under a bullet, as one writes it. In the raw text the id
    # line sits between two fence lines; the parser makes it part of a block
    # "```\nid:: X", whose opener nothing closes.
    "indented": f"- note\n  ```\n  id:: {FRESH}\n  ```",
    # The fence opened on a bullet line, as get-page --format markdown writes it.
    "on a bullet": f"- note\n\t- ```js\n\t  a()\n\t  ```\n\t  id:: {FRESH}",
}


@pytest.mark.parametrize("shape", SPLIT)
class TestAFenceTheParserSplits:
    """Hierarchical text is one block per line, so a fence written across lines
    ends up split over several blocks, and the block that carries the id line
    has an opener nothing closes. Logseq reads that as no code block: the id is
    the block's (measured, keepUUID takes it). Read over the raw text instead,
    the id line of the indented shape is inside a code block, and the removal
    kept it."""

    def test_without_keep_ids_the_id_is_dropped(self, shape):
        api = _graph()
        r = _run(["add-note-content", "--page", "Page A", "--content", SPLIT[shape]], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" in r.stderr
        assert not [b for b in _blocks(api.graph) if FRESH in b["content"]]

    def test_with_keep_ids_it_is_kept(self, shape):
        api = _graph()
        r = _run(["add-note-content", "--page", "Page A", "--content", SPLIT[shape],
                  "--keep-ids"], api)
        assert r.exit_code == 0, r.stderr
        page, siblings, i, _ = api.graph.locate(FRESH)
        assert siblings[i]["content"].startswith("```\n")

    def test_with_keep_ids_an_existing_one_is_refused(self, shape):
        api = _graph()
        r = _run(["add-note-content", "--page", "Page A", "--keep-ids",
                  "--content", SPLIT[shape].replace(FRESH, EXISTING)], api)
        assert r.exit_code == 1
        assert "already belong" in r.stderr


class TestTreeInput:
    """A --tree node may carry a code block over several lines. Without
    keepUUID, insertBatchBlock takes every id:: line out of the content, one in
    a code block too (measured), so such a tree is written block by block."""

    TREES = {
        "sibling": [{"content": "first"}, {"content": EXAMPLE}],
        "child": [{"content": "first", "children": [{"content": EXAMPLE}]}],
        # The batch drops any "id:: " line, not only one that names a uuid.
        "commented": [{"content": "first"}, {"content": EXAMPLE.replace(
            f"id:: {EXISTING}", f"id:: {EXISTING} # the id")}],
        # And behind any whitespace JavaScript's \s knows, measured.
        "no-break space": [{"content": "first"}, {"content": EXAMPLE.replace(
            f"id:: {EXISTING}", f"\u00a0id:: {EXISTING}")}],
    }

    @pytest.mark.parametrize("shape", TREES)
    @pytest.mark.parametrize("keep", [[], ["--keep-ids"]], ids=["plain", "keep-ids"])
    def test_the_example_is_written_as_is(self, keep, shape):
        api = _graph()
        r = _run(["insert-block", "--child-of", ANCHOR,
                  "--tree", json.dumps(self.TREES[shape]), *keep], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" not in r.stderr
        example = self.TREES[shape][-1]
        example = (example.get("children") or [example])[0]["content"]
        assert [b for b in _blocks(api.graph) if b["content"] == example]
        page, siblings, i, _ = api.graph.locate(EXISTING)
        assert siblings[i]["content"] == "keep me"


class TestTheRule:
    def test_a_line_in_a_code_block_is_no_id(self):
        assert block_id_property(EXAMPLE) == ""
        assert without_block_ids(EXAMPLE) == EXAMPLE

    def test_after_the_code_block_it_is(self):
        content = f"```js\na()\n```\nid:: {FRESH}"
        assert block_id_property(content) == FRESH
        assert without_block_ids(content) == "```js\na()\n```"

    def test_an_opener_nothing_closes_hides_nothing(self):
        assert block_id_property(f"```\nid:: {FRESH}") == FRESH

    def test_a_quoted_id_does_not_make_two(self):
        assert blocks_with_several_ids([{"content": f"a\nid:: {FRESH}\n{EXAMPLE}"}]) == []
