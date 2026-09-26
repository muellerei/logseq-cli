"""Every writing command proves its write, or says why it could not (spec 030).

Logseq answers every write method with ``null``, whether it wrote or not, and
a thrown error with HTTP 200 and ``{"error": ...}`` (M1-M9). These tests run
each writing command through the real ``LogseqAPI`` against the HTTP double
and let one write method fail in one of those two ways: ``noop`` answers
``null`` and writes nothing, ``error`` answers an error object. Every other
write executes. Either way the command must end with a non-zero exit and a
``reason`` an agent can act on, never with exit 0.

The writing commands are derived from the registry (every command with
``--dry-run``, ``init`` named as the exception: it writes a local file, not
to Logseq), and each needs a row in ``ROWS``: a command without one fails
``test_argument_table_is_complete`` rather than going unchecked.

A row names the write method it lets fail. Which task of spec 030 makes a row
pass follows from that method, not from the command, so each task removes
exactly the marks of the methods it proves, and no mark turns XPASS in
another task's commit.
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

# The task that proves each write method in LogseqAPI.
PROOF_TASK = {
    "insertBlock": "030-C2", "appendBlockInPage": "030-C2",
    "insertBatchBlock": "030-C3", "moveBlock": "030-C3",
    "updateBlock": "030-C4", "upsertBlockProperty": "030-C4",
    "removeBlockProperty": "030-C4",
    "removeBlock": "030-C5", "deletePage": "030-C5", "renamePage": "030-C5",
    "createPage": "030-C5", "setBlocksId": "030-C5",
}
# An error object on a write fails in call() for every method alike.
ERROR_OBJECT_TASK = "030-B5"

# replace-text reads its blocks back itself since 0.8.0 and reports a write
# that did not land with write_not_verified already (commands/edit.py), so
# its noop row is a guard, not an expected failure. It reports through its
# own fail() with a list of failed blocks, not through the error handler, so
# the handler's fields (method, writes_landed) are not asked of it.
PROVEN_BY_THE_COMMAND = {"replace-text"}

# Block texts in the graph below; an argument "@<text>" is that block's uuid.
ROWS = [
    # (row id, arguments, the write method that fails; the others execute)
    ("insert-block-page", ["insert-block", "--page", "Probe Page", "--content", "new one"],
     "appendBlockInPage"),
    ("insert-block-after", ["insert-block", "--after", "@alpha block", "--content", "new one"],
     "insertBlock"),
    ("insert-block-tree-after", ["insert-block", "--after", "@alpha block", "--tree", "- a\n- b"],
     "insertBlock"),
    ("insert-block-multiline", ["insert-block", "--child-of", "@parent block",
                                "--content", "new one\n\t- kid"], "insertBatchBlock"),
    ("insert-block-property", ["insert-block", "--after", "@alpha block", "--content", "new one",
                               "--property", "k=v"], "upsertBlockProperty"),
    ("insert-block-keep-ids", ["insert-block", "--after", "@alpha block", "--content", "new one",
                               "--keep-ids"], "insertBatchBlock"),
    ("add-note-content", ["add-note-content", "--page", "Probe Page", "--content", "note one"],
     "appendBlockInPage"),
    ("add-note-content-heading", ["add-note-content", "--page", "Probe Page", "--content",
                                  "note one", "--under-heading", "## Heading"], "insertBlock"),
    ("add-note-content-multiline", ["add-note-content", "--page", "Probe Page", "--content",
                                    "- n1\n  - n2"], "insertBlock"),
    ("add-note-content-multiline-heading", ["add-note-content", "--page", "Probe Page",
                                            "--content", "- n1\n- n2",
                                            "--under-heading", "## Heading"], "insertBatchBlock"),
    ("add-note-content-property", ["add-note-content", "--page", "Probe Page", "--content",
                                   "note one", "--property", "k=v"], "upsertBlockProperty"),
    ("add-note-content-keep-ids", ["add-note-content", "--page", "Probe Page", "--content",
                                   "note one", "--keep-ids"], "insertBatchBlock"),
    ("add-note-content-new-page", ["add-note-content", "--page", "Fresh Page", "--content",
                                   "note one"], "createPage"),
    ("add-journal-block-top", ["add-journal-block", "--date", "2099-01-05", "--content", "jb one",
                               "--top-level"], "appendBlockInPage"),
    ("add-journal-block-heading", ["add-journal-block", "--date", "2099-01-05", "--content",
                                   "jb one", "--under-heading", "## Log"], "insertBlock"),
    ("add-journal-block-tree", ["add-journal-block", "--date", "2099-01-05", "--content",
                                "jb one\n\t- kid", "--under-heading", "## Log"], "insertBatchBlock"),
    ("add-journal-block-keep-ids", ["add-journal-block", "--date", "2099-01-05", "--content",
                                    "jb one", "--top-level", "--keep-ids"], "insertBatchBlock"),
    ("add-journal-block-upsert", ["add-journal-block", "--date", "2099-01-05", "--content",
                                  "logged new", "--under-heading", "## Log",
                                  "--upsert-heading", "logged"], "updateBlock"),
    ("add-journal-block-new-journal", ["add-journal-block", "--date", "2099-01-06", "--content",
                                       "jb one", "--top-level"], "createPage"),
    ("add-journal-content", ["add-journal-content", "--date", "2099-01-05", "--content", "jc one",
                             "--top-level"], "appendBlockInPage"),
    ("add-journal-content-multiline", ["add-journal-content", "--date", "2099-01-05", "--content",
                                       "- jc one\n- jc two", "--under-heading", "## Log"],
     "insertBatchBlock"),
    ("add-journal-content-keep-ids", ["add-journal-content", "--date", "2099-01-05", "--content",
                                      "jc one", "--top-level", "--keep-ids"], "insertBatchBlock"),
    ("add-journal-content-new-journal", ["add-journal-content", "--date", "2099-01-06",
                                         "--content", "jc one", "--top-level"], "createPage"),
    ("add-journal-entry", ["add-journal-entry", "--date", "2099-01-05", "--content", "je one"],
     "appendBlockInPage"),
    ("add-journal-entry-multiline", ["add-journal-entry", "--date", "2099-01-05", "--content",
                                     "je one\nje two", "--multi-block"], "appendBlockInPage"),
    ("add-journal-entry-new-journal", ["add-journal-entry", "--date", "2099-01-06", "--content",
                                       "je one"], "createPage"),
    ("add-block-ref", ["add-block-ref", "--source-id", "@alpha block", "--page", "Probe Page",
                       "--under-heading", "## Heading"], "insertBlock"),
    ("add-block-ref-source-id", ["add-block-ref", "--source-id", "@alpha block", "--page",
                                 "Probe Page", "--under-heading", "## Heading"], "setBlocksId"),
    ("copy-block", ["copy-block", "--id", "@alpha block", "--to-page", "Other Page"],
     "appendBlockInPage"),
    ("copy-block-tree", ["copy-block", "--id", "@parent block", "--to-page", "Other Page"],
     "insertBlock"),
    ("copy-block-remove", ["copy-block", "--id", "@parent block", "--to-page", "Other Page",
                           "--remove"], "removeBlock"),
    ("create-page", ["create-page", "--page", "Fresh Page"], "createPage"),
    # create-page writes content only through append_block_in_page
    # (commands/pages.py); it has no batch path.
    ("create-page-content", ["create-page", "--page", "Fresh Page", "--content", "fresh text"],
     "appendBlockInPage"),
    ("delete-block", ["delete-block", "--id", "@alpha block"], "removeBlock"),
    ("delete-page", ["delete-page", "--page", "Other Page", "--force"], "deletePage"),
    ("move-block-under", ["move-block", "--id", "@alpha block", "--under", "@parent block"],
     "moveBlock"),
    ("move-block-before", ["move-block", "--id", "@alpha block", "--before", "@parent block"],
     "moveBlock"),
    ("remove-block", ["remove-block", "--id", "@alpha block"], "removeBlock"),
    ("remove-property-id", ["remove-property", "--id", "@beta\nprio:: 1", "--key", "prio"],
     "removeBlockProperty"),
    ("remove-property-page", ["remove-property", "--page", "Props Page", "--key", "status"],
     "updateBlock"),
    ("rename-page", ["rename-page", "--page", "Other Page", "--new-name", "Renamed Page"],
     "renamePage"),
    ("replace-text", ["replace-text", "--page", "Probe Page", "--find", "alpha",
                      "--replace", "gamma"], "updateBlock"),
    ("set-block-property", ["set-block-property", "--id", "@alpha block", "--key", "k",
                            "--value", "v"], "upsertBlockProperty"),
    ("set-property", ["set-property", "--page", "Props Page", "--key", "status",
                      "--value", "done"], "updateBlock"),
    # A page without a property block gets one first (commands/properties.py).
    ("set-property-new-block", ["set-property", "--page", "Probe Page", "--key", "k",
                                "--value", "v"], "insertBlock"),
    ("set-todo-status", ["set-todo-status", "--id", "@TODO task one", "--status", "DONE"],
     "updateBlock"),
    ("update-block", ["update-block", "--id", "@alpha block", "--content", "alpha changed"],
     "updateBlock"),
]

# Requests each row's --dry-run sends, measured 2026-09-26 on fix/write-proof
# before spec 030's first change (all of them reads). A proof read that slips
# into the preview raises the count. Checks the preview shares with the run
# are no proof and may add a read, each with its comment here.
DRY_RUN_READS = {
    "insert-block-page": 1,
    "insert-block-after": 0,
    "insert-block-tree-after": 0,
    "insert-block-multiline": 0,
    "insert-block-property": 0,
    "insert-block-keep-ids": 0,
    "add-note-content": 1,
    "add-note-content-heading": 2,
    "add-note-content-multiline": 1,
    "add-note-content-multiline-heading": 2,
    "add-note-content-property": 1,
    "add-note-content-keep-ids": 1,
    "add-note-content-new-page": 2,
    "add-journal-block-top": 2,
    "add-journal-block-heading": 2,
    "add-journal-block-tree": 2,
    "add-journal-block-keep-ids": 2,
    "add-journal-block-upsert": 2,
    "add-journal-block-new-journal": 2,
    "add-journal-content": 2,
    "add-journal-content-multiline": 2,
    "add-journal-content-keep-ids": 2,
    "add-journal-content-new-journal": 2,
    "add-journal-entry": 2,
    "add-journal-entry-multiline": 2,
    "add-journal-entry-new-journal": 2,
    "add-block-ref": 3,
    "add-block-ref-source-id": 3,
    "copy-block": 2,
    "copy-block-tree": 2,
    "copy-block-remove": 3,
    "create-page": 1,
    "create-page-content": 1,
    "delete-block": 2,
    "delete-page": 3,
    "move-block-under": 2,
    "move-block-before": 2,
    "remove-block": 2,
    "remove-property-id": 2,
    "remove-property-page": 3,
    # 030-B6 adds one: rename_refusal reads the new name, shared with the run.
    "rename-page": 2,
    "replace-text": 2,
    "set-block-property": 2,
    "set-property": 3,
    "set-property-new-block": 2,
    "set-todo-status": 1,
    "update-block": 2,
}

# Methods that change no data (spec 030, Baustein 0: "Oberfläche").
UI_METHODS = ("checkEditing", "exitEditingMode")


def _graph():
    double = LogseqHttpDouble()
    double.add_page("Probe Page", [
        {"content": "## Heading", "children": ["child one"]},
        "alpha block",
        "TODO task one",
        "beta\nprio:: 1",
        {"content": "parent block", "children": ["kid block"]},
    ])
    double.add_page("Other Page", ["other block"])
    double.add_page("Props Page", ["status:: open\nkind:: probe", "props body"])
    double.add_page("2099-01-05, Monday", [{"content": "## Log", "children": ["logged"]},
                                           "journal top"])
    return double


def _invoke(double, args, *extra):
    args = [double.uuid_of(a[1:]) if a.startswith("@") else a for a in args]
    return split_runner().invoke(cli, ["--token", "t", *args, "--json", *extra])


def _error_object(stderr):
    """The --json error object on stderr, after any notes, or ``None``."""
    start = stderr.find("{")
    try:
        return json.loads(stderr[start:]) if start >= 0 else None
    except json.JSONDecodeError:
        return None


def _short(method):
    return method.rsplit(".", 1)[-1]


def _writing_commands():
    return {name for name, command in cli.commands.items()
            if any("--dry-run" in getattr(p, "opts", ()) for p in command.params)} - {"init"}


def _spec(task):
    return pytest.mark.xfail(strict=True, reason=f"spec 030: {task}")


def _failure_params():
    for row_id, args, method in ROWS:
        for mode in ("noop", "error"):
            if mode == "error":
                task = ERROR_OBJECT_TASK
            elif args[0] in PROVEN_BY_THE_COMMAND:
                task = None
            else:
                task = PROOF_TASK[method]
            yield pytest.param(args, method, mode, id=f"{row_id}-{mode}",
                               marks=[_spec(task)] if task else [])


def test_argument_table_is_complete():
    """Every writing command has a row, and every row a writing command."""
    tabled = {args[0] for _, args, _ in ROWS}
    writing = _writing_commands()
    assert len(writing) == 19, sorted(writing)
    assert not writing - tabled, f"writing commands without a row: {sorted(writing - tabled)}"
    assert not tabled - writing, f"rows for no writing command: {sorted(tabled - writing)}"
    assert len({row_id for row_id, _, _ in ROWS}) == len(ROWS)


@pytest.mark.parametrize("args,method", [pytest.param(a, m, id=i) for i, a, m in ROWS])
def test_every_row_reaches_its_method(monkeypatch, args, method):
    """Guards the table: with every write executing, the row succeeds and
    sends the method it names. A row that never reached its method would let
    the failure tests below fail for the wrong reason."""
    double = _graph().install(monkeypatch)
    r = _invoke(double, args)
    assert r.exit_code == 0, r.stdout + r.stderr
    assert method in [_short(m) for m, _ in double.writes()]


@pytest.mark.parametrize("args,method,mode", list(_failure_params()))
def test_a_write_logseq_did_not_do_fails_with_its_reason(monkeypatch, args, method, mode):
    double = _graph().install(monkeypatch)
    double.set_mode(method, mode)
    r = _invoke(double, args)
    assert r.exit_code != 0, f"exit 0 for a write Logseq did not do: {r.stdout}"
    error = _error_object(r.stderr)
    assert error is not None, f"no --json error object on stderr: {r.stderr!r}"
    assert error.get("reason") == ("write_not_verified" if mode == "noop" else "logseq_error"), error
    if args[0] in PROVEN_BY_THE_COMMAND:
        return
    assert error.get("method") == method, error
    assert isinstance(error.get("writes_landed"), int), error
    if mode == "error":
        assert error.get("logseq_message") == f"{method} failed", error


@pytest.mark.parametrize("row_id,args", [pytest.param(i, a, id=i) for i, a, _ in ROWS])
def test_dry_run_sends_no_write_and_no_proof_read(monkeypatch, row_id, args):
    double = _graph().install(monkeypatch)
    r = _invoke(double, args, "--dry-run")
    assert r.exit_code == 0, r.stdout + r.stderr
    assert double.writes() == []
    sent = [_short(m) for m, _ in double.requests]
    assert not [m for m in sent if m in UI_METHODS], sent
    assert len(sent) == DRY_RUN_READS[row_id], sent
