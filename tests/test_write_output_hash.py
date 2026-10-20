"""The write outputs carry the block's new hash (#133).

A caller that writes the same block twice passes the ``hash`` of the first
write's ``--json`` output as ``--expect-hash`` to the second, with no read in
between. The hash is the one the write's proof read: the state that was
verified, the same ``get-block --json`` reports for it afterwards.

Where no write was sent and proven there is no hash: ``--dry-run``,
``set-todo-status`` answering ``unchanged``, a new block from
``--upsert-heading``. A hash that cannot be taken after a write that landed
is left out; the write is done, so the command still succeeds.
"""
import json

import pytest

import logseq_cli.api
from logseq_cli.blocktext import block_hash
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble
from tests.test_precondition_coverage import OTHER, RECIPES, TASK, _expecting_commands

# The commands whose write output carries the hash.
CARRYING = ["update-block", "set-todo-status", "set-block-property", "remove-property",
            "add-journal-block"]
TEXT_WRITES = ["update-block", "set-todo-status", "add-journal-block"]
PROPERTY_WRITES = ["set-block-property", "remove-property"]

# Text output before #133, on the recipes of test_precondition_coverage:
# the hash is for scripts, which take --json, so the text stays as it was.
TEXT_BEFORE = {
    "update-block": "Updated block 00000000-0000-4000-8000-0000000000a1\n"
                    "  was: TODO ship the parser\n  now: TODO ship the parser, fixed\n",
    "set-todo-status": "Updated: TODO ship the parser\n      → DONE ship the parser\n",
    "set-block-property": "Set 'prio:: high' on block '00000000-0000-4000-8000-0000000000a1'\n",
    "remove-property": "Removed 'prio' from block '00000000-0000-4000-8000-0000000000a1'\n",
    "add-journal-block": "Added 1 block(s) to journal: 2099-01-05, monday "
                         "(upsert '### Carol' under '## Log' (updated))\n"
                         "  uuid: 00000000-0000-4000-8000-0000000000a1\n",
}


def invoke(*args):
    return split_runner().invoke(cli, ["--token", "t", *args])


def write(monkeypatch, name, *extra, pages=None, args=None):
    """Run ``name``'s recipe (or ``args`` on ``pages``) with ``--json``; the
    double and the parsed output."""
    recipe_pages, change, _, _ = RECIPES[name]
    double = LogseqHttpDouble.installed(monkeypatch, pages or recipe_pages)
    result = invoke(name, *(args or change), *extra, "--json")
    assert result.exit_code == 0, result.stderr
    return double, json.loads(result.stdout)


def hash_now(uuid):
    result = invoke("get-block", "--id", uuid, "--no-children", "--json")
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)["hash"]


def stored(double, uuid):
    return next(n["content"] for p in double.pages for n in double._walk(p["blocks"])
                if n["uuid"] == uuid)


@pytest.mark.parametrize("name", CARRYING)
def test_the_output_carries_the_hash_get_block_reports_after(monkeypatch, name):
    double, out = write(monkeypatch, name)
    assert out["hash"] == block_hash(stored(double, TASK))
    assert out["hash"] == hash_now(TASK)


@pytest.mark.parametrize("name", CARRYING)
def test_no_hash_under_dry_run(monkeypatch, name):
    _, out = write(monkeypatch, name, "--dry-run")
    assert out["dry_run"] is True
    assert "hash" not in out


@pytest.mark.parametrize("name", CARRYING)
def test_text_output_is_unchanged(monkeypatch, name):
    pages, change, _, _ = RECIPES[name]
    LogseqHttpDouble.installed(monkeypatch, pages)
    result = invoke(name, *change)
    assert result.exit_code == 0, result.stderr
    assert result.stdout == TEXT_BEFORE[name]


# --- set-todo-status ------------------------------------------------------------

def test_set_todo_status_unchanged_carries_no_hash(monkeypatch):
    """Nothing was sent, so nothing was proven: a hash from the command's own
    read could come from the cache."""
    _, _, no_change, _ = RECIPES["set-todo-status"]
    _, out = write(monkeypatch, "set-todo-status", args=no_change)
    assert out["status"] == "unchanged"
    assert "hash" not in out


def test_set_todo_status_follow_refs_carries_the_hash_of_the_task_it_changed(monkeypatch):
    pages = {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK},
                            {"content": f"(({TASK}))", "uuid": OTHER}]}
    double, out = write(monkeypatch, "set-todo-status", pages=pages,
                        args=["--id", OTHER, "--status", "DONE", "--follow-refs"])
    assert out["uuid"] == TASK
    assert out["hash"] == block_hash(stored(double, TASK)) == hash_now(TASK)


# --- remove-property --------------------------------------------------------------

@pytest.mark.parametrize("key", ["type", "custom-id"], ids=["key", "key-the-parser-refuses"])
def test_remove_property_page_carries_no_hash(monkeypatch, key):
    """--page names a page, not the block written; a key the parser refuses
    goes through the same block write as --id, and still carries none."""
    pages = {"Probe Page": [{"content": f"{key}:: project"}, {"content": "a block"}]}
    _, out = write(monkeypatch, "remove-property", pages=pages,
                   args=["--page", "Probe Page", "--key", key])
    assert "hash" not in out


def test_remove_property_of_a_key_the_block_lacks_carries_the_hash(monkeypatch):
    """The write is sent and proven (the block lacks the key), so the hash
    stands: the unchanged block's."""
    pages = {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]}
    double, out = write(monkeypatch, "remove-property", pages=pages,
                        args=["--id", TASK, "--key", "prio"])
    assert out["hash"] == block_hash("TODO ship the parser") == hash_now(TASK)


# --- add-journal-block --upsert-heading -------------------------------------------

def test_upsert_with_children_and_further_roots_carries_the_replaced_blocks_hash(
        monkeypatch, tmp_path):
    """The children and the further root are inserts after the replace;
    the hash is the replaced block's, not theirs nor the heading's."""
    f = tmp_path / "entry.md"
    f.write_text("- ### Carol\n\t- a child\n- another root", encoding="utf-8")
    pages, change, _, _ = RECIPES["add-journal-block"]
    args = [*change[:change.index("--content")], "--content-file", str(f)]
    double, out = write(monkeypatch, "add-journal-block", args=args)
    assert out["position"].endswith("(updated)") and out["blocks"] == 3
    assert out["uuid"] == TASK
    assert out["hash"] == block_hash(stored(double, TASK)) == hash_now(TASK)


def test_upsert_that_creates_the_block_carries_no_hash(monkeypatch):
    pages, change, _, _ = RECIPES["add-journal-block"]
    args = [a if a != "### Carol" else "### Dave" for a in change]
    args[-1] = "### Dave\nTODO a new block"
    _, out = write(monkeypatch, "add-journal-block", args=args)
    assert out["position"].endswith("(created)")
    assert "hash" not in out


# --- a hash that cannot be taken ----------------------------------------------------

@pytest.mark.parametrize("name", PROPERTY_WRITES)
def test_property_write_without_content_in_the_proof_read_succeeds_without_hash(
        monkeypatch, name):
    """Only the double answers getBlock without content; the property proofs
    hold anyway, since they check the properties, not the text."""
    pages, change, _, _ = RECIPES[name]
    double = LogseqHttpDouble.installed(monkeypatch, pages)
    out_block = double._block_out

    def without_content(*args, **kwargs):
        block = out_block(*args, **kwargs)
        block.pop("content", None)
        return block
    monkeypatch.setattr(double, "_block_out", without_content)
    result = invoke(name, *change, "--json")
    assert result.exit_code == 0, result.stderr
    assert double.writes()
    assert "hash" not in json.loads(result.stdout)


@pytest.mark.parametrize("name", TEXT_WRITES)
def test_text_write_with_no_hash_returned_succeeds_without_hash(monkeypatch, name):
    """_prove_text fails before a block without content could pass, so the
    missing hash is put in place of the returned one."""
    update = logseq_cli.api.LogseqAPI.update_block

    def returns_none(self, *args, **kwargs):
        update(self, *args, **kwargs)
    monkeypatch.setattr(logseq_cli.api.LogseqAPI, "update_block", returns_none)
    double, out = write(monkeypatch, name)
    assert double.writes()
    assert "hash" not in out


# --- every command with a precondition ------------------------------------------------

# Commands that take a precondition but whose output carries no hash, and why.
WAIVED = {
    "move-block": "a move keeps the content, so the hash of the last read still holds",
    "remove-block": "after the write there is no block, so no hash and no next write",
    "copy-block": "the copy has the source's hash, but the output does not name the copy",
}


def _commands_with_a_precondition():
    """Each command once: `delete-block` is the same command as `remove-block`."""
    names = {}
    for name in _expecting_commands():
        names.setdefault(id(cli.commands[name]), name)
    return sorted(names.values())


def test_waived_commands_exist():
    assert all(name in cli.commands for name in WAIVED)


@pytest.mark.parametrize("name", _commands_with_a_precondition())
def test_every_command_with_a_precondition_carries_the_hash_or_says_why(monkeypatch, name):
    """A new command that takes --expect-* fails here until its output
    carries the hash or it is waived above with a reason. One call per
    command, its recipe's: a new write path in a command needs its own."""
    if name in WAIVED:
        assert name not in CARRYING
        return
    assert name in CARRYING, f"{name} takes a precondition: carry the hash or waive it"
    _, out = write(monkeypatch, name)
    assert isinstance(out.get("hash"), str)


# --- a chain of writes, and a change in between --------------------------------------

@pytest.mark.parametrize("name", CARRYING)
def test_the_next_write_passes_on_the_hash_without_a_read(monkeypatch, name):
    double, out = write(monkeypatch, name)
    result = invoke("update-block", "--id", TASK, "--content", "TODO ship the parser, next",
                    "--expect-hash", out["hash"], "--json")
    assert result.exit_code == 0, result.stderr
    # update-block keeps the block's properties (the one set-block-property wrote).
    assert stored(double, TASK).split("\n")[0] == "TODO ship the parser, next"


@pytest.mark.parametrize("name", CARRYING)
def test_a_change_in_between_refuses_the_next_write(monkeypatch, name):
    double, out = write(monkeypatch, name)
    double.set_content(TASK, "TODO ship the parser, theirs")
    before = len(double.writes())
    result = invoke("update-block", "--id", TASK, "--content", "TODO ship the parser, mine",
                    "--expect-hash", out["hash"], "--json")
    assert result.exit_code != 0 and result.stdout == ""
    assert json.loads(result.stderr)["reason"] == "precondition_failed"
    assert len(double.writes()) == before
    assert stored(double, TASK) == "TODO ship the parser, theirs"


# --- no extra request ---------------------------------------------------------------

# Requests of one recipe call, measured before #133 (the last getBlock is
# the proof read the hash comes from): without and with --expect-hash.
REQUESTS = {
    "update-block": (5, 7),
    "set-todo-status": (5, 6),
    "set-block-property": (5, 6),
    "remove-property": (5, 6),
    "add-journal-block": (8, 10),
}


@pytest.mark.parametrize("name", CARRYING)
@pytest.mark.parametrize("expect", [False, True], ids=["plain", "expect-hash"])
def test_the_hash_costs_no_request(monkeypatch, name, expect):
    pages, change, _, _ = RECIPES[name]
    double = LogseqHttpDouble.installed(monkeypatch, pages)
    extra = ["--expect-hash", block_hash(stored(double, TASK))] if expect else []
    result = invoke(name, *change, *extra, "--json")
    assert result.exit_code == 0, result.stderr
    assert "hash" in json.loads(result.stdout)
    assert len(double.requests) == REQUESTS[name][expect]
