"""A quote ends at a blank line, which text written as one quote rarely means (#50).

mldoc, Logseq's parser, takes every line after a ``>`` into the quote until a
blank line; the paragraph after it is plain text. The CLI writes the text as
sent and the block stays one block, so this is not what #47 refuses. It is a
Markdown trap, and the write says so on stderr.

The cases are the ones measured with mldoc 1.5.7, the version Logseq 0.10.15
pins (table in #50). A case mldoc reads as one quote, or as two quotes, gets
no note: a note on text that renders as meant would teach callers to ignore it.
"""
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.outlinetext import quote_break_lines
from tests.conftest import fake_api, split_runner

B1 = "6650d3a4-1b2c-4d5e-8f90-0a1b2c3d4e5f"
MISSING = "0b0b0b0b-1b2c-4d5e-8f90-0a1b2c3d4e5f"


@pytest.mark.parametrize("text, expected", [
    ("> first\n\nsecond", [3]),                    # the trap
    ("> first\n  \t\nsecond", [3]),                # a whitespace-only line is blank too
    ("intro\n> first\n\nsecond", [4]),             # the quote need not open the block
    ("> > first\n\nsecond", [3]),                  # nested
    ("  > first\n\nsecond", [3]),                  # indented quote marker
    ("> first\n\n\nsecond", [4]),                  # several blank lines
    ("> a\n\nb\n> c\n\nd", [3, 6]),                # every break is named
    ("> first\n>\n> second", []),                  # a ">" line keeps the quote going
    ("> first\nsecond", []),                       # lazy continuation: still quoted
    ("> first\n\n> second", []),                   # two quotes, both quoted
    ("> first\n\n", []),                           # nothing after the blank line
    ("> first\n\nk:: v", []),                      # a property, not quoted text
    ("```\n> first\n\nsecond\n```", []),           # code, not a quote
    ("first\n\nsecond", []),                       # no quote at all
    ("a > b\n\nc", []),                            # ">" not at the line's start
    ("> first\n\n```\ncode\n```", []),             # a code block after it is code
    ("> a\n\nb\n\nc", [3]),                         # only the paragraph right after the quote
    # From the adversarial review of #50, each checked against mldoc 1.5.7:
    ("> a\n\n>\nb", [3]),                           # ">" alone after the blank opens no quote
    ("> a\n\n> \nb", [3]),
    ("> a\n\nk:: v\nb", [4]),                       # the paragraph after the property
    ("> a\n\u00a0\nb", []),                          # NBSP is text to mldoc, the quote goes on
    ("> a\n\u2003\nb", []),
    ("> a\n \t \nb", [3]),                          # spaces and tabs are blank
    ("#+BEGIN_SRC python\n>>> x\n\nTraceback\n#+END_SRC", []),  # code
    ("#+BEGIN_EXAMPLE\n> a\n\nb\n#+END_EXAMPLE", []),
    ("> a\n\n#+BEGIN_QUOTE\nb\n#+END_QUOTE", []),  # a second quote
    # Second review: an org block counts only when closed by its own name,
    # and not inside code; a form feed line is blank; so is ">\r" after one.
    ("#+BEGIN_QUOTE\n> a\n\nb", [4]),                # never closed: no block
    ("> a\n\n#+BEGIN_SRC\nx\n\n> c\n\nd", [3, 8]),
    ("```\n#+BEGIN_SRC\n```\n> a\n\nb", [6]),        # the marker is code
    ("> a\n#+END_QUOTE\n\nb", [4]),                  # a stray end is text
    ("#+BEGIN_QUOTE\n#+BEGIN_SRC\nx\n#+END_SRC\n> a\n\nb\n#+END_QUOTE", []),
    ("```\n#+BEGIN_SRC\n```\n> a\n\nb\n#+END_SRC", [6]),    # a closer does not pair with code
    ("#+BEGIN_QUOTE\n#+BEGIN_SRC\n#+END_QUOTE\n> a\n\nb\n#+END_SRC", [6]),  # the outer block ends first
    ("> a\n\f\nb", [3]),
    ("> a\r\n\r\n>\r\nb", [3]),
])
def test_quote_break_lines(text, expected):
    assert quote_break_lines(text) == expected


def _api():
    api = fake_api([f"u{i}" for i in range(1, 40)])
    block = {"uuid": B1, "content": "old", "page": {"id": 7},
             "parent": {"id": 7}, "properties": {}, "children": []}
    graph_get_block = api.get_block.side_effect

    def get_block(uuid, *args, **kwargs):
        if uuid == MISSING:
            return None
        got = graph_get_block(uuid, *args, **kwargs)
        return {**block, "children": got["children"]} if uuid == B1 else got

    api.get_block.side_effect = get_block
    api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    page = {"id": 7, "name": "notes", "originalName": "notes", "uuid": "p1"}
    api.get_page.side_effect = lambda name, *a, **k: None if name == "new" else page
    api.get_page_blocks_tree.return_value = [block]
    api.append_block_in_page.return_value = {"uuid": "a1"}
    api.create_page.return_value = {"name": "new"}
    api.update_block.return_value = None
    api.datascript_query.return_value = []
    return api


def _run(args):
    api = _api()
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args), api


TRAP = "> first\n\nsecond"

# Every command that writes text the caller sends as one block. The note goes
# where the text arrives, before the write, so --dry-run shows it too.
# add-note-content and add-journal-content are not here: they read outline
# text, where every line is a block of its own, so no block they write holds
# a blank line (see test_outline_text_has_no_quote_to_break).
WRITERS = [
    ["update-block", "--id", B1, "--content", TRAP],
    ["insert-block", "--child-of", B1, "--content", TRAP],
    ["insert-block", "--child-of", B1, "--tree", '[{"content": "> first\\n\\nsecond"}]'],
    ["add-journal-block", "--date", "2026-08-03", "--content", TRAP],
    ["create-page", "--name", "new", "--content", TRAP],
    ["add-journal-entry", "--date", "2026-08-03", "--content", TRAP],
]


@pytest.mark.parametrize("args", WRITERS, ids=lambda a: " ".join(a[:1] + a[3:4]))
class TestEveryWriterSaysSo:
    def test_note_on_stderr_and_the_text_is_written(self, args):
        result, api = _run(args)
        assert result.exit_code == 0, result.output
        assert "Note:" in result.stderr and "quote" in result.stderr
        assert "line 3" in result.stderr
        assert ">" in result.stderr  # names the fix: start the blank line with ">"
        assert "Note:" not in result.stdout

    def test_dry_run_says_so_too(self, args):
        result, _ = _run(args + ["--dry-run"])
        assert result.exit_code == 0, result.output
        assert "quote" in result.stderr


@pytest.mark.parametrize("args", WRITERS, ids=lambda a: " ".join(a[:1] + a[3:4]))
def test_no_note_when_the_quote_is_kept(args):
    kept = [a.replace("\\n\\nsecond", "\\n>\\n> second") if a.startswith("[{")
            else a.replace(TRAP, "> first\n>\n> second") for a in args]
    result, _ = _run(kept)
    assert result.exit_code == 0, result.output
    assert "quote" not in result.stderr


def test_json_output_stays_parseable():
    import json
    result, _ = _run(["update-block", "--id", B1, "--content", TRAP, "--json"])
    assert result.exit_code == 0, result.output
    json.loads(result.stdout)
    assert "quote" in result.stderr


@pytest.mark.parametrize("text", [TRAP, "- > first\n\n  second"])
def test_outline_text_has_no_quote_to_break(text):
    """Outline text puts each line in a block of its own: the quote and the
    line after it are two blocks, and neither holds a blank line."""
    from logseq_cli.outlinetext import parse_hierarchical_content, quote_break_note
    tree = parse_hierarchical_content(text)
    assert all("\n" not in node["content"] for node in tree)
    assert quote_break_note(tree) is None


def test_a_child_node_is_checked_and_named():
    from logseq_cli.outlinetext import quote_break_note
    note = quote_break_note([{"content": "parent", "children": [
        {"content": "> quoted\n\nafter", "children": []}]}])
    assert note is not None and '"> quoted" line 3' in note


@pytest.mark.parametrize("args", [
    ["update-block", "--id", MISSING, "--content", TRAP, "--json"],
    ["create-page", "--name", "notes", "--content", TRAP, "--json"],
], ids=["update-block on a missing block", "create-page on a page that exists"])
def test_no_note_ahead_of_a_refusal(args):
    """A refusal under --json is one JSON object on stderr (AGENTS.md). A note
    in front of it made stderr unparseable, and spoke of text never written."""
    import json
    result, api = _run(args)
    assert result.exit_code != 0
    json.loads(result.stderr)


def test_no_note_in_a_preview_of_a_page_that_exists():
    result, _ = _run(["create-page", "--name", "notes", "--content", TRAP, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "quote" not in result.stderr


def test_the_note_counts_lines_of_the_text_as_written():
    """A dropped id:: line is gone before the note is worked out, so the line
    number is that of the text written, not of the text sent."""
    result, _ = _run(["add-journal-block", "--date", "2026-08-03", "--content",
                      "> a\nid:: 11111111-1b2c-4d5e-8f90-0a1b2c3d4e5f\n\nb"])
    assert result.exit_code == 0, result.output
    assert "line 3" in result.stderr
