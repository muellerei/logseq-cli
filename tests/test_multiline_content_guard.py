"""Tests for reject_unsupported_multiline: which commands may take a tree in --content."""

import pytest

from logseq_cli.helpers import (
    MultilineContentError,
    reject_unsupported_multiline,
)

FLUSH = "**09:16** Header\n- point a\n- point b"
INDENTED = "**17:00** Main\n\t- sub 1\n\t- sub 2"
PLAIN = "**14:30** just a log line"
QUOTE = "> Zitat Zeile eins\n> Zitat Zeile zwei"


class TestTreeCapableCommands:
    """insert-block / add-journal-block / add-note-content: indented content is the normal case."""

    def test_indented_children_pass(self):
        reject_unsupported_multiline(INDENTED, command="add-journal-block", accepts_tree=True)

    def test_flush_bullets_rejected(self):
        with pytest.raises(MultilineContentError) as e:
            reject_unsupported_multiline(FLUSH, command="add-journal-block", accepts_tree=True)
        assert "insert-block --tree" in str(e.value)

    def test_plain_line_passes(self):
        reject_unsupported_multiline(PLAIN, command="insert-block", accepts_tree=True)


class TestUpdateBlock:
    """update-block replaces ONE block: no newline bullet can become a child."""

    def test_indented_children_rejected(self):
        """The real-world failure: sub-bullets silently became raw text (2026-08-22)."""
        with pytest.raises(MultilineContentError) as e:
            reject_unsupported_multiline(INDENTED, command="update-block", accepts_tree=False)
        msg = str(e.value)
        assert "update-block" in msg
        assert "insert-block --child-of" in msg

    def test_flush_bullets_rejected(self):
        with pytest.raises(MultilineContentError):
            reject_unsupported_multiline(FLUSH, command="update-block", accepts_tree=False)

    def test_plain_line_passes(self):
        reject_unsupported_multiline(PLAIN, command="update-block", accepts_tree=False)

    def test_multiline_blockquote_passes(self):
        """Blockquotes are legitimate multi-line block content — '> ' is not '- '."""
        reject_unsupported_multiline(QUOTE, command="update-block", accepts_tree=False)
