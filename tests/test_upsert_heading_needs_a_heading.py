"""--upsert-heading without a heading is refused before anything is written.

The check stood after the journal page was looked up and, when missing,
created: a refused run left a new empty journal page behind. It also came
after the quote note (#50), so the note spoke of text that was never written.
"""
from unittest.mock import MagicMock, patch

from logseq_cli.cli import cli
from tests.conftest import split_runner


def _run(args):
    api = MagicMock()
    api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    api.get_page.return_value = None  # the journal page does not exist yet
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args), api


def test_refused_before_the_journal_page_is_created():
    result, api = _run(["add-journal-block", "--date", "2026-08-03", "--top-level",
                        "--upsert-heading", "### X", "--content", "text"])
    assert result.exit_code == 1
    assert "requires --under-heading" in result.stderr
    api.create_page.assert_not_called()


def test_no_note_about_text_that_is_not_written():
    result, _ = _run(["add-journal-block", "--date", "2026-08-03", "--top-level",
                      "--upsert-heading", "### X", "--content", "> first\n\nsecond"])
    assert result.exit_code == 1
    assert "quote" not in result.stderr
