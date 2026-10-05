"""Every writing command proves its write, or says why it could not.

Logseq answers every write method with ``null``, whether it wrote or not, and
a thrown error with HTTP 200 and ``{"error": ...}`` (measured, 0.10.15).
These tests run each writing command through the real ``LogseqAPI`` against
the HTTP double and let one write method fail in one of those two ways:
``noop`` answers ``null`` and writes nothing, ``error`` answers an error
object. Every other
write executes. Either way the command must end with a non-zero exit and a
``reason`` an agent can act on, never with exit 0.

The writing commands are derived from the registry (every command with
``--dry-run``, ``init`` named as the exception: it writes a local file, not
to Logseq), and each needs a row in ``ROWS``: a command without one fails
``test_argument_table_is_complete`` rather than going unchecked.

A row names the write method it lets fail; every write method proves its
write in ``LogseqAPI``, so no row is expected to fail.
"""
import json

import pytest

from logseq_cli.api import _MUTATING_METHODS, LogseqAPI, LogseqWriteError
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

# replace-text catches the refusal of each block, writes the others and
# reports through its own fail(): the blocks that failed and why, and
# writes_landed, instead of the handler's one method (commands/edit.py).
REPORTED_BY_THE_COMMAND = {"replace-text"}

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
# before the first change for the write proofs (all of them reads). A proof
# read that slips into the preview raises the count. Checks the preview
# shares with the run are no proof and may add a read, each with its comment
# here.
DRY_RUN_READS = {
    "insert-block-page": 1,
    # One getBlock each: the preview checks the anchor the run writes at
    # (refuse_missing_anchor in commands/edit.py), as move-block's does.
    "insert-block-after": 1,
    "insert-block-tree-after": 1,
    "insert-block-multiline": 1,
    "insert-block-property": 1,
    "insert-block-keep-ids": 1,
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
    # One more than before the rename target check: rename_refusal reads the
    # new name, a check the preview shares with the run, not a proof.
    "rename-page": 3,
    "replace-text": 2,
    "set-block-property": 2,
    "set-property": 3,
    "set-property-new-block": 2,
    "set-todo-status": 1,
    "update-block": 2,
}

# Methods that change no data: they ask or change Logseq's editor.
UI_METHODS = ("checkEditing", "exitEditingMode")


def _graph():
    double = LogseqHttpDouble()
    double.add_page("Probe Page", [
        {"content": "## Heading", "children": ["child one"]},
        "alpha block",
        {"content": "TODO task one", "marker": "TODO"},
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
    """The --json error object, all of stderr, or ``None``: a note printed
    in front of it would make stderr no JSON (notes go in its ``notes``)."""
    try:
        return json.loads(stderr)
    except json.JSONDecodeError:
        return None


def _short(method):
    return method.rsplit(".", 1)[-1]


def _writing_commands():
    return {name for name, command in cli.commands.items()
            if any("--dry-run" in getattr(p, "opts", ()) for p in command.params)} - {"init"}


def _failure_params():
    for row_id, args, method in ROWS:
        for mode in ("noop", "error"):
            yield pytest.param(args, method, mode, id=f"{row_id}-{mode}")


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
    reason = "write_not_verified" if mode == "noop" else "logseq_error"
    assert error.get("reason") == reason, error
    assert isinstance(error.get("writes_landed"), int), error
    if args[0] in REPORTED_BY_THE_COMMAND:
        # The one block the row changes, with its own reason.
        block = double.uuid_of("alpha block")
        assert (error.get("failed"), error.get("failed_reasons")) == ([block], {block: reason}), error
        return
    assert error.get("method") == method, error
    if mode == "error":
        assert error.get("logseq_message") == f"{method} failed", error


@pytest.mark.parametrize("method", sorted(_MUTATING_METHODS), ids=_short)
def test_error_object_on_each_write_method(monkeypatch, method):
    """Logseq answers a write it threw on with HTTP 200 and ``{"error": ...}``
    (measured). call() raises for every write in the registry, not only
    for those the table above reaches."""
    double = _graph().install(monkeypatch)
    double.set_mode(_short(method), "error")
    with pytest.raises(LogseqWriteError) as caught:
        LogseqAPI(token="t").call(method, [double.uuid_of("alpha block"), "x"])
    assert caught.value.reason == "logseq_error"
    assert caught.value.fields == {"method": _short(method),
                                   "logseq_message": f"{_short(method)} failed"}


def test_a_write_answer_with_an_error_key_and_a_uuid_is_a_block(monkeypatch):
    """The check is the one get_block makes: a block map carries a uuid, an
    error object does not. A block with a key named error is no failure."""
    block = {"uuid": "6500c0de-0000-4000-8000-000000000001", "error": "a value"}
    double = _graph().install(monkeypatch)
    monkeypatch.setitem(double._handlers, "logseq.Editor.insertBlock", lambda args: block)
    anchor = double.uuid_of("alpha block")
    assert LogseqAPI(token="t").call("logseq.Editor.insertBlock", [anchor, "x"]) == block


@pytest.mark.parametrize("row_id,args", [pytest.param(i, a, id=i) for i, a, _ in ROWS])
def test_dry_run_sends_no_write_and_no_proof_read(monkeypatch, row_id, args):
    double = _graph().install(monkeypatch)
    r = _invoke(double, args, "--dry-run")
    assert r.exit_code == 0, r.stdout + r.stderr
    assert double.writes() == []
    sent = [_short(m) for m, _ in double.requests]
    assert not [m for m in sent if m in UI_METHODS], sent
    assert len(sent) == DRY_RUN_READS[row_id], sent


# --- updateBlock, upsertBlockProperty, removeBlockProperty ------------------
# The first four are guards against a false alarm, green without a proof too:
# a proof that compared more than the text written would turn them red.

def _one_page(monkeypatch, *blocks, time_tracking=False):
    double = LogseqHttpDouble()
    double.time_tracking = time_tracking
    double.add_page("Probe Page", list(blocks))
    return double.install(monkeypatch)


def test_update_block_with_properties_is_not_a_false_alarm(monkeypatch):
    # Logseq writes the properties carried along back as key:: lines below
    # the text; they are not part of the text sent.
    double = _one_page(monkeypatch, "alpha\ncollapsed:: true\nprio:: 1")
    r = _invoke(double, ["update-block", "--id", "@alpha\ncollapsed:: true\nprio:: 1",
                         "--content", "alpha changed"])
    assert r.exit_code == 0, r.stderr
    assert double.uuid_of("alpha changed\ncollapsed:: true\nprio:: 1")


def test_set_todo_status_with_logbook_is_not_a_false_alarm(monkeypatch):
    # With time tracking on, TODO -> DOING appends a drawer (read in the code,
    # not measured).
    double = _one_page(monkeypatch, {"content": "TODO task one", "marker": "TODO"},
                       time_tracking=True)
    r = _invoke(double, ["set-todo-status", "--id", "@TODO task one", "--status", "DOING"])
    assert r.exit_code == 0, r.stderr
    assert double.tree("Probe Page")[0][0] == "DOING task one"
    assert ":LOGBOOK:" in double.snapshot()[0][0]["blocks"][0]["content"]


def test_doing_to_done_with_existing_drawer_is_success(monkeypatch):
    # set-todo-status sends the old drawer back; Logseq closes its CLOCK line.
    from tests.logseq_http_double import CLOCK_IN, CLOCK_OUT
    text = f"DOING task one\n:LOGBOOK:\n{CLOCK_IN}\n:END:"
    double = _one_page(monkeypatch, {"content": text, "marker": "DOING"}, time_tracking=True)
    r = _invoke(double, ["set-todo-status", "--id", f"@{text}", "--status", "DONE"])
    assert r.exit_code == 0, r.stderr
    assert double.uuid_of(f"DONE task one\n:LOGBOOK:\n{CLOCK_OUT}\n:END:")


def test_marker_change_without_drawer_is_success(monkeypatch):
    # Time tracking off: read back is what was written (measured).
    double = _one_page(monkeypatch, {"content": "TODO task one", "marker": "TODO"})
    for old, new in (("TODO", "DOING"), ("DOING", "DONE")):
        r = _invoke(double, ["set-todo-status", "--id", f"@{old} task one", "--status", new])
        assert r.exit_code == 0, r.stderr
    assert double.uuid_of("DONE task one")


def test_update_block_that_loses_a_property_is_not_verified(monkeypatch):
    # The text lands, the property carried along does not: the values are
    # proven as well, not only the text.
    double = _one_page(monkeypatch, "alpha\nprio:: 1")
    execute = double._handlers["logseq.Editor.updateBlock"]
    monkeypatch.setitem(double._handlers, "logseq.Editor.updateBlock",
                        lambda args: execute(args[:2]))
    r = _invoke(double, ["update-block", "--id", "@alpha\nprio:: 1", "--content", "alpha changed"])
    assert r.exit_code != 0, r.stdout
    error = _error_object(r.stderr)
    assert (error["reason"], error["method"]) == ("write_not_verified", "updateBlock"), error


@pytest.mark.parametrize("key,value", [
    ("created_at", "x"),    # stored as created-at
    ("n", 5),               # measured: a number as its digits
    ("l", ["a", "b"]),      # measured: a list as a,b
    ("z", "01234"),         # measured: stays text
    ("link", "[[Link]]"),   # measured: stays text
])
def test_property_value_forms(monkeypatch, key, value):
    double = _one_page(monkeypatch, "alpha block")
    api = LogseqAPI(token="t")
    api.upsert_block_property(double.uuid_of("alpha block"), key, value)
    assert api.writes_landed == 1


def test_property_value_is_compared_stripped(monkeypatch):
    # Logseq's parser trims a value (blockprops.py), so "x " reads back "x".
    double = _one_page(monkeypatch, "alpha block")
    api = LogseqAPI(token="t")
    api.upsert_block_property(double.uuid_of("alpha block"), "k", "x ")
    assert api.writes_landed == 1


@pytest.mark.parametrize("write", [
    lambda api, uuid: api.upsert_block_property(uuid, "k", "v"),
    lambda api, uuid: api.remove_block_property(uuid, "k"),
    lambda api, uuid: api.update_block(uuid, "text"),
], ids=["upsertBlockProperty", "removeBlockProperty", "updateBlock"])
def test_property_write_on_unknown_uuid_is_not_verified(monkeypatch, write):
    # Logseq answers null for a uuid no block has (measured), and the property
    # reader finds no key there: "key gone" would be a false proof.
    from logseq_cli.api import WriteNotVerified
    _one_page(monkeypatch, "alpha block")
    api = LogseqAPI(token="t")
    with pytest.raises(WriteNotVerified):
        write(api, "6500c0de-0000-4000-8000-00000000abcd")
    assert api.writes_landed == 0


@pytest.mark.parametrize("held", ["Status", "STATUS"])
def test_a_property_held_in_another_spelling_is_not_removed(monkeypatch, held):
    # A CLI before #21, or another client, stored the key as given, and the
    # database holds :Status; remove-property --key Status sends "status",
    # Logseq removes the keyword it was sent and leaves :Status
    # (editor/property.cljs remove-block-property!, 0.10.15, read in the
    # code). The proof looked for "status" only, the key as sent, and said
    # removed.
    double = _one_page(monkeypatch, "beta block")
    props = {held: "open"}
    pull = double._pull_properties
    monkeypatch.setattr(double, "_pull_properties", lambda query: (
        [[{"properties": dict(props), "properties-text-values": dict(props)}]]
        if double.uuid_of("beta block") in query else pull(query)))
    remove = double._handlers["logseq.Editor.removeBlockProperty"]

    def remove_as_sent(args):
        props.pop(args[1], None)
        return remove(args)
    monkeypatch.setitem(double._handlers, "logseq.Editor.removeBlockProperty", remove_as_sent)
    r = _invoke(double, ["remove-property", "--id", "@beta block", "--key", "Status"])
    assert r.exit_code == 1, r.stdout
    error = _error_object(r.stderr)
    assert (error["reason"], error["method"]) == ("write_not_verified", "removeBlockProperty")
    assert props == {held: "open"}


def test_read_back_is_not_served_from_cache(monkeypatch):
    # update-block reads the block before the write; with the cache on, the
    # proof must still ask Logseq, not take that old answer.
    monkeypatch.setenv("LOGSEQ_CLI_CACHE_TTL", "60")
    double = _one_page(monkeypatch, "alpha block")
    double.set_mode("updateBlock", "noop")
    r = _invoke(double, ["update-block", "--id", "@alpha block", "--content", "alpha changed"])
    assert _error_object(r.stderr)["reason"] == "write_not_verified", r.stderr
    sent = [_short(m) for m, _ in double.requests]
    assert "getBlock" in sent[sent.index("updateBlock"):], sent


def test_set_property_the_page_does_not_show_is_not_verified(monkeypatch):
    # The property block's text lands, but the page does not take it up
    # (the double's _saved, #80): set-property's own check, with a reason.
    double = _graph().install(monkeypatch)
    monkeypatch.setattr(double, "_saved", lambda *args: None)
    r = _invoke(double, ["set-property", "--page", "Props Page", "--key", "status",
                         "--value", "done"])
    assert r.exit_code != 0, r.stdout
    error = _error_object(r.stderr)
    assert (error["reason"], error["property"]) == ("write_not_verified", "status"), error


# --- removeBlock, deletePage, renamePage, createPage, setBlocksId -----------

@pytest.mark.parametrize("write,landed", [
    (lambda api, d: api.remove_block(d.uuid_of("alpha block")), 1),
    (lambda api, d: api.delete_page("Other Page"), 1),
    (lambda api, d: api.rename_page("Other Page", "Renamed Page"), 1),
    (lambda api, d: api.create_page("Fresh Page"), 1),
    # One per block asked for.
    (lambda api, d: api.set_blocks_id([d.uuid_of("alpha block"), d.uuid_of("other block")]), 2),
], ids=["removeBlock", "deletePage", "renamePage", "createPage", "setBlocksId"])
def test_a_proven_write_counts(monkeypatch, write, landed):
    double = _graph().install(monkeypatch)
    api = LogseqAPI(token="t")
    write(api, double)
    assert api.writes_landed == landed


def _namespace_graph(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {"Project": ["project notes"],
                                                    "Project/Alpha": ["alpha notes"],
                                                    "Loose Page": []})


def test_delete_of_a_namespace_page_is_proven(monkeypatch):
    # Logseq keeps a page others name as their namespace: delete! removes
    # its blocks and file and leaves the entity (page.cljs delete!; measured,
    # 0.10.15: getPage still answers it, getPageBlocksTree []).
    double = _namespace_graph(monkeypatch)
    r = _invoke(double, ["delete-page", "--page", "Project", "--force"])
    assert r.exit_code == 0, r.stderr
    assert double.tree("Project") == []
    assert double.tree("Project/Alpha") == [("alpha notes", [])]


def test_delete_of_a_namespace_page_that_kept_its_blocks_is_not_verified(monkeypatch):
    double = _namespace_graph(monkeypatch)
    double.set_mode("deletePage", "noop")
    r = _invoke(double, ["delete-page", "--page", "Project", "--force"])
    error = _error_object(r.stderr)
    assert (error["reason"], error["got"]) == ("write_not_verified", "the page still there"), \
        r.stderr


def test_delete_of_a_page_without_blocks_that_stayed_is_not_verified(monkeypatch):
    # No blocks and still there, but no page names it as its namespace: the
    # delete did nothing.
    double = _namespace_graph(monkeypatch)
    double.set_mode("deletePage", "noop")
    r = _invoke(double, ["delete-page", "--page", "Loose Page", "--force"])
    error = _error_object(r.stderr)
    assert (error["reason"], error["got"]) == ("write_not_verified", "the page still there"), \
        r.stderr


def test_rename_by_case_only_is_proven_by_the_new_original_name(monkeypatch):
    # getPage finds the page under the new name before the rename as well
    # (names compare in lower case), so the uuid alone proves nothing here:
    # the new originalName does (measured).
    double = _graph().install(monkeypatch)
    r = _invoke(double, ["rename-page", "--page", "Other Page", "--new-name", "OTHER page"])
    assert r.exit_code == 0, r.stderr
    double.set_mode("renamePage", "noop")
    r = _invoke(double, ["rename-page", "--page", "OTHER page", "--new-name", "Other Page"])
    error = _error_object(r.stderr)
    assert (error["reason"], error["method"]) == ("write_not_verified", "renamePage"), r.stderr


def test_rename_that_leaves_another_page_under_the_name_is_not_verified(monkeypatch):
    # The name alone would pass a page someone else made under it meanwhile,
    # with the page asked for unrenamed: the uuid tells them apart.
    double = _graph().install(monkeypatch)
    monkeypatch.setitem(double._handlers, "logseq.Editor.renamePage",
                        lambda args: double.add_page(args[1], []) and None)
    r = _invoke(double, ["rename-page", "--page", "Other Page", "--new-name", "Renamed Page"])
    error = _error_object(r.stderr)
    assert (error["reason"], error["got"]) == ("write_not_verified", "another page"), r.stderr


def test_set_blocks_id_skips_pre_block(monkeypatch):
    # A page's property block is skipped by setBlocksId (measured); asked for it,
    # the proof would fail a write whose ref Logseq keeps without the id.
    double = LogseqHttpDouble()
    double.add_page("Props Page", ["kind:: probe", "body"])
    double.install(monkeypatch)
    pre = double.uuid_of("kind:: probe")
    api = LogseqAPI(token="t")
    api.insert_block(double.uuid_of("body"), f"see (({pre}))")
    assert double.sent("setBlocksId") == []
    assert api.writes_landed == 1


def test_ref_targets_are_counted_with_the_write(monkeypatch):
    double = _graph().install(monkeypatch)
    api = LogseqAPI(token="t")
    refs = f"(({double.uuid_of('alpha block')})) (({double.uuid_of('other block')}))"
    api.insert_block(double.uuid_of("kid block"), refs)
    assert len(double.sent("setBlocksId")[0][0]) == 2
    assert api.writes_landed == 3


def test_create_page_answer_the_name_does_not_find_is_not_verified(monkeypatch):
    # An answer with a uuid is half the proof: the name sent must find it.
    from logseq_cli.api import WriteNotVerified
    double = _graph().install(monkeypatch)
    monkeypatch.setitem(double._handlers, "logseq.Editor.createPage",
                        lambda args: {"uuid": "6500c0de-0000-4000-8000-00000000abcd"})
    api = LogseqAPI(token="t")
    with pytest.raises(WriteNotVerified):
        api.create_page("Fresh Page")
    assert api.writes_landed == 0


def test_set_blocks_id_counts_the_blocks_that_took_it(monkeypatch):
    # Not atomic: a block that took its id keeps it when another did not.
    from logseq_cli.api import WriteNotVerified
    double = _graph().install(monkeypatch)
    api = LogseqAPI(token="t")
    with pytest.raises(WriteNotVerified) as caught:
        api.set_blocks_id([double.uuid_of("alpha block"), "6500c0de-0000-4000-8000-00000000abcd"])
    assert caught.value.fields["target"] == "6500c0de-0000-4000-8000-00000000abcd"
    assert api.writes_landed == 1
