"""Page properties are written through the page's property block (#80).

Logseq takes a page's properties from its property block when that block is
saved; ``upsertBlockProperty`` writes the line without that step. So
set-property wrote the file and the page kept its old properties until Logseq
read the file again, and on a page whose first block held text the property
went into that block, where Logseq never reads it as the page's (measured,
0.10.15). The graph here answers the way Logseq does: see ``PageGraph``.
"""
import json
from unittest.mock import patch

import pytest

from logseq_cli.blocktext import with_property_line
from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner


class TestWithPropertyLine:
    """The text of a property block with one key set or taken away."""

    def test_a_new_key_goes_last(self):
        assert with_property_line("a:: 1", "b", "2") == "a:: 1\nb:: 2"

    def test_an_existing_key_keeps_its_place(self):
        assert with_property_line("a:: 1\nb:: 2", "a", "3") == "a:: 3\nb:: 2"

    def test_any_spelling_of_the_key_is_the_key(self):
        """Logseq reads ``Due_Date::`` as ``due-date``; a second line would
        leave two values for one key."""
        assert with_property_line("Due_Date:: x\nb:: 2", "due-date", "y") == \
            "due-date:: y\nb:: 2"

    def test_two_lines_of_one_key_become_one(self):
        assert with_property_line("Type:: a\ntype:: b", "type", "c") == "type:: c"

    def test_removing_drops_every_line_of_the_key(self):
        assert with_property_line("a:: 1\nA:: 2\nb:: 3", "a", None) == "b:: 3"

    def test_removing_the_last_key_leaves_nothing(self):
        assert with_property_line("a:: 1", "a", None) == ""

    def test_an_empty_block_gets_the_line(self):
        assert with_property_line("", "a", 1) == "a:: 1"

    def test_other_lines_stay_as_they_are(self):
        content = "id:: 00000000-0000-4000-8000-000000000001\ncollapsed:: true\na:: 1"
        assert with_property_line(content, "a", "2") == \
            "id:: 00000000-0000-4000-8000-000000000001\ncollapsed:: true\na:: 2"

    def test_a_line_in_a_code_block_is_not_a_property(self):
        content = "text\n```\na:: 1\n```"
        assert with_property_line(content, "a", None) == content

    def test_an_absent_key_changes_nothing(self):
        assert with_property_line("a:: 1", "b", None) == "a:: 1"


def _invoke(api, *args):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, list(args))


def _run(graph, *args):
    api = page_graph_api(graph)
    return api, _invoke(api, *args)


def _with_a_write_in_between(graph, page, text):
    """An api on ``graph`` where another call saves ``text`` into the page's
    first block right after the command has read the page."""
    api = page_graph_api(graph)
    read = graph.get_page_blocks_tree

    def tree_then_another_write(name):
        tree = read(name)
        graph.update_block(page["blocks"][0]["uuid"], text)
        return tree
    api.get_page_blocks_tree.side_effect = tree_then_another_write
    return api


def _props(graph, name):
    return graph.page_named(name)["props"]


class TestSetProperty:

    def test_a_new_key_on_a_property_block_reaches_the_page(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a", "text")
        _, r = _run(graph, "set-property", "--name", "P", "--key", "team", "--value", "x")
        assert r.exit_code == 0, r.output
        assert _props(graph, "P") == {"typ": "a", "team": "x"}

    def test_a_new_value_reaches_the_page(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a", "text")
        _, r = _run(graph, "set-property", "--name", "P", "--key", "typ", "--value", "b")
        assert r.exit_code == 0, r.output
        assert _props(graph, "P") == {"typ": "b"}

    def test_a_page_starting_with_text_gets_a_property_block_before_it(self):
        graph = PageGraph({"P": ["hello text", "second"]})
        _, r = _run(graph, "set-property", "--name", "P", "--key", "typ", "--value", "x")
        assert r.exit_code == 0, r.output
        assert graph.tree("P") == [("typ:: x", []), ("hello text", []), ("second", [])]
        assert _props(graph, "P") == {"typ": "x"}

    def test_an_empty_first_block_is_filled(self):
        graph = PageGraph({"P": ["", "text"]})
        _, r = _run(graph, "set-property", "--name", "P", "--key", "typ", "--value", "x")
        assert r.exit_code == 0, r.output
        assert graph.tree("P") == [("typ:: x", []), ("text", [])]
        assert _props(graph, "P") == {"typ": "x"}

    def test_a_first_block_of_property_lines_becomes_the_property_block(self):
        """What set-property left on a page created empty: the line is in
        the file, the page has no properties."""
        graph = PageGraph({"P": ["typ:: x", "text"]})
        _, r = _run(graph, "set-property", "--name", "P", "--key", "team", "--value", "y")
        assert r.exit_code == 0, r.output
        assert graph.tree("P") == [("typ:: x", []), ("text", [])]
        assert _props(graph, "P") == {"typ": "x", "team": "y"}

    def test_the_same_value_again_writes_nothing(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a", "text")
        api, r = _run(graph, "set-property", "--name", "P", "--key", "typ",
                      "--value", "a", "--json")
        assert r.exit_code == 0, r.output
        assert json.loads(r.stdout)["status"] == "unchanged"
        api.update_block.assert_not_called()

    def test_the_same_value_on_a_page_that_does_not_show_it_is_saved_again(self):
        """What set-property left on a page created empty: the line is right,
        the page never took it. Logseq saves only a change, so the block is
        saved empty first, then with its text."""
        graph = PageGraph({"P": ["typ:: x", "text"]})
        api, r = _run(graph, "set-property", "--name", "P", "--key", "typ",
                      "--value", "x", "--json")
        assert r.exit_code == 0, r.output
        assert json.loads(r.stdout)["status"] == "updated"
        assert _props(graph, "P") == {"typ": "x"}
        assert graph.tree("P") == [("typ:: x", []), ("text", [])]

    def test_a_key_the_old_way_added_to_a_property_block_reaches_the_page(self):
        graph = PageGraph()
        page = graph.with_property_block("P", "typ:: a", "text")
        graph.upsert_block_property(page["blocks"][0]["uuid"], "team", "x")
        _, r = _run(graph, "set-property", "--name", "P", "--key", "team", "--value", "x")
        assert r.exit_code == 0, r.output
        assert _props(graph, "P") == {"typ": "a", "team": "x"}

    def test_title_is_refused(self):
        """Saved into the property block, ``title::`` renames the page, past
        every check rename-page makes (measured)."""
        graph = PageGraph({"P": ["text"]})
        api, r = _run(graph, "set-property", "--name", "P", "--key", "title", "--value", "Q")
        assert r.exit_code == 1
        assert "rename-page" in r.stderr
        assert graph.tree("P") == [("text", [])]

    def test_a_front_matter_block_is_refused(self):
        graph = PageGraph()
        graph.with_property_block("P", "---\ntyp: a\n---", "text")
        api, r = _run(graph, "set-property", "--name", "P", "--key", "typ", "--value", "b")
        assert r.exit_code == 1
        api.update_block.assert_not_called()

    def test_a_write_the_page_does_not_show_fails(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a", "text")
        api = page_graph_api(graph)
        api.update_block.side_effect = None  # Logseq drops it
        r = _invoke(api, "set-property", "--name", "P", "--key", "team", "--value", "x")
        assert r.exit_code == 1
        assert "team" in r.stderr

    def test_collapsed_is_refused(self):
        """Logseq takes ``collapsed::`` out of the properties as the block's
        folded state (graph-parser block.cljs), so no page shows it."""
        graph = PageGraph({"P": ["text"]})
        api, r = _run(graph, "set-property", "--name", "P", "--key", "collapsed", "--value", "true")
        assert r.exit_code == 1
        assert graph.tree("P") == [("text", [])]

    def test_a_write_since_the_page_was_read_is_kept(self):
        """The block's text is written whole, so it is read again just before:
        a key another call set in between must not be undone."""
        graph = PageGraph()
        page = graph.with_property_block("P", "typ:: a", "text")
        api = _with_a_write_in_between(graph, page, "typ:: a\nother:: b")
        r = _invoke(api, "set-property", "--name", "P", "--key", "team", "--value", "x")
        assert r.exit_code == 0, r.stderr
        assert _props(graph, "P") == {"typ": "a", "other": "b", "team": "x"}

    def test_upsert_is_not_used(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a", "text")
        api, r = _run(graph, "set-property", "--name", "P", "--key", "team", "--value", "x")
        api.upsert_block_property.assert_not_called()


class TestRemoveProperty:

    def test_a_key_leaves_the_page(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a\nteam:: x", "text")
        _, r = _run(graph, "remove-property", "--name", "P", "--key", "team")
        assert r.exit_code == 0, r.output
        assert _props(graph, "P") == {"typ": "a"}

    def test_the_last_key_takes_the_block_with_it(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a", "text")
        _, r = _run(graph, "remove-property", "--name", "P", "--key", "typ")
        assert r.exit_code == 0, r.output
        assert graph.tree("P") == [("text", [])]
        assert _props(graph, "P") == {}

    def test_a_write_since_the_page_was_read_is_kept(self):
        graph = PageGraph()
        page = graph.with_property_block("P", "typ:: a\nteam:: x", "text")
        api = _with_a_write_in_between(graph, page, "typ:: a\nteam:: x\nother:: b")
        r = _invoke(api, "remove-property", "--name", "P", "--key", "team")
        assert r.exit_code == 0, r.stderr
        assert _props(graph, "P") == {"typ": "a", "other": "b"}

    def test_the_last_key_of_a_page_with_no_other_block_leaves_it_empty(self):
        """Removed, it would leave a page without blocks, which set-property
        then refuses."""
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a")
        _, r = _run(graph, "remove-property", "--name", "P", "--key", "typ")
        assert r.exit_code == 0, r.output
        assert graph.tree("P") == [("", [])]
        assert _props(graph, "P") == {}

    def test_a_property_in_a_first_text_block_is_removed_from_it(self):
        """Where set-property put it on a page starting with text."""
        graph = PageGraph({"P": ["hello text\ntyp:: x", "second"]})
        _, r = _run(graph, "remove-property", "--name", "P", "--key", "typ")
        assert r.exit_code == 0, r.output
        assert graph.page_named("P")["blocks"][0]["content"] == "hello text"

    def test_a_removal_the_page_does_not_show_fails(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a\nteam:: x", "text")
        api = page_graph_api(graph)
        api.update_block.side_effect = None  # Logseq drops it
        r = _invoke(api, "remove-property", "--name", "P", "--key", "team")
        assert r.exit_code == 1
        assert "team" in r.stderr

    def test_a_key_the_page_still_shows_is_removed_from_it(self):
        """The line went, the page kept the key: what remove-property did on
        a property block before #80."""
        graph = PageGraph()
        page = graph.with_property_block("P", "typ:: a\nteam:: x", "text")
        page["blocks"][0]["content"] = "typ:: a"
        _, r = _run(graph, "remove-property", "--name", "P", "--key", "team")
        assert r.exit_code == 0, r.output
        assert _props(graph, "P") == {"typ": "a"}

    def test_a_rule_in_a_first_text_block_is_not_front_matter(self):
        """Only a property block written as front matter is refused."""
        graph = PageGraph({"P": ["---", "text"]})
        _, r = _run(graph, "remove-property", "--name", "P", "--key", "typ", "--json")
        assert r.exit_code == 0, r.stderr
        assert json.loads(r.stdout)["status"] == "not_present"

    def test_an_absent_key_writes_nothing_and_says_so(self):
        graph = PageGraph()
        graph.with_property_block("P", "typ:: a", "text")
        api, r = _run(graph, "remove-property", "--name", "P", "--key", "team", "--json")
        assert r.exit_code == 0, r.output
        assert json.loads(r.stdout)["status"] == "not_present"
        api.update_block.assert_not_called()
        api.remove_block.assert_not_called()


class TestDryRunShowsTheSameTarget:

    @pytest.mark.parametrize("blocks, where", [
        (["hello text"], "new property block"),
        (["typ:: a", "text"], "property block"),
    ])
    def test_set(self, blocks, where):
        graph = PageGraph({"P": blocks})
        api, r = _run(graph, "set-property", "--name", "P", "--key", "typ",
                      "--value", "b", "--dry-run", "--json")
        assert r.exit_code == 0, r.output
        preview = json.loads(r.stdout)
        assert preview["target"] == where
        api.update_block.assert_not_called()
        api.insert_block.assert_not_called()

    def test_the_old_value_is_the_one_the_write_replaces(self):
        """Not one from a text block the write would leave alone."""
        graph = PageGraph({"P": ["hello text\ntyp:: a"]})
        _, r = _run(graph, "set-property", "--name", "P", "--key", "typ",
                    "--value", "b", "--dry-run", "--json")
        preview = json.loads(r.stdout)
        assert preview["existed"] is False
