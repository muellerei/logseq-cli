"""A block id Logseq rejects is "not found", not a block.

For a malformed id ``getBlock`` answers HTTP 200 with ``{"error": "foo is not a
valid UUID string."}`` (measured, Logseq 0.10.15); for a well-formed unknown one
it answers ``null``. Every command guards with ``if not block``, which catches
the ``null`` and let the error object through as if it were a block: a mistyped
``--id`` went on to the write and reported success, and since property keys are
read by uuid it ended in a traceback instead.
"""
from unittest.mock import patch

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.cli import cli
from tests.conftest import split_runner

ERROR = {"error": "foo is not a valid UUID string."}


def test_get_block_turns_an_error_object_into_none():
    api = LogseqAPI(token="t")
    with patch.object(api, "call", return_value=ERROR):
        assert api.get_block("foo") is None


def test_get_block_passes_a_block_through():
    api = LogseqAPI(token="t")
    block = {"uuid": "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70", "content": "x"}
    with patch.object(api, "call", return_value=block):
        assert api.get_block(block["uuid"]) == block


@pytest.mark.parametrize("args", [
    ["update-block", "--id", "foo", "--content", "x"],
    ["update-block", "--id", "foo", "--content", "x", "--dry-run"],
    ["set-block-property", "--id", "foo", "--key", "k", "--value", "v", "--dry-run"],
    ["remove-property", "--id", "foo", "--key", "k", "--dry-run"],
    ["remove-block", "--id", "foo"],
])
def test_mistyped_id_is_reported_as_not_found_and_nothing_is_written(args):
    api = LogseqAPI(token="t")
    with patch.object(api, "call", return_value=ERROR) as call, \
            patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, args)
    assert r.exit_code == 1, r.output
    assert "not found" in r.stderr
    methods = [c.args[0] for c in call.call_args_list]
    assert methods == ["logseq.Editor.getBlock"], methods
