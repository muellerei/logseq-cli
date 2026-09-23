"""The two text renderers behind ``get-page`` and ``get-journal-range``.

``blocks_to_markdown`` produces the default output of ``get-page`` — the shape
a user reads and a script pipes on. Nothing tested it. Two independent
mutations survived the whole suite: dropping the properties-block branch, and
dropping indentation entirely.

That is worth stating plainly, because ``is_properties_block`` *is* covered:
the tests for it exercise ``get-backlinks --with-context``, a different caller.
Coverage of a function says nothing about coverage of the branch that calls it.

The properties branch is the reason the renderer is not a one-liner. Logseq
stores a page's ``key:: value`` header without a bullet; rendering it as a list
item produces a page that no longer round-trips into the graph.

``blocks_with_ids`` promises in its own docstring that indentation matches
``blocks_to_markdown``. That is asserted here by rendering the same tree
through both, rather than by writing the expected tab runs out twice.
"""


from logseq_cli.helpers import outline_text, process_blocks
from logseq_cli.render import blocks_to_markdown, blocks_with_ids


def _block(content, uuid="u", children=()):
    return {"content": content, "uuid": uuid, "children": list(children)}


PROPERTIES = "type:: note\nstatus:: open"


class TestPropertiesBlockKeepsItsShape:
    """A top-level properties block is written without a bullet."""

    def test_properties_block_has_no_bullet(self):
        out = blocks_to_markdown([_block(PROPERTIES)])
        assert out == PROPERTIES
        assert not out.startswith("- ")

    def test_ordinary_block_gets_a_bullet(self):
        assert blocks_to_markdown([_block("plain text")]) == "- plain text"

    def test_single_property_line_counts_as_properties(self):
        assert blocks_to_markdown([_block("type:: note")]) == "type:: note"

    def test_nested_properties_block_still_gets_a_bullet(self):
        """Only the top level is a page header; deeper down it is content."""
        tree = [_block("parent", children=[_block(PROPERTIES)])]
        out = blocks_to_markdown(tree)
        assert "\t- type:: note" in out

    def test_mixed_block_is_not_a_properties_block(self):
        """One prose line is enough to make it ordinary content."""
        content = "type:: note\nthis is prose"
        assert blocks_to_markdown([_block(content)]) == "- type:: note\n  this is prose"


class TestIndentation:
    """Depth is carried by tabs; losing it flattens the tree."""

    def test_child_is_indented_one_tab(self):
        tree = [_block("parent", children=[_block("child")])]
        assert blocks_to_markdown(tree) == "- parent\n\t- child"

    def test_depth_accumulates(self):
        tree = [_block("a", children=[_block("b", children=[_block("c")])])]
        assert blocks_to_markdown(tree) == "- a\n\t- b\n\t\t- c"

    def test_siblings_share_a_level(self):
        tree = [_block("a"), _block("b")]
        assert blocks_to_markdown(tree) == "- a\n- b"

    def test_empty_content_is_skipped_but_children_survive(self):
        tree = [_block("", children=[_block("child")])]
        assert blocks_to_markdown(tree) == "\t- child"


class TestBlocksWithIds:
    """``<uuid>\\t<indent>\\t<content>``, for callers that avoid JSON."""

    def test_line_carries_uuid_and_content(self):
        assert blocks_with_ids([_block("text", uuid="abc")]) == "abc\t\ttext"

    def test_missing_uuid_leaves_the_field_empty(self):
        block = {"content": "text", "children": []}
        assert blocks_with_ids([block]) == "\t\ttext"

    def test_indentation_matches_blocks_to_markdown(self):
        """The docstring's claim, asserted against the other renderer."""
        tree = [_block("a", children=[_block("b", children=[_block("c")])])]
        md_depths = [
            len(line) - len(line.lstrip("\t"))
            for line in blocks_to_markdown(tree).split("\n")
        ]
        id_depths = [
            len(line.split("\t", 1)[1]) - len(line.split("\t", 1)[1].lstrip("\t")) - 1
            for line in blocks_with_ids(tree).split("\n")
        ]
        assert md_depths == id_depths == [0, 1, 2]


class TestRoundTrip:
    """What the renderer writes must read back as the same structure."""

    def test_page_header_then_content_matches_logseq_file_layout(self):
        tree = [_block(PROPERTIES), _block("first note",
                                           children=[_block("detail")])]
        assert blocks_to_markdown(tree) == (
            "type:: note\nstatus:: open\n- first note\n\t- detail"
        )


class TestFurtherLinesSitUnderTheirBullet:
    """A block's second and later lines sit under its bullet, indented (#75)."""

    # Measured: the file Logseq 0.10.15 wrote for this tree. Blank lines
    # inside a block get the indent too.
    TREE = [
        _block("top line one\ntop line two\nk1:: v1", children=[
            _block("child one\nchild cont\nk2:: v2", children=[
                _block("grand\n```\ncode a\n\ncode b\n```\nafter fence")]),
            _block("child two\n\nafter blank")]),
        _block("last"),
    ]
    LOGSEQ_FILE = (
        "- top line one\n  top line two\n  k1:: v1\n"
        "\t- child one\n\t  child cont\n\t  k2:: v2\n"
        "\t\t- grand\n\t\t  ```\n\t\t  code a\n\t\t  \n\t\t  code b\n\t\t  ```\n"
        "\t\t  after fence\n"
        "\t- child two\n\t  \n\t  after blank\n"
        "- last"
    )

    def test_markdown_is_the_file_logseq_writes(self):
        assert blocks_to_markdown(self.TREE) == self.LOGSEQ_FILE

    def test_page_properties_stay_as_they_are(self):
        tree = [_block(PROPERTIES), _block("a\nb")]
        assert blocks_to_markdown(tree) == PROPERTIES + "\n- a\n  b"

    def test_read_on_from_a_properties_only_block_indents_it(self):
        """Mid-page (``--from-block``) it is a bulleted block like any other."""
        out = blocks_to_markdown([_block(PROPERTIES)], page_start=False)
        assert out == "- type:: note\n  status:: open"

    def test_three_renderers_agree(self):
        """``outline_text`` shows what a write will be; the reads lay out a
        block's lines the same way. They part only on an empty block, which
        the reads skip and a write keeps."""
        tree = self.TREE
        assert outline_text(tree) == blocks_to_markdown(tree)
        assert process_blocks(tree) == outline_text(tree).replace("\t", "  ")
