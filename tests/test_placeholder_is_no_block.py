"""The placeholder of a dead ref is no block, for every command (#106).

Once Logseq reads a file again, it keeps a placeholder for each ref whose
block does not exist: getBlock answers a map with content ``id:: <uuid>``
and no page (measured, 0.10.15). Passed on, it looked like a block to every
``if not block`` guard; update-block and set-todo-status sent their write
to it, and get-block printed it. api.block_or_none reads it as no block.
"""
from unittest.mock import patch

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

GONE = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4aff"


def test_get_block_reads_a_placeholder_as_none():
    # null and the error object: test_get_block_error_object.
    api = LogseqAPI(token="t")
    placeholder = {"uuid": GONE, "content": f"id:: {GONE}", "children": []}
    with patch.object(api, "call", return_value=placeholder):
        assert api.get_block(GONE) is None


@pytest.fixture
def double(monkeypatch):
    # The page holds a ref to GONE, so the double keeps a placeholder for it.
    return LogseqHttpDouble.installed(monkeypatch, {"Page A": [f"see (({GONE}))"]})


@pytest.mark.parametrize("args", [
    ["update-block", "--id", GONE, "--content", "x"],
    ["set-block-property", "--id", GONE, "--key", "k", "--value", "v"],
    ["set-todo-status", "--id", GONE, "--status", "DONE"],
    ["get-block", "--id", GONE],
], ids=["update-block", "set-block-property", "set-todo-status", "get-block"])
def test_a_command_on_a_placeholder_finds_no_block(double, args):
    before = double.snapshot()
    r = split_runner().invoke(cli, ["--token", "t", *args])
    assert r.exit_code != 0, r.stdout
    assert "not found" in r.stderr.lower()
    assert double.writes() == [] and double.snapshot() == before
