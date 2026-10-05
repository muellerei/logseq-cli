"""set-todo-status changes the marker and nothing else.

It split the whole content at its first whitespace to find the marker, and a
line break is whitespace: "TODO\\nnotes" came out as "DONE notes", the two lines
joined, and a code block under a bare "TODO" lost the line break before its
fence. Written that way, "DONE ```js" leaves the closing fence without an
opener, and Logseq lets such a fence swallow the blocks after it when it reads
the page file again (#47). The marker is now changed on the first line only.
"""
import json
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

UUID = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"


def _api_with(block):
    api = page_graph_api(PageGraph({"Page A": [block]}))
    api.update_block.side_effect = lambda u, c, properties=None, replacing=None: \
        api.graph.locate(u)[1][api.graph.locate(u)[2]].update(content=c)
    return api


def _set_status(block, *extra, status="DONE", api=None):
    api = api or _api_with(block)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, ["set-todo-status", "--id", block["uuid"],
                                        "--status", status, *extra])
    return r, api


def _block(content, marker=None, uuid=UUID):
    return {"uuid": uuid, "content": content, **({"marker": marker} if marker else {})}


# The marker is what Logseq stored for the block, the third value.
@pytest.mark.parametrize("old,new,marker", [
    ("TODO write it\nnotes:: more", "DONE write it\nnotes:: more", "TODO"),
    ("TODO  spaced", "DONE spaced", "TODO"),
    ("## TODO ship it", "## DONE ship it", "TODO"),
    ("TODO \nnotes", "DONE \nnotes", "TODO"),
    ("STARTED x", "DONE x", "STARTED"),
    ("WAITING x", "DONE x", "WAITING"),
], ids=["property line", "extra space", "heading prefix", "space before the line break",
        "started", "waiting"])
def test_only_the_marker_changes(old, new, marker):
    r, api = _set_status(_block(old, marker))
    assert r.exit_code == 0, r.stderr
    assert api.update_block.call_args.args[1] == new


GENERAL = 'is not a task: Logseq reads no marker in it. A task starts with a marker, e.g. "TODO'
NOT_THERE = "not where this command can change it"
NOT_A_TASK = [
    ("todo a", None, GENERAL),
    ("no marker", None, GENERAL),
    ("TODO x", None, GENERAL),
    ("TODO\nnotes", None, 'a line break follows "TODO" directly'),
    ("TODO\n```js\nx()\n```", None, 'a line break follows "TODO" directly'),
    ("##\tTODO x", "TODO", NOT_THERE),
    ("TODO\nnotes", "TODO", NOT_THERE),
    ("```py\nx\n```", None, GENERAL),
    ("status:: open\nid:: T", None, GENERAL),
    ("id:: T", None, GENERAL),
]


@pytest.mark.parametrize("dry", [[], ["--dry-run"]], ids=["write", "dry-run"])
@pytest.mark.parametrize("text,marker,fragment", NOT_A_TASK,
                         ids=[t[0].replace("\n", "|")[:24] + ("+db" if t[1] else "") for t in NOT_A_TASK])
def test_a_block_that_is_no_task_is_refused(text, marker, fragment, dry):
    r, api = _set_status(_block(text, marker), "--json", *dry)
    assert r.exit_code != 0
    out = json.loads(r.stderr)
    assert out["reason"] == "not_a_task"
    assert out["id"] == UUID
    assert fragment in out["error"]
    api.update_block.assert_not_called()


def test_a_heading_task_previews_both_markers():
    r, _ = _set_status(_block("## TODO x", "TODO"), "--dry-run", "--json")
    out = json.loads(r.stdout)
    assert (out["old_marker"], out["new_marker"], out["new"]) == ("TODO", "DONE", "## DONE x")


@pytest.mark.parametrize("text", ["DONE x", "DONE"])
@pytest.mark.parametrize("dry", [[], ["--dry-run"]], ids=["write", "dry-run"])
def test_a_task_that_already_has_the_status_is_unchanged(text, dry):
    r, api = _set_status(_block(text, "DONE"), *dry)
    assert r.exit_code == 0, r.stderr
    assert r.stdout.strip() == "No change (block already has this status)."
    api.update_block.assert_not_called()


def test_unchanged_under_json():
    r, _ = _set_status(_block("DONE x", "DONE"), "--json")
    assert json.loads(r.stdout)["status"] == "unchanged"


def test_extra_spaces_after_the_marker_are_a_change_not_a_no_op():
    """with_marker puts one space behind the marker, so the text changes and is
    written; the no-op is "same text after the swap", not "same marker"."""
    r, api = _set_status(_block("DONE  x", "DONE"))
    assert r.exit_code == 0, r.stderr
    assert api.update_block.call_args.args[1] == "DONE x"


def test_a_pointer_is_refused_naming_follow_refs_not_a_todo():
    target = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a71"
    graph = PageGraph({"Page A": [_block(f"(({target}))\nid:: {UUID}", None),
                                  _block("TODO ship it", "TODO", target)]})
    api = page_graph_api(graph)
    r, _ = _set_status(_block("", None), "--json", api=api)
    out = json.loads(r.stderr)
    assert out["reason"] == "not_a_task"
    assert out["points_to"] == target
    assert "--follow-refs" in out["error"]
    assert "TODO" not in out["error"]
    api.update_block.assert_not_called()


def test_a_task_property_block_at_the_end_of_a_chain_is_refused():
    host, end = UUID, "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a72"
    graph = PageGraph({"Page A": [_block(f"(({end}))\nid:: {host}", None, host),
                                  _block("status:: open\nid:: T", None, end)]})
    api = page_graph_api(graph)
    r, _ = _set_status(_block("", None, host), "--follow-refs", "--json", api=api)
    assert json.loads(r.stderr)["reason"] == "not_a_task"
    api.update_block.assert_not_called()


FRONTEND = ["TODO", "DOING", "DONE", "LATER", "NOW", "CANCELED", "CANCELLED", "WAIT", "WAITING",
            "IN-PROGRESS"]


def test_started_is_not_a_choice():
    r, api = _set_status(_block("TODO x", "TODO"), status="STARTED")
    assert r.exit_code != 0
    assert "Invalid value" in r.stderr and "--status" in r.stderr
    api.update_block.assert_not_called()


@pytest.mark.parametrize("value", ["WAITING", "waiting"])
def test_waiting_is_a_choice(value):
    r, api = _set_status(_block("TODO x", "TODO"), status=value)
    assert r.exit_code == 0, r.stderr
    assert api.update_block.call_args.args[1] == "WAITING x"


@pytest.mark.parametrize("marker", FRONTEND)
@pytest.mark.parametrize("case", [str.upper, str.lower], ids=["upper", "lower"])
def test_every_frontend_marker_is_accepted(marker, case):
    r, _ = _set_status(_block("TODO x", "TODO"), status=case(marker))
    assert r.exit_code == 0, r.stderr


def _help(*args):
    r = split_runner().invoke(cli, [*args, "--help"])
    return " ".join(r.output.split())


def test_status_help_says_it_takes_a_marker():
    text = _help("set-todo-status")
    assert "takes a marker" in text and "DOING" in text
    assert "WAITING" in text
    assert "Status values: DOING, NOW, IN-PROGRESS, TODO, LATER, WAIT, WAITING, DONE, " \
        "CANCELED, CANCELLED." in text
    assert "STARTED" not in text


def test_update_block_help_names_no_marker_list():
    text = _help("update-block")
    assert "to change a task's marker" in text
    assert "TODO/DOING/DONE" not in text
