"""``create-page`` decides by the name whether a page is a journal.

``is_journal_date`` is what makes ``create-page`` pass ``journal?: true`` to
Logseq. Get it wrong in one direction and an ordinary page is filed as a
journal entry, which puts it in the journal timeline and changes how Logseq
treats it; get it wrong in the other and a date page is created as a plain
one, so the journal for that day exists twice.

Nothing tested it: making the function return ``True`` for every name left
all 842 tests green.

The recogniser and the formatter are two halves of one claim, so the central
test here does not restate the four formats by hand — it feeds
``format_journal_date`` output back in. A format the tool writes but does not
recognise is exactly the asymmetry that creates a duplicate journal page.
"""

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.helpers import format_journal_date, is_journal_date
from tests.conftest import split_runner


@pytest.fixture
def api():
    mock = MagicMock()
    mock.get_page.return_value = None
    mock.create_page.return_value = {"id": 1, "name": "p"}
    with patch("logseq_cli.group.LogseqAPI", return_value=mock):
        yield mock


class TestRecogniserMatchesFormatter:
    """Every name the tool writes must be read back as a journal name."""

    @pytest.mark.parametrize("fmt", [
        None,                 # Logseq's default, 'MMM do, yyyy'
        "MMM do, yyyy",
        "yyyy-MM-dd",
        "yyyy-MM-dd, EEEE",
        "dd.MM.yyyy",
    ])
    def test_formatter_output_is_recognised(self, fmt):
        d = datetime.date(2025, 3, 3)
        name = format_journal_date(d, fmt) if fmt else format_journal_date(d)
        assert is_journal_date(name), f"{name!r} written but not recognised"

    def test_holds_for_every_day_of_a_year(self):
        """Ordinal suffixes and zero padding vary across a year; all must match."""
        d = datetime.date(2025, 1, 1)
        misses = []
        while d.year == 2025:
            name = format_journal_date(d)
            if not is_journal_date(name):
                misses.append(name)
            d += datetime.timedelta(days=1)
        assert not misses, f"written but not recognised: {misses[:5]}"


class TestOrdinaryNamesAreNotJournals:
    """The other direction: a plain page must not be filed as a journal."""

    @pytest.mark.parametrize("name", [
        "Weekly Review",
        "meeting notes",
        "2025",                    # a year alone is not a date
        "mar 2025",                # no day
        "14th",                    # no month, no year
        # Anchored at both ends, so a date inside a longer name does not count.
        # These pass with re.search as well -- the ^...$ in the patterns is what
        # rejects them, not the match/search choice.
        "notes from 2025-03-14",
        "2025-03-14 review",
        "",
    ])
    def test_not_a_journal_name(self, name):
        assert not is_journal_date(name)


class TestCreatePageSetsTheFlag:
    """The user-visible half: what create-page hands to Logseq."""

    def test_journal_name_gets_the_property(self, api):
        result = split_runner().invoke(cli, ["create-page", "--name", "mar 3rd, 2025"])
        assert result.exit_code == 0, result.output
        _, kwargs_or_args = api.create_page.call_args[0][0], api.create_page.call_args[0]
        assert kwargs_or_args[1] == {"journal?": True}

    def test_ordinary_name_gets_no_property(self, api):
        result = split_runner().invoke(cli, ["create-page", "--name", "Weekly Review"])
        assert result.exit_code == 0, result.output
        assert api.create_page.call_args[0][1] is None

    def test_iso_date_name_gets_the_property(self, api):
        result = split_runner().invoke(cli, ["create-page", "--name", "2025-03-14"])
        assert result.exit_code == 0, result.output
        assert api.create_page.call_args[0][1] == {"journal?": True}
