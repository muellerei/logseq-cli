"""add-journal-block reports where it wrote, not where it was asked to.

When the heading given to --under-heading cannot be found or created, the
journal writers fall back to the top of the page, warn, and say so in
`position`. The batch path (several --content values) fell back as well but
reported `under '<heading>'`, so the output named a place the blocks were
not (#93).
"""
import json
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner


ARGS = ["add-journal-block", "--date", "2026-01-05", "--under-heading", "## Log", "--json"]


def _run(extra):
    api = page_graph_api(PageGraph({"Page A": []}))
    with patch("logseq_cli.group.LogseqAPI", return_value=api), \
         patch("logseq_cli.commands.journal.find_or_create_heading", return_value=None):
        return split_runner().invoke(cli, ARGS + extra)


@pytest.mark.parametrize("extra", [
    ["--content", "one"],
    ["--content", "one", "--content", "two"],
], ids=["single", "batch"])
def test_the_fallback_to_the_page_top_is_reported(extra):
    result = _run(extra)
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["position"] == "top-level (heading not found)"
    assert "Could not find or create '## Log'" in result.stderr
