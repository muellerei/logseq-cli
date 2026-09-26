"""No write changes a block open in Logseq's editor, and no insert moves the
cursor (spec 030, Baustein 2 and E2).

Measured (M8): a write to the block someone is typing in replaces the
editor's text at once, and everything typed and not yet saved is gone;
Logseq answers null, and a read right after still shows the old text, so no
proof after the write can catch it. The gate asks ``checkEditing`` before the
write and refuses. Inserts never refuse (Logseq saves the open block first,
M10), but they go with ``focus: false`` so the cursor stays where it is.

Against the HTTP double with the real ``LogseqAPI``: the tests that replace
``LogseqAPI`` method by method cannot see a gate that lives inside it.
"""
import json

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble
from tests.test_write_proof_every_command import ROWS, _graph, _invoke

UNKNOWN = "6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6"


def _spec(task):
    return pytest.mark.xfail(strict=True, reason=f"spec 030: {task}")


@pytest.fixture
def double(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", [
        "target block",
        "ref target",
        {"content": "parent block", "children": ["kid block"]},
        "anchor block",
    ])
    double.add_page("Other Page", ["see [[Probe Page]]", "unrelated block"])
    return double


@pytest.fixture
def api(double):
    api = LogseqAPI(token="t")
    api.cache_enabled = False
    return api


def _uuid(double, content):
    return double.uuid_of(content)


def _assert_refused(double, write, open_uuid):
    """``write`` raises EditorOpen naming the open block, and nothing is written."""
    from logseq_cli.writerefused import EditorOpen
    double.editing = open_uuid
    before = double.snapshot()
    with pytest.raises(EditorOpen) as refused:
        write()
    assert refused.value.reason == "open_in_editor"
    assert refused.value.fields["block"] == open_uuid
    assert "page" in refused.value.fields
    assert double.writes() == []
    assert double.snapshot() == before


def _error_object(result):
    start = result.stderr.find("{")
    assert start >= 0, f"no --json error object on stderr: {result.stderr!r}"
    return json.loads(result.stderr[start:])


def _sent_options(double, method):
    """The options argument of each request to ``method``, ``None`` where absent."""
    position = {"insertBlock": 2, "appendBlockInPage": 2, "createPage": 2}[method]
    return [a[position] if len(a) > position else None for a in double.sent(method)]


# --- 030-B1: the cursor stays where it is ------------------------------------

def test_inserts_send_focus_false(monkeypatch, double, api):
    # M10: without focus: false the new block opens in the editor, on a
    # visible page the cursor jumps into it. A caller's own focus is overridden.
    double.show_page("Probe Page")
    anchor = _uuid(double, "anchor block")
    api.insert_block(anchor, "one", {"sibling": True})
    api.insert_block(anchor, "two", {"sibling": False, "focus": True})
    api.insert_block(anchor, "three")
    api.append_block_in_page("Probe Page", "four")
    api.append_block_in_page("Probe Page", "five", {"focus": True})
    options = _sent_options(double, "insertBlock") + _sent_options(double, "appendBlockInPage")
    assert len(options) == 5
    assert all(o and o.get("focus") is False for o in options), options
    assert _sent_options(double, "insertBlock")[0]["sibling"] is True
    assert double.editing is None
    # Every command that inserts, on every path.
    seen = 0
    for _, args, _ in ROWS:
        graph = _graph().install(monkeypatch)
        _invoke(graph, args)
        for method in ("insertBlock", "appendBlockInPage"):
            for o in _sent_options(graph, method):
                seen += 1
                assert o and o.get("focus") is False, (args, method, o)
    assert seen > 20


def test_create_page_sends_redirect_false(monkeypatch, double, api):
    # M13: createPage without options turns Logseq's view to the new page.
    api.create_page("New Page")
    api.create_page("Page With Properties", {"status": "a"})
    api.create_page("Page Written At Once", first_block=False)
    options = _sent_options(double, "createPage")
    assert len(options) == 3
    assert all(o and o.get("redirect") is False for o in options), options
    assert options[2]["createFirstBlock"] is False
    seen = 0
    for _, args, _ in ROWS:
        graph = _graph().install(monkeypatch)
        _invoke(graph, args)
        for o in _sent_options(graph, "createPage"):
            seen += 1
            assert o and o.get("redirect") is False, (args, o)
    assert seen >= 5


# --- 030-B3: the gate, method by method --------------------------------------

@_spec("030-B3")
def test_gate_refuses_update_block(double, api):
    x = _uuid(double, "target block")
    _assert_refused(double, lambda: api.update_block(x, "new text"), x)


@_spec("030-B3")
def test_gate_refuses_upsert_block_property(double, api):
    x = _uuid(double, "target block")
    _assert_refused(double, lambda: api.upsert_block_property(x, "k", "v"), x)


@_spec("030-B3")
def test_gate_refuses_remove_block_property(double, api):
    double.add_page("Props Page", ["body\nprio:: 1"])
    x = _uuid(double, "body\nprio:: 1")
    _assert_refused(double, lambda: api.remove_block_property(x, "prio"), x)


@_spec("030-B3")
def test_gate_refuses_move_block_source(double, api):
    src, anchor = _uuid(double, "target block"), _uuid(double, "anchor block")
    _assert_refused(double, lambda: api.move_block(src, anchor, {"children": True}), src)


@_spec("030-B3")
def test_gate_refuses_move_block_subtree(double, api):
    src, anchor = _uuid(double, "parent block"), _uuid(double, "anchor block")
    kid = _uuid(double, "kid block")
    _assert_refused(double, lambda: api.move_block(src, anchor, {"children": True}), kid)


@_spec("030-B3")
def test_gate_refuses_set_blocks_id(double, api):
    y = _uuid(double, "ref target")
    _assert_refused(double, lambda: api.set_blocks_id([y]), y)


@_spec("030-B3")
def test_gate_refuses_remove_block_target(double, api):
    x = _uuid(double, "target block")
    _assert_refused(double, lambda: api.remove_block(x), x)


@_spec("030-B3")
def test_gate_refuses_remove_block_subtree(double, api):
    parent, kid = _uuid(double, "parent block"), _uuid(double, "kid block")
    _assert_refused(double, lambda: api.remove_block(parent), kid)


@_spec("030-B3")
def test_gate_refuses_delete_page(double, api):
    open_block = _uuid(double, "kid block")
    _assert_refused(double, lambda: api.delete_page("Probe Page"), open_block)


@_spec("030-B3")
def test_gate_refuses_rename_page_block_on_page(double, api):
    open_block = _uuid(double, "target block")
    _assert_refused(double, lambda: api.rename_page("Probe Page", "New Name"), open_block)


@_spec("030-B3")
def test_gate_refuses_rename_page_linking_block(double, api):
    # Logseq rewrites [[Probe Page]] in the open block; leaving the editor
    # would save the old text back (M15: found through the block's refs).
    open_block = _uuid(double, "see [[Probe Page]]")
    _assert_refused(double, lambda: api.rename_page("Probe Page", "New Name"), open_block)


@_spec("030-B3")
def test_gate_refuses_update_block_uppercase_target(double, api):
    # update-block passes --id through as typed; checkEditing answers in
    # lower case. Compared as typed, the write would pass the gate (M8).
    x = _uuid(double, "target block")
    _assert_refused(double, lambda: api.update_block(x.upper(), "new text"), x)


@pytest.mark.parametrize("write", [
    "update_block", "upsert_block_property", "remove_block_property", "move_block_next_to_open",
    "set_blocks_id", "remove_block", "delete_page", "rename_page",
])
def test_gate_lets_unrelated_writes_through(double, api, write):
    """A block open elsewhere refuses nothing; neither does the anchor of a
    move (a block next to the open one changes nothing in it, as with an
    insert, M10). A guard against a gate that refuses too much."""
    double.add_page("Props Page", ["body\nprio:: 1"])
    x, y = _uuid(double, "target block"), _uuid(double, "ref target")
    anchor = _uuid(double, "anchor block")
    double.editing = _uuid(double, "unrelated block")
    calls = {
        "update_block": lambda: api.update_block(x, "new text"),
        "upsert_block_property": lambda: api.upsert_block_property(x, "k", "v"),
        "remove_block_property": lambda: api.remove_block_property(
            _uuid(double, "body\nprio:: 1"), "prio"),
        "move_block_next_to_open": lambda: api.move_block(x, anchor, {"children": True}),
        "set_blocks_id": lambda: api.set_blocks_id([y]),
        "remove_block": lambda: api.remove_block(x),
        "delete_page": lambda: api.delete_page("Props Page"),
        "rename_page": lambda: api.rename_page("Props Page", "Renamed Props"),
    }
    if write == "move_block_next_to_open":
        double.editing = anchor
    calls[write]()
    assert len(double.writes()) == 1


def test_gate_never_asks_for_inserts(double, api):
    # Inserts never refuse (M10: Logseq saves the open block first), so they
    # do not ask; a text without a block ref has no id to store either.
    anchor = _uuid(double, "anchor block")
    double.editing = anchor
    api.insert_block(anchor, "plain child", {"sibling": False})
    api.append_block_in_page("Probe Page", "plain last")
    api.create_page("New Page")
    assert double.sent("checkEditing") == []
    assert [m.rsplit(".", 1)[-1] for m, _ in double.writes()] == \
        ["insertBlock", "appendBlockInPage", "createPage"]


@_spec("030-B3")
def test_gate_refuses_before_storing_ref_ids(double, api):
    # X is open, the text refers to Y, which has no id:: yet. Refused before
    # setBlocksId, else Y would get its id:: for a write that never happens.
    x, y = _uuid(double, "target block"), _uuid(double, "ref target")
    _assert_refused(double, lambda: api.update_block(x, f"new text (({y}))"), x)
    assert double.sent("setBlocksId") == []


@_spec("030-B3")
def test_update_block_with_ref_asks_check_editing_once(double, api):
    x, y = _uuid(double, "target block"), _uuid(double, "ref target")
    api.update_block(x, f"new text (({y}))")
    assert len(double.sent("checkEditing")) == 1
    assert double.sent("setBlocksId") == [[[y]]]
    assert len(double.sent("updateBlock")) == 1


def test_insert_ref_to_its_own_anchor_stores_id(double, api):
    # #95: the anchor of an insert is no "own" block, so it gets its id::.
    # A guard for the rebuild: own is only for updateBlock and upsertBlockProperty.
    x = _uuid(double, "anchor block")
    api.insert_block(x, f"see (({x}))", {"sibling": False})
    assert double.sent("setBlocksId") == [[[x]]]
    assert double.tree("Probe Page")[3][0] == "anchor block"
    block = api.get_block(x, include_children=False)
    assert block["content"] == f"anchor block\nid:: {x}"


@_spec("030-B3")
def test_insert_with_ref_to_open_block_is_refused(double, api):
    # Not the insert refuses: storing Y's id would write into the open Y.
    anchor, y = _uuid(double, "anchor block"), _uuid(double, "ref target")
    _assert_refused(double, lambda: api.insert_block(anchor, f"see (({y}))", {"sibling": True}), y)


def test_insert_with_ref_to_open_block_that_has_its_id_goes_through(double, api):
    # Y has its id:: already: nothing is written into Y, nothing to refuse.
    double.add_page("Ids Page", [{"content": f"with id\nid:: {UNKNOWN}", "uuid": UNKNOWN}])
    anchor = _uuid(double, "anchor block")
    double.editing = UNKNOWN
    api.insert_block(anchor, f"see (({UNKNOWN}))", {"sibling": True})
    assert double.sent("setBlocksId") == []
    assert len(double.sent("insertBlock")) == 1


# --- 030-B3: what checkEditing answers ---------------------------------------

@_spec("030-B3")
@pytest.mark.parametrize("form,editing", [
    ("raw", UNKNOWN),       # M8: the uuid as raw text
    ("json", UNKNOWN),      # the same as a JSON string
    ("raw", None),          # false; as JSON it is the same four letters
    ("json", None),
])
def test_check_editing_forms(double, api, form, editing):
    double.check_editing_form = form
    double.editing = editing
    assert api.check_editing() == editing


@_spec("030-B3")
@pytest.mark.parametrize("form", ["empty", "error", "ok1"])
def test_unknown_editor_state_refuses(double, api, form):
    # Anything else fails closed: no write on an answer not understood.
    from logseq_cli.writerefused import EditorStateUnknown
    double.check_editing_form = form
    x = _uuid(double, "target block")
    with pytest.raises(EditorStateUnknown) as refused:
        api.update_block(x, "new text")
    assert refused.value.reason == "editor_state_unknown"
    assert "answer" in refused.value.fields
    assert double.writes() == []


@_spec("030-B3")
def test_check_editing_timeout_is_timeout(double):
    # A timeout is the known failure, not an editor state nobody understood.
    double.check_editing_form = "timeout"
    x = _uuid(double, "target block")
    r = split_runner().invoke(cli, ["--token", "t", "update-block", "--id", x,
                                    "--content", "new text", "--json"])
    assert r.exit_code == 1
    assert _error_object(r)["reason"] == "timeout"
    assert double.writes() == []


# --- 030-B3: through the command ---------------------------------------------

@_spec("030-B3")
def test_update_block_command_open_in_editor_json(double):
    x = _uuid(double, "target block")
    double.editing = x
    r = split_runner().invoke(cli, ["--token", "t", "update-block", "--id", x,
                                    "--content", "new text", "--json"])
    assert r.exit_code == 1
    assert r.stdout == ""
    error = _error_object(r)
    assert error["reason"] == "open_in_editor"
    assert error["block"] == x
    assert error["writes_landed"] == 0
    assert double.writes() == []


@_spec("030-B3")
def test_copy_block_remove_open_source_reports_landed_copies(double):
    # The copies land, then removing the open source is refused. The message
    # says so: a retry would copy again.
    parent = _uuid(double, "parent block")
    double.editing = parent
    r = split_runner().invoke(cli, ["--token", "t", "copy-block", "--id", parent,
                                    "--to-page", "Other Page", "--remove", "--json"])
    assert r.exit_code == 1
    error = _error_object(r)
    assert error["reason"] == "open_in_editor"
    assert error["block"] == parent
    assert error["writes_landed"] == 2
    assert "2 earlier write(s) in this call landed and remain" in error["error"]
    assert double.sent("removeBlock") == []
    assert double.tree("Other Page")[-1] == ("parent block", [("kid block", [])])
    assert ("parent block", [("kid block", [])]) in double.tree("Probe Page")


# --- 030-B4: batches and an open editor (E2) ---------------------------------

MULTI_BLOCK = ["insert-block", "--child-of", "@parent block", "--content", "new one\n\t- kid"]
KEEP_IDS = ["insert-block", "--after", "@alpha block", "--content", "new one", "--keep-ids"]


@_spec("030-B4")
def test_batch_goes_block_by_block_while_editing(monkeypatch):
    # insertBatchBlock opens its last block in the editor (editor.cljs:1998,
    # M16); with someone typing, the tree goes block by block, focus: false.
    graph = _graph().install(monkeypatch)
    graph.editing = graph.uuid_of("other block")
    r = _invoke(graph, MULTI_BLOCK)
    assert r.exit_code == 0, r.stdout + r.stderr
    assert graph.sent("insertBatchBlock") == []
    assert [o.get("focus") for o in _sent_options(graph, "insertBlock")] == [False, False]
    assert ("parent block", [("kid block", []), ("new one", [("kid", [])])]) in \
        graph.tree("Probe Page")
    assert graph.editing == graph.uuid_of("other block")


@_spec("030-B4")
def test_keep_ids_refused_while_editing(monkeypatch):
    # A kept id can only go through the batch (#31), which would move the cursor.
    graph = _graph().install(monkeypatch)
    graph.editing = graph.uuid_of("other block")
    r = _invoke(graph, KEEP_IDS)
    assert r.exit_code == 1
    assert _error_object(r)["reason"] == "open_in_editor"
    assert graph.writes() == []


@_spec("030-B4")
@pytest.mark.parametrize("args", [MULTI_BLOCK, KEEP_IDS], ids=["multi-block", "keep-ids"])
def test_exit_editing_mode_after_batch_when_nobody_typed(monkeypatch, args):
    # Nobody typed, the page is on screen: the batch opens its last block,
    # and the agent's next write to it would end in open_in_editor.
    graph = _graph().install(monkeypatch)
    graph.show_page("Probe Page")
    r = _invoke(graph, args)
    assert r.exit_code == 0, r.stdout + r.stderr
    assert len(graph.sent("insertBatchBlock")) == 1
    assert len(graph.sent("exitEditingMode")) == 1
    assert graph.editing is None


@_spec("030-B4")
def test_insert_batch_block_refuses_while_editing(double, api):
    # The method refuses on its own, so no caller can set off the jump.
    anchor = _uuid(double, "anchor block")
    _assert_refused(double, lambda: api.insert_batch_block(
        anchor, [{"content": "x"}, {"content": "y"}], {"sibling": True}),
        _uuid(double, "unrelated block"))
