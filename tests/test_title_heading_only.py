"""Content that is nothing but the page's title heading is refused.

The writers drop a ``# <page name>`` line from the content, since the page
already shows its name. Content that was only that line was left empty after
the removal, and the check for empty content had run before it: the value
passed, and ``add-journal-block`` wrote an empty block with exit 0, while
``add-note-content`` and ``add-journal-content`` reported "Added 0 block(s)"
with exit 0, dropping any ``--property`` with a warning. The check now runs on
the text as it is written, in the one function that removes the heading.
"""
import datetime
import json

import pytest

from logseq_cli.cli import cli
from logseq_cli.headings import TitleHeadingOnly, strip_title_heading
from logseq_cli.pagenames import journal_page_name
from tests.conftest import fake_api, split_runner

DAY = "2026-01-05"
PAGE = "Reading List"


def _api(monkeypatch):
    api = fake_api(["u1", "u2"])
    api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    api.get_page.return_value = {"name": PAGE.lower(), "originalName": PAGE, "uuid": "p1"}
    monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
    return api


def _journal(api):
    return journal_page_name(api, datetime.date.fromisoformat(DAY))


def _nothing_written(api):
    for method in ("insert_block", "insert_batch_block", "append_block_in_page",
                   "create_page", "update_block", "upsert_block_property"):
        getattr(api, method).assert_not_called()


class TestStripTitleHeading:
    def test_only_the_heading_is_refused(self):
        with pytest.raises(TitleHeadingOnly) as exc:
            strip_title_heading("# Reading List\n", PAGE)
        assert "only the page's title heading" in str(exc.value)

    def test_heading_with_text_below_keeps_the_text(self):
        assert strip_title_heading("# Reading List\nfirst entry", PAGE) == "first entry"

    def test_indented_lines_below_the_heading_keep_their_levels(self):
        # Only the first line lost its indentation, so "- first" became the
        # parent of "- second" instead of its sibling.
        content = "# Reading List\n  - first\n  - second\n    - detail"
        assert strip_title_heading(content, PAGE) == "- first\n- second\n  - detail"

    @pytest.mark.parametrize("content", [
        "intro\n# Reading List\nmore",
        "```markdown\n# Reading List\n```",
    ], ids=["later line", "code block"])
    def test_only_a_leading_heading_is_the_title(self, content):
        # The heading was removed wherever a line held it, in a code block too.
        assert strip_title_heading(content, PAGE) == content

    def test_a_leading_heading_after_blank_lines_is_the_title(self):
        assert strip_title_heading("\n\n# reading list\nfirst entry\n", PAGE) == "first entry"

    def test_empty_content_is_left_to_the_empty_check(self):
        # "" is refused as "--content is empty" by require_content where the
        # command checks it; this function only refuses what it emptied.
        assert strip_title_heading("", PAGE) == ""


class TestCommandsRefuse:
    @pytest.mark.parametrize("dry_run", [False, True])
    def test_add_journal_block(self, monkeypatch, dry_run):
        api = _api(monkeypatch)
        args = ["--token", "t", "add-journal-block", "--date", DAY, "--top-level",
                "--content", f"# {_journal(api)}"] + (["--dry-run"] if dry_run else [])
        result = split_runner().invoke(cli, args)
        assert result.exit_code != 0, result.stdout
        assert "only the page's title heading" in result.stderr
        _nothing_written(api)

    def test_add_journal_content(self, monkeypatch):
        api = _api(monkeypatch)
        result = split_runner().invoke(cli, [
            "--token", "t", "add-journal-content", "--date", DAY, "--top-level",
            "--content", f"# {_journal(api)}"])
        assert result.exit_code != 0, result.stdout
        assert "only the page's title heading" in result.stderr
        _nothing_written(api)

    def test_add_journal_entry(self, monkeypatch):
        api = _api(monkeypatch)
        result = split_runner().invoke(cli, [
            "--token", "t", "add-journal-entry", "--date", DAY,
            "--content", f"# {_journal(api)}"])
        assert result.exit_code != 0, result.stdout
        assert "only the page's title heading" in result.stderr
        _nothing_written(api)

    def test_add_note_content_with_property(self, monkeypatch):
        api = _api(monkeypatch)
        result = split_runner().invoke(cli, [
            "--token", "t", "add-note-content", "--page", PAGE,
            "--content", f"# {PAGE}", "--property", "status=open"])
        assert result.exit_code != 0, result.stdout
        assert "only the page's title heading" in result.stderr
        _nothing_written(api)


@pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
def test_refusal_is_an_error_object_under_json(monkeypatch, dry_run):
    # Reported through fail(), not as Click's usage dump, which no caller
    # can parse (CONTRIBUTING.md).
    api = _api(monkeypatch)
    result = split_runner().invoke(cli, [
        "--token", "t", "add-note-content", "--page", PAGE, "--content", f"# {PAGE}",
        "--json", *(["--dry-run"] if dry_run else [])])
    assert result.exit_code == 1, result.stderr
    error = json.loads(result.stderr)
    assert error["reason"] == "empty_content"
    assert error["page"] == PAGE
    assert "only the page's title heading" in error["error"]
    _nothing_written(api)


def test_indented_notes_below_the_title_are_written_as_siblings(monkeypatch):
    from tests.logseq_http_double import LogseqHttpDouble
    double = LogseqHttpDouble.installed(monkeypatch, {PAGE: ["existing"]})
    result = split_runner().invoke(cli, [
        "--token", "t", "add-note-content", "--page", PAGE, "--content",
        f"# {PAGE}\n  - first\n  - second", "--json"])
    assert result.exit_code == 0, result.stderr
    assert double.tree(PAGE) == [("existing", []), ("first", []), ("second", [])]
