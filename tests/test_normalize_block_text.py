"""One normalisation for every comparison of block text.

A proof compares what was sent with what Logseq reads back, and Logseq
rewrites a few things on the way: it trims spaces at the end (measured),
stores a ref target's id:: line (#95), and with time tracking on appends or
rewrites a :LOGBOOK: drawer on a marker change (upstream
editor.cljs:256-285, util/clock.cljs:75-93; read in the code, not
measured). None of these is a failed write, and a comparison that took
them for one raised a false alarm (#95, replace-text).

The drawer format is assumed from Logseq's file format, not measured: time
tracking is off in the measured graph. The CLOCK lines are the double's.
"""
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import CLOCK_IN, CLOCK_OUT, LogseqHttpDouble

UUID = "6a1d2e3f-4b5c-4d6e-8f70-8192a3b4c5d6"
DRAWER_IN = f":LOGBOOK:\n{CLOCK_IN}\n:END:"
DRAWER_OUT = f":LOGBOOK:\n{CLOCK_OUT}\n:END:"


def _normalize(text):
    from logseq_cli.blocktext import normalize_block_text
    return normalize_block_text(text)


def _matches(sent, read):
    from logseq_cli.blocktext import block_text_matches
    return block_text_matches(sent, read)


def test_id_lines_go():
    # #95: Logseq stores a ref target's id as a line of its text.
    assert _normalize(f"text\nid:: {UUID}") == "text"
    assert _normalize(f"text\nid:: {UUID}\nprio:: 1") == "text\nprio:: 1"


def test_an_id_line_in_code_stays():
    # Code, not a property (without_block_ids).
    text = f"text\n```\nid:: {UUID}\n```"
    assert _normalize(text) == text


def test_spaces_at_the_end_go():
    # Measured: a text ending in spaces is read back without them.
    assert _normalize("new  ") == "new"


def test_spaces_at_each_line_end_go():
    # Assumed, not measured: harmless, as both sides are normalised alike.
    assert _normalize("first  \nsecond\t") == "first\nsecond"


def test_every_logbook_drawer_goes():
    assert _normalize(f"DOING task\n{DRAWER_IN}") == "DOING task"
    assert _normalize(f"DONE task\n{DRAWER_OUT}\nprio:: 1") == "DONE task\nprio:: 1"
    assert _normalize(f"NOW task\n{DRAWER_OUT}\nnote\n{DRAWER_IN}") == "NOW task\nnote"


def test_text_without_any_of_it_is_unchanged():
    assert _normalize("TODO task\nprio:: 1") == "TODO task\nprio:: 1"


def test_a_rewritten_clock_line_matches():
    # set-todo-status sends the old text with its drawer; clock-out rewrites
    # the drawer's last CLOCK line (DOING -> DONE).
    assert _matches(f"DONE task\n{DRAWER_IN}", f"DONE task\n{DRAWER_OUT}")


def test_an_appended_drawer_matches():
    # Read in the code: a marker change to DOING appends a drawer the sender
    # never wrote.
    assert _matches("DOING task", f"DOING task\n{DRAWER_IN}")


def test_a_lost_drawer_does_not_match():
    # Presence, not content: the caller's drawer did not arrive.
    assert not _matches(f"DONE task\n{DRAWER_IN}", "DONE task")


def test_a_stored_id_and_trimmed_spaces_match():
    assert _matches("text  ", f"text\nid:: {UUID}")


def test_other_text_does_not_match():
    assert not _matches("new text", "old text")
    assert not _matches("new text", f"old text\n{DRAWER_IN}")


def test_replace_text_appended_logbook_is_success(monkeypatch):
    # With time tracking on (Logseq's default), TODO -> DOING appends a
    # drawer: the write landed, and replace-text must not report it failed.
    double = LogseqHttpDouble().install(monkeypatch)
    double.time_tracking = True
    double.add_page("Probe Page", ["TODO task one", "other"])
    r = split_runner().invoke(cli, ["--token", "t", "replace-text", "--page", "Probe Page",
                                    "--find", "TODO", "--replace", "DOING", "--json"])
    assert double.tree("Probe Page")[0] == ("DOING task one", [])
    assert r.exit_code == 0, r.stderr
    assert '"failed"' not in r.stdout


def test_whitespace_at_the_start_goes():
    # Measured, 0.10.15: leading spaces, a leading tab and leading blank
    # lines are read back without them; Logseq trims the
    # text on both sides (editor.cljs:1291-1296).
    assert _normalize("  indented") == "indented"
    assert _normalize("\ttabbed") == "tabbed"
    assert _normalize("\n\nafter blank lines") == "after blank lines"
    # Only at the start of the text: the lines after it keep their indent.
    assert _normalize("first\n  second") == "first\n  second"


def test_update_block_with_leading_whitespace_is_proven(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", ["old text"])
    uuid = double.uuid_of("old text")
    r = split_runner().invoke(cli, ["--token", "t", "update-block", "--id", uuid,
                                    "--content", "  new text", "--json"])
    assert r.exit_code == 0, r.stderr
    assert double.tree("Probe Page") == [("new text", [])]


def test_update_block_with_a_ref_to_itself_is_proven(monkeypatch):
    # Measured, 0.10.15: Logseq drops a ref to the
    # block from its own text ("see ((own)) here" is read back as
    # "see  here"; editor.cljs:323-324). A ref that can only point at
    # itself; the write landed as Logseq stores it.
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Probe Page", [{"content": "old text", "uuid": UUID}])
    r = split_runner().invoke(cli, ["--token", "t", "update-block", "--id", UUID.upper(),
                                    "--content", f"see (({UUID})) here", "--json"])
    assert r.exit_code == 0, r.stderr
    assert double.tree("Probe Page") == [("see  here", [])]
