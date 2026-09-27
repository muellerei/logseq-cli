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
