"""get-block's text output, against the HTTP double (#104).

Asked without children, Logseq answers each direct child as a
``["uuid", <uuid>]`` pair, not a block (measured, 0.10.15).
"""
import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


@pytest.fixture
def double(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", [{"content": "parent", "children": [
        {"content": "child", "children": ["grandchild"]}]}])
    return double


def _get_block(double, content, *options):
    return split_runner().invoke(
        cli, ["--token", "t", "get-block", "--id", double.uuid_of(content), *options])


def test_no_children_prints_the_block_without_its_children(double):
    r = _get_block(double, "parent", "--no-children")
    assert r.exit_code == 0, r.output
    assert r.stdout.rstrip().endswith("parent")
    assert "child" not in r.stdout.replace("parent", "")


def test_no_children_json_keeps_logseq_answer(double):
    r = _get_block(double, "parent", "--no-children", "--json")
    assert r.exit_code == 0, r.output
    assert f'"{double.uuid_of("child")}"' in r.stdout


def test_children_are_printed_by_default(double):
    r = _get_block(double, "parent")
    assert r.exit_code == 0, r.output
    assert "child" in r.stdout and "grandchild" in r.stdout


# --- Page: and Parent: -------------------------------------------------------
# Logseq answers page and parent as {"id": <db id>}; no command takes a db id.

def _line(result, label):
    return [ln for ln in result.stdout.splitlines() if ln.startswith(f"{label}:")]


@pytest.mark.parametrize("options", [(), ("--no-children",)], ids=["children", "no-children"])
def test_page_is_named(double, options):
    r = _get_block(double, "child", *options)
    assert r.exit_code == 0, r.output
    assert _line(r, "Page") == ["Page: Probe Page"]


@pytest.mark.parametrize("options", [(), ("--no-children",)], ids=["children", "no-children"])
def test_parent_block_is_its_uuid(double, options):
    r = _get_block(double, "child", *options)
    assert r.exit_code == 0, r.output
    assert _line(r, "Parent") == [f"Parent: {double.uuid_of('parent')}"]


@pytest.mark.parametrize("options", [(), ("--no-children",)], ids=["children", "no-children"])
def test_parent_line_says_page_directly_under_the_page(double, options):
    """Always a Parent line, so the first one never comes from the content."""
    r = _get_block(double, "parent", *options)
    assert r.exit_code == 0, r.output
    assert _line(r, "Parent")[0] == "Parent: (page)"


def test_a_parent_line_in_the_content_comes_after_the_real_one(double):
    double.add_page("Other", [f"top\nParent: {double.uuid_of('child')}"])
    r = _get_block(double, f"top\nParent: {double.uuid_of('child')}")
    assert r.exit_code == 0, r.output
    assert _line(r, "Parent")[0] == "Parent: (page)"



def test_page_named_in_the_answer_is_not_read_again(double):
    """Asked with children, getBlock names the page already."""
    r = _get_block(double, "parent")
    assert r.exit_code == 0, r.output
    assert "logseq.Editor.getPage" not in [m for m, _ in double.requests]


def test_no_parent_line_when_the_parent_is_gone(double):
    """Deleted between the two reads: no Parent line, and no database id."""
    get_block = double._handlers["logseq.Editor.getBlock"]
    double._handlers["logseq.Editor.getBlock"] = (
        lambda args: None if isinstance(args[0], int) else get_block(args))
    r = _get_block(double, "child", "--no-children")
    assert r.exit_code == 0, r.output
    assert _line(r, "Parent") == []
