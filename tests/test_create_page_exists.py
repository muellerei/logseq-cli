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


# --- against the HTTP double: the proof in LogseqAPI.create_page ------------

def _double(monkeypatch, *pages):
    from tests.logseq_http_double import LogseqHttpDouble
    return LogseqHttpDouble.installed(monkeypatch, {name: ["a block"] for name in pages})


def test_create_page_method_refuses_existing(monkeypatch):
    # Logseq answers createPage on a page that exists with that page and
    # drops the properties sent (measured). The commands ask first; the method
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
    # Measured: a new journal under the name format_journal_date gives it, the
    # weekday in lower case, answers the page with Logseq's own spelling.
    from logseq_cli.api import LogseqAPI
    _double(monkeypatch)
    api = LogseqAPI(token="t")
    result = api.create_page("2099-01-05, monday")
    assert result["originalName"] == "2099-01-05, Monday"
    assert api.writes_landed == 1


def test_create_page_converts_journal_name(monkeypatch):
    # Measured: "Jan 1st, 2099" in a graph of another format is created under the
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
    # A name with decomposed accents (NFD, as macOS copies them) is found
    # under its composed form (NFC): the proof leaves resolving the name
    # to Logseq instead of comparing names itself.
    double = _double(monkeypatch)
    name = unicodedata.normalize("NFD", "Café Crème")
    assert name != unicodedata.normalize("NFC", name)
    r = _invoke(double, ["create-page", "--page", name])
    assert r.exit_code == 0, r.stderr
    assert [a[0] for a in double.sent("createPage")] == [name]
    assert json.loads(r.stdout)["created"] == name


@pytest.mark.parametrize("name,day", [
    ("Jan 1st, 2099", (2099, 1, 1)),            # MMM do, yyyy
    ("2099-01-01", (2099, 1, 1)),               # yyyy-MM-dd
    ("2099_01_01", (2099, 1, 1)),               # yyyy_MM_dd
    ("mar 14th, 2025", (2025, 3, 14)),
    # A journal title only in a graph of that format, which Logseq then
    # creates and answers under that name.
    ("2099-01-01, Thursday", None),
    ("01.01.2099", None),
    ("Weekly Review", None),
    ("2099-02-30", None),                       # no such day
    ("foo 1st, 2099", None),                    # no such month
])
def test_parse_journal_name_formats(name, day):
    from logseq_cli.dates import parse_journal_name
    assert parse_journal_name(name) == (datetime.date(*day) if day else None)


def _invoke(double, args):
    return split_runner().invoke(cli, ["--token", "t", *args, "--json"])


# --- the name Logseq creates -------------------------------------------------
# createPage runs the name through create! (handler/page.cljs create!, read in
# the code): trimmed, [[...]] unwrapped, a leading # dropped, a slash at either
# end dropped. It answers the page under that name, and getPage under the name
# as sent finds nothing. The name goes as asked, since create! cleans it
# itself, in one pass that is not idempotent (test_page_name_created_once.py).
# A journal title Logseq takes as one whatever the graph's format (MMM do,
# yyyy; yyyy-MM-dd; yyyy_MM_dd; date_time_util.cljs safe-journal-title-formatters)
# becomes the journal under the graph's name, answered with null (measured).

@pytest.mark.parametrize("sent", ["[[Fresh Page]]", "#Fresh Page", "  Fresh Page ",
                                  "/Fresh Page/"])
def test_create_page_method_creates_under_the_name_logseq_gives(monkeypatch, sent):
    from logseq_cli.api import LogseqAPI
    double = _double(monkeypatch)
    api = LogseqAPI(token="t")
    result = api.create_page(sent)
    assert result["originalName"] == "Fresh Page"
    assert [a[0] for a in double.sent("createPage")] == [sent]
    assert api.writes_landed == 1


@pytest.mark.parametrize("sent", ["[[Probe Page]]", "#probe page", " Probe Page"])
def test_create_page_method_refuses_existing_under_the_name_logseq_gives(monkeypatch, sent):
    from logseq_cli.api import LogseqAPI, PageExists
    double = _double(monkeypatch, "Probe Page")
    with pytest.raises(PageExists):
        LogseqAPI(token="t").create_page(sent, {"k": "v"})
    assert double.writes() == []


def test_create_page_command_reports_the_name_logseq_gives(monkeypatch):
    double = _double(monkeypatch)
    r = _invoke(double, ["create-page", "--page", "[[Fresh Page]]", "--dry-run"])
    assert json.loads(r.stdout)["page"] == "Fresh Page"
    r = _invoke(double, ["create-page", "--page", "[[Fresh Page]]", "--content", "x"])
    assert r.exit_code == 0, r.stderr
    assert json.loads(r.stdout)["created"] == "Fresh Page"
    assert double.tree("Fresh Page") == [("x", [])]


def test_create_page_command_refuses_existing_under_the_name_logseq_gives(monkeypatch):
    double = _double(monkeypatch, "Probe Page")
    r = _invoke(double, ["create-page", "--page", "#Probe Page"])
    assert r.exit_code == 1
    assert json.loads(r.stderr)["reason"] == "page_exists"
    assert double.writes() == []


def test_create_page_method_converts_an_underscore_journal_title(monkeypatch):
    from logseq_cli.api import LogseqAPI
    double = _double(monkeypatch)
    result = LogseqAPI(token="t").create_page("2099_01_07")
    assert result["originalName"] == "2099-01-07, Wednesday"
    assert [a[0] for a in double.sent("createPage")] == ["2099-01-07, wednesday"]


@pytest.mark.parametrize("args", [
    ["add-note-content", "--page", "Jan 1st, 2099", "--content", "x"],
    ["insert-block", "--page", "Jan 1st, 2099", "--content", "x", "--keep-ids"],
    ["insert-block", "--page", "Jan 1st, 2099", "--content", "x"],
], ids=["add-note-content", "insert-block-keep-ids", "insert-block"])
def test_writes_to_a_new_journal_named_in_another_format(monkeypatch, args):
    # Created under the graph's name and written there, not reported as
    # not written. appendBlockInPage makes a missing page with an empty first
    # block of its own (measured), which the flat insert keeps.
    double = _double(monkeypatch)
    r = _invoke(double, args)
    assert r.exit_code == 0, r.stderr
    assert [b for b in double.tree("2099-01-01, Thursday") if b[0]] == [("x", [])]
    assert all(p["name"] != "Jan 1st, 2099" for p in double.pages)


def test_add_note_content_writes_to_an_existing_journal_named_in_another_format(monkeypatch):
    double = _double(monkeypatch, "2099-01-05, Monday")
    r = _invoke(double, ["add-note-content", "--page", "Jan 5th, 2099", "--content", "x"])
    assert r.exit_code == 0, r.stderr
    assert double.tree("2099-01-05, Monday") == [("a block", []), ("x", [])]
    assert double.sent("createPage") == []


def test_a_date_logseq_does_not_take_for_a_journal_is_a_page(monkeypatch):
    # dd.MM.yyyy is a journal title only in a graph of that format; Logseq
    # creates a page of that name, and so does the CLI.
    double = _double(monkeypatch)
    r = _invoke(double, ["create-page", "--page", "01.01.2099"])
    assert r.exit_code == 0, r.stderr
    assert [a[0] for a in double.sent("createPage")] == ["01.01.2099"]


def test_keep_ids_at_page_end_of_a_journal_named_in_another_format(monkeypatch):
    # The functions themselves, not only through a command: the page is
    # created under the name as asked and written under the name it got.
    from logseq_cli.api import LogseqAPI
    from logseq_cli.pagenames import page_to_write
    from logseq_cli.strictinsert import create_missing_page, insert_tree_keeping_ids
    double = _double(monkeypatch)
    api = LogseqAPI(token="t")
    target = page_to_write(api, "Jan 1st, 2099")
    create_missing_page(api, target)
    [uuid] = insert_tree_keeping_ids(api, [{"content": "x", "children": []}],
                                     "page_end", target.name)
    assert double.tree("2099-01-01, Thursday") == [("x", [])]
    assert double.uuid_of("x") == uuid


@pytest.mark.parametrize("extra", [(), ("--dry-run",)], ids=["run", "dry-run"])
@pytest.mark.parametrize("heading", [(), ("--under-heading", "## Log")], ids=["page", "heading"])
def test_add_note_content_json_names_the_page_written(monkeypatch, extra, heading):
    # "page" stays the name asked for, as every result names it; the page
    # written is in "position", the same in the run and its preview.
    double = _double(monkeypatch)
    r = _invoke(double, ["add-note-content", "--page", "Jan 1st, 2099", "--content", "x",
                         *heading, *extra])
    assert r.exit_code == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["page"] == "Jan 1st, 2099"
    where = "'2099-01-01, thursday'"
    assert result["position"] == (f"under '## Log' on {where}" if heading else where)


@pytest.mark.parametrize("heading", [(), ("--under-heading", "## Log")], ids=["page", "heading"])
def test_add_note_content_text_names_the_page_as_its_preview_does(monkeypatch, heading):
    double = _double(monkeypatch)
    args = ["--token", "t", "add-note-content", "--page", "Jan 1st, 2099", "--content", "x",
            *heading]
    preview = split_runner().invoke(cli, [*args, "--dry-run"])
    run = split_runner().invoke(cli, args)
    assert run.exit_code == 0, run.stderr
    where = "'2099-01-01, thursday'"
    where = f"under '## Log' on {where}" if heading else where
    assert f"Would add 1 block(s) to {where}\n" in preview.stdout
    assert f"Added 1 block(s) to {where}\n" in run.stdout
    assert double.tree("2099-01-01, Thursday")
