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


from logseq_cli.helpers import parse_repeater, next_occurrence


class TestParseRepeater:
    def test_double_plus_week(self):
        assert parse_repeater("SCHEDULED: <2020-01-06 Mon ++1w>") == ("++", 1, "w")

    def test_dotted_day(self):
        assert parse_repeater("SCHEDULED: <2024-03-01 Fri .+3d>") == (".+", 3, "d")

    def test_plain_plus_month(self):
        assert parse_repeater("DEADLINE: <2024-03-01 Fri +2m>") == ("+", 2, "m")

    def test_with_a_time_before_the_repeater(self):
        assert parse_repeater("SCHEDULED: <2022-12-01 Thu 18:00 ++1w>") == ("++", 1, "w")

    def test_no_repeater_is_none(self):
        assert parse_repeater("SCHEDULED: <2024-03-01 Fri>") is None

    def test_no_timestamp_at_all_is_none(self):
        assert parse_repeater("TODO just a task") is None

    def test_deadline_line_among_others(self):
        content = "TODO pay rent\nDEADLINE: <2024-03-01 Fri ++1m>\n:LOGBOOK:\n:END:"
        assert parse_repeater(content) == ("++", 1, "m")


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
        from logseq_cli.helpers import _add_interval
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
