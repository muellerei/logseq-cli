"""Under --dry-run --json, stdout is one JSON object and nothing else.

An agent checks a write with ``--dry-run --json`` and parses stdout with
``json.loads``. A text line after the object ("the journal page does not exist
yet...") makes that fail with "Extra data" while the exit code is 0: the batch
and the tree path of add-journal-block printed one on a journal page that did
not exist yet.

The rows come from ``ROWS`` in :mod:`tests.test_write_proof_every_command`,
which ``test_argument_table_is_complete`` keeps complete for every writing
command in the registry. ``EXTRA_ROWS`` adds the paths that row table does
not reach: a journal page that does not exist yet, for each path of the three
commands that write into a page section.
"""
import json

import pytest

from tests.test_write_proof_every_command import ROWS, _graph, _invoke, _writing_commands

# 2099-01-06 has no journal page in _graph(); 2099-01-05 has one with "## Log".
EXTRA_ROWS = [
    ("add-journal-block-batch-new-journal",
     ["add-journal-block", "--date", "2099-01-06", "--content", "a", "--content", "b",
      "--under-heading", "## Log"]),
    ("add-journal-block-batch-top-new-journal",
     ["add-journal-block", "--date", "2099-01-06", "--content", "a", "--content", "b",
      "--top-level"]),
    ("add-journal-block-tree-new-journal",
     ["add-journal-block", "--date", "2099-01-06", "--content", "parent\n\t- child",
      "--under-heading", "## Log"]),
    ("add-journal-block-tree-top-new-journal",
     ["add-journal-block", "--date", "2099-01-06", "--content", "parent\n\t- child",
      "--top-level"]),
    ("add-journal-block-flat-heading-new-journal",
     ["add-journal-block", "--date", "2099-01-06", "--content", "a",
      "--under-heading", "## Log"]),
    ("add-journal-block-upsert-new-journal",
     ["add-journal-block", "--date", "2099-01-06", "--content", "logged new",
      "--under-heading", "## Log", "--upsert-heading", "logged"]),
    ("add-journal-block-batch",
     ["add-journal-block", "--date", "2099-01-05", "--content", "a", "--content", "b",
      "--under-heading", "## Log"]),
    ("add-journal-content-heading-new-journal",
     ["add-journal-content", "--date", "2099-01-06", "--content", "- a\n- b",
      "--under-heading", "## Log"]),
    ("add-note-content-heading-new-page",
     ["add-note-content", "--page", "Fresh Page", "--content", "note one",
      "--under-heading", "## Heading"]),
]


def test_every_writing_command_has_a_row():
    assert _writing_commands() <= {args[0] for _, args, _ in ROWS}


@pytest.mark.parametrize("args", [pytest.param(a, id=i) for i, a, _ in ROWS]
                         + [pytest.param(a, id=i) for i, a in EXTRA_ROWS])
def test_dry_run_json_writes_one_json_object(monkeypatch, args):
    double = _graph().install(monkeypatch)
    r = _invoke(double, args, "--dry-run")
    assert r.exit_code == 0, r.stdout + r.stderr
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"stdout is not one JSON object ({e}): {r.stdout!r}")
    assert isinstance(payload, dict), r.stdout
    assert double.writes() == []
