"""Both test doubles answer getBlock in Logseq's shape, as measured.

``PageGraph`` answered ``parent`` with the parent's uuid and ``children``
without children as ``[]``; the HTTP double left ``children`` out. Logseq
answers a database id and ``["uuid", <uuid>]`` pairs (measured, 0.10.15).
So ``get-block`` printed a database id as ``Parent:`` and crashed on
``--no-children``, and no test saw either (#104). The shapes are pinned
here once, and each double is held to them.
"""
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
