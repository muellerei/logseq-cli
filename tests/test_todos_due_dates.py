"""get-todos: when a task is due, as distinct from when it was noted.

``--from/--to`` date a task by the journal page it sits on — the day it was
written down. Logseq separately stores when a task is scheduled or due, and
this is the filter for those.

The repeating case decided the shape. Measured against a live graph: a task
with ``++1w`` carries ``:block/scheduled`` as the date written in its text, not
the next occurrence — a weekly task created in 2020 still reads 20200106.
Filtering on the stored date would place a live weekly task in the year it was
created.

The next occurrence is therefore derived, using Logseq's own formula from
``frontend/handler/repeated.cljs`` (see :mod:`tests.test_repeater`), and the
range is applied to that. A repeater whose interval cannot be read gets no
derived date: it is reported and left out rather than guessed at.
"""
import datetime
import json

import pytest
from click.testing import CliRunner
from unittest.mock import MagicMock, patch

from logseq_cli.cli import cli
from tests.conftest import split_runner


def _rows(*specs):
    """Each spec: (content, marker, page, journal_day, scheduled, deadline, repeated)."""
    rows = []
    for i, (content, marker, page, jd, sched, dead, rep) in enumerate(specs):
        block = {"content": content, "marker": marker, "uuid": f"u{i}"}
        if sched is not None:
            block["scheduled"] = sched
        if dead is not None:
            block["deadline"] = dead
        if rep:
            block["repeated?"] = True
        page_map = {"original-name": page, "name": page.lower()}
        if jd is not None:
            page_map["journal-day"] = jd
        rows.append([block, page_map])
    return rows


def _api(rows):
    api = MagicMock()
    api.datascript_query.return_value = rows
    return api


def _run(args, rows):
    with patch("logseq_cli.group.LogseqAPI", return_value=_api(rows)):
        return split_runner().invoke(cli, args)


DUE_SOON = _rows(
    ("TODO pay invoice", "TODO", "Finance", None, None, 20260520, False),
)
SCHEDULED_SOON = _rows(
    ("TODO call back", "TODO", "Work", None, 20260520, None, False),
)


class TestDueDatesInPayload:
    def test_deadline_is_surfaced(self):
        result = _run(["get-todos", "--json"], DUE_SOON)
        assert result.exit_code == 0, result.output
        todo = json.loads(result.stdout)["todos"][0]
        assert todo["deadline"] == "2026-05-20"

    def test_scheduled_is_surfaced(self):
        result = _run(["get-todos", "--json"], SCHEDULED_SOON)
        todo = json.loads(result.stdout)["todos"][0]
        assert todo["scheduled"] == "2026-05-20"

    def test_a_task_without_dates_carries_neither_key(self):
        """A graph that does not use these fields must see no change."""
        rows = _rows(("TODO plain", "TODO", "Notes", None, None, None, False))
        todo = json.loads(_run(["get-todos", "--json"], rows).stdout)["todos"][0]
        assert "scheduled" not in todo
        assert "deadline" not in todo


class TestDueRangeFilter:
    def test_due_from_excludes_earlier(self):
        result = _run(["get-todos", "--due-from", "2026-06-01", "--json"], DUE_SOON)
        assert json.loads(result.stdout)["todos"] == []

    def test_due_to_excludes_later(self):
        result = _run(["get-todos", "--due-to", "2026-05-01", "--json"], DUE_SOON)
        assert json.loads(result.stdout)["todos"] == []

    def test_task_inside_the_range_is_kept(self):
        result = _run(
            ["get-todos", "--due-from", "2026-05-01", "--due-to", "2026-05-31", "--json"],
            DUE_SOON)
        assert len(json.loads(result.stdout)["todos"]) == 1

    def test_scheduled_counts_as_due(self):
        """Either field puts a task in the range; a task has one or the other."""
        result = _run(
            ["get-todos", "--due-from", "2026-05-01", "--due-to", "2026-05-31", "--json"],
            SCHEDULED_SOON)
        assert len(json.loads(result.stdout)["todos"]) == 1

    def test_undated_task_falls_out_of_a_due_range(self):
        """Same rule as --from/--to: what cannot be shown to be inside, is out."""
        rows = _rows(("TODO plain", "TODO", "Notes", None, None, None, False))
        result = _run(["get-todos", "--due-from", "2026-01-01", "--json"], rows)
        assert json.loads(result.stdout)["todos"] == []

    def test_without_a_due_range_everything_is_returned(self):
        rows = DUE_SOON + _rows(("TODO plain", "TODO", "Notes", None, None, None, False))
        result = _run(["get-todos", "--json"], rows)
        assert len(json.loads(result.stdout)["todos"]) == 2

    def test_deadline_wins_when_a_task_carries_both(self):
        """A deadline is the commitment; a schedule is when work on it starts."""
        rows = _rows(("TODO both", "TODO", "Work", None, 20260101, 20260520, False))
        inside = _run(
            ["get-todos", "--due-from", "2026-05-01", "--due-to", "2026-05-31", "--json"],
            rows)
        assert len(json.loads(inside.stdout)["todos"]) == 1
        outside = _run(
            ["get-todos", "--due-from", "2025-12-01", "--due-to", "2026-01-31", "--json"],
            rows)
        assert json.loads(outside.stdout)["todos"] == []


class TestRepeatingTasks:
    """The stored date is the first occurrence; the next one is derived."""

    # The content matters here: the interval is read from the SCHEDULED line,
    # since :block/scheduled carries only the date.
    REPEATER = _rows(
        ("TODO weekly standup\nSCHEDULED: <2020-01-06 Mon ++1w>",
         "TODO", "Work", None, 20200106, None, True),
    )
    # A repeater whose interval cannot be read: flagged, but not derivable.
    OPAQUE = _rows(
        ("TODO opaque repeat", "TODO", "Work", None, 20200106, None, True),
    )

    def test_stored_date_no_longer_places_the_task(self):
        """A range around the stored 2020 date must not match a live repeater.

        This brackets the stored value on purpose: a range that simply misses
        it would pass whether or not repeaters are handled.
        """
        result = _run(
            ["get-todos", "--due-from", "2020-01-01", "--due-to", "2020-01-31", "--json"],
            self.REPEATER)
        assert json.loads(result.stdout)["todos"] == []

    def test_next_occurrence_is_derived_and_in_the_future(self):
        todo = json.loads(_run(["get-todos", "--json"], self.REPEATER).stdout)["todos"][0]
        assert todo["repeating"] is True
        assert todo["scheduled"] == "2020-01-06", "the stored date is kept as it is"
        nxt = datetime.date.fromisoformat(todo["next_due"])
        assert nxt > datetime.date.today()
        assert nxt.weekday() == 0, "a weekly repeat keeps its weekday"

    def test_a_range_around_the_next_occurrence_matches(self):
        todo = json.loads(_run(["get-todos", "--json"], self.REPEATER).stdout)["todos"][0]
        nxt = datetime.date.fromisoformat(todo["next_due"])
        result = _run(
            ["get-todos", "--due-from", str(nxt - datetime.timedelta(days=1)),
             "--due-to", str(nxt + datetime.timedelta(days=1)), "--json"],
            self.REPEATER)
        assert len(json.loads(result.stdout)["todos"]) == 1

    def test_unreadable_repeat_is_reported_not_guessed(self):
        result = _run(
            ["get-todos", "--due-from", "2026-05-01", "--due-to", "2026-05-31", "--json"],
            self.OPAQUE)
        payload = json.loads(result.stdout)
        assert payload["todos"] == []
        assert payload["repeating_excluded"] == 1
        assert "opaque repeat" in result.stderr
        assert "next_due" not in json.dumps(payload)

    def test_repeater_appears_normally_without_a_due_range(self):
        result = _run(["get-todos", "--json"], self.REPEATER)
        assert len(json.loads(result.stdout)["todos"]) == 1
        assert "repeating_excluded" not in json.loads(result.stdout)

    def test_a_repeater_does_not_disturb_the_noted_on_range(self):
        """--from/--to date by the journal page and are unaffected by repeats."""
        rows = _rows(
            ("TODO weekly", "TODO", "May 20th, 2026", 20260520, 20200106, None, True),
        )
        result = _run(["get-todos", "--from", "2026-05-01", "--to", "2026-05-31", "--json"],
                      rows)
        assert len(json.loads(result.stdout)["todos"]) == 1
