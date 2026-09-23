"""refuse_split_block: the text written as one block comes back as one.

The rule itself, with what was measured, is in test_one_block_round_trip.py.
Here: the way out each command names. A command that can write a tree sends
indented text down its tree path before this check runs, so for it an
indented sub-bullet never reaches the check; update-block writes one block
only, and refuses it.
"""

import pytest

from logseq_cli.blocktext import SplitBlockError, refuse_split_block

FLUSH = "**09:16** Header\n- point a\n- point b"
INDENTED = "**17:00** Main\n\t- sub 1\n\t- sub 2"
PLAIN = "**14:30** just a log line"
QUOTE = "> Quote line one\n> Quote line two"


class TestTreeCapableCommands:
    def test_flush_bullets_rejected_with_the_tree_ways(self):
        with pytest.raises(SplitBlockError) as e:
            refuse_split_block(FLUSH, command="add-journal-block")
        assert "--content several times" in str(e.value)
        assert "indent" in str(e.value)

    def test_plain_line_passes(self):
        refuse_split_block(PLAIN, command="insert-block")


class TestUpdateBlock:
    """update-block replaces ONE block: no newline bullet can become a child."""

    def test_indented_children_rejected(self):
        """The real-world failure: sub-bullets silently became raw text (2026-08-22)."""
        with pytest.raises(SplitBlockError) as e:
            refuse_split_block(INDENTED, command="update-block")
        msg = str(e.value)
        assert "update-block" in msg
        assert "insert-block --child-of" in msg

    def test_flush_bullets_rejected(self):
        with pytest.raises(SplitBlockError):
            refuse_split_block(FLUSH, command="update-block")

    def test_plain_line_passes(self):
        refuse_split_block(PLAIN, command="update-block")

    def test_multiline_blockquote_passes(self):
        """Blockquotes are legitimate multi-line block content — '> ' is not '- '."""
        refuse_split_block(QUOTE, command="update-block")
