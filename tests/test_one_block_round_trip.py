"""#47: text the CLI writes as one block comes back from the file as one block.

Logseq writes a block's text into the page file under one bullet. Some lines
in that text are block boundaries to its file parser, so the block the
database holds is not the one the file gives back the next time Logseq reads
it (an edit from outside, a sync, a re-index). Measured against 0.10.15: a
block written through the API, the page file touched, the page read back.

- After the first line, "- b" (also indented, "-\\tb", "-" alone) becomes a
  child block, and "# h" ("#" alone, up to "#######", also indented) a block
  next to it. Indentation is spaces, tabs, form feeds, carriage returns.
- A fence that nothing in the block closes, on any line, runs on into the
  blocks after it up to the next code block on the page and swallows them.
- Inside a closed code block none of this applies; "* b", "+ b", "1. b",
  "#tag", "-b" and "> - b" are no boundaries.

Every write is checked for this: the commands check all they are going to
write before the first write, and LogseqAPI checks each write it sends, so no
path can go around it. A write that changes an existing block may keep a line
the block already had: Logseq's own editor makes such blocks too.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.cli import cli
from logseq_cli.blocktext import SplitBlockError, block_boundaries
from tests.conftest import PageGraph, page_graph_api, split_runner

ANCHOR = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"
DATE = ["--date", "2026-01-05"]


# --- the rule --------------------------------------------------------------

BOUNDARIES = [
    ("x\n- b", 2, "bullet"), ("x\n  - b", 2, "bullet"), ("x\n\t- b", 2, "bullet"),
    ("x\n-\tb", 2, "bullet"), ("x\n-", 2, "bullet"), ("x\n- ", 2, "bullet"),
    ("x\n\f- b", 2, "bullet"), ("x\n\r- b", 2, "bullet"),
    ("x\n# h", 2, "heading"), ("x\n## h", 2, "heading"), ("x\n####### h", 2, "heading"),
    ("x\n#", 2, "heading"), ("x\n#\th", 2, "heading"), ("x\n  # h", 2, "heading"),
    ("x\n```\ny", 2, "open_fence"), ("```\nx", 1, "open_fence"), ("x\n~~~", 2, "open_fence"),
    ("x\n```\ny\n```\n```", 5, "open_fence"),
    # Whitespace after the mark is the same set as before it (measured).
    ("x\n-\fb", 2, "bullet"), ("x\n-\rb", 2, "bullet"), ("x\r\n-\r\ny", 2, "bullet"),
    ("x\n#\fh", 2, "heading"), ("x\n#\rh", 2, "heading"),
]
NO_BOUNDARY = [
    "- a leading bullet", "# a heading first", "x\n* b", "x\n+ b", "x\n1. b",
    "x\n#tag b", "x\n#!h", "x\n-b", "x\n> - b", "x\nb - c", "x\n - b",
    "x\n```\n- b\n# h\n```", "x\n~~~\n- b\n~~~", "x\n```\ny\n~~~", "plain",
]


@pytest.mark.parametrize("content,line,kind", BOUNDARIES)
def test_a_boundary_is_found(content, line, kind):
    assert [(b[0], b[1]) for b in block_boundaries(content)][:1] == [(line, kind)]


@pytest.mark.parametrize("content", NO_BOUNDARY)
def test_no_boundary(content):
    assert block_boundaries(content) == []


# --- every write through LogseqAPI ------------------------------------------

class TestTheApiRefusesBeforeSending:
    def _api(self):
        api = LogseqAPI(token="t")
        api.call = MagicMock()
        return api

    @pytest.mark.parametrize("write", [
        lambda api: api.insert_block(ANCHOR, "x\n- b"),
        lambda api: api.append_block_in_page("P", "x\n# h"),
        lambda api: api.update_block(ANCHOR, "x\n```\ny"),
        lambda api: api.insert_batch_block(ANCHOR, [{"content": "ok", "children": [
            {"content": "x\n- b"}]}]),
    ], ids=["insertBlock", "appendBlockInPage", "updateBlock", "insertBatchBlock"])
    def test_refused(self, write):
        api = self._api()
        with pytest.raises(SplitBlockError):
            write(api)
        api.call.assert_not_called()

    def test_an_update_may_keep_a_line_the_block_had(self):
        api = self._api()
        api.update_block(ANCHOR, "DONE x\n- b", replacing="TODO x\n- b")
        api.call.assert_called_once()

    @pytest.mark.parametrize("had", ["a\n- b", "a\n- b\n```\n- b\n```"],
                             ids=["repeated", "freed from a code block"])
    def test_an_update_may_not_add_a_second_of_one_it_had(self, had):
        api = self._api()
        with pytest.raises(SplitBlockError):
            api.update_block(ANCHOR, "a\n- b\n- b", replacing=had)
        api.call.assert_not_called()

    def test_a_property_value_is_block_text_too(self):
        api = self._api()
        with pytest.raises(SplitBlockError):
            api.upsert_block_property(ANCHOR, "k", "v\n- x")
        api.call.assert_not_called()

    def test_the_message_ends_with_its_last_line(self):
        api = self._api()
        with pytest.raises(SplitBlockError) as e:
            api.insert_block(ANCHOR, "x\n- b")
        assert not str(e.value).endswith("\n")

    def test_an_update_may_not_add_one(self):
        api = self._api()
        with pytest.raises(SplitBlockError):
            api.update_block(ANCHOR, "x\n- b\n# h", replacing="x\n- b")
        api.call.assert_not_called()


# --- the commands: refused before the first write, with the way out ---------

def _graph():
    return page_graph_api(PageGraph({"Page A": [
        {"uuid": ANCHOR, "content": "## Log", "children": [{"content": "entry"}]}]}))


def _run(args, api, input=None):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args, input=input)


def _writes(api):
    return (api.insert_block.call_count + api.append_block_in_page.call_count
            + api.insert_batch_block.call_count + api.update_block.call_count
            + api.create_page.call_count)


WRITERS = {
    "insert-block --content": ["insert-block", "--page", "Page A", "--content", "x\n- b"],
    "insert-block --after": ["insert-block", "--after", ANCHOR, "--content", "x\n# h"],
    "insert-block --tree": ["insert-block", "--child-of", ANCHOR, "--tree",
                            json.dumps([{"content": "ok"}, {"content": "x\n```\ny"}])],
    "add-journal-block": ["add-journal-block", "--top-level", *DATE, "--content", "x\n- b"],
    "add-journal-block, batch": ["add-journal-block", "--top-level", *DATE,
                                 "--content", "ok", "--content", "x\n## h"],
    "add-journal-block, upsert": ["add-journal-block", "--under-heading", "## Log", *DATE,
                                  "--upsert-heading", "entry", "--content", "x\n# h"],
    "add-note-content": ["add-note-content", "--page", "Page A",
                         "--content", "- a\n  ```\n  y"],
    "add-journal-content": ["add-journal-content", "--top-level", *DATE,
                            "--content", "- a\n  ```\n  y"],
    "update-block": ["update-block", "--id", ANCHOR, "--content", "x\n# h"],
    "create-page": ["create-page", "--page", "New", "--content", "x\n- b"],
    "add-journal-entry": ["add-journal-entry", *DATE, "--content", "x\n```\ny"],
}


@pytest.mark.parametrize("name", WRITERS)
@pytest.mark.parametrize("extra", [[], ["--dry-run"]], ids=["write", "dry-run"])
def test_every_writer_refuses_before_writing(name, extra):
    api = _graph()
    r = _run(WRITERS[name] + extra, api)
    assert r.exit_code == 2, (r.stdout, r.stderr)
    assert "line 2" in r.stderr or "line 1" in r.stderr
    assert "Nothing was written" in r.stderr
    assert _writes(api) == 0


def test_the_refusal_is_json_under_json():
    r = _run(WRITERS["insert-block --content"] + ["--json"], _graph())
    assert r.exit_code == 2
    error = json.loads(r.stderr)
    assert error["reason"] == "splits_into_blocks"
    assert (error["line"], error["kind"]) == (2, "bullet")


@pytest.mark.parametrize("content,fix", [
    ("x\n- b", "indent"),
    ("x\n# h", "block of its own"),
    ("x\n```\ny", "close"),
])
def test_the_refusal_names_the_line_and_the_way_out(content, fix):
    r = _run(["insert-block", "--page", "Page A", "--content", content], _graph())
    assert repr(content.split("\n")[1]) in r.stderr
    assert fix in r.stderr


# --- what is no longer refused ----------------------------------------------

@pytest.mark.parametrize("args", [
    ["update-block", "--id", ANCHOR, "--content", "Example:\n```md\n- item\n# title\n```"],
    ["add-journal-block", "--top-level", *DATE, "--content", "Example:\n```md\n- item\n```"],
    # Joined into one line by --no-preserve, the "- " is no line of its own.
    ["add-journal-block", "--top-level", *DATE, "--no-preserve", "--content", "a\n- b"],
], ids=["update-block, code example", "add-journal-block, code example", "--no-preserve"])
def test_accepted(args):
    api = _graph()
    r = _run(args, api)
    assert r.exit_code == 0, r.stderr


# --- writes of text that already exists ---------------------------------------

def test_copy_block_refuses_a_block_that_would_not_come_back():
    api = page_graph_api(PageGraph({"Page A": [
        {"uuid": ANCHOR, "content": "root", "children": [{"content": "x\n- b"}]}]}))
    r = _run(["copy-block", "--id", ANCHOR, "--to-page", "Page A"], api)
    assert r.exit_code == 2
    assert "The source block" in r.stderr and "move-block" in r.stderr
    assert _writes(api) == 0


def test_replace_text_refuses_before_any_block_is_written():
    # Only the second block goes wrong, so a check per write would already
    # have written the first.
    api = page_graph_api(PageGraph({"Page A": [
        {"uuid": ANCHOR, "content": "a word"}, {"content": "x\nword"}]}))
    r = _run(["replace-text", "--page", "Page A", "--find", "word", "--replace", "# h"], api)
    assert r.exit_code == 2
    assert "The block" in r.stderr and "after the replacement" in r.stderr
    assert _writes(api) == 0


def test_replace_text_tells_the_api_what_it_replaces():
    api = page_graph_api(PageGraph({"Page A": [{"uuid": ANCHOR, "content": "a word\n- b"}]}))
    api.update_block.side_effect = lambda u, c, properties=None, replacing=None: \
        api.graph.locate(u)[1][api.graph.locate(u)[2]].update(content=c)
    r = _run(["replace-text", "--page", "Page A", "--find", "word", "--replace", "term"], api)
    assert r.exit_code == 0, r.stderr
    assert api.update_block.call_args.kwargs["replacing"] == "a word\n- b"


def test_replace_text_and_set_todo_status_leave_an_existing_line_alone():
    # Logseq's editor makes such blocks too; changing another line of one is
    # no reason to refuse.
    api = page_graph_api(PageGraph({"Page A": [
        {"uuid": ANCHOR, "content": "TODO a word\n- b"}]}))
    api.update_block.side_effect = lambda u, c, properties=None, replacing=None: \
        api.graph.locate(u)[1][api.graph.locate(u)[2]].update(content=c)
    r = _run(["replace-text", "--page", "Page A", "--find", "word", "--replace", "term"], api)
    assert r.exit_code == 0, r.stderr
    r = _run(["set-todo-status", "--id", ANCHOR, "--status", "DONE"], api)
    assert r.exit_code == 0, r.stderr


# --- the heading written first, and the words of each refusal -----------------

HEADING = "## Log\n- x"


@pytest.mark.parametrize("args", [
    ["add-journal-block", "--under-heading", HEADING, *DATE, "--content", "hi"],
    ["add-journal-content", "--under-heading", HEADING, *DATE, "--content", "hi"],
    ["add-note-content", "--page", "New page", "--under-heading", HEADING, "--content", "hi"],
    ["add-block-ref", "--source-id", ANCHOR, "--journal-date", "2026-01-05",
     "--under-heading", HEADING],
], ids=["add-journal-block", "add-journal-content", "add-note-content", "add-block-ref"])
def test_the_heading_is_checked_before_the_page_is_made(args):
    api = _graph()
    r = _run(args, api)
    assert r.exit_code == 2, r.stderr
    assert "The heading" in r.stderr and "single line" in r.stderr
    assert _writes(api) == 0


@pytest.mark.parametrize("args,where,way", [
    (["insert-block", "--child-of", ANCHOR, "--tree", json.dumps([{"content": "x\n- b"}])],
     "The --tree node", '"children"'),
    (["add-journal-entry", *DATE, "--multi-block", "--content", "x\n```\ncode\n```"],
     "The block", "leave out --multi-block"),
    (["add-note-content", "--page", "Page A", "--content", "- ```sh\n- make\n- ```"],
     "The block", "without a bullet"),
], ids=["--tree node", "--multi-block", "outline closer on a bullet"])
def test_the_refusal_fits_the_input(args, where, way):
    r = _run(args, _graph())
    assert r.exit_code == 2, r.stderr
    assert where in r.stderr and way in r.stderr


@pytest.mark.parametrize("args", [
    ["add-block-ref", "--source-id", "abc\n- x", "--journal-date", "2026-01-05",
     "--under-heading", "## Refs"],
    ["set-block-property", "--id", ANCHOR, "--key", "k", "--value", "v\n- x"],
    ["set-property", "--page", "Page A", "--key", "k", "--value", "v\n# h"],
    # Not a block boundary, and still a line of its own in the block's text.
    ["set-block-property", "--id", ANCHOR, "--key", "note", "--value", "x\ncustom-id:: foo"],
    ["insert-block", "--page", "Page A", "--content", "ok", "--property", "k=v\n# h"],
    ["add-note-content", "--page", "New page", "--content", "ok", "--property", "k=v\n- x"],
], ids=["add-block-ref", "set-block-property", "set-property", "value with an id line",
        "insert-block --property",
        "add-note-content --property"])
def test_other_text_is_checked_before_the_first_write(args):
    api = _graph()
    r = _run(args, api)
    assert r.exit_code == 2, r.stderr
    assert "Nothing was written" in r.stderr
    assert _writes(api) + api.upsert_block_property.call_count == 0


@pytest.mark.parametrize("dry", [[], ["--dry-run"]], ids=["write", "dry-run"])
def test_set_todo_status_on_a_block_that_starts_with_a_fence(dry):
    # "TODO ```js" is no fence: the closing one would be left open.
    api = page_graph_api(PageGraph({"Page A": [{"uuid": ANCHOR, "content": "```js\nrun()\n```"}]}))
    r = _run(["set-todo-status", "--id", ANCHOR, "--status", "TODO", *dry], api)
    assert r.exit_code == 2
    assert "first line of text" in r.stderr
    assert _writes(api) == 0


def test_replace_text_may_change_the_text_of_a_line_the_block_had():
    api = page_graph_api(PageGraph({"Page A": [{"uuid": ANCHOR, "content": "TODO a\n- old"}]}))
    api.update_block.side_effect = lambda u, c, properties=None, replacing=None: \
        api.graph.locate(u)[1][api.graph.locate(u)[2]].update(content=c)
    r = _run(["replace-text", "--page", "Page A", "--find", "old", "--replace", "new"], api)
    assert r.exit_code == 0, r.stderr
