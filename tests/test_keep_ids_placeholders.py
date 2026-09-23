"""--keep-ids restores an id that only a reference still holds (#31).

The restore case: a block was deleted, other pages still point at it, and the
outline is written back from a copy. Logseq keeps a placeholder under such a
uuid, and ``insertBlock``/``appendBlockInPage`` refuse to give it to a new
block. ``insertBatchBlock`` with ``keepUUID`` and the id as an ``id::`` line
takes it over (measured, 0.10.15). So every write with ``--keep-ids`` now goes
through that one call, whichever position it writes to, instead of a second
path for placeholders only.

The position rules are measured, and two of them are not the obvious ones:

* Before the page's first block, and on a page with no blocks, the batch comes
  out with ``* `` in front of every node's content. The first is written after
  that block and its roots are then moved before it; the second is written
  after a stand-in block that is removed again.
* The batch answers ``null``, so every write is proven by reading the page
  back: the new blocks, their count, their ids, and their place.

The stand-in is ``PageGraph`` (tests/conftest.py), which answers the way the
measurements say, the ``* `` included, so a write that takes the obvious
route turns these tests red.
"""
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

ID = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
FIRST = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"   # the page's first block
SECOND = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a71"  # a later top-level block
LOG = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a72"     # the journal's ## Log

TOP = f"restored\nid:: {ID}"
ONE_CHILD = f"root\n\t- restored\n\t  id:: {ID}"


def _graph(*, placeholder=True, **extra):
    return PageGraph({
        "Page A": [{"uuid": FIRST, "content": "first",
                    "children": [{"content": "first child"}]},
                   {"uuid": SECOND, "content": "second"}],
        "2026-01-05": [{"uuid": LOG, "content": "## Log",
                        "children": [{"content": "earlier entry"}]}],
    }, placeholders=[ID] if placeholder else [], **extra)


def _run(args, graph):
    api = page_graph_api(graph)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args), api


def _restored(graph):
    """The restored block's place: (page, first line of its parent or None,
    index among its siblings)."""
    page, siblings, i, parent = graph.locate(ID)
    return page["name"], parent["content"].split("\n")[0] if parent else None, i


DATE = ["--date", "2026-01-05"]
UNDER = ["--under-heading", "## Log"]

# Every write path of every command with --keep-ids, and where the restored
# block must end up: (page, parent's first line or None for top level, index).
CASES = {
    "insert-block tree child-of": (
        ["insert-block", "--child-of", FIRST, "--tree", TOP], ("Page A", "first", 1)),
    "insert-block tree first child": (
        ["insert-block", "--child-of", FIRST, "--first", "--tree", TOP], ("Page A", "first", 0)),
    "insert-block tree after": (
        ["insert-block", "--after", FIRST, "--tree", TOP], ("Page A", None, 1)),
    "insert-block tree before a later block": (
        ["insert-block", "--before", SECOND, "--tree", TOP], ("Page A", None, 1)),
    "insert-block tree before the first block": (
        ["insert-block", "--before", FIRST, "--tree", TOP], ("Page A", None, 0)),
    "insert-block tree top-level": (
        ["insert-block", "--page", "Page A", "--top-level", "--tree", TOP], ("Page A", None, 2)),
    "insert-block tree nested": (
        ["insert-block", "--after", SECOND, "--tree", ONE_CHILD], ("Page A", "root", 0)),
    "insert-block content on page": (
        ["insert-block", "--page", "Page A", "--content", TOP], ("Page A", None, 2)),
    "insert-block content after": (
        ["insert-block", "--after", FIRST, "--content", TOP], ("Page A", None, 1)),
    "insert-block content before the first block": (
        ["insert-block", "--before", FIRST, "--content", TOP], ("Page A", None, 0)),
    "insert-block content child-of": (
        ["insert-block", "--child-of", FIRST, "--content", TOP], ("Page A", "first", 1)),
    "insert-block content first child": (
        ["insert-block", "--child-of", FIRST, "--first", "--content", TOP], ("Page A", "first", 0)),
    "insert-block nested content before": (
        ["insert-block", "--before", SECOND, "--content", ONE_CHILD], ("Page A", "root", 0)),
    "add-note-content": (
        ["add-note-content", "--page", "Page A", "--content", TOP], ("Page A", None, 2)),
    "add-note-content nested": (
        ["add-note-content", "--page", "Page A", "--content", ONE_CHILD], ("Page A", "root", 0)),
    "add-note-content under heading": (
        ["add-note-content", "--page", "2026-01-05", *UNDER, "--content", TOP],
        ("2026-01-05", "## Log", 1)),
    "add-journal-block top-level": (
        ["add-journal-block", "--top-level", *DATE, "--content", TOP], ("2026-01-05", None, 1)),
    "add-journal-block under heading": (
        ["add-journal-block", *UNDER, *DATE, "--content", TOP], ("2026-01-05", "## Log", 1)),
    "add-journal-block batch": (
        ["add-journal-block", *UNDER, *DATE, "--content", "before it", "--content", TOP],
        ("2026-01-05", "## Log", 2)),
    "add-journal-block nested": (
        ["add-journal-block", "--top-level", *DATE, "--content", ONE_CHILD],
        ("2026-01-05", "root", 0)),
    "add-journal-content top-level": (
        ["add-journal-content", "--top-level", *DATE, "--content", TOP], ("2026-01-05", None, 1)),
    "add-journal-content under heading": (
        ["add-journal-content", *UNDER, *DATE, "--content", TOP], ("2026-01-05", "## Log", 1)),
}


@pytest.mark.parametrize("args,where", CASES.values(), ids=CASES.keys())
class TestEveryPathRestoresAPlaceholder:
    def test_the_block_takes_the_id_over_where_it_was_sent(self, args, where):
        graph = _graph()
        result, _ = _run(args + ["--keep-ids"], graph)
        assert result.exit_code == 0, result.output + result.stderr
        assert ID not in graph.placeholders
        assert _restored(graph) == where

    def test_nothing_is_written_with_a_stray_bullet(self, args, where):
        graph = _graph()
        _run(args + ["--keep-ids"], graph)
        def walk(tree):
            for first_line, kids in tree:
                yield first_line
                yield from walk(kids)
        for page in ("Page A", "2026-01-05"):
            assert not any(line.startswith("* ") for line in walk(graph.tree(page)))

    def test_a_fresh_id_is_kept_the_same_way(self, args, where):
        graph = _graph(placeholder=False)
        result, _ = _run(args + ["--keep-ids"], graph)
        assert result.exit_code == 0, result.output + result.stderr
        assert _restored(graph) == where


class TestPositionsTheBatchCannotTakeDirectly:
    def test_before_the_first_block_keeps_order_and_children(self):
        graph = _graph()
        tree = f"- one\n  id:: {ID}\n\t- one child\n- two"
        result, _ = _run(["insert-block", "--before", FIRST, "--content", tree,
                          "--keep-ids"], graph)
        assert result.exit_code == 0, result.stderr
        assert graph.tree("Page A") == [
            ("one", [("one child", [])]), ("two", []),
            ("first", [("first child", [])]), ("second", [])]

    def test_a_page_without_blocks_gets_the_blocks_and_nothing_else(self):
        graph = _graph(blockless=["Linked Only"])
        result, _ = _run(["add-note-content", "--page", "Linked Only", "--content",
                          f"- restored\n  id:: {ID}\n\t- child", "--keep-ids"], graph)
        assert result.exit_code == 0, result.stderr
        assert graph.tree("Linked Only") == [("restored", [("child", [])])]
        assert _restored(graph) == ("Linked Only", None, 0)

    def test_a_fresh_page_keeps_its_empty_block_as_append_does(self):
        """createPage leaves one empty block, and appendBlockInPage writes
        after it; the restore does not remove what it did not write."""
        graph = _graph()
        result, _ = _run(["add-note-content", "--page", "New Page", "--content", TOP,
                          "--keep-ids"], graph)
        assert result.exit_code == 0, result.stderr
        assert graph.tree("New Page") == [("", []), ("restored", [])]


class TestTheWriteIsProven:
    """The batch answers null whatever it did; only the read-back can tell."""

    def _broken(self, graph, change):
        real = graph.insert_batch_block

        def batch(anchor, nodes, options=None):
            return change(real, anchor, nodes, dict(options or {}))
        graph.insert_batch_block = batch
        return graph

    def test_a_batch_that_wrote_nothing_fails(self):
        graph = self._broken(_graph(), lambda real, a, n, o: None)
        result, _ = _run(["insert-block", "--after", FIRST, "--tree", TOP, "--keep-ids"], graph)
        assert result.exit_code != 0
        assert "0 of 1" in result.output + result.stderr

    def test_a_batch_that_minted_new_ids_fails(self):
        def drop_keep(real, a, n, o):
            o.pop("keepUUID", None)
            return real(a, n, o)
        graph = self._broken(_graph(), drop_keep)
        result, _ = _run(["insert-block", "--after", FIRST, "--tree", TOP, "--keep-ids"], graph)
        assert result.exit_code != 0
        assert ID in result.output + result.stderr

    # Each position's own check, fed a batch that lands one place off: at
    # the head of the page instead of where it was sent.
    ELSEWHERE = {
        "last_child": ["insert-block", "--child-of", FIRST],
        "first_child": ["insert-block", "--child-of", FIRST, "--first"],
        "after": ["insert-block", "--after", SECOND],
        "before": ["insert-block", "--before", SECOND],
        "page_end": ["insert-block", "--page", "Page A", "--top-level"],
    }

    @pytest.mark.parametrize("args", ELSEWHERE.values(), ids=ELSEWHERE.keys())
    def test_a_batch_that_landed_elsewhere_fails(self, args):
        graph = _graph()
        page_uuid = graph.page_named("Page A")["uuid"]

        def to_the_head(real, a, n, o):
            return real(page_uuid, n, {"sibling": False, "keepUUID": True})
        self._broken(graph, to_the_head)
        result, _ = _run(args + ["--tree", TOP, "--keep-ids"], graph)
        assert result.exit_code != 0
        assert "not where" in result.output + result.stderr

    def test_earlier_writes_of_the_same_command_are_named(self):
        """add-journal-block with two --content writes twice; when the second
        write fails, the first one still stands, and the message says so."""
        graph = _graph()
        real, calls = graph.insert_batch_block, []

        def second_fails(anchor, nodes, options=None):
            calls.append(anchor)
            return real(anchor, nodes, options) if len(calls) == 1 else None
        graph.insert_batch_block = second_fails
        result, _ = _run(["add-journal-block", *UNDER, *DATE, "--content", "first",
                          "--content", TOP, "--keep-ids"], graph)
        assert result.exit_code != 0
        assert "1 block(s) written earlier" in result.output + result.stderr


class TestTargets:
    def test_a_target_uuid_in_capitals_is_found(self):
        graph = _graph()
        result, _ = _run(["insert-block", "--after", FIRST.upper(), "--tree", TOP,
                          "--keep-ids"], graph)
        assert result.exit_code == 0, result.output + result.stderr
        assert _restored(graph) == ("Page A", None, 1)

    def test_a_missing_page_is_created_as_append_would(self):
        """appendBlockInPage creates a missing page, with its empty block,
        and writes after it (measured); the restore does the same."""
        graph = _graph()
        result, _ = _run(["insert-block", "--page", "Nowhere Yet", "--content", TOP,
                          "--keep-ids"], graph)
        assert result.exit_code == 0, result.output + result.stderr
        assert graph.tree("Nowhere Yet") == [("", []), ("restored", [])]


class TestStillRefused:
    def test_a_block_with_two_id_lines(self):
        """Only one of them can be the block's id, and which one Logseq keeps
        from a batch is not the CLI's to guess: the other would reach the
        graph unchecked. Before #31 a single block went out with customUUID set
        to the first; now every write carries its lines as they are."""
        graph = _graph(placeholder=False)
        result, api = _run(["insert-block", "--after", FIRST, "--keep-ids",
                            "--content", f"moved\nid:: {ID}\nid:: {SECOND}"], graph)
        assert result.exit_code == 1
        assert "more than one id::" in result.stderr
        api.insert_batch_block.assert_not_called()


    def test_a_page_uuid(self):
        """A page is not a block, but its uuid is taken all the same: before
        #31 the check found it with the placeholders, and refused it."""
        graph = _graph(placeholder=False)
        page_uuid = graph.page_named("Page A")["uuid"]
        result, api = _run(["insert-block", "--after", FIRST, "--keep-ids",
                            "--tree", f"copy\nid:: {page_uuid}"], graph)
        assert result.exit_code == 1
        assert page_uuid in result.stderr and "page" in result.stderr
        api.insert_batch_block.assert_not_called()

    def test_an_id_a_block_still_has(self):
        graph = _graph(placeholder=False)
        result, api = _run(["insert-block", "--after", FIRST, "--keep-ids",
                            "--tree", f"copy\nid:: {SECOND}"], graph)
        assert result.exit_code == 1
        assert "already belong to a block" in result.stderr
        api.insert_batch_block.assert_not_called()
