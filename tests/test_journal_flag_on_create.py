"""``create-page`` and every journal write create a page by name alone.

Logseq tells a journal by its name in the graph's date format: without any
property the page is a journal, with ``journalDay`` set and its file under
``journals/`` (M18, spec 030). The CLI used to send ``journal?: true`` as a
page property, and Logseq wrote that as a line ``journal?:: true`` at the top
of the file. No write sends it now.

Without the property, Logseq would start the page with an empty block, the
place the property line had taken; a command that writes right after asks
for no first block (``createFirstBlock: false``), and the file holds only
what was written. ``create-page`` without ``--content`` keeps the block:
without a first block and without text Logseq writes no file, and the page
would be lost the next time the graph is indexed.

``parse_journal_name`` must agree with ``format_journal_date`` for the
formats Logseq takes as journal titles in any graph: a name the CLI forms in
one of them reads back as the same day, so sending it under the graph's
name changes nothing (``pagenames.page_name_to_create``). The recogniser and
the formatter are two halves of one claim, so the central test here does not
restate the formats by hand — it feeds ``format_journal_date`` output back in.
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.dates import format_journal_date, parse_journal_name
from tests.conftest import split_runner
from tests.test_write_proof_every_command import ROWS, _graph, _invoke


@pytest.fixture
def api():
    mock = MagicMock()
    mock.get_page.return_value = None
    mock.create_page.return_value = {"id": 1, "name": "p"}
    # create-page sends a journal name as the graph spells it (M14); on a
    # bare MagicMock the format would be a mock, and the name sent empty.
    mock.get_user_configs.return_value = {"preferredDateFormat": "MMM do, yyyy"}
    with patch("logseq_cli.group.LogseqAPI", return_value=mock):
        yield mock


class TestRecogniserMatchesFormatter:
    """Every name the tool forms in these formats reads back as its day."""

    @pytest.mark.parametrize("fmt", [
        None,                 # Logseq's default, 'MMM do, yyyy'
        "MMM do, yyyy",
        "yyyy-MM-dd",
        "yyyy_MM_dd",
    ])
    def test_formatter_output_is_recognised(self, fmt):
        d = datetime.date(2025, 3, 3)
        name = format_journal_date(d, fmt) if fmt else format_journal_date(d)
        assert parse_journal_name(name) == d, f"{name!r} written but not recognised"

    def test_holds_for_every_day_of_a_year(self):
        """Ordinal suffixes and zero padding vary across a year; all must match."""
        d = datetime.date(2025, 1, 1)
        misses = []
        while d.year == 2025:
            name = format_journal_date(d)
            if parse_journal_name(name) != d:
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
        assert parse_journal_name(name) is None


def _sent_properties(call):
    """The properties a mocked ``create_page`` call passed, ``None`` if none."""
    args, kwargs = call
    return args[1] if len(args) > 1 else kwargs.get("properties")


class TestCreatePageSendsNoProperty:
    """The user-visible half: what create-page hands to Logseq."""

    def test_journal_name_sends_no_property(self, api):
        result = split_runner().invoke(cli, ["create-page", "--name", "mar 3rd, 2025"])
        assert result.exit_code == 0, result.output
        assert api.create_page.call_args[0][0] == "mar 3rd, 2025"
        assert _sent_properties(api.create_page.call_args) is None

    def test_ordinary_name_gets_no_property(self, api):
        result = split_runner().invoke(cli, ["create-page", "--name", "Weekly Review"])
        assert result.exit_code == 0, result.output
        assert _sent_properties(api.create_page.call_args) is None

    def test_iso_date_name_sends_no_property(self, api):
        result = split_runner().invoke(cli, ["create-page", "--name", "2025-03-14"])
        assert result.exit_code == 0, result.output
        # Sent in the graph's format, not as typed (M14).
        assert api.create_page.call_args[0][0] == "mar 14th, 2025"
        assert _sent_properties(api.create_page.call_args) is None


# --- against the HTTP double: what reaches Logseq ------------------------------

NEW_JOURNAL = "2099-01-06"


def _created(graph):
    """``(properties, options)`` of every createPage sent."""
    return [(a[1], a[2]) for a in graph.sent("createPage")]


def test_new_journal_sends_no_property_and_no_first_block(monkeypatch):
    graph = _graph().install(monkeypatch)
    result = _invoke(graph, ["add-journal-block", "--date", NEW_JOURNAL,
                             "--content", "first entry", "--top-level"])
    assert result.exit_code == 0, result.stderr
    [(properties, options)] = _created(graph)
    assert properties == {}
    assert options["createFirstBlock"] is False
    [page] = [p for p in graph.pages if p["journal_day"] == 20990106]
    assert graph.tree(page["name"]) == [("first entry", [])]


def test_create_page_without_content_keeps_first_block(monkeypatch):
    # Without a first block and without text Logseq writes no file (M18).
    graph = _graph().install(monkeypatch)
    result = _invoke(graph, ["create-page", "--page", "Fresh Page"])
    assert result.exit_code == 0, result.stderr
    [(properties, options)] = _created(graph)
    assert properties == {}
    assert options.get("createFirstBlock", True) is True
    assert graph.tree("Fresh Page") == [("", [])]


def test_create_page_with_content_has_no_empty_block(monkeypatch):
    graph = _graph().install(monkeypatch)
    result = _invoke(graph, ["create-page", "--page", "Fresh Page", "--content", "fresh text"])
    assert result.exit_code == 0, result.stderr
    [(_, options)] = _created(graph)
    assert options["createFirstBlock"] is False
    assert graph.tree("Fresh Page") == [("fresh text", [])]


def test_no_writer_sends_the_property(monkeypatch):
    # Every command that creates a page, on every path the write table
    # knows, plus a block ref into a new journal. Only create-page without
    # content keeps its first block; all others write right after.
    monkeypatch.delenv("LOGSEQ_JOURNAL_HEADING", raising=False)
    runs = [(row, args) for row, args, _ in ROWS]
    runs.append(("add-block-ref-new-journal",
                 ["add-block-ref", "--source-id", "@alpha block", "--journal-date", NEW_JOURNAL]))
    seen = set()
    for row, args in runs:
        graph = _graph().install(monkeypatch)
        _invoke(graph, args)
        for properties, options in _created(graph):
            seen.add(row)
            assert properties == {}, (row, properties)
            keeps_block = row == "create-page"
            assert options.get("createFirstBlock", True) is keeps_block, (row, options)
    assert {"add-journal-block-new-journal", "add-journal-content-new-journal",
            "add-journal-entry-new-journal", "add-note-content-new-page", "create-page",
            "create-page-content", "add-block-ref-new-journal"} <= seen, seen
