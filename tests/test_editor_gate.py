"""No write changes a block open in Logseq's editor, and no insert moves the
cursor.

Measured (Logseq 0.10.15): a write to the block someone is typing in replaces the
editor's text at once, and everything typed and not yet saved is gone;
Logseq answers null, and a read right after still shows the old text, so no
proof after the write can catch it. The gate asks ``checkEditing`` before the
write and refuses. Inserts never refuse (Logseq saves the open block first,
measured), but they go with ``focus: false`` so the cursor stays where it is.

Against the HTTP double with the real ``LogseqAPI``: the tests that replace
``LogseqAPI`` method by method cannot see a gate that lives inside it.
"""
import json

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.writerefused import EditorOpen
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble
from tests.test_write_proof_every_command import ROWS, _graph, _invoke

UNKNOWN = "6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6"


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


@pytest.fixture(params=[False, True], ids=["uncached", "cached"])
def api(request, double):
    """The API with its read cache off, and on as a run has it (TTL 60 s):
    the gate must see the graph as it is when it asks, not as read before."""
    api = LogseqAPI(token="t")
    api.cache_enabled = request.param
    return api


@pytest.fixture
def cached_api(double):
    api = LogseqAPI(token="t")
    assert api.cache_enabled
    return api


def _open_new_child(double, parent, content="typed here"):
    """Someone presses Enter under ``parent`` and types in the new child;
    returns its uuid."""
    _, siblings, i, _ = double._locate(parent)
    node = double._build(content)
    siblings[i]["children"].append(node)
    double._index(node)
    double.editing = node["uuid"]
    return node["uuid"]


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
    return json.loads(result.stderr)


def _sent_options(double, method):
    """The options argument of each request to ``method``, ``None`` where absent."""
    position = {"insertBlock": 2, "appendBlockInPage": 2, "createPage": 2}[method]
    return [a[position] if len(a) > position else None for a in double.sent(method)]


# --- The cursor stays where it is -------------------------------------------

def test_inserts_send_focus_false(monkeypatch, double, api):
    # Measured: without focus: false the new block opens in the editor, on a
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
    # Measured: createPage without options turns Logseq's view to the new page.
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


# --- The gate, method by method ---------------------------------------------

def test_gate_refuses_update_block(double, api):
    x = _uuid(double, "target block")
    _assert_refused(double, lambda: api.update_block(x, "new text"), x)


def test_gate_refuses_upsert_block_property(double, api):
    x = _uuid(double, "target block")
    _assert_refused(double, lambda: api.upsert_block_property(x, "k", "v"), x)


def test_gate_refuses_remove_block_property(double, api):
    double.add_page("Props Page", ["body\nprio:: 1"])
    x = _uuid(double, "body\nprio:: 1")
    _assert_refused(double, lambda: api.remove_block_property(x, "prio"), x)


def test_gate_refuses_move_block_source(double, api):
    src, anchor = _uuid(double, "target block"), _uuid(double, "anchor block")
    _assert_refused(double, lambda: api.move_block(src, anchor, {"children": True}), src)


def test_gate_refuses_move_block_subtree(double, api):
    src, anchor = _uuid(double, "parent block"), _uuid(double, "anchor block")
    kid = _uuid(double, "kid block")
    _assert_refused(double, lambda: api.move_block(src, anchor, {"children": True}), kid)


def test_gate_refuses_set_blocks_id(double, api):
    y = _uuid(double, "ref target")
    _assert_refused(double, lambda: api.set_blocks_id([y]), y)


def test_gate_refuses_remove_block_target(double, api):
    x = _uuid(double, "target block")
    _assert_refused(double, lambda: api.remove_block(x), x)


def test_gate_refuses_remove_block_subtree(double, api):
    parent, kid = _uuid(double, "parent block"), _uuid(double, "kid block")
    _assert_refused(double, lambda: api.remove_block(parent), kid)


# The subtree and the open block are read when the gate asks, not taken from
# the cache: a child made after the call's first read, and typed in, was
# missing from a subtree read earlier, and the write went through.

def test_gate_remove_block_sees_a_child_made_after_the_first_read(double, cached_api):
    parent = _uuid(double, "parent block")
    cached_api.get_block(parent, include_children=True)
    kid = _open_new_child(double, parent)
    before = double.snapshot()
    with pytest.raises(EditorOpen) as refused:
        cached_api.remove_block(parent)
    assert refused.value.fields["block"] == kid
    assert double.writes() == [] and double.snapshot() == before


def test_gate_move_block_sees_a_child_made_after_the_first_read(double, cached_api):
    parent, anchor = _uuid(double, "parent block"), _uuid(double, "anchor block")
    cached_api.get_block(parent, include_children=True)
    _open_new_child(double, parent)
    with pytest.raises(EditorOpen):
        cached_api.move_block(parent, anchor, {"children": True})
    assert double.writes() == []


def test_gate_rename_page_sees_a_link_saved_after_the_first_read(double, cached_api):
    # Logseq saved the open block with a link to the page after the CLI had
    # read it without one.
    open_block = _uuid(double, "unrelated block")
    cached_api.get_block(open_block, include_children=False)
    cached_api.get_page("Probe Page")
    double._locate(open_block)[1][double._locate(open_block)[2]]["content"] = \
        "now links [[Probe Page]]"
    double.editing = open_block
    with pytest.raises(EditorOpen):
        cached_api.rename_page("Probe Page", "New Name")
    assert double.writes() == []


def test_remove_block_command_sees_a_child_made_during_the_call(double):
    # Through the command, with its cache: the child appears while the call
    # looks for refs into the block, after it read the block's subtree.
    parent = _uuid(double, "parent block")
    query = double._handlers["logseq.DB.datascriptQuery"]

    def child_appears(args):
        _open_new_child(double, parent)
        return query(args)
    double._handlers["logseq.DB.datascriptQuery"] = child_appears
    r = split_runner().invoke(cli, ["--token", "t", "remove-block", "--id", parent, "--json"])
    assert r.exit_code == 1
    assert _error_object(r)["reason"] == "open_in_editor"
    assert double.sent("removeBlock") == []


def test_gate_refuses_delete_page(double, api):
    open_block = _uuid(double, "kid block")
    _assert_refused(double, lambda: api.delete_page("Probe Page"), open_block)


def test_gate_refuses_rename_page_block_on_page(double, api):
    open_block = _uuid(double, "target block")
    _assert_refused(double, lambda: api.rename_page("Probe Page", "New Name"), open_block)


def test_gate_refuses_rename_page_linking_block(double, api):
    # Logseq rewrites [[Probe Page]] in the open block; leaving the editor
    # would save the old text back (found through the block's refs).
    open_block = _uuid(double, "see [[Probe Page]]")
    _assert_refused(double, lambda: api.rename_page("Probe Page", "New Name"), open_block)


def test_gate_refuses_update_block_uppercase_target(double, api):
    # update-block passes --id through as typed; checkEditing answers in
    # lower case. Compared as typed, the write would pass the gate (measured).
    # A uuid with letters: the double's own are digits, the same in either case.
    double.add_page("Lettered Page", [{"content": "lettered block", "uuid": UNKNOWN}])
    x = UNKNOWN
    assert x.upper() != x
    _assert_refused(double, lambda: api.update_block(x.upper(), "new text"), x)


@pytest.mark.parametrize("write", [
    "update_block", "upsert_block_property", "remove_block_property", "move_block_next_to_open",
    "set_blocks_id", "remove_block", "delete_page", "rename_page",
])
def test_gate_lets_unrelated_writes_through(double, api, write):
    """A block open elsewhere refuses nothing; neither does the anchor of a
    move (a block next to the open one changes nothing in it, as with an
    insert, measured). A guard against a gate that refuses too much."""
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
    # Inserts never refuse (measured: Logseq saves the open block first), so they
    # do not ask; a text without a block ref has no id to store either.
    anchor = _uuid(double, "anchor block")
    double.editing = anchor
    api.insert_block(anchor, "plain child", {"sibling": False})
    api.append_block_in_page("Probe Page", "plain last")
    api.create_page("New Page")
    assert double.sent("checkEditing") == []
    assert [m.rsplit(".", 1)[-1] for m, _ in double.writes()] == \
        ["insertBlock", "appendBlockInPage", "createPage"]


def test_gate_refuses_before_storing_ref_ids(double, api):
    # X is open, the text refers to Y, which has no id:: yet. Refused before
    # setBlocksId, else Y would get its id:: for a write that never happens.
    x, y = _uuid(double, "target block"), _uuid(double, "ref target")
    _assert_refused(double, lambda: api.update_block(x, f"new text (({y}))"), x)
    assert double.sent("setBlocksId") == []


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


# --- What checkEditing answers ---------------------------------------------

@pytest.mark.parametrize("form,editing", [
    ("raw", UNKNOWN),       # measured: the uuid as raw text
    ("json", UNKNOWN),      # the same as a JSON string
    ("raw", None),          # false; as JSON it is the same four letters
    ("json", None),
])
def test_check_editing_forms(double, api, form, editing):
    double.check_editing_form = form
    double.editing = editing
    assert api.check_editing() == editing


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


def test_check_editing_timeout_is_timeout(double):
    # A timeout is the known failure, not an editor state nobody understood.
    double.check_editing_form = "timeout"
    x = _uuid(double, "target block")
    r = split_runner().invoke(cli, ["--token", "t", "update-block", "--id", x,
                                    "--content", "new text", "--json"])
    assert r.exit_code == 1
    assert _error_object(r)["reason"] == "timeout"
    assert double.writes() == []


def test_check_editing_http_error_is_http_error(monkeypatch, double):
    # An HTTP error on checkEditing takes the known http_error way, not that
    # of an editor state nobody understood. Only checkEditing fails here, so
    # the reads before it do not answer for it.
    from tests.conftest import _TextResponse

    def post(url, json=None, **kwargs):
        if json["method"] == "logseq.Editor.checkEditing":
            return _TextResponse("Unauthorized", 401)
        return double.post(url, json=json, **kwargs)
    monkeypatch.setattr("logseq_cli.api.requests.post", post)
    x = _uuid(double, "target block")
    r = split_runner().invoke(cli, ["--token", "wrong", "update-block", "--id", x,
                                    "--content", "new text", "--json"])
    assert r.exit_code == 1
    error = _error_object(r)
    assert error["reason"] == "http_error"
    assert error["status_code"] == 401


# --- Through the command ---------------------------------------------------

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


# --- Batches and an open editor --------------------------------------------

MULTI_BLOCK = ["insert-block", "--child-of", "@parent block", "--content", "new one\n\t- kid"]
KEEP_IDS = ["insert-block", "--after", "@alpha block", "--content", "new one", "--keep-ids"]


def test_batch_goes_block_by_block_while_editing(monkeypatch):
    # insertBatchBlock opens its last block in the editor (editor.cljs:1998;
    # measured); with someone typing, the tree goes block by block, focus: false.
    graph = _graph().install(monkeypatch)
    graph.editing = graph.uuid_of("other block")
    r = _invoke(graph, MULTI_BLOCK)
    assert r.exit_code == 0, r.stdout + r.stderr
    assert graph.sent("insertBatchBlock") == []
    assert [o.get("focus") for o in _sent_options(graph, "insertBlock")] == [False, False]
    assert ("parent block", [("kid block", []), ("new one", [("kid", [])])]) in \
        graph.tree("Probe Page")
    assert graph.editing == graph.uuid_of("other block")


def test_keep_ids_refused_while_editing(monkeypatch):
    # A kept id can only go through the batch (#31), which would move the cursor.
    graph = _graph().install(monkeypatch)
    graph.editing = graph.uuid_of("other block")
    r = _invoke(graph, KEEP_IDS)
    assert r.exit_code == 1
    assert _error_object(r)["reason"] == "open_in_editor"
    assert graph.writes() == []


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


def test_insert_batch_block_refuses_while_editing(double, api):
    # The method refuses on its own, so no caller can set off the jump.
    anchor = _uuid(double, "anchor block")
    _assert_refused(double, lambda: api.insert_batch_block(
        anchor, [{"content": "x"}, {"content": "y"}], {"sibling": True}),
        _uuid(double, "unrelated block"))


def test_keep_ids_refusal_names_the_cursor(monkeypatch):
    # Inserting saves the open block first (measured): nothing typed is lost, the
    # cursor would move. The message says that, not "would discard".
    graph = _graph().install(monkeypatch)
    graph.editing = graph.uuid_of("other block")
    r = _invoke(graph, KEEP_IDS)
    error = _error_object(r)
    assert "move the cursor out of the block being edited" in error["error"]
    assert "discard" not in error["error"]
    assert error["block"] == graph.uuid_of("other block")
    assert error["page"] == "Other Page"


# A --keep-ids write whose first write is not the batch: a missing page, or a
# missing heading on a page that exists, is written before it. 2099-01-05 is
# a journal of _graph() with "## Log"; 2099-01-06 is none.
_KEEP_IDS_FIRST_WRITES = {
    "insert-block-page": ["insert-block", "--page", "Fresh Page", "--content", "x"],
    "insert-block-tree-top-level": ["insert-block", "--page", "Fresh Page", "--top-level",
                                    "--tree", "- x"],
    "add-note-content-page": ["add-note-content", "--page", "Fresh Page", "--content", "x"],
    "add-note-content-heading": ["add-note-content", "--page", "Probe Page", "--content", "x",
                                 "--under-heading", "## Fresh"],
    "add-journal-block-page": ["add-journal-block", "--date", "2099-01-06", "--top-level",
                               "--content", "x"],
    "add-journal-block-tree-page": ["add-journal-block", "--date", "2099-01-06", "--top-level",
                                    "--content", "- x\n\t- y"],
    "add-journal-block-several-page": ["add-journal-block", "--date", "2099-01-06",
                                       "--top-level", "--content", "x", "--content", "y"],
    "add-journal-block-heading": ["add-journal-block", "--date", "2099-01-05",
                                  "--under-heading", "## Fresh", "--content", "x"],
    "add-journal-block-tree-heading": ["add-journal-block", "--date", "2099-01-05",
                                       "--under-heading", "## Fresh", "--content", "- x\n\t- y"],
    "add-journal-block-several-heading": ["add-journal-block", "--date", "2099-01-05",
                                          "--under-heading", "## Fresh",
                                          "--content", "x", "--content", "y"],
    "add-journal-content-page": ["add-journal-content", "--date", "2099-01-06", "--top-level",
                                 "--content", "x"],
    "add-journal-content-heading": ["add-journal-content", "--date", "2099-01-05",
                                    "--under-heading", "## Fresh", "--content", "x"],
}


@pytest.mark.parametrize("args", list(_KEEP_IDS_FIRST_WRITES.values()),
                         ids=list(_KEEP_IDS_FIRST_WRITES))
def test_keep_ids_refused_before_the_page_or_heading_is_made(monkeypatch, args):
    # The page or heading is the call's first write; the refusal comes before
    # it, so nothing is left behind.
    graph = _graph().install(monkeypatch)
    graph.editing = graph.uuid_of("other block")
    r = _invoke(graph, [*args, "--keep-ids"])
    assert r.exit_code == 1, r.stdout + r.stderr
    assert _error_object(r)["reason"] == "open_in_editor"
    assert graph.writes() == []


def _batch(api, double):
    api.insert_batch_block(_uuid(double, "anchor block"),
                           [{"content": "x"}, {"content": "y"}], {"sibling": True})


def test_batch_waits_for_the_editor_logseq_opens_late(monkeypatch, double, api):
    # Measured: Logseq opens the block 16–34 ms after it answered the batch. The
    # method asks again every 10 ms, up to 100 ms, and closes it once it shows.
    naps = []
    monkeypatch.setattr(LogseqAPI, "batch_editor_wait_s", 0.1)
    monkeypatch.setattr(api, "_sleep", naps.append)
    double.show_page("Probe Page")
    double.batch_opens_after_checks = 3
    _batch(api, double)
    assert len(double.sent("exitEditingMode")) == 1
    assert double.editing is None
    assert naps == [0.01] * 3


def test_batch_window_runs_full_when_nothing_opens(monkeypatch, double, api):
    # The page is not on screen (the usual agent case): nothing opens, the
    # window runs its 100 ms, and nothing is closed.
    naps = []
    monkeypatch.setattr(LogseqAPI, "batch_editor_wait_s", 0.1)
    monkeypatch.setattr(api, "_sleep", naps.append)
    _batch(api, double)
    assert double.sent("exitEditingMode") == []
    assert len(naps) == 10 and sum(naps) == pytest.approx(0.1)
    # One question before the batch (the gate), eleven after it.
    assert len(double.sent("checkEditing")) == 12


def _someone_enters_after_the_batch(double, content):
    """Someone clicks into the block ``content`` right after the batch."""
    batch = double._handlers["logseq.Editor.insertBatchBlock"]

    def entered(args):
        answer = batch(args)
        double.editing = double.uuid_of(content)
        return answer
    double._handlers["logseq.Editor.insertBatchBlock"] = entered


def test_batch_leaves_a_block_someone_else_opened(double, api):
    # Only a block of the batch is closed. Logseq does not save a block left
    # while its last editor op is :paste-blocks (lifecycle.cljs:35-43,
    # editor.cljs:2024, read in the code): closing another one would lose
    # what is typed there.
    _someone_enters_after_the_batch(double, "unrelated block")
    _batch(api, double)
    assert double.sent("exitEditingMode") == []
    assert double.editing == _uuid(double, "unrelated block")


def test_batch_still_closes_its_own_block_after_someone_else_opened(monkeypatch, double, api):
    # Someone is in a block when the window starts; Logseq then opens the
    # batch's last block, which the window closes.
    naps = []
    monkeypatch.setattr(LogseqAPI, "batch_editor_wait_s", 0.1)
    monkeypatch.setattr(api, "_sleep", naps.append)
    double.show_page("Probe Page")
    double.batch_opens_after_checks = 2
    _someone_enters_after_the_batch(double, "unrelated block")
    _batch(api, double)
    assert len(double.sent("exitEditingMode")) == 1
    assert double.editing is None


@pytest.mark.parametrize("form", ["empty", "timeout"])
def test_batch_that_landed_counts_when_the_editor_cannot_be_asked(double, form):
    # The batch landed in full; checkEditing after it answers nothing
    # readable. The error must count the blocks, or a retry writes them twice.
    batch = double._handlers["logseq.Editor.insertBatchBlock"]

    def then_unreadable(args):
        answer = batch(args)
        double.check_editing_form = form
        return answer
    double._handlers["logseq.Editor.insertBatchBlock"] = then_unreadable
    parent = _uuid(double, "parent block")
    r = split_runner().invoke(cli, ["--token", "t", "insert-block", "--child-of", parent,
                                    "--content", "x1\n\t- x2", "--json"])
    assert r.exit_code == 1
    error = _error_object(r)
    assert error["reason"] == "editor_state_unknown"
    assert error["writes_landed"] == 2
    assert "Nothing was written" not in error["error"]
    assert "not sent" not in error["error"]
    assert "2 earlier write(s)" in error["error"]
    assert double.uuid_of("x1") and double.uuid_of("x2")


def test_batch_proof_failure_names_an_editor_it_could_not_ask(double, api):
    # Both fail: the proof's error is the one raised, the editor's added.
    from logseq_cli.writerefused import WriteNotVerified

    def nothing_then_unreadable(args):
        double.check_editing_form = "empty"
        return None
    double._handlers["logseq.Editor.insertBatchBlock"] = nothing_then_unreadable
    with pytest.raises(WriteNotVerified) as refused:
        _batch(api, double)
    assert "checkEditing" in str(refused.value)
    assert api.writes_landed == 0
