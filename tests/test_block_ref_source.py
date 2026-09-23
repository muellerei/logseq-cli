"""#70: add-block-ref writes a ref only to a block that exists.

A ((ref)) to a uuid no block has renders as nothing, and the TODO it was
meant to carry over looks linked and is not. Measured against 0.10.15:

- getBlock answers null for an unknown uuid, for a malformed one and for a
  page's uuid.
- Once the page holding a dead ref has been read from its file, getBlock
  answers the ref's uuid with a placeholder, ``id:: <uuid>`` with no page. So
  a second ref to the same typo would pass a plain "is there a block" check.
- getBlock finds a block by its uuid in capitals too; ``(( <uuid>))`` with a
  space is no reference at all. The ref is written with the uuid Logseq hands
  back, so neither can reach the text.
"""
import json
import re
from unittest.mock import patch

import pytest

from logseq_cli.api import LogseqAPI, _MUTATING_METHODS
from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

SOURCE = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"
GHOST = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a71"
TYPO = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a7f"


def _graph():
    return page_graph_api(PageGraph({
        "Project": [{"uuid": SOURCE, "content": "TODO Ship it", "children": []}],
        "Target": [{"content": "## Refs", "children": []}],
    }, placeholders=[GHOST]))


def _run(args, api):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args)


# Every LogseqAPI wrapper of a writing endpoint, derived from the one list.
WRITERS = [re.sub(r"(?<!^)(?=[A-Z])", "_", m.rsplit(".", 1)[1]).lower()
           for m in _MUTATING_METHODS]


assert all(hasattr(LogseqAPI, name) for name in WRITERS)


def _writes(api):
    return sum(getattr(api, name).call_count for name in WRITERS)


TARGETS = {
    "page, heading": ["--page", "Target", "--under-heading", "## Refs"],
    "page, new heading": ["--page", "Target", "--under-heading", "## New"],
    "new journal": ["--journal-date", "2026-01-05", "--under-heading", "## Refs"],
}
MISSING = {
    "unknown uuid": lambda graph: TYPO,
    "placeholder of a dead ref": lambda graph: GHOST,
    "a page's uuid": lambda graph: graph.page_named("Project")["uuid"],
    "not a uuid, with an id line": lambda graph: f"A))\nid:: {TYPO}\n((",
}


@pytest.mark.parametrize("dry", [[], ["--dry-run"]], ids=["write", "dry-run"])
@pytest.mark.parametrize("target", TARGETS.values(), ids=TARGETS.keys())
@pytest.mark.parametrize("source", MISSING.values(), ids=MISSING.keys())
def test_a_ref_to_no_block_is_refused_before_anything_is_written(source, target, dry):
    api = _graph()
    r = _run(["add-block-ref", "--source-id", source(api.graph), *target, *dry], api)
    assert r.exit_code == 1, r.output
    assert "No block has the uuid" in r.stderr
    assert "Nothing was written" in r.stderr
    assert _writes(api) == 0
    assert api.graph.page_named("2026-01-05") is None


def test_the_refusal_is_json_under_json():
    r = _run(["add-block-ref", "--source-id", TYPO, "--page", "Target", "--json"], _graph())
    assert r.exit_code == 1
    assert json.loads(r.stderr) == {
        "error": f"No block has the uuid '{TYPO}': a ref to it would render as "
                 f"nothing. Nothing was written.",
        "source_id": TYPO}


def test_the_refusal_shows_what_the_input_hid():
    r = _run(["add-block-ref", "--source-id", f"\u200b{SOURCE}", "--page", "Target"],
             _graph())
    assert r.exit_code == 1
    assert f"'\\u200b{SOURCE}'" in r.stderr


@pytest.mark.parametrize("given", [f" {SOURCE}\n", f"(({SOURCE}))", f" (({SOURCE})) "],
                         ids=["whitespace", "brackets", "both"])
def test_the_ref_is_the_uuid_alone(given):
    api = _graph()
    r = _run(["add-block-ref", "--source-id", given, "--page", "Target",
              "--under-heading", "## Refs"], api)
    assert r.exit_code == 0, r.output
    assert api.insert_block.call_args.args[1] == f"(({SOURCE}))"


def test_the_ref_carries_the_uuid_logseq_hands_back():
    # getBlock finds a block by its uuid in capitals (measured); what is
    # written is the block's own uuid.
    api = _graph()
    api.get_block.side_effect = lambda u, **kw: api.graph.get_block(u.lower(), **kw)
    r = _run(["add-block-ref", "--source-id", SOURCE.upper(), "--page", "Target",
              "--under-heading", "## Refs", "--json"], api)
    assert r.exit_code == 0, r.output
    assert api.insert_block.call_args.args[1] == f"(({SOURCE}))"
    assert json.loads(r.stdout)["source_id"] == SOURCE


def test_dry_run_shows_the_source():
    r = _run(["add-block-ref", "--source-id", SOURCE, "--page", "Target", "--dry-run"],
             _graph())
    assert r.exit_code == 0, r.output
    assert "TODO Ship it" in r.stdout
