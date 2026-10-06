"""A refused or unproven write reaches the caller with its reason.

click.ClickException carries no reason and, under --json, no JSON (measured:
stdout empty, stderr "Error: ..."), so an agent could not tell a block open
in the editor from a write Logseq ignored. Each type below has its reason;
the error handler adds what already landed in the call, from the API's own
count, so every refusal says whether a retry would write something twice.

The types are raised here from a mocked API method, through a real command,
so the tests hold the handler and not a particular write path.
"""
import json
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import mock_api, split_runner

BLOCK = "1f7fab12-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
MESSAGE = "The write was refused here."

# (type, reason, fields): the contract --json callers rely on.
TYPES = [
    ("BlockChanged", "block_changed", {"block": BLOCK}),
    ("EditorOpen", "open_in_editor", {"block": BLOCK, "page": "Probe Page"}),
    ("EditorStateUnknown", "editor_state_unknown", {"answer": '{"ok": 1}'}),
    ("LogseqWriteError", "logseq_error",
     {"method": "upsertBlockProperty", "logseq_message": "foo is not a valid UUID string."}),
    ("PageExists", "page_exists", {"page": "Probe Page"}),
    ("RenameRefused", "rename_refused", {"old": "Old Page", "new": "Taken Page", "why": "exists"}),
    ("ReadOnly", "read_only", {"source": ["config"], "config_path": "/x/config.toml"}),
    ("WriteNotVerified", "write_not_verified",
     {"method": "upsertBlockProperty", "target": "block 1f7fab12", "expected": "k:: v",
      "got": "k not set"}),
]

NOTHING = "Nothing was written."


def _refusal(name, fields):
    from logseq_cli import writerefused
    return getattr(writerefused, name)(MESSAGE, **fields)


def _run(error, *, as_json=True, landed=0):
    """set-block-property with its write raising ``error``."""
    api = mock_api(writes_landed=landed)
    api.get_block.return_value = {"uuid": BLOCK}
    api.upsert_block_property.side_effect = error
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, [
            "set-block-property", "--id", BLOCK, "--key", "k", "--value", "v",
            *(["--json"] if as_json else [])])


def test_each_type_has_its_reason():
    from logseq_cli import writerefused
    for name, reason, _ in TYPES:
        cls = getattr(writerefused, name)
        assert issubclass(cls, writerefused.WriteRefused), name
        assert cls.reason == reason, name
    # The API re-exports them.
    import logseq_cli.api
    assert logseq_cli.api.WriteNotVerified is writerefused.WriteNotVerified


@pytest.mark.parametrize("name,reason,fields", TYPES, ids=[t[0] for t in TYPES])
def test_refusal_under_json_is_an_error_object(name, reason, fields):
    r = _run(_refusal(name, fields))
    assert r.exit_code == 1
    assert r.stdout == ""
    error = json.loads(r.stderr)
    assert error == {"error": f"{MESSAGE} {NOTHING}", "reason": reason,
                     "writes_landed": 0, **fields}


@pytest.mark.parametrize("name,reason,fields", TYPES, ids=[t[0] for t in TYPES])
def test_refusal_without_json_goes_to_stderr(name, reason, fields):
    r = _run(_refusal(name, fields), as_json=False)
    assert r.exit_code == 1
    assert r.stdout == ""
    assert r.stderr == f"Error: {MESSAGE} {NOTHING}\n"


def test_refusal_with_mock_api_is_valid_json():
    # mock_api answers writes_landed with 0, as a fresh LogseqAPI does: the
    # handler must not choke on the mock, and says "Nothing" exactly once.
    r = _run(_refusal("WriteNotVerified", TYPES[-1][2]))
    error = json.loads(r.stderr)
    assert error["writes_landed"] == 0
    assert r.stderr.count(NOTHING) == 1


def test_refusal_names_the_writes_that_landed():
    # No rollback: a retry would write the landed ones again.
    r = _run(_refusal("EditorOpen", TYPES[0][2]), landed=2)
    error = json.loads(r.stderr)
    assert error["writes_landed"] == 2
    assert error["error"] == (f"{MESSAGE} 2 earlier write(s) in this call landed and "
                              "remain (no rollback); check before retrying.")
    assert NOTHING not in r.stderr


def test_base_type_has_no_reason():
    # Only the subclasses are raised; the base has nothing to report.
    from logseq_cli import writerefused
    assert not hasattr(writerefused.WriteRefused, "reason")


# --- the count the handler reads ---------------------------------------------

def test_writes_landed_counts_blocks_written(monkeypatch):
    # One per write, a batch with the number of its blocks: the count says
    # how much a retry would write again.
    from logseq_cli.api import LogseqAPI
    from tests.logseq_http_double import LogseqHttpDouble
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", ["alpha block"])
    api = LogseqAPI(token="t")
    assert api.writes_landed == 0
    api.append_block_in_page("Probe Page", "one")
    api.insert_block(double.uuid_of("alpha block"), "two")
    assert api.writes_landed == 2
    api.insert_batch_block(double.uuid_of("alpha block"),
                           [{"content": "three", "children": [{"content": "four"}]}],
                           {"sibling": True})
    assert api.writes_landed == 4


def test_every_write_method_goes_through_write():
    # _write is where the gate, the proof and the count sit; a wrapper that
    # sent its write with call() would pass all three by. Checked on the
    # source, so a new wrapper is held to it without a test of its own.
    import ast
    import inspect
    from logseq_cli import api
    tree = ast.parse(inspect.getsource(api))
    sent_by = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in api._METHODS):
            sent_by.setdefault(node.args[0].value, set()).add(node.func.attr)
    for method in api._MUTATING_METHODS:
        assert sent_by.get(method) == {"_write"}, (method, sent_by.get(method))
