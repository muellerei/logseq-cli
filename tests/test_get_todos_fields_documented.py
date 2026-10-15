"""get-todos --json: every key the payload can carry is named in --help and docs.

The field list exists twice by hand, in the command's epilog and in its row of
docs/commands.md, and the two had already drifted: ``hash`` was in the docs and
missing from --help. The keys are therefore taken from real output, not from a
third hand-kept list, so a new field fails here until both texts name it.
"""
import json
import re
from pathlib import Path
from unittest.mock import patch

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.test_get_todos import _mock_api_for_todos

DOCS = Path(__file__).resolve().parent.parent / "docs" / "commands.md"

# One task that carries every optional field: it stands on a journal page, is
# scheduled and due with a readable repeater, and is referenced from two other
# journals, of which --refs-limit 1 keeps one.
_TASK = ({"content": "TODO weekly review\nSCHEDULED: <2026-01-05 Mon ++1w>\n"
                     "DEADLINE: <2026-01-09 Fri ++1w>",
          "marker": "TODO", "uuid": "u-full",
          "scheduled": 20260105, "deadline": 20260109, "repeated?": True},
         {"original-name": "Jan 2nd, 2026", "name": "jan 2nd, 2026",
          "journal-day": 20260102})
_REFS = [({"uuid": "u-full"}, {"original-name": "Jan 12th, 2026", "journal-day": 20260112}),
         ({"uuid": "u-full"}, {"original-name": "Jan 19th, 2026", "journal-day": 20260119})]
# A repeater whose interval cannot be read: only a due range reports it, as
# repeating_excluded, so it needs a call of its own.
_OPAQUE = ({"content": "TODO opaque repeat", "marker": "TODO", "uuid": "u-opaque",
            "scheduled": 20260105, "repeated?": True},
           {"original-name": "Work", "name": "work"})


def _payload(args, todo_rows, ref_rows=()):
    with patch("logseq_cli.group.LogseqAPI",
               return_value=_mock_api_for_todos(list(todo_rows), list(ref_rows))):
        result = split_runner().invoke(cli, ["get-todos", *args, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _emitted_keys():
    full = _payload(["--refs-limit", "1"], [_TASK], _REFS)
    excluded = _payload(["--due-from", "2026-01-01", "--due-to", "2026-12-31"], [_OPAQUE])
    keys = set(full) | set(excluded)
    for todo in full["todos"]:
        keys |= set(todo)
    return keys


def _json_part(text, start):
    """The stretch of ``text`` that describes the payload, from ``start`` to
    "not null". Only there does naming a key count: elsewhere ``references``
    turns up in --refs-limit and ``page`` in --page, so a key dropped from the
    payload description would still be found."""
    begin = text.index(start)
    end = re.compile(r"not\s+null").search(text, begin)
    assert end, f"no 'not null' after {start!r}"
    return text[begin:end.start()]


def _help_text():
    result = split_runner().invoke(cli, ["get-todos", "--help"])
    assert result.exit_code == 0, result.output
    return _json_part(result.stdout, "--json:")


def _docs_row():
    rows = [line for line in DOCS.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `get-todos")]
    assert len(rows) == 1, f"expected one get-todos row in {DOCS.name}, found {len(rows)}"
    return _json_part(rows[0], "`--json` gives")


def test_the_fixture_reaches_every_optional_field():
    """Guards the two tests below: a key the fixture never produces is never checked."""
    assert {"journal_day", "scheduled", "deadline", "next_due", "repeating",
            "references", "references_withheld", "repeating_excluded",
            "hash"} <= _emitted_keys()


def test_help_names_every_json_key():
    text = _help_text()
    missing = sorted(k for k in _emitted_keys() if not re.search(rf"\b{k}\b", text))
    assert not missing, f"get-todos --help does not name: {missing}"


def test_docs_name_every_json_key():
    # Inside a code span, so a key counts only where the row names it as one:
    # `hash` or `{"todos": [...], "count": N}`, not the word in a sentence.
    spans = " ".join(re.findall(r"`([^`]+)`", _docs_row()))
    missing = sorted(k for k in _emitted_keys() if not re.search(rf"\b{k}\b", spans))
    assert not missing, f"{DOCS.name} get-todos row does not name: {missing}"
