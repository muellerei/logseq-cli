"""The journal page name is an address, not a label.

``format_journal_date`` turns a date into the page title Logseq stores it
under, and nine call sites across the journal, edit and analysis commands
write through it. A wrong name does not produce a wrong-looking output — it
addresses a different page, so ``add-journal-entry`` creates one instead of
appending to the entry that is already there.

Nothing tested it. Making ``get_day_suffix`` return ``"th"`` for every day
left all 842 tests green, although it turns "mar 3rd, 2025" into
"mar 3th, 2025" under the default format Logseq ships with.

The ordinal rule is the part worth spelling out: 11, 12 and 13 take "th"
although they end in 1, 2 and 3. That exception is why the suffix cannot be
read off the last digit alone.
"""

import datetime

import pytest

from logseq_cli.dates import format_journal_date, get_day_suffix


class TestOrdinalSuffix:
    """st/nd/rd/th, including the teens that break the last-digit rule."""

    @pytest.mark.parametrize("day,expected", [
        (1, "st"), (2, "nd"), (3, "rd"), (4, "th"),
        (11, "th"), (12, "th"), (13, "th"),
        (21, "st"), (22, "nd"), (23, "rd"),
        (31, "st"),
    ])
    def test_suffix_for_day(self, day, expected):
        assert get_day_suffix(day) == expected

    def test_teens_differ_from_their_last_digit(self):
        """The rule a naive implementation gets wrong, stated on its own."""
        for teen, ones in ((11, 1), (12, 2), (13, 3)):
            assert get_day_suffix(teen) == "th"
            assert get_day_suffix(ones) != "th"

    def test_every_day_of_a_month_gets_a_known_suffix(self):
        for day in range(1, 32):
            assert get_day_suffix(day) in {"st", "nd", "rd", "th"}


class TestDefaultFormatIsLogseqs:
    """Without configuration the name must match what Logseq itself writes."""

    # The suffix rule itself is exercised above; what is checked here is that
    # the default format assembles month, day and year around it. One case per
    # distinct suffix plus one teen is enough for that -- 2nd/3rd/22nd and the
    # rest fall with any of these when the rule breaks.
    @pytest.mark.parametrize("date,expected", [
        (datetime.date(2025, 3, 1), "mar 1st, 2025"),
        (datetime.date(2025, 3, 4), "mar 4th, 2025"),
        (datetime.date(2025, 3, 12), "mar 12th, 2025"),
        (datetime.date(2025, 12, 31), "dec 31st, 2025"),
    ])
    def test_default_format(self, date, expected):
        assert format_journal_date(date) == expected

    def test_none_means_the_logseq_default(self):
        d = datetime.date(2025, 3, 3)
        assert format_journal_date(d, None) == format_journal_date(d)


class TestConfiguredFormats:
    """A graph may configure :journal/page-title-format; the tokens must hold."""

    @pytest.mark.parametrize("fmt,expected", [
        ("yyyy-MM-dd", "2025-03-03"),
        ("yyyy-MM-dd, EEEE", "2025-03-03, monday"),
        ("dd.MM.yyyy", "03.03.2025"),
        ("MMMM d, yyyy", "march 3, 2025"),
        ("MMM do, yyyy", "mar 3rd, 2025"),
        ("do MMMM yyyy", "3rd march 2025"),
    ])
    def test_format_tokens(self, fmt, expected):
        assert format_journal_date(datetime.date(2025, 3, 3), fmt) == expected

    def test_zero_padded_day_carries_no_ordinal(self):
        """``dd`` and ``do`` are different tokens; mixing them changes the name."""
        d = datetime.date(2025, 3, 3)
        assert format_journal_date(d, "dd") == "03"
        assert format_journal_date(d, "do") == "3rd"


class TestNameIsStableAcrossAYear:
    """Whatever the format, the same date must always give the same name.

    A page name that varies between two calls addresses two pages, which is
    the failure this whole module exists to prevent.
    """

    def test_every_day_of_a_year_is_stable_and_unique(self):
        seen = {}
        d = datetime.date(2025, 1, 1)
        while d.year == 2025:
            name = format_journal_date(d)
            assert name == format_journal_date(d), f"{d} not stable"
            assert name not in seen, f"{d} and {seen.get(name)} share a name"
            seen[name] = d
            d += datetime.timedelta(days=1)
        assert len(seen) == 365
