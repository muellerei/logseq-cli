"""Both test doubles answer getBlock in Logseq's shape, as measured.

``PageGraph`` answered ``parent`` with the parent's uuid and ``children``
without children as ``[]``; the HTTP double left ``children`` out. Logseq
answers a database id and ``["uuid", <uuid>]`` pairs (measured, 0.10.15).
So ``get-block`` printed a database id as ``Parent:`` and crashed on
``--no-children``, and no test saw either (#104). The shapes are pinned
here once, and each double is held to them.
"""
from functools import partial

import pytest

from tests.conftest import PageGraph
from tests.logseq_http_double import LogseqHttpDouble
from tests.test_logseq_http_double import answer

TREE = [{"content": "parent", "children": [
    {"content": "child", "children": ["grandchild"]}]}]


def _page_graph(monkeypatch):
    graph = PageGraph({"Probe Page": TREE})

    def uuid_of(content):
        return next(u for u in graph.every_uuid()
                    if graph.get_block(u, include_children=False)["content"] == content)

    return (lambda ref, children: graph.get_block(ref, include_children=children),
            uuid_of, graph.page_named("Probe Page")["id"])


def _http_double(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    page = double.add_page("Probe Page", TREE)
    return (lambda ref, children: answer(double, "getBlock", ref,
                                         {"includeChildren": children}),
            double.uuid_of, page["id"])


@pytest.fixture(params=[_page_graph, _http_double], ids=["PageGraph", "http-double"])
def double(request, monkeypatch):
    return request.param(monkeypatch)


def test_parent_is_a_database_id(double):
    get, uuid_of, page_id = double
    parent = get(uuid_of("parent"), False)
    child = get(uuid_of("child"), False)
    assert isinstance(parent["id"], int)
    assert parent["parent"] == {"id": page_id}
    assert child["parent"] == {"id": parent["id"]}


def test_a_database_id_reads_the_block(double):
    get, uuid_of, _ = double
    child = get(uuid_of("child"), False)
    assert get(child["parent"]["id"], False)["uuid"] == uuid_of("parent")


def test_without_children_page_is_its_id_and_children_are_pairs(double):
    get, uuid_of, page_id = double
    flat = get(uuid_of("parent"), False)
    assert flat["page"] == {"id": page_id}
    assert flat["children"] == [["uuid", uuid_of("child")]]
    assert get(uuid_of("grandchild"), False)["children"] == []


def test_with_children_page_is_named_and_children_are_blocks(double):
    get, uuid_of, page_id = double
    deep = get(uuid_of("parent"), True)
    assert deep["page"]["id"] == page_id
    assert deep["page"]["originalName"] == "Probe Page"
    assert deep["page"]["name"] == "probe page"
    child = deep["children"][0]
    assert child["uuid"] == uuid_of("child") and isinstance(child["id"], int)
    assert child["parent"] == {"id": deep["id"]}


# Both doubles make up uuids of digits only, which read the same in capitals.
LETTERED = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b"


def _page_graph_with(monkeypatch, block=None, placeholder=None):
    graph = PageGraph({"Probe Page": [block] if block else []},
                      placeholders=[placeholder] if placeholder else ())
    return graph.get_block, graph.update_block


def _http_double_with(monkeypatch, block=None, placeholder=None):
    # The double keeps a placeholder for a ref to a block it does not have.
    double = LogseqHttpDouble.installed(
        monkeypatch, {"Probe Page": [block or f"see (({placeholder}))"]})
    return (lambda ref, children: answer(double, "getBlock", ref, {"includeChildren": children}),
            lambda ref, text: answer(double, "updateBlock", ref, text))


@pytest.fixture(params=[_page_graph_with, _http_double_with], ids=["PageGraph", "http-double"])
def holding(request, monkeypatch):
    """``holding(block=..., placeholder=...)``: getBlock and updateBlock of
    a double that holds the block, or only the placeholder of that uuid."""
    return partial(request.param, monkeypatch)


def test_a_uuid_in_capitals_reads_the_block(holding):
    """Logseq finds a block by its uuid in capitals too (measured, 0.10.15)."""
    get, _ = holding(block={"uuid": LETTERED, "content": "x"})
    assert get(LETTERED.upper(), False)["uuid"] == LETTERED


def test_an_update_by_uuid_in_capitals_lands(holding):
    """updateBlock takes the uuid in capitals too (measured, 0.10.15)."""
    get, update = holding(block={"uuid": LETTERED, "content": "old"})
    update(LETTERED.upper(), "new")
    assert get(LETTERED, False)["content"] == "new"


def test_a_placeholder_by_uuid_in_capitals_is_the_placeholder(holding):
    """The placeholder of a missing ref target answers to its uuid in capitals
    as a block does, lower-cased."""
    get, _ = holding(placeholder=LETTERED)
    found = get(LETTERED.upper(), False)
    assert (found["uuid"], found["content"]) == (LETTERED, f"id:: {LETTERED}")
    assert "page" not in found
