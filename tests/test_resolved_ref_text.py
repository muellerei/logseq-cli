"""#98: --resolve-refs puts what Logseq shows of a Block Ref in its place.

Logseq 0.10.15 draws a ref as the target's title, its first line; it draws
the body only when the block has no title, and its properties only when it
has neither, the id never (``block-content`` with ``:block-ref?``). The CLI
inlined the target's whole text instead. Its property lines, the ``id::``
line first of all, then read as the ref block's own: an id taken from the
output for a write edited the target. Text after the ref landed behind the
target's last line, where after a closing fence Logseq drops it from view.
The resolved text now keeps to the ref's line.

The page came from ``getBlock``, which answers it as ``{id}`` alone when
asked without children (measured), so the ``↳ <page>`` the README promises
never showed. The page's name is read with ``getPage`` by that id. The
tests run against the HTTP double, which answers as Logseq does; the old
MagicMock tests answered with a name and passed.
"""
import json

import pytest

from logseq_cli.cli import cli
from logseq_cli.render import ref_text
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

TARGET = "00000000-0000-4000-8000-0000000000a1"
DEAD = "00000000-0000-4000-8000-0000000000d1"


def _read(monkeypatch, target_text, ref_block, *args):
    double = LogseqHttpDouble.installed(monkeypatch, {
        "Meeting Notes": [{"uuid": TARGET, "content": target_text}],
        "Probe": [ref_block],
    })
    result = split_runner().invoke(
        cli, ["get-page", "--page", "Probe", "--no-backlinks", "--resolve-refs", *args])
    return result, double


class TestTheResolvedText:
    def test_names_the_page_the_target_is_on(self, monkeypatch):
        result, _ = _read(monkeypatch, "TODO Draft the agenda", f"see (({TARGET})) here")
        assert result.exit_code == 0, result.stderr
        assert "- see TODO Draft the agenda ↳ Meeting Notes here" in result.stdout

    def test_carries_no_id_line_of_the_target(self, monkeypatch):
        result, _ = _read(monkeypatch, f"TODO Draft the agenda\nid:: {TARGET}", f"(({TARGET}))")
        assert result.exit_code == 0, result.stderr
        assert TARGET not in result.stdout
        assert "- TODO Draft the agenda ↳ Meeting Notes\n" in result.stdout

    def test_carries_no_property_and_no_body_of_a_target_with_a_title(self, monkeypatch):
        target = f"Title line\nfoo:: bar\nid:: {TARGET}\nbody line"
        result, _ = _read(monkeypatch, target, f"(({TARGET})) after")
        assert result.exit_code == 0, result.stderr
        assert "- Title line ↳ Meeting Notes after\n" in result.stdout
        assert "foo::" not in result.stdout and "body line" not in result.stdout

    def test_leaves_the_ref_blocks_own_lines_as_they_are(self, monkeypatch):
        target = f"Snippet\nid:: {TARGET}\n```\ncode\n```"
        result, _ = _read(monkeypatch, target, f"(({TARGET})) after\nmine:: 1")
        assert result.exit_code == 0, result.stderr
        assert "- Snippet ↳ Meeting Notes after\n  mine:: 1\n" in result.stdout

    def test_a_target_with_nothing_to_show_leaves_no_gap(self, monkeypatch):
        result, _ = _read(monkeypatch, f"id:: {TARGET}", f"see (({TARGET})) after")
        assert result.exit_code == 0, result.stderr
        assert "- see ↳ Meeting Notes after\n" in result.stdout

    def test_a_body_keeps_to_the_line_of_the_ref(self, monkeypatch):
        """A target without a title shows its body; spread over the ref
        block's lines, a code line would read as its property or its id."""
        result, _ = _read(monkeypatch, f"```\nid:: {TARGET}\n```", f"see (({TARGET})) after")
        assert result.exit_code == 0, result.stderr
        assert f"- see ``` id:: {TARGET} ``` ↳ Meeting Notes after\n" in result.stdout

    def test_json_carries_the_same_text(self, monkeypatch):
        result, _ = _read(monkeypatch, f"TODO Draft the agenda\nid:: {TARGET}",
                          f"see (({TARGET}))", "--json")
        assert result.exit_code == 0, result.stderr
        blocks = json.loads(result.stdout)["blocks"]
        assert blocks[0]["content"] == "see TODO Draft the agenda ↳ Meeting Notes"

    def test_looks_up_the_page_once_for_several_refs_to_it(self, monkeypatch):
        result, double = _read(monkeypatch, "TODO Draft the agenda",
                               f"(({TARGET})) and (({TARGET.upper()}))")
        assert result.exit_code == 0, result.stderr
        assert result.stdout.count("↳ Meeting Notes") == 2
        by_id = [a for a in double.sent("getPage") if isinstance(a[0], int)]
        assert len(by_id) == 1


class TestADeadRef:
    def test_a_placeholder_is_dead_not_resolved(self, monkeypatch):
        """Logseq keeps a placeholder, ``id:: <uuid>`` without a page, for a
        ref whose block does not exist (#70); it was inlined as text."""
        result, double = _read(monkeypatch, "x", f"see (({DEAD}))")
        assert DEAD in double.placeholders
        assert result.exit_code == 0, result.stderr
        assert f"- see (({DEAD}))\n" in result.stdout
        assert "id::" not in result.stdout
        assert "no longer exists" in result.stderr


@pytest.mark.parametrize("content,shown", [
    pytest.param("TODO Draft the agenda", "TODO Draft the agenda", id="title"),
    pytest.param("  indented", "indented", id="indented"),
    pytest.param("\n\nlater first line", "later first line", id="blank-first"),
    pytest.param("## TODO Heading task\nid:: x", "TODO Heading task", id="heading"),
    pytest.param("#tag first", "#tag first", id="tag"),
    pytest.param("Learn C# today", "Learn C# today", id="hash-inside"),
    pytest.param("Title\nfoo:: bar\nbody", "Title", id="title-props-body"),
    # The dates show with the title.
    pytest.param("TODO pay\nSCHEDULED: <2026-09-30 Wed>\nDEADLINE: <2026-10-01 Thu>\nbody",
                 "TODO pay SCHEDULED: <2026-09-30 Wed> DEADLINE: <2026-10-01 Thu>",
                 id="dates"),
    pytest.param("Title\n```\nSCHEDULED: <2026-09-30 Wed>\n```", "Title", id="date-in-code"),
    # A code block that nothing closes, and a one-line one, are text.
    pytest.param("```npm i``` then\nsecond", "```npm i``` then", id="one-line-code"),
    pytest.param("```\nunclosed", "```", id="unclosed-fence"),
    # No title (mldoc 1.5.7, the parser of 0.10.15): the body, on one line.
    pytest.param("```\ncode\n```", "``` code ```", id="fence"),
    pytest.param("~~~\ncode\n~~~", "~~~ code ~~~", id="tilde-fence"),
    pytest.param("  ```\ncode\n```", "``` code ```", id="indented-fence"),
    pytest.param("$$\nx\n$$", "$$ x $$", id="math"),
    pytest.param("#+BEGIN_QUOTE\nq\n#+END_QUOTE", "#+BEGIN_QUOTE q #+END_QUOTE", id="begin-block"),
    pytest.param("> quoted\n> more", "> quoted > more", id="quote"),
    pytest.param("| a |\n| - |\n| 1 |", "| a | | - | | 1 |", id="table"),
    pytest.param("```\ncode\n```\nid:: x", "``` code ```", id="fence-id"),
    pytest.param("```\nfoo:: bar\n```", "``` foo:: bar ```", id="property-in-code"),
    pytest.param("foo:: bar\nbody", "body", id="props-then-body"),
    pytest.param("foo:: bar\nline one\nline two", "line one line two", id="props-then-lines"),
    # Measured against mldoc 1.5.7: title or not.
    pytest.param("$$ unclosed\nmore", "$$ unclosed", id="math-unclosed"),
    pytest.param("$$ x $$ first\nsecond", "$$ x $$ first second", id="math-one-line"),
    pytest.param("#+BEGIN_QUOTE unclosed\nx", "#+BEGIN_QUOTE unclosed", id="begin-unclosed"),
    pytest.param("#+BEGIN_QUOTE\nq\n#+end_quote", "#+BEGIN_QUOTE q #+end_quote", id="begin-lower-end"),
    pytest.param("|x\nbody", "|x", id="row-open"),
    pytest.param("|x|\nbody", "|x| body", id="row"),
    pytest.param("<div>x</div>\nmore", "<div>x</div> more", id="html"),
    pytest.param("<2026 note\nmore", "<2026 note", id="not-html"),
    pytest.param("---\nmore", "--- more", id="rule"),
    pytest.param("--- x\nmore", "--- x", id="not-rule"),
    pytest.param("***", "***", id="stars"),
    # Neither: Logseq shows the properties, which here would read as the ref
    # block's own; nothing is shown.
    pytest.param("foo:: bar\nid:: x", "", id="props-only"),
    pytest.param("collapsed:: true", "", id="hidden-prop"),
    pytest.param("id:: x", "", id="id-only"),
])
def test_ref_text(content, shown):
    assert ref_text(content) == shown
