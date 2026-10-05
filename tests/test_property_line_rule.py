"""#39: one rule for "this line is a property line", the one Logseq reads by.

Four readers decided it with three different patterns, and none accepted a
``.`` in the key, while Logseq writes ``logseq.order-list-type:: number``
itself for every block of a numbered list. ``replace-text`` would rewrite
that line like text.

The table below was measured against Logseq 0.10.15: each line was written
into a page file, the file parsed by Logseq, and ``:block/properties`` read
back. The characters that stop a line from being a property are exactly the
ones #21 measured for the writer, except ``/``, which reads as a namespace.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from logseq_cli.blocktext import PROPERTY_LINE_RE
from logseq_cli.outlinetext import parse_hierarchical_content
from logseq_cli.render import is_properties_block
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


READ_AS_PROPERTY = [
    "logseq.order-list-type:: number",
    "a.b:: x",
    "a/b:: x",          # read as "b"
    "?x:: y",
    "+k:: y",
    "a<b:: v",
    "123:: v",
    "Mixed_Case:: v",   # read as "mixed-case"
    "k::",              # empty value
    "k:: ",
    "  deep:: z",       # indented, as in a file
    "prio:: A",
    "id:: 00000000-0000-4000-8000-000000000001",
    "\fdeep:: z",       # a form feed indents too
    "\f\tdeep:: z",
]

READ_AS_TEXT = [
    "std::cout << 1",
    "k::v",             # no space after ::
    "k::\tv",           # a tab is not the space
    "Key With Space:: v",
    "#tag:: v",
    "a,b:: x", "a:b:: x", 'a"b:: x', "a(b):: x", "a[b]:: x", "a;b:: x",
    "a@b:: x", "a^b:: x", "a{b}:: x", "a|b:: x", "a~b:: x", "a`b:: x",
    "a\\b:: x",
    "plain text",
    "\u00a0k:: v",       # a no-break space does not indent
]


def _replace_text(content, find, replace):
    """Run replace-text over one block; return what it wrote."""
    api = MagicMock()
    api.get_page_blocks_tree.return_value = [
        {"uuid": "b1", "content": content, "children": []}]
    written = {}
    api.update_block.side_effect = lambda u, c, properties=None, replacing=None: written.update(c=c)
    api.get_block.side_effect = lambda u, include_children=False: {
        "uuid": "b1", "content": written.get("c", content)}
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = CliRunner().invoke(cli, [
            "replace-text", "--page", "P", "--find", find, "--replace", replace])
    assert r.exit_code == 0, r.output
    return written["c"]


class TestTheRule:
    @pytest.mark.parametrize("line", READ_AS_PROPERTY)
    def test_logseq_reads_a_property(self, line):
        assert PROPERTY_LINE_RE.match(line)

    @pytest.mark.parametrize("line", READ_AS_TEXT)
    def test_logseq_reads_text(self, line):
        assert not PROPERTY_LINE_RE.match(line)


class TestEveryReaderUsesIt:
    def test_replace_text_leaves_a_numbered_list_property_alone(self):
        assert _replace_text("first number item\nlogseq.order-list-type:: number",
                             "number", "x") == "first x item\nlogseq.order-list-type:: number"

    def test_parsed_content_keeps_the_line_with_its_block(self):
        tree = parse_hierarchical_content("- step one\n  logseq.order-list-type:: number")
        assert tree == [{"content": "step one\nlogseq.order-list-type:: number",
                         "children": []}]

    def test_a_page_properties_block_with_a_dotted_key_is_recognised(self):
        assert is_properties_block("logseq.order-list-type:: number\ntags:: a")

    def test_get_todos_does_not_search_the_line(self):
        api = MagicMock()
        api.datascript_query.return_value = [
            [{"content": "TODO ship it\nlogseq.order-list-type:: number",
              "marker": "TODO", "uuid": "u1"},
             {"original-name": "Page A", "name": "page a"}]]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, ["get-todos", "--no-follow-refs",
                                         "--match", "number", "--json"])
        assert '"count": 0' in r.output


class TestABulletedPropertyLineAtItsOwnLevelIsABlock:
    """With the rule widened (umlauts, dots, empty values), bulleted lines the
    old rule skipped started to merge into whatever block came last, however
    far away. Logseq reads `- k:: v` as a block of its own (measured: `- first::
    v` is one); the merge exists for the shape seen in real use, a property
    bullet one level below the block it belongs to (`- ## Plan` / `\\t- collapsed::
    true`, commit aebcb21). A continuation line without a bullet always merges."""

    def test_sibling_level_bullet_is_its_own_block(self):
        tree = parse_hierarchical_content("- Task A\n  - detail\n- Priority:: high")
        assert [n["content"] for n in tree] == ["Task A", "Priority:: high"]
        assert tree[0]["children"][0]["content"] == "detail"

    def test_it_keeps_its_children(self):
        tree = parse_hierarchical_content("- FAQ\n- Question::\n  - What does it cost?")
        assert [n["content"] for n in tree] == ["FAQ", "Question::"]
        assert tree[1]["children"][0]["content"] == "What does it cost?"

    def test_a_deeper_bullet_still_belongs_to_the_block_above(self):
        tree = parse_hierarchical_content("- ## Plan\n\t- logseq.order-list-type:: number")
        assert tree == [{"content": "## Plan\nlogseq.order-list-type:: number",
                         "children": []}]

    def test_a_continuation_line_at_the_same_level_merges(self):
        tree = parse_hierarchical_content("- Child\n  Priority:: high")
        assert tree == [{"content": "Child\nPriority:: high", "children": []}]


class TestTheIdLineFollowsTheSameSeparator:
    """`id::` lines have a rule of their own (every spelling of the id key), and
    it accepted `id::x` without the space, which Logseq reads as text (`k::v`,
    measured). Such a line was dropped from content as if it were the id."""

    def test_no_space_is_text(self):
        from logseq_cli.blocktext import block_id_property, without_block_ids
        line = "id::00000000-0000-4000-8000-000000000001"
        assert block_id_property("A\n" + line) == ""
        assert without_block_ids("A\n" + line) == "A\n" + line

    def test_with_the_space_it_is_the_id(self):
        from logseq_cli.blocktext import block_id_property
        assert block_id_property("A\nid:: 00000000-0000-4000-8000-000000000001") == \
            "00000000-0000-4000-8000-000000000001"


class TestInsideACodeFenceItIsText:
    """Measured: a `k:: v` line between ``` fences is not a property to Logseq.
    replace-text skipped it (reporting "No matches") and get-todos hid it."""

    def test_the_mask_follows_the_fences(self):
        from logseq_cli.blocktext import property_line_mask
        lines = ["Template", "```", "  template:: meeting", "```", "real:: yes"]
        assert property_line_mask(lines) == [False, False, False, False, True]

    def test_replace_text_replaces_inside_a_fence(self):
        content = "Template doc\n```\ntemplate:: meeting\n```\nkind:: meeting"
        assert _replace_text(content, "meeting", "call") == \
            "Template doc\n```\ntemplate:: call\n```\nkind:: meeting"


# Which lines a code block hides, measured against Logseq 0.10.15 like the
# table above: each block written into a page file, ``k:: v`` read back from
# :block/properties (the whitespace cases through keepUUID). A fence line is
# one that starts, after spaces, tabs or form feeds, with ``` or ~~~. Two such
# lines make a code block, whichever of the two they use and whatever follows
# them on the line; an opener with no closer after it is no code block at all,
# and the lines after it are read as usual.
FENCED = [
    ("closed", ["x", "```", "k:: v", "```"]),
    ("with a language", ["x", "```js", "k:: v", "```"]),
    ("tilde", ["x", "~~~", "k:: v", "~~~"]),
    ("tilde closes backticks", ["x", "```", "k:: v", "~~~"]),
    ("backticks close tilde", ["x", "~~~", "k:: v", "```"]),
    ("four closed by three", ["x", "````", "k:: v", "```"]),
    ("closer with text after it", ["x", "```", "k:: v", "``` y"]),
    ("closer indented further", ["x", "```", "k:: v", "    ```"]),
    ("indented by a tab", ["x", "\t```", "k:: v", "\t```"]),
    ("indented by a form feed", ["x", "\f```", "k:: v", "\f```"]),
    ("one-line opener, closed later", ["x", "```y```", "k:: v", "```"]),
    ("first line", ["```", "k:: v", "```"]),
]
NOT_FENCED = [
    ("never closed", ["```", "k:: v"]),
    ("tilde never closed", ["x", "~~~", "k:: v"]),
    ("one line", ["```x```", "k:: v"]),
    ("one line with text after it", ["x", "```y``` z", "k:: v"]),
    ("tilde on one line", ["x", "~~~y~~~", "k:: v"]),
    ("a pair, then an opener", ["x", "```", "```", "k:: v", "```"]),
    ("between two pairs", ["x", "```", "a", "```", "k:: v", "```", "b", "```"]),
    ("not at the line start", ["x", "a ```", "k:: v", "```"]),
    ("two backticks", ["x", "``", "k:: v", "``"]),
    # str.lstrip() takes these away too, Logseq does not.
    ("after a no-break space", ["x", "\u00a0```", "k:: v", "\u00a0```"]),
    ("after a carriage return", ["x", "\r```", "k:: v", "\r```"]),
]


class TestTheCodeBlockRule:
    """Fixed after #43 measured it: an opener without a closer hid every line
    after it, a one-line ```x``` opened a block, and ~~~ did not."""

    @pytest.mark.parametrize("name,lines", FENCED, ids=[n for n, _ in FENCED])
    def test_a_code_block_hides_the_line(self, name, lines):
        from logseq_cli.blocktext import property_line_mask
        assert not property_line_mask(lines)[lines.index("k:: v")]

    @pytest.mark.parametrize("name,lines", NOT_FENCED, ids=[n for n, _ in NOT_FENCED])
    def test_without_a_code_block_the_line_is_a_property(self, name, lines):
        from logseq_cli.blocktext import property_line_mask
        assert property_line_mask(lines)[lines.index("k:: v")]

    def test_a_property_after_a_code_block_is_one(self):
        from logseq_cli.blocktext import property_line_mask
        lines = ["x", "```", "k:: v", "```", "k2:: w", "```"]
        assert property_line_mask(lines) == [False, False, False, False, True, False]

    def test_replace_text_leaves_a_property_after_an_unclosed_fence(self):
        content = "A meeting doc\n```\nkind:: meeting"
        assert _replace_text(content, "meeting", "call") == \
            "A call doc\n```\nkind:: meeting"


SCHED = "SCHEDULED: <2026-09-25 Fri>"
CLOCK = "CLOCK: [2026-09-29 Tue 10:00:00]--[2026-09-29 Tue 10:05:00] =>  00:05:00"
ATTACHED = [
    ("scheduled", ["TODO x", SCHED], [False, True]),
    ("deadline", ["TODO x", "DEADLINE: <2026-09-25 Fri>"], [False, True]),
    ("indented", ["TODO x", "  DEADLINE: <2026-09-25 Fri>"], [False, True]),
    ("no angle bracket", ["TODO x", "SCHEDULED: soon"], [False, False]),
    ("closed drawer", ["TODO x", ":LOGBOOK:", CLOCK, ":END:", "after"],
     [False, True, True, True, False]),
    ("indented drawer", ["TODO x", "  :LOGBOOK:",
                         '  * State "DONE" from "TODO" [2026-09-29 Tue 10:00]', "  :END:"],
     [False, True, True, True]),
    ("opener without closer is text", ["TODO x", ":LOGBOOK:", "no end"], [False, False, False]),
    ("closer without opener is text", ["TODO x", ":END:", "y"], [False, False, False]),
    ("drawer in a code block", ["TODO x", "```", ":LOGBOOK:", CLOCK, ":END:", "```"],
     [False] * 6),
    ("planning line in a code block", ["TODO x", "```", SCHED, "```"], [False] * 4),
    ("property line is not attached", ["TODO x", "prio:: high", SCHED], [False, False, True]),
    ("no lines", [], []),
]


class TestAttachedLineMask:
    """The one rule for which lines belong to the block above them."""

    @pytest.mark.parametrize("name,lines,expected", ATTACHED, ids=[n for n, _, _ in ATTACHED])
    def test_mask(self, name, lines, expected):
        from logseq_cli.blocktext import attached_line_mask
        assert attached_line_mask(lines) == expected

    def test_it_is_disjoint_from_the_property_mask(self):
        from logseq_cli.blocktext import attached_line_mask, property_line_mask
        lines = ["TODO x", "prio:: high", SCHED]
        assert property_line_mask(lines) == [False, True, False]
        assert attached_line_mask(lines) == [False, False, True]

    def test_drawer_lines_is_public(self):
        from logseq_cli.blocktext import drawer_lines
        assert drawer_lines(["a", ":LOGBOOK:", "b", ":END:"]) == {1, 2, 3}
        assert drawer_lines([":LOGBOOK:", "x"]) == set()


PLAN = "SCHEDULED: <2026-09-25 Fri>"
DUE = "DEADLINE: <2026-09-30 Wed>"
LOG = "CLOCK: [2026-09-25 Fri 10:00:00]--[2026-09-25 Fri 10:05:00] =>  00:05:00"


def _contents(tree):
    return [n["content"] for n in tree]


class TestOutlineKeepsAttachedLines:
    """A SCHEDULED/DEADLINE line and a closed logbook drawer belong to the
    block above, as in Logseq's files; the one rule is blocktext's
    attached_line_mask."""

    @pytest.mark.parametrize("text", [
        f"TODO x\n{PLAN}", f"TODO x\n{DUE}", f"TODO x\n{PLAN}\n{DUE}",
        f"TODO x\n:LOGBOOK:\n{LOG}\n:END:",
        f"TODO x\n{PLAN}\n:LOGBOOK:\n{LOG}\n:END:\nprio:: high",
    ], ids=["scheduled", "deadline", "both", "drawer", "all three"])
    def test_the_lines_stay_with_their_task(self, text):
        tree = parse_hierarchical_content(text)
        assert [(n["content"], n["children"]) for n in tree] == [(text, [])]

    def test_an_indented_planning_line_under_a_bullet(self):
        tree = parse_hierarchical_content(f"- TODO x\n  {PLAN}\n  {DUE}")
        assert [(n["content"], n["children"]) for n in tree] == \
            [(f"TODO x\n{PLAN}\n{DUE}", [])]

    def test_a_planning_line_in_a_code_block_stays_code(self):
        tree = parse_hierarchical_content(f"- note\n  ```\n  {PLAN}\n  ```")
        assert len(tree) == 1
        assert tree[0]["content"].count(PLAN) == 1

    def test_a_bullet_fence_with_a_drawer_inside_stays_one_block(self):
        fence = "```sh\n:LOGBOOK:\nx\n```"
        tree = parse_hierarchical_content("- ```sh\n  :LOGBOOK:\n  x\n  ```\n  :END:\n- b")
        assert tree == [{"content": fence, "children": [{"content": ":END:", "children": []}]},
                        {"content": "b", "children": []}]
        tree = parse_hierarchical_content("- ```sh\n  :LOGBOOK:\n  x\n  ```\n- b")
        assert tree == [{"content": fence, "children": []}, {"content": "b", "children": []}]

    @pytest.mark.parametrize("text", [
        PLAN, DUE, f":LOGBOOK:\n{LOG}\n:END:"], ids=["scheduled", "deadline", "drawer"])
    def test_a_leading_line_has_no_block_to_join(self, text):
        tree = parse_hierarchical_content(text)
        assert [(n["content"], n["children"]) for n in tree] == [(text, [])]

    def test_a_bullet_fence_line_inside_a_code_block_opens_nothing(self):
        text = ("- TODO a\n```md\n- ```js\n```\n  :LOGBOOK:\n"
                "  CLOCK: [2026-09-25 Fri 10:00:00]\n  :END:\n- b\n```\nx\n```")
        tree = parse_hierarchical_content(text)
        assert [n["children"] for n in tree] == [[], []]
        assert tree[0]["content"].endswith(":END:")

    def test_a_bullet_line_ends_a_drawer_that_nothing_closed_before_it(self):
        # An opener whose closer comes only after a bullet line is text, like
        # an opener nothing closes: no block is swallowed, every line is a block.
        tree = parse_hierarchical_content("TODO x\n:LOGBOOK:\n- foo\n:END:")
        assert _contents(tree) == ["TODO x", ":LOGBOOK:", "foo", ":END:"]
        tree = parse_hierarchical_content("- a\n:LOGBOOK:\n- b\n:END:")
        assert _contents(tree) == ["a", ":LOGBOOK:", "b", ":END:"]
        tree = parse_hierarchical_content("- TODO a\n:LOGBOOK:\n- TODO b\n- c\n:END:")
        assert _contents(tree) == ["TODO a", ":LOGBOOK:", "TODO b", "c", ":END:"]

    def test_a_drawer_closed_before_the_next_bullet_stays_with_its_block(self):
        text = f"- TODO a\n  :LOGBOOK:\n  {LOG}\n  :END:\n- TODO b"
        assert _contents(parse_hierarchical_content(text)) == [
            f"TODO a\n:LOGBOOK:\n{LOG}\n:END:", "TODO b"]

    def test_a_blank_line_inside_a_drawer_goes_and_the_drawer_stays(self):
        tree = parse_hierarchical_content(f"TODO x\n:LOGBOOK:\n\n{LOG}\n:END:")
        assert _contents(tree) == [f"TODO x\n:LOGBOOK:\n{LOG}\n:END:"]

    def test_siblings_are_not_swallowed(self):
        tree = parse_hierarchical_content(f"- TODO x\n  {PLAN}\n- TODO y")
        assert _contents(tree) == [f"TODO x\n{PLAN}", "TODO y"]


def _page_blocks(double, name="P"):
    page = next(p for p in double.pages if p["name"] == name)
    return [n["content"] for n in double._walk(page["blocks"])]


def _command(monkeypatch, args, *, page="P"):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page(page, ["first"])
    result = split_runner().invoke(cli, ["--token", "t", *args])
    return result, double


class TestAnUnclosedLogbookIsText:
    """An opener no closer ends is text, not a drawer (mldoc 1.5.7). The outline
    parser makes a block of every line without a bullet, so the opener and what
    follows it are blocks of their own: nothing is swallowed, nothing refused,
    nothing said on stderr."""

    def test_the_parser(self):
        tree = parse_hierarchical_content("TODO x\n:LOGBOOK:\nno end")
        assert _contents(tree) == ["TODO x", ":LOGBOOK:", "no end"]
        tree = parse_hierarchical_content("TODO x\n:LOGBOOK:\nno end\n- TODO y")
        assert _contents(tree) == ["TODO x", ":LOGBOOK:", "no end", "TODO y"]

    @pytest.mark.parametrize("text,count", [
        ("TODO x\n:LOGBOOK:\nno end", 3),
        ("TODO x\n:LOGBOOK:\nno end\n- TODO y", 4),
        (f"TODO x\n:LOGBOOK:\n{LOG}\n:END:", 1),
    ])
    def test_the_command(self, monkeypatch, text, count):
        result, double = _command(monkeypatch, ["add-note-content", "--page", "P",
                                                "--content", text, "--dry-run", "--json"])
        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["blocks_added"] == count
        assert result.stderr == ""
        result, double = _command(monkeypatch, ["add-note-content", "--page", "P",
                                                "--content", text, "--json"])
        assert result.exit_code == 0, result.stderr
        assert result.stderr == ""
        assert len(_page_blocks(double)) == 1 + count
        if text.endswith("TODO y"):
            assert _page_blocks(double)[-1] == "TODO y"


class TestTheCommandsKeepAScheduledTaskInOneBlock:
    """What took two blocks, a task and a block holding only its date, is one."""

    @pytest.mark.parametrize("text", [
        f"TODO x\n{PLAN}", f"TODO x\n{DUE}", f"TODO x\n:LOGBOOK:\n{LOG}\n:END:"],
        ids=["scheduled", "deadline", "drawer"])
    def test_add_note_content(self, monkeypatch, text):
        args = ["add-note-content", "--page", "P", "--content", text, "--json"]
        result, double = _command(monkeypatch, [*args, "--dry-run"])
        assert json.loads(result.stdout)["blocks_added"] == 1
        result, double = _command(monkeypatch, args)
        assert result.exit_code == 0, result.stderr
        assert _page_blocks(double) == ["first", text]

    def test_other_parser_users_keep_the_scheduled_line(self, monkeypatch):
        text = f"- TODO x\n  {PLAN}"
        result, double = _command(monkeypatch, ["add-journal-content", "--content", text,
                                                "--date", "2026-09-25", "--dry-run", "--json"])
        assert json.loads(result.stdout)["blocks"] == 1
        result, double = _command(monkeypatch, ["add-journal-content", "--content", text,
                                                "--date", "2026-09-25", "--json"])
        assert f"TODO x\n{PLAN}" in _page_blocks(double, "2026-09-25, Friday")

        child = f"- TODO x\n  {PLAN}\n  - child"
        result, double = _command(monkeypatch, ["insert-block", "--page", "P", "--content",
                                                child, "--dry-run", "--json"])
        assert json.loads(result.stdout)["blocks"] == 2
        result, double = _command(monkeypatch, ["insert-block", "--page", "P", "--content",
                                                child, "--json"])
        assert _page_blocks(double) == ["first", f"TODO x\n{PLAN}", "child"]

        result, double = _command(monkeypatch, ["add-journal-block", "--content", child,
                                                "--date", "2026-09-25", "--json"])
        assert json.loads(result.stdout)["blocks_added"] == 2
        assert f"TODO x\n{PLAN}" in _page_blocks(double, "2026-09-25, Friday")

    def test_a_content_file_and_a_text_tree(self, monkeypatch, tmp_path):
        path = tmp_path / "in.txt"
        path.write_text(f"TODO x\n{PLAN}", encoding="utf-8")
        result, double = _command(monkeypatch, ["add-journal-block", "--content-file", str(path),
                                                "--date", "2026-09-25", "--json"])
        assert json.loads(result.stdout)["blocks_added"] == 1
        double = LogseqHttpDouble().install(monkeypatch)
        double.add_page("P", ["first"])
        result = split_runner().invoke(cli, ["--token", "t", "insert-block", "--child-of",
                                             double.uuid_of("first"), "--tree",
                                             f"TODO x\n{PLAN}", "--json"])
        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["blocks_added"] == 1
        assert f"TODO x\n{PLAN}" in _page_blocks(double)


class TestOnlyThePageOwnPropertiesLoseTheirBullet:
    """Logseq writes the page's properties, its first block, without a bullet;
    every other block keeps one, properties-only or not (an empty
    numbered-list item, say)."""

    def test_first_block_is_page_properties(self):
        from logseq_cli.render import blocks_to_markdown
        md = blocks_to_markdown([
            {"content": "tags:: a", "children": []},
            {"content": "text", "children": []},
            {"content": "logseq.order-list-type:: number", "children": []}])
        assert md == "tags:: a\n- text\n- logseq.order-list-type:: number"


class TestBacklinkContext:
    """get-backlinks --with-context leaves out properties-only blocks; it
    shares the rule, so it follows the same corrections."""

    def test_follows_the_rule(self):
        assert not is_properties_block("std::cout << x [[P]]")
        assert is_properties_block("a.b:: [[P]]")
