"""--content-file on the four commands that took block text as --content only (#49).

``add-journal-block`` had it alone; a call reaching for it on ``update-block``
failed with "No such option". On these commands the option is ``--content``
read from a file, nothing more, so the tests hold exactly that: the same text
sent both ways must reach the graph through the same calls and print the same
answer. A test per behaviour (the #47 refusal, --dry-run, --keep-ids) would
restate what the --content tests already hold, and would miss the one way this
can break: a path where the two diverge.

``add-journal-block`` is not here. Its --content-file reads the file as one
tree, which --content does not, and tests/test_content_file.py holds that.
"""
from unittest.mock import patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import fake_api, split_runner

TEXT = "Alice's note: Größe geprüft\n\t- a child"
ONE_LINE = "Alice's note: Größe geprüft"
B1 = "6650d3a4-1b2c-4d5e-8f90-0a1b2c3d4e5f"

# (command, arguments before the content, text) — a text each command takes
# as one write, so the comparison runs through the write and not a refusal.
COMMANDS = [
    ("update-block", ["--id", B1], ONE_LINE),
    ("insert-block", ["--child-of", B1], TEXT),
    ("add-note-content", ["--page", "notes"], TEXT),
    ("add-journal-content", ["--date", "2026-08-03"], TEXT),
]


def _api():
    api = fake_api([f"u{i}" for i in range(1, 40)])
    block = {"uuid": B1, "content": "old", "page": {"id": 7},
             "parent": {"id": 7}, "properties": {}, "children": []}
    graph_get_block = api.get_block.side_effect

    def get_block(uuid, *args, **kwargs):
        got = graph_get_block(uuid, *args, **kwargs)
        return {**block, "children": got["children"]} if uuid == B1 else got

    api.get_block.side_effect = get_block
    api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    api.get_page.return_value = {"id": 7, "name": "notes", "originalName": "notes",
                                 "uuid": "p1"}
    api.get_page_blocks_tree.return_value = [block]
    api.append_block_in_page.return_value = {"uuid": "a1"}
    api.update_block.return_value = None
    api.datascript_query.return_value = []
    return api


def _writes(api):
    return [c for c in api.mock_calls
            if any(w in c[0] for w in ("insert", "update", "append"))]


def _run(args, input=None):
    api = _api()
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        result = split_runner().invoke(cli, args, input=input)
    return result, api


@pytest.mark.parametrize("command, before, text", COMMANDS, ids=[c[0] for c in COMMANDS])
class TestSameAsContent:
    def test_same_calls_and_output(self, command, before, text, tmp_path):
        f = tmp_path / "entry.md"
        f.write_text(text + "\n", encoding="utf-8")
        inline, inline_api = _run([command, *before, "--content", text])
        from_file, file_api = _run([command, *before, "--content-file", str(f)])
        assert inline.exit_code == 0, inline.output
        assert from_file.exit_code == 0, from_file.output
        assert _writes(inline_api), "the comparison must run through a write"
        assert file_api.mock_calls == inline_api.mock_calls
        assert from_file.stdout == inline.stdout

    def test_dash_reads_stdin(self, command, before, text):
        inline, inline_api = _run([command, *before, "--content", text])
        piped, piped_api = _run([command, *before, "--content-file", "-"],
                                input=text + "\n")
        assert piped.exit_code == 0, piped.output
        assert piped_api.mock_calls == inline_api.mock_calls

    def test_both_is_refused_before_any_write(self, command, before, text, tmp_path):
        f = tmp_path / "entry.md"
        f.write_text(text, encoding="utf-8")
        result, api = _run([command, *before, "--content", text, "--content-file", str(f)])
        assert result.exit_code != 0
        assert "not both" in result.output
        assert not _writes(api)

    def test_missing_file_is_refused_before_any_write(self, command, before, text, tmp_path):
        result, api = _run([command, *before, "--content-file", str(tmp_path / "gone.md")])
        assert result.exit_code != 0
        assert "--content-file not found" in result.output
        assert not _writes(api)


@pytest.mark.parametrize("command, before", [
    ("update-block", ["--id", B1]),
    ("add-note-content", ["--page", "notes"]),
    ("add-journal-content", ["--date", "2026-08-03"]),
])
def test_neither_is_an_error_that_names_both(command, before):
    """--content was required; it still is, in one of its two forms."""
    result, _ = _run([command, *before])
    assert result.exit_code != 0
    assert "--content" in result.output and "--content-file" in result.output


def test_file_text_meets_the_one_block_rule():
    """The file is --content: a flush "- " line is refused as it is inline (#47),
    not read as a tree the way add-journal-block --content-file reads it."""
    result, api = _run(["update-block", "--id", B1, "--content-file", "-"],
                       input="first\n- second\n")
    assert result.exit_code == 2, result.output
    api.update_block.assert_not_called()


def test_insert_block_content_file_and_tree_are_exclusive(tmp_path):
    f = tmp_path / "entry.md"
    f.write_text("x", encoding="utf-8")
    result, api = _run(["insert-block", "--child-of", B1,
                        "--content-file", str(f), "--tree", "- a"])
    assert result.exit_code != 0
    assert "--content-file" in result.output
    api.insert_block.assert_not_called()
    api.insert_batch_block.assert_not_called()


def test_the_file_text_is_passed_on_verbatim(tmp_path):
    """Only the trailing newline an editor adds goes; leading indentation and
    trailing spaces are the caller's text, as they are in --content."""
    from logseq_cli.helpers import content_or_file
    f = tmp_path / "entry.md"
    f.write_text("\tindented  \n", encoding="utf-8")
    assert content_or_file(None, str(f)) == "\tindented  "


def test_insert_block_with_nothing_to_insert_names_every_source():
    result, _ = _run(["insert-block", "--child-of", B1])
    assert result.exit_code != 0
    for option in ("--content", "--content-file", "--tree", "--tree-file"):
        assert option in result.output
