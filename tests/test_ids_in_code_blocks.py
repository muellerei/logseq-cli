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
they do, so the code-block rule can apply to all three. Since #47 outline text
keeps a code block in one block too (test_code_blocks_in_outlines.py).
"""
import json
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.blocktext import block_id_property, without_block_ids
from logseq_cli.helpers import blocks_with_several_ids
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


class TestOutlineText:
    """Outline text keeps a code block in one block (#47). Indented under a
    bullet, the fence goes on that block, and an id:: line inside it is code.
    On a bullet line the code block is a block of its own, and an id:: line
    after its closing fence is that block's id."""

    INSIDE = f"- note\n  ```\n  id:: {FRESH}\n  ```"
    AFTER = f"- note\n\t- ```js\n\t  a()\n\t  ```\n\t  id:: {FRESH}"

    def test_inside_the_code_block_it_is_written_as_is(self):
        api = _graph()
        r = _run(["add-note-content", "--page", "Page A", "--keep-ids",
                  "--content", self.INSIDE.replace(FRESH, EXISTING)], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" not in r.stderr
        assert [b for b in _blocks(api.graph)
                if b["content"] == f"note\n```\nid:: {EXISTING}\n```"]
        assert api.graph.every_uuid().count(EXISTING) == 1

    def test_after_the_code_block_it_is_dropped_without_keep_ids(self):
        api = _graph()
        r = _run(["add-note-content", "--page", "Page A", "--content", self.AFTER], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" in r.stderr
        assert not [b for b in _blocks(api.graph) if FRESH in b["content"]]

    def test_after_the_code_block_it_is_kept_with_keep_ids(self):
        api = _graph()
        r = _run(["add-note-content", "--page", "Page A", "--content", self.AFTER,
                  "--keep-ids"], api)
        assert r.exit_code == 0, r.stderr
        page, siblings, i, _ = api.graph.locate(FRESH)
        assert siblings[i]["content"] == f"```js\na()\n```\nid:: {FRESH}"

    def test_after_the_code_block_an_existing_one_is_refused(self):
        api = _graph()
        r = _run(["add-note-content", "--page", "Page A", "--keep-ids",
                  "--content", self.AFTER.replace(FRESH, EXISTING)], api)
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


class TestWhatIndentsAnIdLine:
    """Measured with keepUUID (0.10.15): Logseq takes the id of a line indented
    by a form feed or a carriage return as it does one indented by spaces, and
    not one behind a no-break space or a vertical tab. The CLI saw only spaces
    and tabs, so an existing uuid behind a form feed went out unchecked. Before
    #43 already; a fence is indented by a different set (no carriage return)."""

    @pytest.mark.parametrize("indent", ["\f", "\r", "\t\f"], ids=["ff", "cr", "tab-ff"])
    def test_an_existing_uuid_is_refused(self, indent):
        api = _graph()
        r = _run(["insert-block", "--page", "Page A", "--keep-ids",
                  "--content", f"Restored\n{indent}id:: {EXISTING}"], api)
        assert r.exit_code == 1
        assert "already belong" in r.stderr

    @pytest.mark.parametrize("indent", ["\u00a0", "\v"], ids=["nbsp", "vt"])
    def test_behind_other_whitespace_it_is_text(self, indent):
        assert block_id_property(f"Restored\n{indent}id:: {EXISTING}") == ""
