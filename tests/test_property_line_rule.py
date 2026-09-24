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
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from logseq_cli.blocktext import PROPERTY_LINE_RE
from logseq_cli.outlinetext import parse_hierarchical_content
from logseq_cli.render import is_properties_block


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
        tree = parse_hierarchical_content("- Task A\n  - detail\n- Priorität:: hoch")
        assert [n["content"] for n in tree] == ["Task A", "Priorität:: hoch"]
        assert tree[0]["children"][0]["content"] == "detail"

    def test_it_keeps_its_children(self):
        tree = parse_hierarchical_content("- FAQ\n- Frage::\n  - Was kostet es?")
        assert [n["content"] for n in tree] == ["FAQ", "Frage::"]
        assert tree[1]["children"][0]["content"] == "Was kostet es?"

    def test_a_deeper_bullet_still_belongs_to_the_block_above(self):
        tree = parse_hierarchical_content("- ## Plan\n\t- logseq.order-list-type:: number")
        assert tree == [{"content": "## Plan\nlogseq.order-list-type:: number",
                         "children": []}]

    def test_a_continuation_line_at_the_same_level_merges(self):
        tree = parse_hierarchical_content("- Child\n  Priorität:: hoch")
        assert tree == [{"content": "Child\nPriorität:: hoch", "children": []}]


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
