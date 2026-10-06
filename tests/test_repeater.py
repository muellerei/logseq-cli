"""Next occurrence of a repeating task, by Logseq's own rule.

The interval grammar and the weekday rule come from
`frontend/handler/repeated.cljs` (0.10.12). The starting point does not, and
the difference is deliberate.

Logseq's `next-timestamp-text` runs at the moment a task is ticked off
(`update-timestamps-content!` in `handler/editor.cljs`), so the stored date is
near today and one step suffices:

    .+   add delta until the result is in the future; week intervals keep their
         weekday (repeat-until-future-timestamp)
    ++   add delta once, but only if the stored date is already past
    +    add delta once, unconditionally

"What is due" asks something else. Applied to a task that was never ticked off,
`+` and `++` land on a date that is still in the past — a weekly task from 2020
would answer 2020-01-13. So the single step is kept where it lands in the
future, and otherwise the `.+` loop runs for every form. Same interval, same
weekday rule, different starting point, because the question differs.

Derived rather than stored: Logseq keeps no "next occurrence" anywhere, and
:block/scheduled holds the date as written.
"""
import datetime

import pytest


from logseq_cli.dates import Timestamp, next_occurrence, parse_timestamp, timestamps


class TestNextOccurrence:
    TODAY = datetime.date(2026, 5, 15)

    def test_double_plus_advances_once_when_past(self):
        """++ adds one interval to a date that has gone by."""
        start = datetime.date(2026, 5, 10)
        assert next_occurrence(start, ("++", 1, "w"), self.TODAY) == datetime.date(2026, 5, 17)

    def test_double_plus_keeps_a_future_date(self):
        """A date still ahead is already the next occurrence."""
        start = datetime.date(2026, 5, 20)
        assert next_occurrence(start, ("++", 1, "w"), self.TODAY) == start

    def test_plus_keeps_stepping_until_it_reaches_the_future(self):
        """One step off a 2020 date is still 2020; the answer must be ahead.

        Logseq's own rule stops after one step, which is right at tick-off time
        and wrong for a query about what is due.
        """
        start = datetime.date(2020, 1, 6)
        result = next_occurrence(start, ("+", 1, "w"), self.TODAY)
        assert result > self.TODAY
        assert result.weekday() == start.weekday()

    def test_plus_stops_at_one_step_when_that_is_already_future(self):
        start = datetime.date(2026, 5, 10)
        assert next_occurrence(start, ("+", 1, "w"), self.TODAY) == datetime.date(2026, 5, 17)

    def test_dotted_repeats_until_future(self):
        """.+ loops forward until it lands after today."""
        start = datetime.date(2026, 5, 1)
        result = next_occurrence(start, (".+", 1, "w"), self.TODAY)
        assert result > self.TODAY
        assert result == datetime.date(2026, 5, 22)

    def test_dotted_over_years_still_terminates(self):
        """The 2020 weekly case from the reference graph."""
        start = datetime.date(2020, 1, 6)
        result = next_occurrence(start, (".+", 1, "w"), self.TODAY)
        assert result > self.TODAY
        assert result.weekday() == start.weekday(), "week repeats keep their weekday"

    def test_dotted_preserves_weekday_for_week_intervals(self):
        start = datetime.date(2026, 1, 5)  # a Monday
        result = next_occurrence(start, (".+", 2, "w"), self.TODAY)
        assert result.weekday() == 0

    def test_month_interval_clamps_to_the_shorter_month(self):
        """31 January plus one month is 28 February, as a calendar reads it."""
        from logseq_cli.dates import _add_interval
        assert _add_interval(datetime.date(2026, 1, 31), 1, "m") == datetime.date(2026, 2, 28)

    def test_month_repeat_lands_in_the_future(self):
        start = datetime.date(2026, 1, 31)
        assert next_occurrence(start, ("++", 1, "m"), self.TODAY) > self.TODAY

    def test_year_interval(self):
        start = datetime.date(2020, 3, 1)
        result = next_occurrence(start, ("++", 1, "y"), self.TODAY)
        assert result.month == 3 and result.day == 1

    def test_day_interval_steps_past_today(self):
        start = datetime.date(2026, 5, 10)
        result = next_occurrence(start, ("++", 3, "d"), self.TODAY)
        assert result > self.TODAY
        assert (result - start).days % 3 == 0, "still on the task's own 3-day grid"

    def test_unknown_unit_is_none(self):
        """Not guessed: an unparseable repeater is reported, not invented."""
        assert next_occurrence(datetime.date(2026, 5, 10), ("++", 1, "x"), self.TODAY) is None


D = datetime.date
T = datetime.time


class TestAnIntervalNoDateCanHold:
    """A repeater is text a person typed or pasted, so its number can be any
    size. The date it would lead to does not exist then, and the answer is
    "cannot be derived" (None), never an exception: one such block must not
    take get-todos down for the whole graph. Four sizes, four ways it failed:
    past the date range of a day count (11 digits), past the range of the year
    (5 digits, unit y), past what a date takes at all, and past the 4300 digits
    Python converts to an integer."""

    TODAY = datetime.date(2026, 10, 6)

    # 99999 days or weeks still land on a date; 99999 months and years do not.
    @pytest.mark.parametrize("digits,unit", [
        (digits, unit)
        for digits in ("99999", "9" * 11, "9" * 13, "9" * 5000)
        for unit in "dwmy"
        if not (digits == "99999" and unit in "dw")
    ], ids=lambda value: f"{len(value)}-digits" if value.startswith("9") else value)
    @pytest.mark.parametrize("kind", ["+", "++", ".+"])
    def test_is_read_as_a_repeater_and_has_no_next_occurrence(self, kind, digits, unit):
        stamp = parse_timestamp(f"2026-01-01 Thu {kind}{digits}{unit}")
        assert stamp is not None and stamp.repeater is not None, "still a repeater"
        assert (stamp.repeater[0], stamp.repeater[2]) == (kind, unit)
        assert next_occurrence(stamp.date, stamp.repeater, self.TODAY) is None

    def test_a_large_interval_that_a_date_can_hold_is_not_cut(self):
        stamp = parse_timestamp("2026-01-01 Thu .+100y")
        assert next_occurrence(stamp.date, stamp.repeater, self.TODAY) == datetime.date(2126, 1, 1)


class TestTimestamps:
    """Every SCHEDULED/DEADLINE of a block, each with the repeater of its own."""

    @pytest.mark.parametrize("content,expected", [
        ("SCHEDULED: <2020-01-06 Mon ++1w>",
         [Timestamp("SCHEDULED", D(2020, 1, 6), None, ("++", 1, "w"))]),
        ("SCHEDULED: <2024-03-01 Fri .+3d>",
         [Timestamp("SCHEDULED", D(2024, 3, 1), None, (".+", 3, "d"))]),
        ("DEADLINE: <2024-03-01 Fri +2m>",
         [Timestamp("DEADLINE", D(2024, 3, 1), None, ("+", 2, "m"))]),
        ("SCHEDULED: <2022-12-01 Thu 18:00 ++1w>",
         [Timestamp("SCHEDULED", D(2022, 12, 1), T(18, 0), ("++", 1, "w"))]),
        ("SCHEDULED: <2024-03-01 Fri>", [Timestamp("SCHEDULED", D(2024, 3, 1), None, None)]),
        ("SCHEDULED: <2026-09-24 Thu 18:00>",
         [Timestamp("SCHEDULED", D(2026, 9, 24), T(18, 0), None)]),
        ("TODO just a task", []),
        ("TODO pay rent\nDEADLINE: <2024-03-01 Fri ++1m>\n:LOGBOOK:\n:END:",
         [Timestamp("DEADLINE", D(2024, 3, 1), None, ("++", 1, "m"))]),
        ("SCHEDULED: <2026-09-24 Thu 10:00 .+2h>",
         [Timestamp("SCHEDULED", D(2026, 9, 24), T(10, 0), (".+", 2, "h"))]),
    ])
    def test_timestamps(self, content, expected):
        assert timestamps(content) == expected

    def test_two_timestamps_on_two_lines_each_with_its_own_repeater(self):
        got = timestamps("TODO x\nSCHEDULED: <2026-09-22 Tue .+1d>\nDEADLINE: <2026-09-23 Wed +1w>")
        assert [(t.kind, t.repeater) for t in got] == [
            ("SCHEDULED", (".+", 1, "d")), ("DEADLINE", ("+", 1, "w"))]

    def test_the_order_of_the_text_is_kept(self):
        got = timestamps("DEADLINE: <2026-09-23 Wed +1w>\nSCHEDULED: <2026-09-22 Tue .+1d>")
        assert [t.kind for t in got] == ["DEADLINE", "SCHEDULED"]

    def test_both_on_one_line(self):
        got = timestamps("SCHEDULED: <2026-09-22 Tue +1d> DEADLINE: <2026-09-23 Wed ++1w>")
        assert [(t.kind, t.repeater) for t in got] == [
            ("SCHEDULED", ("+", 1, "d")), ("DEADLINE", ("++", 1, "w"))]

    def test_an_indented_line_is_read(self):
        assert len(timestamps("TODO x\n  SCHEDULED: <2026-09-22 Tue +1d>")) == 1

    @pytest.mark.parametrize("content", [
        "TODO see SCHEDULED: <2026-09-22 Tue +1d> later",
        "```\nSCHEDULED: <2026-09-22 Tue +1d>\n```",
        "SCHEDULED: <2026-02-30 Fri +1d>",
        "", None,
    ], ids=["mid text", "code fence", "no such date", "empty", "none"])
    def test_no_timestamp(self, content):
        assert timestamps(content) == []


class TestParseTimestamp:
    @pytest.mark.parametrize("text,expected", [
        ("2026-09-24 Thu .+1d", Timestamp(None, D(2026, 9, 24), None, (".+", 1, "d"))),
        ("2026-09-24 Thu 18:00 ++1w", Timestamp(None, D(2026, 9, 24), T(18, 0), ("++", 1, "w"))),
        ("2026-09-24 Thu 18:00", Timestamp(None, D(2026, 9, 24), T(18, 0), None)),
        ("2026-09-24 Thu 9:05", Timestamp(None, D(2026, 9, 24), T(9, 5), None)),
        ("2026-09-24 Thu +2m", Timestamp(None, D(2026, 9, 24), None, ("+", 2, "m"))),
        ("2026-09-24", Timestamp(None, D(2026, 9, 24), None, None)),
        ("2026-09-24 Mon", Timestamp(None, D(2026, 9, 24), None, None)),
        ("2026-09-24 thu", Timestamp(None, D(2026, 9, 24), None, None)),
        ("2026-09-24 Thu -2d", Timestamp(None, D(2026, 9, 24), None, None)),
    ])
    def test_read(self, text, expected):
        assert parse_timestamp(text) == expected

    @pytest.mark.parametrize("text", ["2026-02-30 Fri", "tomorrow", "", "2026-09-24 Thu 25:00"])
    def test_none(self, text):
        assert parse_timestamp(text) is None
