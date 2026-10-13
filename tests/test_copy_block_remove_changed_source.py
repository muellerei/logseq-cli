"""copy-block names its copy whenever it fails after the copy's root landed.

The copy is written block by block and the source removed last, with no
rollback: a failure after the root landed leaves a copy in the target that a
retry would copy again. The error object names it (``copied_id``) and the
message says "The copy is <uuid>.", so a caller checks it instead of
repeating the command.
"""
import json

import pytest
import requests

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

SOURCE = {"content": "source block", "children": ["first kid", "second kid", "third kid"]}


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(
        monkeypatch, {"Source Page": [SOURCE], "Target Page": ["target block"]})


def _move(double):
    uuid = double.uuid_of("source block")
    return split_runner().invoke(cli, ["--token", "t", "copy-block", "--id", uuid,
                                       "--to-page", "Target Page", "--remove", "--json"])


def _copy_root(double):
    """The uuid of the copy's root: the last top-level block of the target."""
    return double._require_page("Target Page")["blocks"][-1]["uuid"]


def test_a_child_copy_not_proven_names_the_copy(double):
    double.set_mode("insertBlock", "noop", from_call=2)
    r = _move(double)
    assert r.exit_code == 1
    error = json.loads(r.stderr)
    assert error["reason"] == "write_not_verified"
    assert error["copied_id"] == _copy_root(double)
    assert f"The copy is {_copy_root(double)}." in error["error"]
    assert double.tree("Target Page")[-1] == ("source block", [("first kid", [])])
    assert double.sent("removeBlock") == []


def test_a_timeout_at_the_removal_names_the_copy(double):
    def timeout(args):
        raise requests.Timeout("removeBlock timed out (test)")
    double._handlers["logseq.Editor.removeBlock"] = timeout
    r = _move(double)
    assert r.exit_code == 1
    error = json.loads(r.stderr)
    assert error["reason"] == "timeout"
    assert error["copied_id"] == _copy_root(double)
    assert f"The copy is {_copy_root(double)}." in error["error"]


def test_a_root_that_did_not_land_names_no_copy(double):
    double.set_mode("appendBlockInPage", "noop")
    r = _move(double)
    assert r.exit_code == 1
    error = json.loads(r.stderr)
    assert error["reason"] == "write_not_verified"
    assert "copied_id" not in error
    assert "The copy is" not in error["error"]
