"""Tests for create-page refusing to write onto a page that already exists.

The defect this guards: ``create-page`` called Logseq's createPage without
asking whether the page was there. Logseq answers with the existing page, so a
second call reported ``created`` and exit 0 while creating nothing — and
``--content`` was appended to the page that was already there. An agent
retrying after a timeout duplicated content and was told the write succeeded.

The preview added alongside reports the same state the check reads, which is
why both live in one test module.
"""
import datetime
import json
import unicodedata
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


@pytest.fixture
def api():
    mock = MagicMock()
    mock.create_page.return_value = {"id": 1, "name": "new page"}
    mock.append_block_in_page.return_value = {"uuid": "u1"}
    with patch("logseq_cli.group.LogseqAPI", return_value=mock):
        yield mock


class TestExistingPageIsRefused:
    def test_existing_page_fails(self, api):
        api.get_page.return_value = {"id": 42, "name": "example"}
        r = CliRunner().invoke(cli, ["create-page", "--page", "Example"])
        assert r.exit_code != 0, r.output
        api.create_page.assert_not_called()

    def test_existing_page_does_not_append_content(self, api):
        """The duplication path: content must not land on the existing page."""
        api.get_page.return_value = {"id": 42, "name": "example"}
        r = CliRunner().invoke(
            cli, ["create-page", "--page", "Example", "--content", "second"])
        assert r.exit_code != 0, r.output
        api.append_block_in_page.assert_not_called()

    def test_error_is_json_when_asked(self, api):
        api.get_page.return_value = {"id": 42, "name": "example"}
        r = split_runner().invoke(
            cli, ["create-page", "--page", "Example", "--json"])
        assert r.exit_code != 0
        payload = json.loads(r.stderr)
        assert "error" in payload
        assert payload.get("page") == "Example"
        assert payload["reason"] == "page_exists"

    def test_absent_page_is_created(self, api):
        """An empty result from getPage means absent, and must still work."""
        api.get_page.return_value = None
        r = CliRunner().invoke(cli, ["create-page", "--page", "Fresh"])
        assert r.exit_code == 0, r.output
        api.create_page.assert_called_once()


class TestDryRun:
    def test_dry_run_writes_nothing(self, api):
        api.get_page.return_value = None
        r = CliRunner().invoke(
            cli, ["create-page", "--page", "Fresh", "--content", "x", "--dry-run"])
        assert r.exit_code == 0, r.output
        api.create_page.assert_not_called()
        api.append_block_in_page.assert_not_called()

    def test_dry_run_reports_the_existing_page(self, api):
        """The preview must name the state that would make the real run fail."""
        api.get_page.return_value = {"id": 42, "name": "example"}
        r = CliRunner().invoke(
            cli, ["create-page", "--page", "Example", "--dry-run", "--json"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["exists"] is True
        assert payload["would_create"] is False
        api.create_page.assert_not_called()

    def test_dry_run_reports_a_new_page(self, api):
        api.get_page.return_value = None
        r = CliRunner().invoke(
            cli, ["create-page", "--page", "Fresh", "--content", "x",
                  "--dry-run", "--json"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["exists"] is False
        assert payload["would_create"] is True
        assert payload["has_content"] is True


# --- against the HTTP double: the proof in LogseqAPI.create_page (spec 030) --

def _double(monkeypatch, *pages):
    from tests.logseq_http_double import LogseqHttpDouble
    double = LogseqHttpDouble()
    for name in pages:
        double.add_page(name, ["a block"])
    return double.install(monkeypatch)


def test_create_page_method_refuses_existing(monkeypatch):
    # Logseq answers createPage on a page that exists with that page and
    # drops the properties sent (M5). The commands ask first; the method
    # refuses too, for a caller that did not.
    from logseq_cli.api import LogseqAPI, PageExists
    double = _double(monkeypatch, "Probe Page")
    api = LogseqAPI(token="t")
    with pytest.raises(PageExists) as caught:
        api.create_page("probe page", {"k": "v"})
    assert caught.value.fields == {"page": "probe page"}
    assert double.writes() == []
    assert api.writes_landed == 0


def test_create_page_journal_answer_is_verified(monkeypatch):
    # M14b: a new journal under the name format_journal_date gives it, the
    # weekday in lower case, answers the page with Logseq's own spelling.
    from logseq_cli.api import LogseqAPI
    _double(monkeypatch)
    api = LogseqAPI(token="t")
    result = api.create_page("2099-01-05, monday")
    assert result["originalName"] == "2099-01-05, Monday"
    assert api.writes_landed == 1


def test_create_page_converts_journal_name(monkeypatch):
    # M14: "Jan 1st, 2099" in a graph of another format is created under the
    # graph's name and answered with null. create-page sends the graph's
    # name instead, in the preview, the write, --content and the output.
    from logseq_cli.dates import format_journal_date
    from tests.logseq_http_double import DATE_FORMAT
    name = format_journal_date(datetime.date(2099, 1, 1), DATE_FORMAT)
    assert name == "2099-01-01, thursday"
    double = _double(monkeypatch)
    r = _invoke(double, ["create-page", "--page", "Jan 1st, 2099", "--dry-run"])
    assert r.exit_code == 0, r.stderr
    assert json.loads(r.stdout)["page"] == name
    r = _invoke(double, ["create-page", "--page", "Jan 1st, 2099", "--content", "first"])
    assert r.exit_code == 0, r.stderr
    assert [a[0] for a in double.sent("createPage")] == [name]
    assert json.loads(r.stdout)["created"] == name
    assert double.tree("2099-01-01, Thursday") == [("first", [])]


def test_create_page_journal_that_exists_is_refused_in_preview_and_run(monkeypatch):
    # Converted before the check: otherwise the preview promised "would
    # create" for a journal the run then refused.
    double = _double(monkeypatch, "2099-01-05, Monday")
    r = _invoke(double, ["create-page", "--page", "Jan 5th, 2099", "--dry-run"])
    assert json.loads(r.stdout)["exists"] is True, r.stdout
    r = _invoke(double, ["create-page", "--page", "Jan 5th, 2099"])
    assert r.exit_code != 0
    assert json.loads(r.stderr)["reason"] == "page_exists"
    assert double.writes() == []


def test_create_page_proof_survives_unicode_normalisation(monkeypatch):
    # A name with decomposed umlauts (NFD, as macOS copies them) is found
    # under its composed form (NFC): the proof leaves resolving the name
    # to Logseq instead of comparing names itself.
    double = _double(monkeypatch)
    name = unicodedata.normalize("NFD", "Übersicht Größe")
    assert name != unicodedata.normalize("NFC", name)
    r = _invoke(double, ["create-page", "--page", name])
    assert r.exit_code == 0, r.stderr


@pytest.mark.parametrize("name,day", [
    ("Jan 1st, 2099", (2099, 1, 1)),            # MMM do, yyyy
    ("2099-01-01, Thursday", (2099, 1, 1)),     # yyyy-MM-dd, EEEE
    ("2099-01-01, monday", (2099, 1, 1)),       # a wrong weekday is not read
    ("2099-01-01", (2099, 1, 1)),               # yyyy-MM-dd
    ("01.01.2099", (2099, 1, 1)),               # dd.MM.yyyy
    ("mar 14th, 2025", (2025, 3, 14)),
    ("Weekly Review", None),
    ("2099-02-30", None),                       # no such day
    ("foo 1st, 2099", None),                    # no such month
])
def test_parse_journal_name_formats(name, day):
    from logseq_cli.dates import is_journal_date, parse_journal_name
    assert parse_journal_name(name) == (datetime.date(*day) if day else None)
    if day:
        # The same four formats is_journal_date recognises.
        assert is_journal_date(name)


def _invoke(double, args):
    return split_runner().invoke(cli, ["--token", "t", *args, "--json"])
