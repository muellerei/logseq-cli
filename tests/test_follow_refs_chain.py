"""set-todo-status --follow-refs changes the task a ref leads to.

A task pulled into a journal by reference is a block holding only
``((uuid))``; ticking it off there should change the original, not add a
second task on the reference (#106).
"""
import json
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

# Letters in each uuid, so a ref in capitals differs from one in lower case.
TASK = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a01"
HOST = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a02"
MID = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a03"
GONE = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4aff"      # no block, no placeholder
HOLDER = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4afe"    # only a placeholder


def run(blocks, *args, placeholders=()):
    """Run set-todo-status --status DONE on a page of ``blocks``; return the
    result, a function that reads a block's content afterwards, and the api
    to check its calls."""
    graph = PageGraph({"Page A": blocks}, placeholders=placeholders)
    api = page_graph_api(graph)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        result = split_runner().invoke(cli, ["set-todo-status", *args, "--status", "DONE"])
    return result, lambda uuid: graph.get_block(uuid, include_children=False)["content"], api


def task():
    return {"uuid": TASK, "content": f"TODO ship it\nid:: {TASK}", "marker": "TODO"}


def pointer(uuid, to, embed=False):
    """A block that holds only a ref to ``to``, with its own Id Line, as a
    block that is itself a ref target has."""
    ref = f"{{{{embed (({to}))}}}}" if embed else f"(({to}))"
    return {"uuid": uuid, "content": f"{ref}\nid:: {uuid}"}


def refusal(result):
    """The one JSON object a refused --json run wrote to stderr."""
    assert result.exit_code != 0
    assert result.stdout == ""
    return json.loads(result.stderr)


class TestWhatStays:
    """Behaviour the chain fix keeps."""

    @pytest.mark.parametrize("ref", [TASK, TASK.upper()], ids=["lower", "capitals"])
    def test_a_ref_to_a_task_changes_the_task(self, ref):
        result, content, _ = run([{"uuid": TASK, "content": "TODO ship it", "marker": "TODO"},
                                 {"uuid": HOST, "content": f"(({ref}))"}],
                                 "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == "DONE ship it"
        assert content(HOST) == f"(({ref}))"

    def test_a_block_without_a_ref_is_changed_itself(self):
        result, content, _ = run([{"uuid": HOST, "content": "TODO ship it", "marker": "TODO"}],
                                 "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(HOST) == "DONE ship it"

    def test_a_labelled_ref_is_a_link_and_no_task(self):
        # A labelled ref is a link with text of its own, no ref to follow, and
        # Logseq reads no marker in it: the block is no task, nothing is written.
        result, content, api = run([{"uuid": TASK, "content": "TODO ship it", "marker": "TODO"},
                                    {"uuid": HOST, "content": f"[see]((({TASK})))"}],
                                   "--id", HOST, "--follow-refs", "--json")
        assert refusal(result)["reason"] == "not_a_task"
        assert refusal(result)["id"] == HOST
        api.update_block.assert_not_called()
        assert content(HOST) == f"[see]((({TASK})))"
        assert content(TASK) == "TODO ship it"

    def test_an_unknown_block_is_not_found_in_the_same_words(self):
        result, _, _ = run([], "--id", GONE)
        assert result.exit_code != 0
        assert f"Block {GONE} not found." in result.stderr

    def test_without_follow_refs_the_output_has_no_chain(self):
        result, _, _ = run([{"uuid": HOST, "content": "TODO ship it", "marker": "TODO"}], "--id", HOST, "--json")
        assert "followed" not in json.loads(result.stdout)


class TestChains:
    """A chain of refs leads to the task at its end (#106)."""

    def test_a_host_with_its_id_line_is_followed(self):
        result, content, _ = run([task(), pointer(HOST, TASK)], "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == f"DONE ship it\nid:: {TASK}"
        assert content(HOST) == f"(({TASK}))\nid:: {HOST}"

    def test_a_chain_of_three_changes_the_last_block_only(self):
        result, content, _ = run([task(), pointer(MID, TASK), pointer(HOST, MID)],
                                 "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == f"DONE ship it\nid:: {TASK}"
        assert content(MID) == f"(({TASK}))\nid:: {MID}"
        assert content(HOST) == f"(({MID}))\nid:: {HOST}"

    def test_an_embed_host_is_followed(self):
        result, content, _ = run([task(), pointer(HOST, TASK, embed=True)],
                                 "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == f"DONE ship it\nid:: {TASK}"
        assert content(HOST) == f"{{{{embed (({TASK}))}}}}\nid:: {HOST}"

    def test_an_embed_in_the_middle_of_a_chain_is_followed(self):
        result, content, _ = run([task(), pointer(MID, TASK, embed=True), pointer(HOST, MID)],
                                 "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == f"DONE ship it\nid:: {TASK}"
        assert content(MID) == f"{{{{embed (({TASK}))}}}}\nid:: {MID}"

    @pytest.mark.parametrize("embed", ["{{embed ((T)) }}", "{{embed  ((T))}}"])
    def test_an_embed_with_the_spaces_logseq_allows_is_followed(self, embed):
        # Logseq reads both as an embed of T (measured, 0.10.15).
        result, content, _ = run([task(), {"uuid": HOST, "content": embed.replace("T", TASK)}],
                                 "--id", HOST, "--follow-refs")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == f"DONE ship it\nid:: {TASK}"

    @pytest.mark.parametrize("text", ["{{ embed ((T))}}", "{{EMBED ((T))}}"])
    def test_what_logseq_reads_as_no_embed_is_no_task(self, text):
        # Neither is an embed to Logseq, the second holds no ref at all
        # (measured, 0.10.15): nothing to follow, and no marker either, so the
        # block the caller named is refused and nothing is written.
        host = text.replace("T", TASK)
        result, content, api = run([task(), {"uuid": HOST, "content": host}],
                                   "--id", HOST, "--follow-refs", "--json")
        out = refusal(result)
        assert (out["reason"], out["id"]) == ("not_a_task", HOST)
        api.update_block.assert_not_called()
        assert content(HOST) == host
        assert content(TASK) == f"TODO ship it\nid:: {TASK}"

    def test_a_block_found_by_content_is_followed(self):
        # The search answers in the shape of its datalog pull.
        hit = {"uuid": HOST, "content": f"(({MID}))", "page": {"original-name": "Page A"}}
        with patch("logseq_cli.commands.todos.find_blocks_by_content", return_value=[hit]):
            result, content, _ = run([task(), pointer(MID, TASK), pointer(HOST, MID)],
                                     "--content", MID[-6:], "--page", "Page A", "--follow-refs",
                                     "--json")
        assert result.exit_code == 0, result.stderr
        assert content(TASK) == f"DONE ship it\nid:: {TASK}"
        assert json.loads(result.stdout)["followed"] == [MID, TASK]


class TestRefusals:
    """A chain that loops or breaks is refused, and nothing is written."""

    # Logseq keeps a placeholder for a ref's missing block once it reads the
    # file again: content "id:: <uuid>", no page (measured, 0.10.15).
    @pytest.mark.parametrize("blocks,reason,refused,followed", [
        ([pointer(MID, HOST), pointer(HOST, MID)], "ref_cycle", HOST, [MID]),
        ([pointer(TASK, MID), pointer(MID, TASK), pointer(HOST, MID)],
         "ref_cycle", MID, [MID, TASK]),
        ([pointer(HOST, HOST)], "ref_cycle", HOST, []),
        ([pointer(MID, HOST.upper()), pointer(HOST, MID)], "ref_cycle", HOST, [MID]),
        ([pointer(MID, GONE), pointer(HOST, MID)], "dead_ref", GONE, [MID]),
        ([pointer(HOST, HOLDER)], "dead_ref", HOLDER, []),
    ], ids=["back to the start", "loop further on", "itself", "back in capitals",
            "to nothing", "to a placeholder"])
    def test_a_broken_chain_is_refused(self, blocks, reason, refused, followed):
        result, content, api = run(blocks, "--id", HOST, "--follow-refs", "--json",
                                   placeholders=[HOLDER])
        error = refusal(result)
        assert (error["reason"], error["id"], error["followed"]) == (reason, refused, followed)
        assert not api.update_block.called
        assert content(HOST) == next(b["content"] for b in blocks if b["uuid"] == HOST)

    def test_a_refusal_reads_as_text_without_json(self):
        result, _, _ = run([pointer(HOST, GONE)], "--id", HOST, "--follow-refs")
        assert result.exit_code != 0
        assert GONE in result.stderr and "Nothing was written" in result.stderr

    @pytest.mark.parametrize("blocks,block,reason", [
        ([pointer(HOST, GONE)], HOST, "dead_ref"),
        ([pointer(MID, HOST), pointer(HOST, MID)], HOST, "ref_cycle"),
        ([], HOLDER, "block_not_found"),
    ], ids=["dead_ref", "ref_cycle", "block_not_found"])
    def test_refusals_hold_under_dry_run(self, blocks, block, reason):
        result, _, api = run(blocks, "--id", block, "--follow-refs", "--dry-run", "--json",
                             placeholders=[HOLDER])
        assert refusal(result)["reason"] == reason
        assert not api.update_block.called


class TestAMissingBlock:
    """--id on a block Logseq has no page for is no block (#106)."""

    @pytest.mark.parametrize("follow", [[], ["--follow-refs"]], ids=["plain", "follow-refs"])
    @pytest.mark.parametrize("given", [HOLDER, HOLDER.upper()], ids=["lower", "capitals"])
    def test_a_placeholder_is_not_found(self, follow, given):
        result, _, api = run([], "--id", given, *follow, "--json", placeholders=[HOLDER])
        error = refusal(result)
        assert (error["reason"], error["id"]) == ("block_not_found", given)
        assert not api.update_block.called


class TestFollowed:
    """``followed`` names the blocks passed, first target to last."""

    def test_dry_run_lists_the_chain_in_order_and_writes_nothing(self):
        result, _, api = run([task(), pointer(MID, TASK), pointer(HOST, MID)],
                             "--id", HOST, "--follow-refs", "--dry-run", "--json")
        assert result.exit_code == 0, result.stderr
        preview = json.loads(result.stdout)
        assert preview["followed"] == [MID, TASK] and preview["uuid"] == TASK
        assert not api.update_block.called

    def test_dry_run_shows_the_chain_as_text(self):
        result, _, _ = run([task(), pointer(MID, TASK), pointer(HOST, MID)],
                           "--id", HOST, "--follow-refs", "--dry-run")
        assert f"followed: {MID} -> {TASK}" in result.stdout

    def test_the_write_result_carries_it(self):
        result, _, _ = run([task(), pointer(HOST, TASK)], "--id", HOST, "--follow-refs", "--json")
        assert json.loads(result.stdout)["followed"] == [TASK]

    def test_an_unchanged_result_carries_it(self):
        result, _, _ = run([{"uuid": TASK, "content": "DONE ship it", "marker": "DONE"}, pointer(HOST, TASK)],
                           "--id", HOST, "--follow-refs", "--json")
        payload = json.loads(result.stdout)
        assert payload["status"] == "unchanged" and payload["followed"] == [TASK]

    def test_without_a_chain_it_is_empty(self):
        result, _, _ = run([{"uuid": HOST, "content": "TODO ship it", "marker": "TODO"}],
                           "--id", HOST, "--follow-refs", "--json")
        assert json.loads(result.stdout)["followed"] == []

