"""get-page --outline and --max-chars, get-journal-range --max-chars.

Large pages could only be read whole or cut blindly with ``head``, and the
outline was scraped with ``grep "- ##"`` plus one find-block per heading for
its uuid. Two ways out, one question behind both: how does a partial read say
what it left out?

What these tests pin, and why:

* A heading is what Logseq says is one. The API sets ``properties.heading``
  for ``## X``, ``heading:: true`` and ``heading:: 2`` alike, and not for a
  ``#tag`` or a ``##`` inside a code fence (measured on 0.10.15). The
  stand-ins below answer the same way, so a regex over ``content`` fails here.
* The outline is taken from the same block tree as the full read, not from a
  second query that could disagree with it.
* ``--max-chars`` bounds what is printed, in the format that is printed. A
  JSON block is several times its text form, so a cap counted on content
  would let ``--json`` overshoot by that factor.
* The cut falls between blocks, never inside one, and names what it withheld
  and the block it stopped before. ``--from-block`` continues there. An
  earlier draft suggested ``--heading`` instead, and review broke it three
  ways: a second heading of the same name, a heading rewritten by
  ``--resolve-refs``, and a section larger than the cap, where the follow-up
  read stops at the same block again.
* What lies past the cut is not printed. An earlier draft listed later pages
  and days with a placeholder each, and on short journal days the
  placeholders alone outgrew the cap.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner


def _heading(uuid, content, level, children=()):
    return {"uuid": uuid, "content": content, "properties": {"heading": level},
            "children": list(children)}


def _block(uuid, content, children=()):
    # No "properties" key: that is how the API answers for a plain block.
    return {"uuid": uuid, "content": content, "children": list(children)}


def _page_tree():
    return [
        _heading("h-intro", "# Intro\ncollapsed:: true", 1, [
            _block("b-1", "first note"),
            _heading("h-sub", "### Sub", 3, [_block("b-2", "deep note")]),
        ]),
        _block("p-plain", "plain parent", [
            _heading("h-nested", "## Nested heading", 2),
        ]),
        _heading("h-auto", "auto\nheading:: true", True),
        _block("b-tag", "#tag is not a heading"),
        _block("b-fence", "```\n## in a fence\n```"),
    ]


def _run(args, tree, *, linked=None):
    api = MagicMock()
    api.get_page.return_value = {"name": "p"}
    api.get_page_blocks_tree.return_value = tree
    api.get_page_linked_references.return_value = linked or []
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        result = split_runner().invoke(cli, args)
    return result, api


def _outline(extra=(), tree=None):
    return _run(["get-page", "--name", "p", "--outline", *extra],
                tree if tree is not None else _page_tree())


class TestOutline:
    def test_lists_only_blocks_logseq_marks_as_headings(self):
        result, _ = _outline()
        assert result.exit_code == 0, result.output
        uuids = [line.split("\t")[0] for line in result.stdout.splitlines()
                 if "\t" in line]
        assert uuids == ["h-intro", "h-sub", "h-nested", "h-auto"]

    def test_each_line_carries_the_uuid_and_the_first_line_only(self):
        result, _ = _outline()
        lines = [l for l in result.stdout.splitlines() if "\t" in l]
        assert lines[0] == "h-intro\t\t# Intro"
        assert lines[3] == "h-auto\t\tauto"
        assert "collapsed::" not in result.stdout

    def test_depth_counts_heading_ancestors_only(self):
        """A heading under a plain block moves up to where the plain block
        stood; a heading under a heading is indented one tab."""
        result, _ = _outline()
        by_uuid = {l.split("\t")[0]: l for l in result.stdout.splitlines() if "\t" in l}
        assert by_uuid["h-sub"] == "h-sub\t\t\t### Sub"
        assert by_uuid["h-nested"] == "h-nested\t\t## Nested heading"

    def test_combines_with_heading_to_outline_one_section(self):
        result, _ = _outline(["--heading", "# Intro"])
        uuids = [l.split("\t")[0] for l in result.stdout.splitlines() if "\t" in l]
        assert uuids == ["h-intro", "h-sub"]

    def test_json_keeps_the_heading_blocks_and_drops_the_rest(self):
        result, _ = _outline(["--json"])
        data = json.loads(result.stdout)
        top = data["blocks"]
        assert [b["uuid"] for b in top] == ["h-intro", "h-nested", "h-auto"]
        assert [c["uuid"] for c in top[0]["children"]] == ["h-sub"]
        assert top[0]["children"][0]["children"] == []
        # The block itself is the one the full read returns, not a summary.
        assert top[0]["content"] == "# Intro\ncollapsed:: true"

    def test_does_not_look_up_backlinks(self):
        _, api = _outline()
        api.get_page_linked_references.assert_not_called()

    def test_refs_are_resolved_only_in_the_headings_printed(self):
        """The outline is cut before resolving: a ref in a dropped block would
        cost a lookup and never be printed."""
        ref = "11111111-2222-3333-4444-555555555555"
        tree = [_heading("h", f"## See (({ref}))", 2, [_block("b", f"body (({ref}))")]),
                _block("c", f"more (({ref}))")]
        result, api = _outline(["--resolve-refs"], tree=tree)
        assert result.exit_code == 0, result.output
        assert api.get_block.call_count == 1

    def test_a_page_without_headings_says_so(self):
        result, _ = _outline(tree=[_block("a", "just text")])
        assert result.exit_code == 0
        assert "(no headings)" in result.stdout

    def test_markdown_format_is_refused(self):
        """Markdown has nowhere to put the uuid, which is what the outline is for."""
        result, _ = _outline(["--format", "markdown"])
        assert result.exit_code != 0
        assert "--outline" in result.stderr
        assert result.stdout == ""


def _big_tree():
    """Three sections of ten blocks, each block about a hundred characters."""
    tree = []
    for s in range(3):
        children = [_block(f"s{s}-b{i}", f"section {s} block {i} " + "x" * 80)
                    for i in range(10)]
        tree.append(_heading(f"s{s}", f"## Section {s}", 2, children))
    return tree


def _capped(extra, cap=1500, tree=None):
    return _run(["get-page", "--name", "p", "--no-backlinks",
                 "--max-chars", str(cap), *extra],
                tree if tree is not None else _big_tree())


FORMATS = {
    "text": [],
    "markdown": ["--format", "markdown"],
    "with-ids": ["--with-ids"],
    "json": ["--json"],
    "outline": ["--outline"],
}


class TestMaxCharsOnGetPage:
    @pytest.mark.parametrize("fmt", FORMATS, ids=list(FORMATS))
    def test_stdout_stays_within_the_cap_in_every_format(self, fmt):
        cap = 1500 if fmt != "outline" else 30
        result, _ = _capped(FORMATS[fmt], cap=cap)
        assert result.exit_code == 0, result.output
        assert len(result.stdout) <= cap
        assert "withheld" in result.stderr

    @pytest.mark.parametrize("fmt", ["text", "json"])
    def test_the_cap_uses_most_of_the_room(self, fmt):
        """Guards against a cut that is merely safe: stopping after the first
        block would pass the bound above."""
        result, _ = _capped(FORMATS[fmt], cap=1500)
        assert len(result.stdout) > 1500 - 400

    def test_json_counts_its_own_size_not_the_content(self):
        text, _ = _capped([], cap=1500)
        as_json, _ = _capped(["--json"], cap=1500)
        text_blocks = sum(1 for l in text.stdout.splitlines() if "block" in l)
        json_blocks = as_json.stdout.count('"uuid": "s')
        assert json_blocks < text_blocks

    def test_the_cut_falls_between_blocks(self):
        result, _ = _capped([])
        body = [l.strip() for l in result.stdout.splitlines()
                if l.strip().startswith("- ")]
        whole = {"- " + b["content"] for s in _big_tree()
                 for b in [s] + s["children"]}
        assert body and all(line in whole for line in body)

    def test_stderr_names_what_was_withheld(self):
        result, _ = _capped([])
        printed = sum(1 for l in result.stdout.splitlines() if l.strip().startswith("- "))
        assert 0 < printed < 33
        assert f"{33 - printed} block(s) withheld" in result.stderr
        assert "--max-chars" in result.stderr

    def test_the_cut_names_the_block_and_the_section_it_stopped_in(self):
        result, _ = _capped(["--json"])
        page = json.loads(result.stdout)
        cut = page["cut"]
        printed = {b["uuid"] for s in page["blocks"] for b in [s] + s["children"]}
        assert cut["before"] not in printed
        section = cut["before"].split("-")[0]
        assert cut["section"] == f"## Section {section[1:]}"
        assert cut["section_uuid"] == section
        assert cut["later"] == 0
        assert f"--from-block {cut['before']}" in result.stderr
        assert f"'## Section {section[1:]}'" in result.stderr

    def test_a_cut_on_a_heading_names_that_heading(self):
        tree = _big_tree()
        first, _ = _run(["get-page", "--name", "p", "--no-backlinks"], [tree[0]])
        result, _ = _capped([], cap=len(first.stdout) + 5, tree=tree)
        assert "section 0 block 9" in result.stdout
        assert "Section 1" not in result.stdout
        assert "'## Section 1'" in result.stderr
        assert "--from-block s1" in result.stderr

    def test_a_cut_outside_any_section_names_no_heading(self):
        tree = [_block(f"b{i}", "y" * 100) for i in range(20)]
        result, _ = _capped(["--json"], cap=1500, tree=tree)
        page = json.loads(result.stdout)
        assert page["cut"]["section"] is None
        assert page["cut"]["section_uuid"] is None
        assert "outside any section" in result.stderr

    def test_no_cut_no_note_and_no_fields(self):
        plain, _ = _run(["get-page", "--name", "p", "--no-backlinks"], _big_tree())
        capped, _ = _capped([], cap=len(plain.stdout))
        assert capped.stdout == plain.stdout
        assert capped.stderr == ""
        as_json, _ = _capped(["--json"], cap=10**7)
        assert "withheld" not in json.loads(as_json.stdout)

    def test_the_cap_applies_after_resolving_refs(self):
        ref = "11111111-2222-3333-4444-555555555555"
        tree = [_block(f"b{i}", f"see (({ref}))") for i in range(30)]
        api = MagicMock()
        api.get_page.return_value = {"name": "p"}
        api.get_page_blocks_tree.return_value = tree
        api.get_block.return_value = {"content": "z" * 200, "page": {"originalName": "Q"}}
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-page", "--name", "p", "--no-backlinks", "--resolve-refs",
                "--max-chars", "1000"])
        assert len(result.stdout) <= 1000
        assert "z" * 200 in result.stdout

    def test_the_unresolved_ref_warning_counts_printed_blocks_only(self):
        ref = "11111111-2222-3333-4444-555555555555"
        tree = [_block(f"b{i}", f"see (({ref})) " + "q" * 90) for i in range(30)]
        result, _ = _capped([], cap=1000, tree=tree)
        printed = result.stdout.count(f"(({ref}))")
        assert f"{printed} unresolved block-ref(s)" in result.stderr
        assert printed < 30

    def test_a_dead_ref_only_in_withheld_blocks_is_not_reported(self):
        dead = "99999999-2222-3333-4444-555555555555"
        tree = [_block(f"b{i}", "y" * 100) for i in range(20)]
        tree.append(_block("last", f"gone (({dead}))"))
        api = MagicMock()
        api.get_page.return_value = {"name": "p"}
        api.get_page_blocks_tree.return_value = tree
        api.get_block.return_value = None
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-page", "--name", "p", "--no-backlinks", "--resolve-refs",
                "--max-chars", "1000"])
        assert dead not in result.stdout
        assert "no longer exists" not in result.stderr

    def test_pages_past_the_cut_are_not_printed_but_counted(self):
        api = MagicMock()
        api.get_page.return_value = {"name": "x"}
        api.get_page_blocks_tree.side_effect = lambda name: _big_tree()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-page", "--name", "A", "--name", "B", "--name", "C",
                "--no-backlinks", "--max-chars", "1500", "--json"])
        pages = json.loads(result.stdout)
        assert [p["page"] for p in pages] == ["A"]
        printed = sum(1 for s in pages[0]["blocks"] for _ in [s] + s["children"])
        # Counts A's own blocks: B and C are named as pages, not as blocks.
        assert pages[0]["withheld"] == 33 - printed
        assert f"{33 - printed} block(s) withheld on 'A'" in result.stderr
        assert pages[0]["cut"]["later"] == 2
        assert "2 later page(s)" in result.stderr
        assert "'B'" in result.stderr and "'C'" in result.stderr

    def test_short_results_still_fit(self):
        """Many short journal days: whatever is printed must fit, the headers of
        the days past the cut included."""
        api = MagicMock()
        api.get_all_pages.return_value = _journal_pages(range(1, 31))
        api.get_page_blocks_tree.side_effect = lambda name: [_block("u" + name, "gym")]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-30",
                "--max-chars", "300"])
        assert result.exit_code == 0, result.output
        assert 0 < len(result.stdout) <= 300

    def test_nothing_to_withhold_says_so_instead_of_crashing(self):
        api = MagicMock()
        api.get_page.return_value = None
        api.get_page_blocks_tree.return_value = []
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-page", "--name", "Nope", "--max-chars", "5"])
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert result.exit_code == 1
        assert "Page 'Nope' not found" in result.stderr
        assert "no block to withhold" in result.stderr

    def test_a_frame_over_the_cap_is_said(self):
        """Backlinks are not blocks: an empty page with many of them cannot be
        cut, and the note says what is left over the cap."""
        linked = [[{"originalName": f"Linker {i}"}, []] for i in range(50)]
        api = MagicMock()
        api.get_page.return_value = {"name": "tag"}
        api.get_page_blocks_tree.return_value = [_block("b", "x")]
        api.get_page_linked_references.return_value = linked
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-page", "--name", "tag", "--max-chars", "100"])
        assert result.exit_code == 0
        assert "over --max-chars" in result.stderr
        assert "--no-backlinks" in result.stderr

    @pytest.mark.parametrize("value", ["0", "-5"])
    def test_a_cap_below_one_is_refused(self, value):
        result, api = _capped(["--json"], cap=value)
        assert result.exit_code != 0
        assert "--max-chars" in json.loads(result.stderr)["error"]
        assert result.stdout == ""
        api.get_page_blocks_tree.assert_not_called()


def _journal_pages(days):
    return [{"originalName": f"2026-08-{d:02d}, day", "name": f"2026-08-{d:02d}, day",
             "journalDay": int(f"202608{d:02d}")} for d in days]


def _range(extra, cap):
    api = MagicMock()
    api.get_all_pages.return_value = _journal_pages(range(1, 6))
    api.get_page_blocks_tree.side_effect = lambda name: _big_tree()
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-05",
            "--max-chars", str(cap), *extra])


class TestMaxCharsOnJournalRange:
    @pytest.mark.parametrize("fmt", ["text", "markdown", "json"])
    def test_stdout_stays_within_the_cap(self, fmt):
        result = _range(FORMATS[fmt], 4000)
        assert result.exit_code == 0, result.output
        assert len(result.stdout) <= 4000

    def test_days_past_the_cut_are_not_printed_but_named(self):
        result = _range(["--json"], 4000)
        days = json.loads(result.stdout)
        assert "cut" in days[-1]
        later = days[-1]["cut"]["later"]
        assert later == 5 - len(days) and later > 0
        assert f"{later} later day(s)" in result.stderr
        assert "2026-08-05, day" in result.stderr

    @pytest.mark.parametrize("fmt", ["text", "markdown"])
    def test_text_stops_at_the_cut(self, fmt):
        result = _range(FORMATS[fmt], 4000)
        assert "2026-08-05, day" not in result.stdout
        assert "withheld" not in result.stdout

    def test_the_unresolved_ref_warning_counts_printed_days_only(self):
        ref = "11111111-2222-3333-4444-555555555555"
        api = MagicMock()
        api.get_all_pages.return_value = _journal_pages(range(1, 6))
        api.get_page_blocks_tree.side_effect = lambda name: [
            _block(f"{name}-{i}", f"see (({ref})) " + "q" * 90) for i in range(10)]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-05",
                "--max-chars", "2000"])
        printed = result.stdout.count(f"(({ref}))")
        assert 0 < printed < 50
        assert f"{printed} unresolved block-ref(s)" in result.stderr


def _preorder(blocks):
    for block in blocks:
        yield block
        yield from _preorder(block.get("children") or [])


def _read_in_pieces(args, tree, cap):
    """Follow the notes until the read is complete; answer the new blocks of
    each piece, in order. A resumed piece starts with the ancestors of the
    block it resumes at, printed again for context; those are not new."""
    pieces, start = [], None
    for _ in range(100):
        extra = ["--from-block", start] if start else []
        result, _ = _run(["get-page", "--name", "p", "--no-backlinks", "--json",
                          "--max-chars", str(cap), *args, *extra], tree)
        assert result.exit_code == 0, result.output
        assert len(result.stdout) <= cap
        page = json.loads(result.stdout)
        uuids = [b["uuid"] for b in _preorder(page["blocks"])]
        pieces.append(uuids[uuids.index(start):] if start else uuids)
        if "cut" not in page:
            return pieces
        start = page["cut"]["before"]
        assert f"--from-block {start}" in result.stderr
    raise AssertionError("the read never completed")


class TestFromBlock:
    def _every(self, tree):
        return [b["uuid"] for b in _preorder(tree)]

    def test_following_the_notes_reads_every_block_once(self):
        tree = _big_tree()
        pieces = _read_in_pieces([], tree, 1500)
        assert len(pieces) > 2
        assert [u for piece in pieces for u in piece] == self._every(tree)

    def test_a_section_larger_than_the_cap_is_read_through(self):
        tree = [_heading("log", "## Log", 2,
                         [_block(f"l{i}", "z" * 100) for i in range(40)])]
        pieces = _read_in_pieces([], tree, 1200)
        assert [u for piece in pieces for u in piece] == self._every(tree)

    def test_a_repeated_heading_name_does_not_send_the_reader_back(self):
        tree = [
            _heading(f"m{m}", f"## Meeting {m}", 2, [
                _heading(f"m{m}-notes", "### Notes", 3,
                         [_block(f"m{m}-n{i}", "n" * 100) for i in range(8)])])
            for m in range(2)]
        pieces = _read_in_pieces([], tree, 1000)
        assert [u for piece in pieces for u in piece] == self._every(tree)

    def test_ancestors_come_along_as_context(self):
        result, _ = _run(["get-page", "--name", "p", "--no-backlinks",
                          "--from-block", "s1-b5"], _big_tree())
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert lines[1] == "- ## Section 1"
        assert lines[2].strip().startswith("- section 1 block 5")
        assert "section 1 block 4" not in result.stdout
        assert "Section 0" not in result.stdout

    def test_it_works_on_the_outline_too(self):
        result, _ = _run(["get-page", "--name", "p", "--outline",
                          "--from-block", "h-nested"], _page_tree())
        uuids = [l.split("\t")[0] for l in result.stdout.splitlines() if "\t" in l]
        assert uuids == ["h-nested", "h-auto"]

    def test_an_unknown_block_is_refused(self):
        result, _ = _run(["get-page", "--name", "p", "--json",
                          "--from-block", "nope"], _big_tree())
        assert result.exit_code == 1
        assert "nope" in json.loads(result.stderr)["error"]
        assert result.stdout == ""

    def test_the_journal_range_resumes_on_a_later_day(self):
        api = MagicMock()
        api.get_all_pages.return_value = _journal_pages(range(1, 4))
        api.get_page_blocks_tree.side_effect = lambda name: [
            _block(f"{name[:10]}-{i}", "d" * 50) for i in range(3)]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-03",
                "--from-block", "2026-08-02-1", "--json"])
        days = json.loads(result.stdout)
        assert [d["date"] for d in days] == ["2026-08-02", "2026-08-03"]
        assert [b["uuid"] for b in days[0]["blocks"]] == ["2026-08-02-1", "2026-08-02-2"]

    def test_a_block_too_large_for_the_cap_ends_the_chain_with_what_it_needs(self):
        """Following the notes must end. A block that does not fit, alone or
        with its ancestors, used to be named again and again."""
        tree = [_block("a", "a" * 100), _block("big", "x" * 3000), _block("c", "c")]
        first, _ = _run(["get-page", "--name", "p", "--no-backlinks", "--json",
                         "--max-chars", "1000"], tree)
        assert json.loads(first.stdout)["cut"]["before"] == "big"
        again, _ = _run(["get-page", "--name", "p", "--no-backlinks", "--json",
                         "--max-chars", "1000", "--from-block", "big"], tree)
        page = json.loads(again.stdout)
        assert "--from-block" not in again.stderr
        needs = page["cut"]["needs"]
        assert needs > 3000
        assert f"--max-chars {needs}" in again.stderr
        wide, _ = _run(["get-page", "--name", "p", "--no-backlinks", "--json",
                        "--max-chars", str(needs), "--from-block", "big"], tree)
        assert "big" in [b["uuid"] for b in json.loads(wide.stdout)["blocks"]]

    def test_ancestors_that_crowd_out_the_block_end_the_chain(self):
        tree = [_heading("A", "## " + "A" * 690, 2,
                         [_block(f"c{i}", f"{i}" + "c" * 299) for i in range(4)])]
        start = None
        for _ in range(6):
            result, _ = _run(["get-page", "--name", "p", "--no-backlinks",
                              "--max-chars", "1000"]
                             + (["--from-block", start] if start else []), tree)
            import re
            m = re.search(r"--from-block (\S+),", result.stderr)
            if not m:
                break
            assert m.group(1) != start, "the note sent the reader to the same block"
            start = m.group(1)
        else:
            raise AssertionError("the chain never ended")
        assert "needs" in result.stderr

    def test_a_blockless_page_too_large_for_the_cap_ends_the_chain(self):
        linked = [[{"originalName": f"Linker {i}"}, []] for i in range(20)]
        api = MagicMock()
        api.get_page.return_value = {"name": "x"}
        api.get_page_blocks_tree.side_effect = lambda n: {
            "A": [_block("a1", "a")], "B": [], "C": [_block("c1", "c")]}[n]
        api.get_page_linked_references.side_effect = lambda n: linked if n == "B" else []
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-page", "--name", "A", "--name", "B", "--name", "C",
                "--max-chars", "300"])
        assert "--from-block" not in result.stderr
        assert "--max-chars" in result.stderr and "needs" in result.stderr

    @pytest.mark.parametrize("names", [["A", "A"], ["A", "a"]])
    def test_a_page_named_twice_is_refused_for_a_cut_read(self, names):
        """A cut read continues by block uuid, and a page read twice holds each
        uuid twice. Logseq finds page names regardless of case."""
        args = [a for n in names for a in ("--name", n)]
        result, api = _run(["get-page", *args, "--max-chars", "500", "--json"], _big_tree())
        assert result.exit_code == 1
        assert "twice" in json.loads(result.stderr)["error"]
        api.get_page_blocks_tree.assert_not_called()

    def test_names_that_only_casefold_alike_are_two_pages(self):
        """Logseq lower-cases a page name (JavaScript toLowerCase), which keeps
        'ß' apart from 'ss'; casefold() would merge them."""
        result, _ = _run(["get-page", "--name", "Straße", "--name", "Strasse",
                          "--max-chars", "100000", "--no-backlinks"], _big_tree())
        assert result.exit_code == 0, result.output

    def test_a_page_named_twice_still_reads_without_a_cap(self):
        result, _ = _run(["get-page", "--name", "A", "--name", "A",
                          "--no-backlinks"], _big_tree())
        assert result.exit_code == 0

    def test_markdown_continuation_keeps_the_bullet_of_a_properties_block(self):
        """Only the page's own first block is written without a bullet; a
        properties-only block that merely comes first in a continued read keeps
        it, or the pieces would turn it into page properties."""
        tree = [_block("a", "first"),
                _block("ol", "logseq.order-list-type:: number"),
                _block("z", "last")]
        result, _ = _run(["get-page", "--name", "p", "--format", "markdown",
                          "--from-block", "ol"], tree)
        assert result.stdout.splitlines()[0] == "- logseq.order-list-type:: number"



class TestCapResultsContract:
    def test_context_is_never_the_cut(self):
        """The ancestors start_at keeps are context, never a place to cut: a
        cut before one would send the next read back to it. Called directly,
        because through the commands the context has always fit once."""
        from logseq_cli.render import cap_results
        tree = [_block("A", "A" * 50, [_block("c0", "c"), _block("c1", "c")])]
        results = [{"page": "p", "blocks": tree}]

        def render(rs):
            return "".join(b["content"] for r in rs for b in _preorder(r["blocks"]))

        cut, summary = cap_results(results, render, 10, context=1)
        assert summary["before"] == "c0"
        assert cut[-1]["cut"]["needs"] == 51


def _range_api(blocks_by_day):
    api = MagicMock()
    api.get_all_pages.return_value = _journal_pages(sorted(blocks_by_day))
    api.get_page_blocks_tree.side_effect = lambda name: blocks_by_day[int(name[8:10])]
    return api


class TestFromBlockOnJournalRange:
    def test_markdown_continuation_keeps_the_bullet_of_a_properties_block(self):
        api = _range_api({1: [_block("a", "first"),
                              _block("ol", "logseq.order-list-type:: number")]})
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-01",
                "--format", "markdown", "--from-block", "ol"])
        assert "- logseq.order-list-type:: number" in result.stdout.splitlines()

    def test_ancestors_that_crowd_out_the_block_end_the_chain(self):
        api = _range_api({1: [_heading("A", "## " + "A" * 690, 2,
                                       [_block(f"c{i}", f"{i}" + "c" * 299)
                                        for i in range(4)])]})
        import re
        start = None
        for _ in range(6):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                result = split_runner().invoke(cli, [
                    "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-01",
                    "--max-chars", "1000"] + (["--from-block", start] if start else []))
            m = re.search(r"--from-block (\S+),", result.stderr)
            if not m:
                break
            assert m.group(1) != start, "the note sent the reader to the same block"
            start = m.group(1)
        else:
            raise AssertionError("the chain never ended")
        assert "needs" in result.stderr


class TestCutAtTheStartOfAPageOrDay:
    """A cut that falls on the first block of a page or day still prints its
    header, and says the blocks were withheld rather than calling it empty."""

    @pytest.mark.parametrize("fmt", [[], ["--format", "markdown"]], ids=["text", "markdown"])
    def test_journal_range(self, fmt):
        api = _range_api({1: [_block("a", "a" * 100)], 2: [_block("b", "b" * 100)]})
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            one = split_runner().invoke(cli, [
                "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-01", *fmt])
            result = split_runner().invoke(cli, [
                "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-02",
                "--max-chars", str(len(one.stdout) + 70), *fmt])
        assert "a" * 100 in result.stdout and "b" * 100 not in result.stdout
        assert "(1 block(s) withheld by --max-chars)" in result.stdout
        assert "(empty)" not in result.stdout

    def test_get_page(self):
        api = MagicMock()
        api.get_page.return_value = {"name": "x"}
        api.get_page_blocks_tree.side_effect = lambda n: [_block(n, n * 100)]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            one = split_runner().invoke(cli, ["get-page", "--name", "A",
                                              "--name", "B", "--no-backlinks"])
            cap = one.stdout.index("=== B ===") + 70
            result = split_runner().invoke(cli, [
                "get-page", "--name", "A", "--name", "B", "--no-backlinks",
                "--max-chars", str(cap)])
        assert "A" * 100 in result.stdout and "B" * 100 not in result.stdout
        assert "(1 block(s) withheld by --max-chars)" in result.stdout
        assert "(empty page)" not in result.stdout


class TestMarkdownPageProperties:
    """The other side of the continuation test: a read that starts where the
    page does still writes the page's properties without a bullet."""

    def test_get_page(self):
        tree = [_block("props", "tags:: a"), _block("b", "text")]
        result, _ = _run(["get-page", "--name", "p", "--format", "markdown"], tree)
        assert result.stdout.splitlines()[0] == "tags:: a"

    def test_journal_range(self):
        api = _range_api({1: [_block("props", "tags:: a"), _block("b", "text")]})
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, [
                "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-01",
                "--format", "markdown"])
        assert "tags:: a" in result.stdout.splitlines()
