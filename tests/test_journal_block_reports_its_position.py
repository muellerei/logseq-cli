"""add-journal-block reports where it wrote, not where it was asked to.

When the heading given to --under-heading could not be found or created, the
journal writers fell back to the top of the page, warned, and said so in
`position`. The batch path (several --content values) fell back as well but
reported `under '<heading>'`, so the output named a place the blocks were
not (#93).

Now the fallback is gone: a heading Logseq does not create fails
the command in the API's proof, before any block is written. So no position
is reported that the blocks do not have, on either path.
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


ARGS = ["--token", "t", "add-journal-block", "--date", "2026-01-05",
        "--under-heading", "## Log", "--json"]


@pytest.mark.parametrize("extra", [
    ["--content", "one"],
    ["--content", "one", "--content", "two"],
], ids=["single", "batch"])
def test_a_heading_not_created_fails_instead_of_moving_the_blocks(monkeypatch, extra):
    double = LogseqHttpDouble()
    double.add_page("2026-01-05, Monday", ["journal top"])
    double.set_mode("appendBlockInPage", "noop")
    double.install(monkeypatch)
    result = split_runner().invoke(cli, ARGS + extra)
    assert result.exit_code == 1
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert (error["reason"], error["method"]) == ("write_not_verified", "appendBlockInPage")
    assert double.tree("2026-01-05, Monday") == [("journal top", [])]
