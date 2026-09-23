"""A page name means the page Logseq would open for it, in every command (#63).

Logseq's HTTP API does not resolve an alias: ``getPage`` answers the alias's
own stub. Read through it, a page came back empty with exit 0; written
through it, the text landed on a page of its own that Logseq does not show
under that name (measured, 0.10.15). Exit 0 proves nothing here, so no test
below stops at the exit code: each asserts which page was read or written.

The net: every command that takes a page name is run with an alias, and no
API call after the resolution may name the alias. A command added later with
a page option and without a case here fails ``test_every_page_option_is_covered``.
"""
import json

import click
import pytest
from unittest.mock import patch

from logseq_cli.cli import cli
from logseq_cli.pagenames import (AmbiguousAliasError, PageRef, alias_sources,
                                  empty_or_placeholder, resolve_page)
from tests.conftest import PageGraph, page_graph_api, split_runner

ALIAS = "zz-al"
TARGET = "Ziel"
TODO = "00000000-0000-4000-8000-00000000aaaa"


def _graph(**extra):
    pages = {TARGET: [f"alias:: {ALIAS}\nfarbe:: rot",
                      {"content": "TODO inhalt", "uuid": TODO}]}
    pages.update(extra.pop("pages", {}))
    return PageGraph(pages, aliases=extra.pop("aliases", {ALIAS: [TARGET]}), **extra)


def _ambiguous_graph():
    return _graph(pages={"Other": ["x"]}, aliases={ALIAS: [TARGET, "Other"]})


def _query_api(rows):
    """An API whose alias query answers ``rows``."""
    class Api:
        def datascript_query(self, query):
            return rows
    return Api()


def _run(args, graph=None):
    graph = graph or _graph()
    api = page_graph_api(graph)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        result = split_runner().invoke(cli, args)
    return result, api, graph


def _names(value, name):
    name = name.lower()
    if isinstance(value, str):
        return value.lower() == name or f'"{name}"' in value.lower()
    if isinstance(value, (list, tuple)):
        return any(_names(v, name) for v in value)
    if isinstance(value, dict):
        return any(_names(v, name) for v in value.values())
    return False


def _calls_naming_alias(api):
    """API calls that name the alias, other than the ones that resolve it."""
    offending = []
    for name, args, kwargs in api.mock_calls:
        if name in ("get_page", "get_page_blocks_tree") and args == (ALIAS,):
            continue
        if name == "datascript_query" and ":block/alias" in args[0]:
            continue
        if _names(list(args), ALIAS) or _names(kwargs, ALIAS):
            offending.append((name, args))
    return offending


def _calls_naming_target(api):
    return [n for n, args, kwargs in api.mock_calls
            if _names(list(args), TARGET) or _names(kwargs, TARGET)]


# --- the decision -----------------------------------------------------------

class TestResolvePage:
    def test_empty_alias_means_its_source(self):
        api = page_graph_api(_graph())
        assert resolve_page(api, ALIAS) == PageRef(ALIAS, TARGET, redirected=True)

    def test_placeholder_alias_means_its_source(self):
        # A page created empty before it became an alias: one block, "".
        graph = _graph(pages={ALIAS: [""]})
        assert resolve_page(page_graph_api(graph), ALIAS).page == TARGET

    def test_alias_with_blocks_of_its_own_is_its_own_page(self):
        # Logseq does not redirect it either; its blocks stay reachable.
        graph = _graph(pages={ALIAS: ["written on the alias"]})
        assert resolve_page(page_graph_api(graph), ALIAS) == PageRef(ALIAS, ALIAS)

    def test_page_with_a_file_is_itself_without_reading_its_blocks(self):
        # An alias has no file (measured); a large page is not read whole.
        api = page_graph_api(_graph())
        assert resolve_page(api, TARGET) == PageRef(TARGET, TARGET)
        api.get_page_blocks_tree.assert_not_called()
        api.datascript_query.assert_not_called()

    def test_page_without_a_file_but_with_blocks_is_itself(self):
        graph = _graph(pages={"made-here": ["x"]})
        graph.page_named("made-here")["file"] = False
        api = page_graph_api(graph)
        assert resolve_page(api, "made-here").redirected is False
        api.datascript_query.assert_not_called()

    def test_alias_written_in_another_unicode_form_matches(self):
        rows = [["Ziel", ["mu\u0308ller"]]]
        assert alias_sources(_query_api(rows), "m\u00fcller") == ["Ziel"]

    def test_unknown_name_is_itself(self):
        assert resolve_page(page_graph_api(_graph()), "nowhere") == PageRef("nowhere", "nowhere")

    def test_empty_page_nobody_aliases_is_itself(self):
        graph = _graph(blockless=("only-linked",))
        assert resolve_page(page_graph_api(graph), "only-linked").redirected is False

    def test_two_sources_are_refused_not_guessed(self):
        graph = _ambiguous_graph()
        with pytest.raises(AmbiguousAliasError) as e:
            resolve_page(page_graph_api(graph), ALIAS)
        assert e.value.candidates == ["Other", TARGET]

    def test_query_uses_the_name_logseq_reports(self):
        api = page_graph_api(_graph())
        resolve_page(api, ALIAS.upper())
        query = api.datascript_query.call_args[0][0]
        assert f'"{ALIAS}"' in query

    def test_only_a_source_naming_the_alias_counts(self):
        # :block/alias links a whole group; a sibling alias with a file of its
        # own answers too, but its alias:: does not name this one.
        rows = [["Ziel", ["zz-al", "zz-b"]], ["zz-b", ["zz-c"]]]
        assert alias_sources(_query_api(rows), ALIAS) == ["Ziel"]

    @pytest.mark.parametrize("tree,expected", [
        ([], True), (None, True),
        ([{"content": "", "children": []}], True),
        ([{"content": "x", "children": []}], False),
        ([{"content": "", "children": [{"content": "y"}]}], False),
        ([{"content": ""}, {"content": ""}], False),
    ])
    def test_empty_or_placeholder(self, tree, expected):
        assert empty_or_placeholder(tree) is expected


# --- every command ----------------------------------------------------------

FOLLOWS = {
    "get-page": ["get-page", "--name", ALIAS, "--json"],
    "get-page-stats": ["get-page-stats", "--name", ALIAS, "--json"],
    "get-properties": ["get-properties", "--name", ALIAS, "--json"],
    "get-backlinks": ["get-backlinks", "--name", ALIAS, "--json"],
    "find-block": ["find-block", "--content", "inhalt", "--page", ALIAS, "--json"],
    "replace-text": ["replace-text", "--page", ALIAS, "--find", "inhalt",
                     "--replace", "neu", "--json"],
    "update-block": ["update-block", "--where-content", "inhalt", "--page", ALIAS,
                     "--content", "neu", "--json"],
    "set-todo-status": ["set-todo-status", "--content", "inhalt", "--page", ALIAS,
                        "--status", "DONE", "--json"],
    "add-note-content": ["add-note-content", "--page", ALIAS, "--content", "neu", "--json"],
    "insert-block": ["insert-block", "--page", ALIAS, "--content", "neu", "--json"],
    "add-block-ref": ["add-block-ref", "--source-id", TODO, "--page", ALIAS,
                      "--under-heading", "## Refs", "--json"],
    "copy-block": ["copy-block", "--id", TODO, "--to-page", ALIAS, "--json"],
    "set-property": ["set-property", "--page", ALIAS, "--key", "farbe",
                     "--value", "blau", "--json"],
    "remove-property": ["remove-property", "--page", ALIAS, "--key", "farbe", "--json"],
}
REFUSES = {
    "delete-page": ["delete-page", "--name", ALIAS, "--force", "--json"],
    "rename-page": ["rename-page", "--name", ALIAS, "--new-name", "Neu", "--json"],
}
# Page options that do not name an existing page, with the reason.
EXEMPT = {
    ("get-todos", "page"): "a substring filter on page names, not a page",
    ("rename-page", "new_name"): "a name to be given, not one that means a page",
    ("suggest-connections", "focus"): "a filter on page names, not a page",
}


@pytest.mark.parametrize("command", sorted(FOLLOWS))
def test_command_follows_the_alias(command):
    result, api, graph = _run(FOLLOWS[command])
    assert _calls_naming_alias(api) == [], result.output
    # Not satisfied by stopping early: the page read or written is the target.
    assert _calls_naming_target(api), result.output
    assert graph.page_named(ALIAS)["blocks"] == [], "wrote onto the alias"


@pytest.mark.parametrize("command", sorted(REFUSES))
def test_command_refuses_the_alias(command):
    result, api, graph = _run(REFUSES[command])
    assert result.exit_code == 1, result.output
    error = json.loads(result.stderr)
    assert error["reason"] == "alias"
    assert (error["page"], error["alias_of"]) == (ALIAS, TARGET)
    api.delete_page.assert_not_called()
    api.rename_page.assert_not_called()


def test_every_page_option_is_covered():
    """Each option that names a page has a case above or a reason in EXEMPT."""
    found = set()
    for name, command in cli.commands.items():
        for param in command.params:
            if isinstance(param, click.Option) and param.name in (
                    "page", "to_page", "new_name", "focus"):
                found.add((name, param.name))
    covered = {(c, "to_page" if c == "copy-block" else "page")
               for c in list(FOLLOWS) + list(REFUSES) + ["create-page"]} | set(EXEMPT)
    # add-block-ref and the journal commands name journals by date as well;
    # their --page is covered by the case above.
    assert found - covered == set(), "page option without an alias case"
    assert covered - found == set(), "case for an option that no longer exists"


# --- what the caller sees ---------------------------------------------------

class TestGetPage:
    def test_reads_the_target_and_says_so(self):
        result, _, _ = _run(["get-page", "--name", ALIAS, "--json", "--no-backlinks"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        # page stays the name asked for; alias_of names the page read.
        assert (payload["page"], payload["alias_of"]) == (ALIAS, TARGET)
        assert [b["content"] for b in payload["blocks"]][1] == "TODO inhalt"

    def test_text_mode_notes_it(self):
        result, _, _ = _run(["get-page", "--name", ALIAS, "--no-backlinks"])
        assert "TODO inhalt" in result.stdout
        assert f"'{ALIAS}' is an alias of '{TARGET}'" in result.stderr

    def test_plain_page_has_no_alias_field(self):
        result, _, _ = _run(["get-page", "--name", TARGET, "--json", "--no-backlinks"])
        assert "alias_of" not in json.loads(result.stdout)

    def test_ambiguous_name_is_reported_like_a_missing_one(self):
        graph = _ambiguous_graph()
        result, _, _ = _run(["get-page", "--name", TARGET, "--name", ALIAS,
                             "--name", "nowhere", "--json", "--no-backlinks"], graph)
        assert result.exit_code == 1
        first, second, third = json.loads(result.stdout)
        assert first["blocks"]                     # the other names still read
        assert second == {"page": ALIAS, "ambiguous": ["Other", TARGET]}
        error = json.loads(result.stderr)          # one object on stderr
        assert error["ambiguous"] == {ALIAS: ["Other", TARGET]}
        assert error["missing"] == ["nowhere"]

    def test_alias_and_its_page_count_as_one_page_for_a_cut_read(self):
        # Otherwise a continuation by uuid jumps back into the first copy.
        result, _, _ = _run(["get-page", "--name", ALIAS, "--name", TARGET,
                             "--max-chars", "50", "--json"])
        assert result.exit_code == 1
        assert "named twice" in json.loads(result.stderr)["error"]


def test_write_goes_to_the_target_and_says_so():
    result, _, graph = _run(["add-note-content", "--page", ALIAS, "--content", "neu", "--json"])
    assert result.exit_code == 0, result.output
    assert graph.tree(TARGET)[-1] == ("neu", [])
    payload = json.loads(result.stdout)
    assert (payload["page"], payload["alias_of"], payload["created"]) == (ALIAS, TARGET, False)


def test_backlinks_report_an_ambiguous_alias_per_name():
    graph = _ambiguous_graph()
    result, _, _ = _run(["get-backlinks", "--name", ALIAS, "--name", TARGET, "--json"], graph)
    assert result.exit_code == 1
    first, second = json.loads(result.stdout)
    assert first == {"page": ALIAS, "ambiguous": ["Other", TARGET]}
    assert second["page"] == TARGET and "backlinks" in second
    assert json.loads(result.stderr)["ambiguous"] == {ALIAS: ["Other", TARGET]}


def test_create_page_preview_names_the_page_an_alias_means():
    result, _, _ = _run(["create-page", "--name", ALIAS, "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (payload["alias_of"], payload["would_create"]) == (TARGET, False)


def test_create_page_preview_reports_an_ambiguous_alias():
    # A preview that exits non-zero reads as one that failed to run.
    graph = _ambiguous_graph()
    result, _, _ = _run(["create-page", "--name", ALIAS, "--dry-run", "--json"], graph)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ambiguous"] == ["Other", TARGET]


def test_insert_block_with_an_anchor_does_not_resolve_page():
    # --page is not the target next to --after; it names nothing written to.
    result, _, graph = _run(["insert-block", "--after", TODO, "--page", ALIAS,
                             "--tree", "neu", "--json"])
    assert result.exit_code == 0, result.output
    assert "alias_of" not in json.loads(result.stdout)
    assert graph.tree(TARGET)[-1] == ("neu", [])


def test_create_page_on_an_alias_names_its_page():
    result, api, _ = _run(["create-page", "--name", ALIAS, "--json"])
    assert result.exit_code == 1
    error = json.loads(result.stderr)
    assert error["alias_of"] == TARGET
    api.create_page.assert_not_called()


def test_ambiguous_alias_refuses_a_write():
    graph = _ambiguous_graph()
    result, api, _ = _run(["add-note-content", "--page", ALIAS, "--content", "neu",
                           "--json"], graph)
    assert result.exit_code == 1
    error = json.loads(result.stderr)
    assert error["reason"] == "ambiguous_alias"
    assert error["ambiguous"] == ["Other", TARGET]
    api.append_block_in_page.assert_not_called()
    api.insert_batch_block.assert_not_called()
