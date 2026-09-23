"""An id:: line announced as dropped is not written.

Without --keep-ids the commands that take text check the parsed outline for
``id::`` lines and say they will be dropped. The removal ran over the raw text
instead, line by line, and missed a line the parse had read differently: a
bulleted ``\\t- id:: <uuid>`` one level deeper is a property of the block above
(the shape agents write, see parse_hierarchical_content), and the raw line
starts with ``- ``, so it stayed. The block then went out with its id line,
Logseq minted a fresh uuid (insertBlock and appendBlockInPage keep the text as
given, measured), and the file named a uuid the block did not have.

Checking and removing now run on the one parsed outline that is written.
"""
import pytest
from unittest.mock import patch

from logseq_cli.cli import cli
from tests.conftest import PageGraph, page_graph_api, split_runner

ID = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
ANCHOR = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"
CONTENT = f"- parent\n\t- id:: {ID}\n\t- child"
DATE = ["--date", "2026-01-05"]

WRITES = {
    "add-note-content": ["add-note-content", "--page", "Page A", "--content", CONTENT],
    "add-note-content, under heading": ["add-note-content", "--page", "Page A",
                                        "--under-heading", "## Log", "--content", CONTENT],
    "add-journal-content": ["add-journal-content", "--top-level", *DATE, "--content", CONTENT],
    "add-journal-content, under heading": ["add-journal-content", "--under-heading", "## Log",
                                           *DATE, "--content", CONTENT],
    "add-journal-block": ["add-journal-block", "--top-level", *DATE, "--content", CONTENT],
    "add-journal-block, under heading": ["add-journal-block", "--under-heading", "## Log",
                                         *DATE, "--content", CONTENT],
    "add-journal-block, batch": ["add-journal-block", "--top-level", *DATE,
                                 "--content", CONTENT, "--content", "second"],
    "add-journal-block, file": ["add-journal-block", "--top-level", *DATE,
                                "--content-file", "-"],
    "insert-block --page": ["insert-block", "--page", "Page A", "--content", CONTENT],
    "insert-block --after": ["insert-block", "--after", ANCHOR, "--content", CONTENT],
    "insert-block --child-of": ["insert-block", "--child-of", ANCHOR, "--content", CONTENT],
}


def _contents(graph):
    def walk(blocks):
        for b in blocks:
            yield b["content"]
            yield from walk(b["children"])
    return [c for p in graph.pages for c in walk(p["blocks"])]


@pytest.mark.parametrize("name", WRITES)
def test_the_announced_id_line_is_not_written(name):
    api = page_graph_api(PageGraph({"Page A": [
        {"uuid": ANCHOR, "content": "## Log", "children": [{"content": "entry"}]}]}))
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, WRITES[name],
                                  input=CONTENT if name.endswith("file") else None)
    assert r.exit_code == 0, r.stderr
    assert "will be dropped" in r.stderr
    written = _contents(api.graph)
    assert not [c for c in written if ID in c]
    assert "parent" in written and "child" in written


# What the commands echo (the dry-run preview, --json "content", the text
# preview) is derived from the outline that is written, so a dropped id does
# not reappear there. Removing lines from the raw text for the echo alone
# would bring back the drift fixed above.
ECHOES = {
    "add-journal-content, dry run": ["add-journal-content", "--top-level", *DATE,
                                     "--content", CONTENT, "--dry-run", "--json"],
    "add-journal-block, dry run": ["add-journal-block", "--top-level", *DATE,
                                   "--content", CONTENT, "--dry-run", "--json"],
    "add-journal-block, batch dry run": ["add-journal-block", "--top-level", *DATE,
                                         "--content", CONTENT, "--content", "second",
                                         "--dry-run", "--json"],
    "insert-block --after": ["insert-block", "--after", ANCHOR, "--content", CONTENT, "--json"],
    "insert-block --after, text": ["insert-block", "--after", ANCHOR, "--content", CONTENT],
}


@pytest.mark.parametrize("name", ECHOES)
def test_the_echo_does_not_show_the_dropped_id(name):
    api = page_graph_api(PageGraph({"Page A": [
        {"uuid": ANCHOR, "content": "## Log", "children": [{"content": "entry"}]}]}))
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, ECHOES[name])
    assert r.exit_code == 0, r.stderr
    assert "will be dropped" in r.stderr
    assert ID not in r.stdout
    assert "parent" in r.stdout


def test_the_echo_reads_back_as_the_outline_written():
    from logseq_cli.helpers import outline_text, parse_hierarchical_content, tree_without_block_ids
    tree = tree_without_block_ids(parse_hierarchical_content(
        f"- a\n  k:: v\n  id:: {ID}\n\t- b\n\t\t- c\n- d"))
    assert outline_text(tree) == "- a\n  k:: v\n\t- b\n\t\t- c\n- d"
    assert parse_hierarchical_content(outline_text(tree)) == tree


# add-journal-block writes a flat value after joining it (--no-preserve) and
# after strip_title_heading, which strips the text at both ends. Checked
# before that, "id:: <uuid>\r" (the last line of a CRLF file read through
# "$(cat ...)") was no id, and became one on the way to the write: with
# --keep-ids a uuid another block has went out with keepUUID.
FLAT_WRITTEN_LATER = {
    "carriage return": (["--content", f"Restored\nid:: {ID}\r"], True),
}


@pytest.mark.parametrize("name", FLAT_WRITTEN_LATER)
def test_a_flat_value_is_checked_as_written(name):
    args, keep_ids_allowed = FLAT_WRITTEN_LATER[name]
    api = page_graph_api(PageGraph({"Page A": [{"uuid": ID, "content": "original"}]}))
    base = ["add-journal-block", "--top-level", *DATE, *args]
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, base)
    assert r.exit_code == 0, r.stderr
    assert "will be dropped" in r.stderr
    assert not [c for c in _contents(api.graph) if ID in c]
    if keep_ids_allowed:
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, base + ["--keep-ids"])
        assert r.exit_code == 1
        assert "already belong" in r.stderr


def test_a_value_joined_into_an_id_line_is_checked_as_joined():
    # Joined by --no-preserve, "id::\n<uuid>" becomes the id line
    # "id:: <uuid>": checked as the text before joining, it was text, and
    # went out as an id. With nothing besides it, it is refused (#67).
    api = page_graph_api(PageGraph({"Page A": [{"uuid": ID, "content": "original"}]}))
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, ["add-journal-block", "--top-level", *DATE,
                                        "--no-preserve", "--content", f"id::\n{ID}"])
    assert r.exit_code == 1
    assert "nothing but id:: lines" in r.stderr
    assert not [c for c in _contents(api.graph) if ID in c]


# The text is prepared once and written as prepared. Stripping the title
# heading a second time, after the check, could lay bare an id line the check
# had read as text (a no-break space in front is text to Logseq, measured).
@pytest.mark.parametrize("extra", [[], ["--content", "second"]], ids=["single", "batch"])
@pytest.mark.parametrize("keep", [[], ["--keep-ids"]], ids=["plain", "keep-ids"])
def test_the_title_is_stripped_once(extra, keep):
    api = page_graph_api(PageGraph({"Page A": [{"uuid": ID, "content": "original"}]}))
    value = f" # 2026-01-05\n\u00a0id:: {ID}"
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, ["add-journal-block", "--top-level", *DATE,
                                        "--content", value, *extra, *keep])
    assert r.exit_code == 0, r.stderr
    assert api.graph.every_uuid().count(ID) == 1
    assert f"\u00a0id:: {ID}" in "\n".join(_contents(api.graph))
