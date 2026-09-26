"""The HTTP double answers the way Logseq 0.10.15 does, as measured.

Gate and proof move into ``LogseqAPI``'s methods, where tests that replace
``LogseqAPI`` cannot see them. The double sits one level lower, in place of
``requests.post``, so these tests pin the answers it gives: each one is a
measured form, and a double that drifts from them would let a proof pass
that Logseq itself would fail.
"""
import unicodedata

import pytest
import requests

import logseq_cli.api
from logseq_cli.api import BadResponseError, LogseqAPI
from logseq_cli.blockprops import stored_properties
from logseq_cli.cli import cli
from logseq_cli.ids import uuids_in_use
from logseq_cli.lookup import find_blocks_by_content, incoming_block_refs
from logseq_cli.pagenames import alias_sources, resolve_page
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

URL = "http://127.0.0.1:12315/api"
UNKNOWN = "5f0c7a8e-1b2d-4c3e-9f4a-6b7c8d9e0f1a"


def rpc(double, method, *args):
    """One JSON-RPC request as ``api.call`` sends it; the raw response."""
    if "." not in method:
        method = f"logseq.Editor.{method}"
    return double.post(URL, json={"method": method, "args": list(args)},
                       headers={"Authorization": "Bearer t"}, timeout=30)


def answer(double, method, *args):
    return rpc(double, method, *args).json()


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble().install(monkeypatch)


@pytest.fixture
def api(double):
    api = LogseqAPI(token="t")
    api.cache_enabled = False
    return api


# --- response object ---------------------------------------------------------

def test_every_answer_is_http_200(double):
    r = rpc(double, "getBlock", "foo", {"includeChildren": False})
    assert r.status_code == 200
    r.raise_for_status()


def test_install_replaces_requests_post_in_the_client(double, api):
    double.add_page("Probe Page", ["alpha"])
    assert api.get_page("probe page")["originalName"] == "Probe Page"
    assert logseq_cli.api.requests.post == double.post


# --- getBlock ----------------------------------------------------------------

def test_get_block_unknown_is_null(double):
    assert answer(double, "getBlock", UNKNOWN, {"includeChildren": True}) is None


def test_get_block_malformed_is_error_object(double):
    # HTTP 200, the error in the body.
    assert answer(double, "getBlock", "foo", {"includeChildren": False}) == \
        {"error": "foo is not a valid UUID string."}


def test_get_block_uppercase_uuid(double):
    double.add_page("Probe Page", ["alpha"])
    uuid = double.uuid_of("alpha")
    found = answer(double, "getBlock", uuid.upper(), {"includeChildren": False})
    assert found["uuid"] == uuid


def test_get_block_parent_and_page_are_db_ids(double):
    page = double.add_page("Probe Page", [{"content": "parent", "children": ["child"]}])
    parent = answer(double, "getBlock", double.uuid_of("parent"), {"includeChildren": False})
    child = answer(double, "getBlock", double.uuid_of("child"), {"includeChildren": False})
    assert parent["page"] == {"id": page["id"]}
    assert parent["parent"] == {"id": page["id"]}
    assert child["parent"] == {"id": parent["id"]}
    assert all(isinstance(v["id"], int) for v in (parent["page"], child["parent"]))


def test_get_block_takes_a_db_id(double):
    double.add_page("Probe Page", [{"content": "parent", "children": ["child"]}])
    child = answer(double, "getBlock", double.uuid_of("child"), {"includeChildren": False})
    by_id = answer(double, "getBlock", child["parent"]["id"], {"includeChildren": True})
    assert by_id["uuid"] == double.uuid_of("parent")
    assert [c["uuid"] for c in by_id["children"]] == [child["uuid"]]


def test_get_block_on_a_page_id_is_null(double):
    page = double.add_page("Probe Page", ["alpha"])
    assert answer(double, "getBlock", page["id"], {"includeChildren": True}) is None


def test_get_block_children_only_when_asked(double):
    double.add_page("Probe Page", [{"content": "parent", "children": [
        {"content": "child", "children": ["grandchild"]}]}])
    uuid = double.uuid_of("parent")
    flat = answer(double, "getBlock", uuid, {"includeChildren": False})
    deep = answer(double, "getBlock", uuid, {"includeChildren": True})
    assert "children" not in flat
    assert deep["children"][0]["content"] == "child"
    assert deep["children"][0]["children"][0]["content"] == "grandchild"


def test_refs_for_link_tag_and_tags_property(double):
    # The same {"id": <page id>} for all three spellings, children or not.
    double.add_page("Target Page", ["t"])
    double.add_page("Probe Page", ["see [[Target Page]]", "see #target", "x\ntags:: Target Page"])
    target = answer(double, "getPage", "target page")
    tag = answer(double, "getPage", "target")
    for content, page_id in (("see [[Target Page]]", target["id"]),
                             ("see #target", tag["id"]),
                             ("x\ntags:: Target Page", target["id"])):
        for children in (False, True):
            block = answer(double, "getBlock", double.uuid_of(content),
                           {"includeChildren": children})
            assert {"id": page_id} in block["refs"], (content, block["refs"])


def test_pre_block_flag(double):
    # A page's property block carries preBlock? true, others false.
    double.add_page("Probe Page", ["kind:: probe", "body"])
    pre = answer(double, "getBlock", double.uuid_of("kind:: probe"), {"includeChildren": False})
    body = answer(double, "getBlock", double.uuid_of("body"), {"includeChildren": False})
    assert pre["preBlock?"] is True
    assert body["preBlock?"] is False
    assert answer(double, "getPage", "probe page")["properties"] == {"kind": "probe"}


# --- getPage and friends -----------------------------------------------------

def test_get_page_form_and_unknown(double):
    page = double.add_page("Probe Page", ["alpha"])
    found = answer(double, "getPage", "PROBE page")
    assert {k: found[k] for k in ("id", "uuid", "name", "originalName")} == {
        "id": page["id"], "uuid": page["uuid"], "name": "probe page",
        "originalName": "Probe Page"}
    assert found["journal?"] is False
    assert answer(double, "getPage", "no such page") is None
    assert answer(double, "getPage", page["id"])["uuid"] == page["uuid"]


def test_page_name_nfd_resolves(double):
    page = double.add_page("Café Notes", ["x"])
    nfd = unicodedata.normalize("NFD", "Café Notes")
    assert nfd != "Café Notes"
    assert answer(double, "getPage", nfd)["uuid"] == page["uuid"]
    assert answer(double, "getPageBlocksTree", nfd)[0]["content"] == "x"


def test_get_page_on_alias_is_the_stub(double, api):
    target = double.add_page("Target Page", ["alias:: Short", "text"])
    stub = answer(double, "getPage", "short")
    assert stub["uuid"] != target["uuid"]
    assert stub["name"] == "short"
    assert answer(double, "getPageBlocksTree", "Short") == []
    # The CLI's own resolution finds the source through alias:: and datalog.
    assert alias_sources(api, "short") == ["Target Page"]
    ref = resolve_page(api, "Short")
    assert (ref.page, ref.redirected) == ("Target Page", True)


def test_get_page_blocks_tree_is_nested(double):
    double.add_page("Probe Page", [{"content": "a", "children": ["a1"]}, "b"])
    tree = answer(double, "getPageBlocksTree", "probe page")
    assert [b["content"] for b in tree] == ["a", "b"]
    assert tree[0]["children"][0]["content"] == "a1"
    assert answer(double, "getPageBlocksTree", "nothing here") is None


def test_get_page_blocks_tree_refuses_a_number(double):
    page = double.add_page("Probe Page", ["a"])
    assert "error" in answer(double, "getPageBlocksTree", page["id"])


def test_get_all_pages_and_user_configs(double):
    double.add_page("Probe Page", ["a"])
    names = [p["originalName"] for p in answer(double, "getAllPages")]
    assert "Probe Page" in names
    configs = answer(double, "logseq.App.getUserConfigs")
    assert configs["preferredDateFormat"] == "yyyy-MM-dd, EEEE"  # measured graph


def test_linked_references(double):
    double.add_page("Target Page", ["t"])
    double.add_page("Other Page", ["see [[Target Page]]", "unrelated"])
    refs = answer(double, "getPageLinkedReferences", "target page")
    assert len(refs) == 1
    page, blocks = refs[0]
    assert page["originalName"] == "Other Page"
    assert [b["content"] for b in blocks] == ["see [[Target Page]]"]


# --- datascriptQuery ---------------------------------------------------------

def test_properties_camel_case_and_text_values(double, api):
    # created_at, and the values 5, a,b, 01234, [[Link]].
    double.add_page("Probe Page", ["props\ncreated_at:: 1"])
    uuid = double.uuid_of("props\ncreated_at:: 1")
    for key, value in (("count", 5), ("items", ["a", "b"]), ("zip", "01234"),
                       ("link", "[[Link]]")):
        assert answer(double, "upsertBlockProperty", uuid, key, value) is None
    block = answer(double, "getBlock", uuid, {"includeChildren": False})
    assert block["content"] == ("props\ncreated_at:: 1\ncount:: 5\nitems:: a,b\n"
                                "zip:: 01234\nlink:: [[Link]]")
    assert block["properties"] == {"createdAt": 1, "count": 5, "items": ["a", "b"],
                                   "zip": "01234", "link": "[[Link]]"}
    values, texts = stored_properties(api, uuid)
    assert values == {"created-at": 1, "count": 5, "items": ["a", "b"],
                      "zip": "01234", "link": "[[Link]]"}
    assert texts == {"created-at": "1", "count": "5", "items": "a,b",
                     "zip": "01234", "link": "[[Link]]"}


def test_camel_case_of_a_dashed_key(double):
    double.add_page("Probe Page", ["x\ndue-date:: 2099-01-01"])
    block = answer(double, "getBlock", double.uuid_of("x\ndue-date:: 2099-01-01"),
                   {"includeChildren": False})
    assert block["properties"] == {"dueDate": "2099-01-01"}


def test_queries_of_the_writing_commands(double, api):
    double.add_page("Probe Page", ["needle one", {"content": "parent", "children": ["kid"]}])
    kid = double.uuid_of("kid")
    double.add_page("Other Page", [f"points at (({kid}))"])
    page = answer(double, "getPage", "probe page")
    # ids.uuids_in_use: blocks and pages, not a placeholder or an unknown id.
    double.add_page("Third Page", [f"dead (({UNKNOWN}))"])
    assert uuids_in_use(api, [kid, page["uuid"], UNKNOWN]) == [kid, page["uuid"]]
    # lookup.find_blocks_by_content, both ways, with and without a page.
    assert [b["uuid"] for b in find_blocks_by_content(api, "needle")] == \
        [double.uuid_of("needle one")]
    assert find_blocks_by_content(api, "needle", page="Other Page") == []
    found = find_blocks_by_content(api, r"^needle \w+$", page="Probe Page", use_regex=True)
    assert [b["content"] for b in found] == ["needle one"]
    assert found[0]["page"] == {"original-name": "Probe Page", "name": "probe page"}
    # lookup.incoming_block_refs, by uuids and by page.
    source = double.uuid_of(f"points at (({kid}))")
    expected = [{"target": kid, "block": source, "page": "Other Page"}]
    assert incoming_block_refs(api, uuids=[double.uuid_of("parent"), kid]) == expected
    assert incoming_block_refs(api, page="probe page") == expected


def test_content_search_reads_escaped_literals(double, api):
    double.add_page("Probe Page", ['say "hi" \\ there', "other"])
    assert [b["content"] for b in find_blocks_by_content(api, '"hi" \\')] == \
        ['say "hi" \\ there']


def test_unanswered_query_fails_loudly(double):
    with pytest.raises(NotImplementedError, match="datascriptQuery"):
        rpc(double, "logseq.DB.datascriptQuery", "[:find ?x :where [?x :nothing/here]]")


# --- writes: answers and effects ---------------------------------------------

@pytest.mark.parametrize("method,args", [
    ("updateBlock", ["foo", "x"]),
    ("removeBlock", ["foo"]),
    ("upsertBlockProperty", ["foo", "k", "v"]),
    ("removeBlockProperty", ["foo", "k"]),
])
@pytest.mark.parametrize("mode", ["execute", "noop", "error"])
def test_write_with_malformed_uuid_is_error_object(double, method, args, mode):
    # In every mode, the uuid check comes first.
    double.set_mode(method, mode)
    assert answer(double, method, *args) == {"error": "foo is not a valid UUID string."}


@pytest.mark.parametrize("mode", ["execute", "noop"])
def test_update_block_answers_null_in_execute_and_noop(double, mode):
    double.add_page("Probe Page", ["old"])
    uuid = double.uuid_of("old")
    double.set_mode("updateBlock", mode)
    assert answer(double, "updateBlock", uuid, "new") is None
    assert answer(double, "updateBlock", uuid, "new") is None       # same again
    assert answer(double, "updateBlock", UNKNOWN, "new") is None    # unknown uuid
    content = answer(double, "getBlock", uuid, {"includeChildren": False})["content"]
    assert content == ("new" if mode == "execute" else "old")


def test_update_block_trims_trailing_spaces(double):
    double.add_page("Probe Page", ["old"])
    uuid = double.uuid_of("old")
    answer(double, "updateBlock", uuid, "new  ")
    assert answer(double, "getBlock", uuid, {"includeChildren": False})["content"] == "new"


def test_update_block_with_properties_writes_lines(double, api):
    double.add_page("Probe Page", ["old"])
    uuid = double.uuid_of("old")
    answer(double, "updateBlock", uuid, "new text\nprio:: 9",
           {"properties": {"prio": "1", "due-date": "2099-01-01"}})
    block = answer(double, "getBlock", uuid, {"includeChildren": False})
    # A passed key wins over the text's own line (#66).
    assert block["content"] == "new text\nprio:: 1\ndue-date:: 2099-01-01"
    assert stored_properties(api, uuid)[1] == {"prio": "1", "due-date": "2099-01-01"}


def test_update_block_takes_a_foreign_id_line_as_its_uuid(double):
    # #56: the id:: line in the text becomes the block's uuid.
    double.add_page("Probe Page", ["old"])
    uuid = double.uuid_of("old")
    answer(double, "updateBlock", uuid, f"new\nid:: {UNKNOWN}")
    assert answer(double, "getBlock", uuid, {"includeChildren": False}) is None
    assert answer(double, "getBlock", UNKNOWN, {"includeChildren": False})["content"] == \
        f"new\nid:: {UNKNOWN}"


def test_update_block_makes_or_unmakes_the_property_block(double):
    double.add_page("Probe Page", ["plain", "body"])
    uuid = double.uuid_of("plain")
    answer(double, "updateBlock", uuid, "kind:: probe")
    assert answer(double, "getPage", "probe page")["properties"] == {"kind": "probe"}
    assert answer(double, "getBlock", uuid, {"includeChildren": False})["preBlock?"] is True
    answer(double, "updateBlock", uuid, "plain again")
    assert "properties" not in answer(double, "getPage", "probe page")


def test_remove_block_removes_subtree(double):
    # null, and block and child are null right after.
    double.add_page("Probe Page", [{"content": "parent", "children": ["child"]}, "other"])
    parent, child = double.uuid_of("parent"), double.uuid_of("child")
    assert answer(double, "removeBlock", parent) is None
    assert answer(double, "getBlock", parent, {"includeChildren": False}) is None
    assert answer(double, "getBlock", child, {"includeChildren": False}) is None
    assert answer(double, "removeBlock", UNKNOWN) is None
    assert double.tree("Probe Page") == [("other", [])]


def test_remove_block_property(double):
    double.add_page("Probe Page", ["x\nkeep:: 1\ngone:: 2"])
    uuid = double.uuid_of("x\nkeep:: 1\ngone:: 2")
    assert answer(double, "removeBlockProperty", uuid, "gone") is None
    assert answer(double, "removeBlockProperty", uuid, "never") is None
    assert answer(double, "removeBlockProperty", UNKNOWN, "gone") is None
    block = answer(double, "getBlock", uuid, {"includeChildren": False})
    assert block["content"] == "x\nkeep:: 1"
    assert block["properties"] == {"keep": 1}


def test_upsert_on_unknown_uuid_is_null(double):
    assert answer(double, "upsertBlockProperty", UNKNOWN, "k", "v") is None


def test_create_page_answers_the_page(double):
    # The page, with the properties as sent.
    page = answer(double, "createPage", "New Page", {"status": "a"}, {"redirect": False})
    assert page["name"] == "new page"
    assert page["originalName"] == "New Page"
    assert page["properties"] == {"status": "a"}
    assert page["uuid"] == answer(double, "getPage", "new page")["uuid"]
    assert double.tree("New Page") == [("status:: a", [])]


def test_create_page_existing_ignores_properties(double):
    # The same page, its old properties, the passed ones dropped.
    first = answer(double, "createPage", "New Page", {"status": "a"})
    again = answer(double, "createPage", "new page", {"status": "b"})
    assert again["uuid"] == first["uuid"]
    assert again["properties"] == {"status": "a"}
    assert double.tree("New Page") == [("status:: a", [])]


def test_create_page_first_block(double):
    # An empty first block unless createFirstBlock is false.
    answer(double, "createPage", "With Block")
    answer(double, "createPage", "Without Block", {}, {"createFirstBlock": False})
    assert double.tree("With Block") == [("", [])]
    assert double.tree("Without Block") == []


def test_create_page_journal_title_case(double):
    # Name as sent (lower), originalName with the weekday capitalised.
    page = answer(double, "createPage", "2099-01-05, monday", {"journal?": True})
    assert page["name"] == "2099-01-05, monday"
    assert page["originalName"] == "2099-01-05, Monday"
    assert page["journal?"] is True
    assert double.tree("2099-01-05, Monday") == [("journal?:: true", [])]


def test_create_page_journal_by_name_alone(double):
    # The name in the graph's format makes it a journal, no property needed.
    page = answer(double, "createPage", "2099-02-02, monday", {}, {"createFirstBlock": False})
    assert page["journal?"] is True
    found = answer(double, "getPage", "2099-02-02, monday")
    assert (found["journal?"], found["journalDay"]) == (True, 20990202)
    assert answer(double, "getPage", "Probe Page") is None
    assert answer(double, "createPage", "Probe Page")["journal?"] is False


def test_create_page_journal_in_another_format_is_null(double):
    # Created under the graph's name, answered with null.
    assert answer(double, "createPage", "Jan 1st, 2099") is None
    assert answer(double, "getPage", "2099-01-01, thursday")["journal?"] is True


def test_delete_page(double):
    # null, for a missing page too.
    double.add_page("Probe Page", ["a"])
    assert answer(double, "deletePage", "probe page") is None
    assert answer(double, "getPage", "probe page") is None
    assert answer(double, "deletePage", "probe page") is None


def test_rename_keeps_the_uuid(double):
    page = double.add_page("Old Name", ["a"])
    assert answer(double, "renamePage", "Old Name", "New Name") is None
    assert answer(double, "getPage", "old name") is None
    renamed = answer(double, "getPage", "new name")
    assert (renamed["uuid"], renamed["originalName"]) == (page["uuid"], "New Name")


def test_rename_rewrites_links(double):
    double.add_page("Old Name", ["a"])
    double.add_page("Other Page", ["see [[Old Name]]"])
    answer(double, "renamePage", "Old Name", "New Name")
    assert double.tree("Other Page") == [("see [[New Name]]", [])]


def test_rename_case_only_keeps_name(double):
    page = double.add_page("probe page", ["a"])
    assert answer(double, "renamePage", "probe page", "Probe PAGE") is None
    found = answer(double, "getPage", "probe page")
    assert (found["uuid"], found["name"], found["originalName"]) == \
        (page["uuid"], "probe page", "Probe PAGE")


def test_rename_to_empty_does_nothing(double):
    double.add_page("Probe Page", ["a"])
    assert answer(double, "renamePage", "Probe Page", "") is None
    assert answer(double, "getPage", "probe page")["originalName"] == "Probe Page"


def test_rename_onto_existing_merges(double):
    # null; the source is gone, its blocks hang under the target. Built
    # as measured, each page made by createPage (its empty first block) and
    # then appended to: the target reads "", d, "", c.
    for name, text in (("Source Page", "c-text"), ("Target Page", "d-text")):
        answer(double, "createPage", name, {}, {"redirect": False})
        answer(double, "appendBlockInPage", name, text)
    target = answer(double, "getPage", "target page")
    assert answer(double, "renamePage", "Source Page", "Target Page") is None
    assert answer(double, "getPage", "source page") is None
    assert answer(double, "getPage", "target page")["uuid"] == target["uuid"]
    assert [b["content"] for b in answer(double, "getPageBlocksTree", "target page")] == \
        ["", "d-text", "", "c-text"]


def test_rename_missing_source_is_error_object(double):
    assert answer(double, "renamePage", "No Such Page", "Anything") == \
        {"error": "Cannot read properties of null (reading 'replace')"}


def test_insert_block_answers_the_block(double):
    double.add_page("Probe Page", [{"content": "parent", "children": ["kid"]}])
    parent, kid = double.uuid_of("parent"), double.uuid_of("kid")
    child = answer(double, "insertBlock", parent, "last child", {"sibling": False})
    first = answer(double, "insertBlock", parent, "first child", {"sibling": False, "before": True})
    after = answer(double, "insertBlock", kid, "after kid", {"sibling": True})
    before = answer(double, "insertBlock", kid, "before kid", {"sibling": True, "before": True})
    assert all(b["uuid"] for b in (child, first, after, before))
    assert double.tree("Probe Page") == [("parent", [
        ("first child", []), ("before kid", []), ("kid", []), ("after kid", []),
        ("last child", [])])]
    assert answer(double, "insertBlock", UNKNOWN, "x", {}) is None


def test_insert_block_custom_uuid_taken(double):
    double.add_page("Probe Page", ["a"])
    a = double.uuid_of("a")
    assert answer(double, "insertBlock", a, "x", {"customUUID": a}) == \
        {"error": "Custom block UUID already exists"}


def test_append_creates_missing_page(double):
    # appendBlockInPage on a missing page makes it (empty first block) and
    # writes after that block, even when a createPage before it was a no-op.
    double.set_mode("createPage", "noop")
    assert answer(double, "createPage", "New Page") is None
    assert answer(double, "getPage", "new page") is None
    block = answer(double, "appendBlockInPage", "New Page", "x")
    assert block["uuid"] == double.uuid_of("x")
    assert double.tree("New Page") == [("", []), ("x", [])]


def test_insert_batch_block(double):
    double.add_page("Probe Page", ["anchor", "last"])
    anchor = double.uuid_of("anchor")
    tree = [{"content": "r1", "children": [{"content": "r1a"}]}, {"content": "r2"}]
    assert answer(double, "insertBatchBlock", anchor, tree, {"sibling": True}) is None
    assert double.tree("Probe Page") == [("anchor", []), ("r1", [("r1a", [])]),
                                         ("r2", []), ("last", [])]


def test_insert_batch_block_keeps_uuid_only_when_asked(double):
    double.add_page("Probe Page", ["anchor"])
    anchor = double.uuid_of("anchor")
    answer(double, "insertBatchBlock", anchor, [{"content": f"kept\nid:: {UNKNOWN}"}],
           {"sibling": True, "keepUUID": True})
    assert answer(double, "getBlock", UNKNOWN, {"includeChildren": False})["content"] == \
        f"kept\nid:: {UNKNOWN}"
    other = "6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6"
    answer(double, "insertBatchBlock", anchor, [{"content": f"dropped\nid:: {other}"}],
           {"sibling": True})
    assert answer(double, "getBlock", other, {"includeChildren": False}) is None
    assert double.uuid_of("dropped")


def test_move_block(double):
    double.add_page("Probe Page", ["a", "b", {"content": "c", "children": ["c1"]}])
    a, c = double.uuid_of("a"), double.uuid_of("c")
    assert answer(double, "moveBlock", a, c, {"children": True}) is None
    assert double.tree("Probe Page") == [("b", []), ("c", [("c1", []), ("a", [])])]
    answer(double, "moveBlock", a, double.uuid_of("b"), {"before": True})
    assert double.tree("Probe Page") == [("a", []), ("b", []), ("c", [("c1", [])])]


def test_set_blocks_id_forms(double):
    # Sets id in each asked block's properties; skips an unknown uuid and a
    # page's property block; leaves a stored id as it is.
    double.add_page("Probe Page", ["kind:: probe", "plain", "has id"])
    pre, plain, has = (double.uuid_of(c) for c in ("kind:: probe", "plain", "has id"))
    answer(double, "setBlocksId", [has])
    had = answer(double, "getBlock", has, {"includeChildren": False})["content"]
    assert answer(double, "setBlocksId", [plain, pre, has, UNKNOWN]) is None
    block = answer(double, "getBlock", plain, {"includeChildren": False})
    assert block["properties"]["id"] == plain
    assert block["content"] == f"plain\nid:: {plain}"
    assert "id" not in answer(double, "getBlock", pre, {"includeChildren": False})["properties"]
    assert answer(double, "getBlock", has, {"includeChildren": False})["content"] == had


# --- modes ---------------------------------------------------------------------

def test_noop_mode_writes_nothing_and_answers_null(double):
    double.add_page("Probe Page", ["old"])
    uuid = double.uuid_of("old")
    for method in ("updateBlock", "insertBlock", "createPage", "appendBlockInPage",
                   "removeBlock", "upsertBlockProperty"):
        double.set_mode(method, "noop")
    before = double.snapshot()
    assert answer(double, "updateBlock", uuid, "new") is None
    assert answer(double, "insertBlock", uuid, "child", {"sibling": False}) is None
    assert answer(double, "createPage", "Fresh Page") is None
    assert answer(double, "appendBlockInPage", "Probe Page", "more") is None
    assert answer(double, "upsertBlockProperty", uuid, "k", "v") is None
    assert answer(double, "removeBlock", uuid) is None
    assert double.snapshot() == before


def test_noop_from_call_k(double):
    # The first k-1 calls go through, so a partial state can be built.
    double.add_page("Probe Page", ["a"])
    anchor = double.uuid_of("a")
    double.set_mode("insertBlock", "noop", from_call=2)
    assert answer(double, "insertBlock", anchor, "first", {"sibling": True})["uuid"]
    assert answer(double, "insertBlock", anchor, "second", {"sibling": True}) is None
    assert double.tree("Probe Page") == [("a", []), ("first", [])]


def test_error_mode_returns_error_object(double):
    double.add_page("Probe Page", ["old"])
    uuid = double.uuid_of("old")
    double.set_mode("updateBlock", "error")
    assert answer(double, "updateBlock", uuid, "new") == {"error": "updateBlock failed"}
    assert double.tree("Probe Page") == [("old", [])]


def test_mode_rejects_a_read_or_unknown_name(double):
    with pytest.raises(ValueError):
        double.set_mode("getBlock", "noop")
    with pytest.raises(ValueError):
        double.set_mode("updateBlock", "sometimes")


# --- editor ------------------------------------------------------------------

def test_check_editing_is_raw_text(double, api):
    # The uuid as raw text, not JSON.
    double.editing = "6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6"
    r = rpc(double, "checkEditing")
    assert r.text == double.editing
    with pytest.raises(requests.exceptions.JSONDecodeError):
        r.json()
    with pytest.raises(BadResponseError):
        api.call("logseq.Editor.checkEditing")


def test_check_editing_false(double):
    r = rpc(double, "checkEditing")
    assert (r.text, r.json()) == ("false", False)


@pytest.mark.parametrize("form,text", [
    ("json", '"6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6"'),
    ("empty", ""),
    ("error", '{"error": "checkEditing failed"}'),
    ("ok1", '{"ok": 1}'),
])
def test_check_editing_forms(double, form, text):
    double.editing = "6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6"
    double.check_editing_form = form
    assert rpc(double, "checkEditing").text == text


def test_check_editing_timeout(double):
    double.check_editing_form = "timeout"
    with pytest.raises(requests.exceptions.Timeout):
        rpc(double, "checkEditing")


def test_exit_editing_mode(double):
    double.editing = "6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6"
    assert answer(double, "exitEditingMode") is None
    assert double.editing is None


def test_insert_batch_block_opens_the_last_block_on_a_visible_page(double):
    # editor.cljs edit-last-block-after-inserted!, 0.10.15, measured: only
    # where the page is shown.
    double.add_page("Probe Page", ["anchor"])
    anchor = double.uuid_of("anchor")
    tree = [{"content": "r1", "children": [{"content": "r1a"}]}, {"content": "r2"}]
    answer(double, "insertBatchBlock", anchor, tree, {"sibling": True})
    assert double.editing is None
    double.show_page("probe page")
    answer(double, "insertBatchBlock", anchor, [{"content": "s1"}, {"content": "s2"}],
           {"sibling": True})
    assert double.editing == double.uuid_of("s2")


def test_insert_opens_the_new_block_unless_focus_false(double):
    # The cursor jumps into the new block on a visible page.
    double.add_page("Probe Page", ["anchor"])
    double.show_page("Probe Page")
    anchor = double.uuid_of("anchor")
    answer(double, "insertBlock", anchor, "quiet", {"sibling": True, "focus": False})
    assert double.editing is None
    answer(double, "appendBlockInPage", "Probe Page", "loud")
    assert double.editing == double.uuid_of("loud")


def test_time_tracking_off_by_default(double):
    double.add_page("Probe Page", ["TODO task"])
    uuid = double.uuid_of("TODO task")
    answer(double, "updateBlock", uuid, "DOING task")
    assert double.tree("Probe Page") == [("DOING task", [])]


def test_time_tracking_clocks_in_and_out(double):
    # Upstream util/clock.cljs clock-in and clock-out, 0.10.15; the format
    # is assumed, not measured.
    double.time_tracking = True
    double.add_page("Probe Page", ["TODO task"])
    uuid = double.uuid_of("TODO task")
    answer(double, "updateBlock", uuid, "DOING task")
    content = answer(double, "getBlock", uuid, {"includeChildren": False})["content"]
    assert content == "DOING task\n:LOGBOOK:\nCLOCK: [2026-09-26 Sat 14:00]\n:END:"
    answer(double, "updateBlock", uuid, content.replace("DOING", "DONE", 1))
    content = answer(double, "getBlock", uuid, {"includeChildren": False})["content"]
    assert content == ("DONE task\n:LOGBOOK:\n"
                       "CLOCK: [2026-09-26 Sat 14:00]--[2026-09-26 Sat 14:05] =>  00:05:00\n"
                       ":END:")
    answer(double, "updateBlock", uuid, content.replace("DONE", "NOW", 1))
    content = answer(double, "getBlock", uuid, {"includeChildren": False})["content"]
    assert content.endswith("00:05:00\nCLOCK: [2026-09-26 Sat 14:00]\n:END:")


# --- protocol ------------------------------------------------------------------

def test_unknown_method_is_error_object(double):
    assert answer(double, "logseq.Editor.doesNotExist") == {"error": "MethodNotExist: does_not_exist"}


def test_records_requests(double, api):
    double.add_page("Probe Page", ["a"])
    anchor = double.uuid_of("a")
    api.get_page("Probe Page")
    api.insert_block(anchor, "b", {"sibling": True, "focus": False})
    assert double.requests == [
        ("logseq.Editor.getPage", ["Probe Page"]),
        ("logseq.Editor.insertBlock", [anchor, "b", {"sibling": True, "focus": False}]),
    ]
    assert double.sent("insertBlock") == [[anchor, "b", {"sibling": True, "focus": False}]]
    assert double.writes() == [("logseq.Editor.insertBlock",
                                [anchor, "b", {"sibling": True, "focus": False}])]


def test_answers_are_copies(double):
    # A caller that edits what it got back must not edit the graph.
    double.add_page("Probe Page", ["a"])
    uuid = double.uuid_of("a")
    block = answer(double, "getBlock", uuid, {"includeChildren": False})
    block["content"] = "changed"
    assert double.tree("Probe Page") == [("a", [])]


# --- through the command -----------------------------------------------------

def test_insert_block_command_end_to_end(double):
    """insert-block through CliRunner, the real LogseqAPI and requests.post."""
    double.add_page("Probe Page", ["first"])
    r = split_runner().invoke(cli, ["--token", "t", "insert-block", "--page", "Probe Page",
                                    "--content", "written through", "--json"])
    assert r.exit_code == 0, r.output + r.stderr
    assert double.tree("Probe Page") == [("first", []), ("written through", [])]
    assert "logseq.Editor.appendBlockInPage" in [m for m, _ in double.writes()]
