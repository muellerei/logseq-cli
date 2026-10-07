"""The hash a reader gets for a block (``get-block --json``).

It names what a caller decides on: the text, the marker, the properties that
are the block's own, and the completions logged in its LOGBOOK. What Logseq
or the UI changes on its own does not move it: collapsing, the Id Line and
``CLOCK`` lines (a clock line only ever appears together with a marker change,
which the hash sees anyway).
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import CLOCK_IN, CLOCK_OUT, LogseqHttpDouble

DONE_LINE = '* State "DONE" from "TODO" [2026-03-02 Mon 09:15]'
DONE_LINE_NEXT_DAY = '* State "DONE" from "TODO" [2026-03-03 Tue 09:15]'
DRAWER_CLOCK_OPEN = f":LOGBOOK:\n{CLOCK_IN}\n:END:"
DRAWER_CLOCK_CLOSED = f":LOGBOOK:\n{CLOCK_OUT}\n:END:"
DRAWER_DONE = f":LOGBOOK:\n{DONE_LINE}\n:END:"
DRAWER_DONE_AND_CLOCK = f":LOGBOOK:\n{CLOCK_OUT}\n{DONE_LINE}\n:END:"
DRAWER_TWO_DONE = f":LOGBOOK:\n{DONE_LINE}\n{DONE_LINE_NEXT_DAY}\n:END:"


def _hash(content):
    from logseq_cli.blocktext import block_hash
    return block_hash(content)


def _read(double, content, *options):
    return split_runner().invoke(
        cli, ["--token", "t", "get-block", "--id", double.uuid_of(content), *options])


def _json(double, content, *options):
    result = _read(double, content, "--json", *options)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _hash_via_cli(monkeypatch, content):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", [{"content": content}])
    return _json(double, content)["hash"]


# --- the function ------------------------------------------------------------

def test_hash_is_twelve_hex_characters():
    h = _hash("TODO write the report")
    assert len(h) == 12 and int(h, 16) >= 0


def test_hash_is_the_same_for_the_same_text():
    assert _hash("TODO a") == _hash("TODO a")


@pytest.mark.parametrize("a, b", [
    ("TODO a", "TODO b"),
    ("TODO a", "DOING a"),
    ("a\nprio:: 1", "a\nprio:: 2"),
], ids=["text", "marker", "property"])
def test_hash_changes_with_what_the_caller_decides_on(a, b):
    assert _hash(a) != _hash(b)


@pytest.mark.parametrize("a, b", [
    ("TODO a\ncollapsed:: true", "TODO a"),
    ("TODO a\nid:: 6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6", "TODO a"),
    ("TODO a  \n", "TODO a"),
    (f"TODO a\n{DRAWER_CLOCK_OPEN}", "TODO a"),
    (f"TODO a\n{DRAWER_CLOCK_CLOSED}", f"TODO a\n{DRAWER_CLOCK_OPEN}"),
    (f"TODO a\n\n{DRAWER_CLOCK_OPEN}", "TODO a"),
    (f"TODO a\n{DRAWER_DONE_AND_CLOCK}", f"TODO a\n{DRAWER_DONE}"),
], ids=["collapsed", "id", "trailing-space", "clock-drawer-appears",
        "clock-line-changes", "blank-line-before-drawer", "clock-beside-done"])
def test_hash_ignores_what_changes_on_its_own(a, b):
    assert _hash(a) == _hash(b)


def test_hash_sees_a_new_done_line():
    assert _hash(f"TODO a\n{DRAWER_DONE}") != _hash("TODO a")
    assert _hash(f"TODO a\n{DRAWER_DONE}") != _hash(f"TODO a\n{DRAWER_CLOCK_CLOSED}")


def test_hash_sees_a_second_done_line():
    assert _hash(f"TODO a\n{DRAWER_TWO_DONE}") != _hash(f"TODO a\n{DRAWER_DONE}")


def test_an_unclosed_drawer_is_text():
    assert _hash(f"TODO a\n:LOGBOOK:\n{CLOCK_IN}") != _hash("TODO a")


def test_collapsed_in_a_code_block_is_code():
    fenced = "a\n```\ncollapsed:: true\n```"
    assert _hash(fenced) != _hash("a\n```\n```")


# --- the reading -------------------------------------------------------------

@pytest.fixture
def double(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", [
        {"content": "TODO parent", "children": [
            {"content": "first", "children": ["grandchild"]},
            {"content": "second"}]},
        {"content": "TODO other"},
    ])
    return double


def test_get_block_json_carries_hash_and_tree_hash(double):
    block = _json(double, "TODO parent")
    assert block["hash"] == _hash("TODO parent")
    assert len(block["tree_hash"]) == 12 and block["tree_hash"] != block["hash"]


def test_children_carry_a_hash_and_no_tree_hash(double):
    block = _json(double, "TODO parent")
    first, second = block["children"]
    assert first["hash"] == _hash("first") and second["hash"] == _hash("second")
    assert first["children"][0]["hash"] == _hash("grandchild")
    assert all("tree_hash" not in c for c in block["children"])
    assert "tree_hash" not in first["children"][0]


def test_no_children_keeps_hash_and_drops_tree_hash(double):
    block = _json(double, "TODO parent", "--no-children")
    assert block["hash"] == _hash("TODO parent")
    assert "tree_hash" not in block


def test_text_output_shows_neither(double):
    r = _read(double, "TODO parent")
    assert r.exit_code == 0, r.output
    assert "hash" not in r.stdout.lower()


def test_reading_twice_gives_the_same_answer(double):
    """The fields are added to a copy: a block cached by the first read does
    not carry them into another command's output."""
    assert _json(double, "TODO parent") == _json(double, "TODO parent")


@pytest.mark.parametrize("a, b, same", [
    ("TODO a", "TODO a\ncollapsed:: true", True),
    ("TODO a", f"TODO a\n{DRAWER_CLOCK_OPEN}", True),
    ("TODO a", f"TODO a\n{DRAWER_CLOCK_CLOSED}", True),
    (f"TODO a\n{DRAWER_CLOCK_OPEN}", f"TODO a\n{DRAWER_CLOCK_CLOSED}", True),
    ("TODO a", f"TODO a\n{DRAWER_DONE}", False),
    ("TODO a", "DOING a", False),
], ids=["collapsed", "clock-drawer-appears", "closed-clock-drawer-appears",
        "clock-line-changes", "done-line", "marker"])
def test_get_block_hash_over_the_api(monkeypatch, a, b, same):
    assert (_hash_via_cli(monkeypatch, a) == _hash_via_cli(monkeypatch, b)) is same


# --- the tree hash -----------------------------------------------------------

def _tree_hash(monkeypatch, blocks):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", blocks)
    return _json(double, "root")["tree_hash"]


def test_tree_hash_changes_with_a_new_child(monkeypatch):
    one = _tree_hash(monkeypatch, [{"content": "root", "children": ["a"]}])
    two = _tree_hash(monkeypatch, [{"content": "root", "children": ["a", "b"]}])
    assert one != two


def test_tree_hash_changes_with_the_order_of_the_children(monkeypatch):
    ab = _tree_hash(monkeypatch, [{"content": "root", "children": ["a", "b"]}])
    ba = _tree_hash(monkeypatch, [{"content": "root", "children": ["b", "a"]}])
    assert ab != ba


def test_tree_hash_changes_with_the_depth_of_a_descendant(monkeypatch):
    flat = _tree_hash(monkeypatch, [{"content": "root", "children": ["a", "b"]}])
    deep = _tree_hash(monkeypatch, [{"content": "root", "children": [
        {"content": "a", "children": ["b"]}]}])
    assert flat != deep


def test_tree_hash_changes_with_a_descendants_text(monkeypatch):
    a = _tree_hash(monkeypatch, [{"content": "root", "children": [
        {"content": "a", "children": ["deep"]}]}])
    b = _tree_hash(monkeypatch, [{"content": "root", "children": [
        {"content": "a", "children": ["deeper"]}]}])
    assert a != b


def test_tree_hash_of_a_leaf_differs_from_its_hash(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", [{"content": "root"}])
    block = _json(double, "root")
    assert block["tree_hash"] != block["hash"]


def test_tree_hash_ignores_what_the_hash_ignores(monkeypatch):
    plain = _tree_hash(monkeypatch, [{"content": "root", "children": ["a"]}])
    noisy = _tree_hash(monkeypatch, [{"content": "root", "children": [
        f"a\ncollapsed:: true\n{DRAWER_CLOCK_OPEN}"]}])
    assert plain == noisy


def test_tree_hash_is_in_no_other_read(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", [{"content": "root", "children": ["a"]}])
    for argv in (["get-page", "--name", "Probe Page", "--json"],
                 ["find-block", "--content", "root", "--json"]):
        r = split_runner().invoke(cli, ["--token", "t", *argv])
        assert r.exit_code == 0, r.output
        assert "tree_hash" not in r.stdout
