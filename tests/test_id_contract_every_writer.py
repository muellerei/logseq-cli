"""#56: every write of block text applies the id:: contract.

An ``id::`` line in a block's text is the block's uuid to Logseq. Measured
against 0.10.15, the page file read again after each write:

- ``updateBlock`` with a foreign ``id:: <uuid>`` line: the block carries that
  uuid afterwards, the old one answers ``null``, every ``((ref))`` to it
  dangles. ``createPage`` + ``appendBlockInPage`` with such a line: the value
  becomes the new block's uuid. ``id:: a b c`` is taken as the uuid as well.
- ``getBlock`` hands out the block's own ``id::`` line in its content, and
  writing it back unchanged through ``updateBlock`` keeps the uuid. A block
  read and written back has to keep working.

insert-block, add-note-content, add-journal-block and add-journal-content had
the contract since #22/#31 (dropped with a note, or kept with --keep-ids).
The others did not. Now each command decides what its contract is, and
LogseqAPI refuses any id:: line that reaches it undecided, the way it refuses
a block boundary (#47), so a writer added later cannot go around it.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.blocktext import IdLineError
from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

OWN = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
FOREIGN = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"
TARGET = "8f2a3b4c-5d6e-4f70-8a9b-0c1d2e3f4a81"
TEXT_ID = "9a3b4c5d-6e7f-4081-8a9b-0c1d2e3f4a92"
DATE = ["--date", "2026-01-05"]


def _graph():
    return page_graph_api(PageGraph({"Page A": [
        {"uuid": OWN, "content": f"own\nid:: {OWN}"},
        {"uuid": TARGET, "content": "target", "children": [{"content": "child"}]},
        {"content": "ID: " + TEXT_ID},
    ], "Page B": ["b"]}))


def _run(args, api, input=None):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args, input=input)


def _contents(graph):
    def walk(blocks):
        for b in blocks:
            yield b["content"]
            yield from walk(b["children"])
    return [c for p in graph.pages for c in walk(p["blocks"])]


# --- the seam: LogseqAPI refuses an id:: line nobody decided on --------------

class TestTheApiRefusesAnUndecidedIdLine:
    def _api(self):
        api = LogseqAPI(token="t")
        api.call = MagicMock()
        return api

    @pytest.mark.parametrize("write", [
        lambda api: api.insert_block(TARGET, f"x\nid:: {FOREIGN}"),
        lambda api: api.append_block_in_page("P", f"x\nid:: {FOREIGN}"),
        lambda api: api.update_block(TARGET, f"x\nid:: {FOREIGN}"),
        lambda api: api.update_block(TARGET, "x\nid:: a b c"),
        lambda api: api.update_block(TARGET, f"x\ncustom-id:: {FOREIGN}"),
        lambda api: api.insert_batch_block(TARGET, [{"content": "ok", "children": [
            {"content": f"x\nid:: {FOREIGN}"}]}]),
    ], ids=["insertBlock", "appendBlockInPage", "updateBlock", "updateBlock, multi-word",
            "updateBlock, custom-id", "insertBatchBlock"])
    def test_refused(self, write):
        api = self._api()
        with pytest.raises(IdLineError):
            write(api)
        api.call.assert_not_called()

    @pytest.mark.parametrize("line", [
        f"id:: \t{FOREIGN}", f"id:: {FOREIGN}\u00a0", f"id:: {FOREIGN}\f",
        f"id:: {FOREIGN}\v", f"id:: {FOREIGN}\u3000", f"id:: {FOREIGN}\t",
    ], ids=["tab before", "no-break space", "form feed", "vertical tab",
            "ideographic space", "tab after"])
    def test_whitespace_logseq_trims_off_the_value_hides_nothing(self, line):
        # Each measured: Logseq took the uuid despite it (0.10.15).
        api = self._api()
        with pytest.raises(IdLineError):
            api.insert_block(TARGET, f"x\n{line}")
        api.call.assert_not_called()

    def test_a_trailing_carriage_return_leaves_the_line_text(self):
        # Measured the other way: Logseq does not take this one.
        api = self._api()
        api.insert_block(TARGET, f"x\nid:: {FOREIGN}\r")
        api.call.assert_called_once()

    def test_an_update_may_carry_the_blocks_own_id(self):
        # What getBlock hands out, written back: the uuid stays (measured).
        api = self._api()
        api.update_block(TARGET, f"new\nid:: {TARGET.upper()}")
        api.call.assert_called_once()

    def test_an_update_may_keep_a_line_the_block_had(self):
        # A copy carries its source's line until the file is read again
        # (measured); replace-text and set-todo-status change another line.
        api = self._api()
        api.update_block(TARGET, f"DONE x\nid:: {FOREIGN}", replacing=f"TODO x\nid:: {FOREIGN}")
        api.call.assert_called_once()

    def test_but_not_add_one(self):
        api = self._api()
        with pytest.raises(IdLineError):
            api.update_block(TARGET, f"x\nid:: {FOREIGN}", replacing="x")
        api.call.assert_not_called()

    def test_a_batch_that_keeps_ids_carries_them(self):
        # --keep-ids: check_block_ids has vetted the ids before this call.
        api = self._api()
        api.insert_batch_block(TARGET, [{"content": f"x\nid:: {FOREIGN}"}], {"keepUUID": True})
        api.call.assert_called_once()

    def test_an_id_line_in_a_code_block_is_code(self):
        api = self._api()
        api.insert_block(TARGET, f"x\n```\nid:: {FOREIGN}\n```")
        api.call.assert_called_once()

    def test_the_refusal_is_json_under_json(self):
        api = page_graph_api(PageGraph({"Page A": [{"uuid": TARGET, "content": "target"}]}))
        seam = LogseqAPI(token="t")
        seam.call = MagicMock()
        api.update_block.side_effect = seam.update_block  # the real check, no request
        r = _run(["replace-text", "--page", "Page A", "--find", "target",
                  "--replace", "t\nid:: " + FOREIGN, "--json"], api)
        assert r.exit_code == 2, (r.stdout, r.stderr)
        error = json.loads(r.stderr)
        assert error["reason"] == "id_line"
        assert error["line"] == 2


# --- the commands: each with its contract -------------------------------------

class TestUpdateBlock:
    def test_a_foreign_id_line_is_dropped_and_said(self):
        api = _graph()
        r = _run(["update-block", "--id", TARGET, "--content", f"new\nid:: {FOREIGN}"], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" in r.stderr
        assert api.graph.get_block(TARGET)["content"] == "new"
        assert FOREIGN not in " ".join(_contents(api.graph))

    def test_the_blocks_own_line_passes_in_silence(self):
        # Read, change, write back: the agent's ordinary round trip.
        api = _graph()
        content = api.graph.get_block(OWN)["content"].replace("own", "own, edited")
        r = _run(["update-block", "--id", OWN, "--content", content], api)
        assert r.exit_code == 0, r.stderr
        assert "dropped" not in r.stderr
        assert api.graph.get_block(OWN)["content"] == f"own, edited\nid:: {OWN}"

    def test_a_multi_word_value_is_an_id_too(self):
        api = _graph()
        r = _run(["update-block", "--id", TARGET, "--content", "new\nid:: a b c"], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" in r.stderr
        assert api.graph.get_block(TARGET)["content"] == "new"

    @pytest.mark.parametrize("extra", [["--dry-run", "--json"], ["--json"]],
                             ids=["dry run", "write"])
    def test_what_it_reports_is_what_it_writes(self, extra):
        api = _graph()
        r = _run(["update-block", "--id", TARGET, "--content",
                  f"new\nid:: {FOREIGN}", *extra], api)
        assert r.exit_code == 0, r.stderr
        assert json.loads(r.stdout)["new_content"] == "new"


    def test_text_that_was_only_a_foreign_id_empties_no_block(self):
        api = _graph()
        r = _run(["update-block", "--id", TARGET, "--content", f"id:: {FOREIGN}"], api)
        assert r.exit_code != 0
        assert "nothing but id:: lines" in r.stderr
        assert api.graph.get_block(TARGET)["content"] == "target"


class TestCreatePageAndAddJournalEntry:
    def test_create_page(self):
        api = _graph()
        r = _run(["create-page", "--page", "New", "--content", f"text\nid:: {FOREIGN}"], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" in r.stderr
        assert api.graph.tree("New")[-1][0] == "text"
        assert FOREIGN not in " ".join(_contents(api.graph))

    def test_create_page_dry_run_says_it_too(self):
        api = _graph()
        r = _run(["create-page", "--page", "New", "--content", f"text\nid:: {FOREIGN}",
                  "--dry-run"], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" in r.stderr
        assert FOREIGN not in r.stdout

    def test_blank_content_without_an_id_is_written_as_before(self):
        # Only text the dropped ids emptied is refused; the rest is not #56's.
        api = _graph()
        r = _run(["create-page", "--page", "New", "--content", "   "], api)
        assert r.exit_code == 0, r.stderr
        assert api.append_block_in_page.call_count == 1

    def test_create_page_on_a_page_that_exists_says_nothing_before_the_error(self):
        # The refusal is the one JSON object on stderr; a note ahead of it
        # would break it, and speak of text that is never written.
        r = _run(["create-page", "--page", "Page A", "--json",
                  "--content", f"text\nid:: {FOREIGN}"], _graph())
        assert r.exit_code == 1
        assert json.loads(r.stderr)["exists"] is True

    @pytest.mark.parametrize("args", [
        ["add-journal-entry", *DATE],
        ["add-journal-entry", *DATE, "--multi-block"],
        ["create-page", "--page", "New"],
    ], ids=["add-journal-entry", "add-journal-entry --multi-block", "create-page"])
    def test_text_that_was_nothing_but_an_id_writes_nothing(self, args):
        # Refused before the page is created: an empty page reported as
        # "has_content" or "Added 0 block(s)" at exit 0 is no answer.
        api = _graph()
        r = _run([*args, "--content", f"id:: {FOREIGN}"], api)
        assert r.exit_code != 0
        assert "nothing but id:: lines" in r.stderr
        assert api.create_page.call_count == 0
        assert api.append_block_in_page.call_count == 0

    @pytest.mark.parametrize("mode", [[], ["--multi-block"]], ids=["as block", "multi-block"])
    def test_add_journal_entry(self, mode):
        api = _graph()
        r = _run(["add-journal-entry", *DATE, *mode, "--content",
                  f"text\nid:: {FOREIGN}"], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" in r.stderr
        assert FOREIGN not in " ".join(_contents(api.graph))
        # createPage leaves one empty block; a line that was only the id is
        # not written as a second one.
        assert api.graph.tree("2026-01-05") == [("", []), ("text", [])]


class TestCopyBlock:
    def test_the_copy_carries_no_id_of_the_source(self):
        # The copy gets uuids of its own by definition; refs stay with the
        # original, so there is nothing to warn about.
        graph = PageGraph({"Page A": [{"uuid": OWN, "content": f"own\nid:: {OWN}",
                                       "children": [{"content": f"kid\nid:: {FOREIGN}"}]}],
                           "Page B": ["b"]})
        api = page_graph_api(graph)
        r = _run(["copy-block", "--id", OWN, "--to-page", "Page B"], api)
        assert r.exit_code == 0, r.stderr
        assert "dropped" not in r.stderr
        assert graph.tree("Page B")[-1] == ("own", [("kid", [])])
        copied = [c for c in _contents(graph)][-2:]
        assert not [c for c in copied if "id::" in c]


    def test_the_dry_run_shows_the_copy_without_it(self):
        graph = PageGraph({"Page A": [{"uuid": OWN, "content": f"own\nid:: {OWN}"}],
                           "Page B": ["b"]})
        r = _run(["copy-block", "--id", OWN, "--to-page", "Page B", "--dry-run"],
                 page_graph_api(graph))
        assert r.exit_code == 0, r.stderr
        assert "own" in r.stdout and OWN not in r.stdout


class TestTheHeadingIsBlockText:
    def test_an_id_line_in_the_heading_is_refused_before_the_page_is_made(self):
        # The heading is written as a block of its own, after the page.
        api = _graph()
        r = _run(["add-journal-block", *DATE, "--under-heading", f"## New\nid:: {FOREIGN}",
                  "--content", f"x\nid:: {TARGET}", "--json"], api)
        assert r.exit_code == 2, (r.stdout, r.stderr)
        assert json.loads(r.stderr)["reason"] == "id_line"  # no note ahead of it
        assert api.create_page.call_count == 0

    def test_a_refusal_of_the_options_comes_before_the_id_note(self):
        r = _run(["add-journal-block", *DATE, "--top-level", "--upsert-heading", "## X",
                  "--content", f"x\nid:: {FOREIGN}"], _graph())
        assert r.exit_code == 1
        assert "dropped" not in r.stderr


class TestReplaceText:
    def test_a_replacement_that_makes_an_id_line_is_refused(self):
        api = _graph()
        r = _run(["replace-text", "--page", "Page A", "--find", "ID: ",
                  "--replace", "ID:: "], api)
        assert r.exit_code == 2, (r.stdout, r.stderr)
        assert "id" in r.stderr and "Nothing was written" in r.stderr
        api.update_block.assert_not_called()

    def test_a_block_with_its_own_id_line_may_be_changed(self):
        api = _graph()
        r = _run(["replace-text", "--page", "Page A", "--find", "own",
                  "--replace", "mine"], api)
        assert r.exit_code == 0, r.stderr
        assert api.graph.get_block(OWN)["content"] == f"mine\nid:: {OWN}"


class TestTheWritersThatHadIt:
    def test_insert_block_drops_a_multi_word_id(self):
        api = _graph()
        r = _run(["insert-block", "--page", "Page B", "--content", "x\nid:: a b c"], api)
        assert r.exit_code == 0, r.stderr
        assert "will be dropped" in r.stderr
        assert api.graph.tree("Page B")[-1][0] == "x"
        assert "a b c" not in " ".join(_contents(api.graph))

    def test_keep_ids_refuses_a_multi_word_id(self):
        api = _graph()
        r = _run(["insert-block", "--page", "Page B", "--keep-ids", "--json",
                  "--content", "x\nid:: a b c"], api)
        assert r.exit_code == 1, (r.stdout, r.stderr)
        assert json.loads(r.stderr)["invalid_ids"] == ["a b c"]
