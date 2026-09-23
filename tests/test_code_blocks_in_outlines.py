"""A code block written in outline text stays one block.

Outline text (add-note-content, add-journal-content, indented --content,
--tree as text) is one block per line, and a code block written over several
lines was cut into one block per line: "```js", "a()", "```". None of them
was a code block, and the block holding only "```js" is worse than that. When
Logseq reads the page file again, a fence nothing closes in one block runs on
into the blocks after it up to the next code block on the page, and swallows
them (measured, Logseq 0.10.15).

Logseq's own file format keeps a code block together: from the opening to the
closing fence every line belongs to the block, "- " lines too (measured, #43).
The parser now reads it that way. An opening fence without a bullet goes on
the block above, as a property line does; one on a bullet line is a block of
its own. The closing fence is the next line without a bullet that starts with
``` or ~~~.
"""
from unittest.mock import patch

from logseq_cli.cli import cli
from logseq_cli.helpers import parse_hierarchical_content
from tests.conftest import PageGraph, page_graph_api, split_runner


def _contents(tree):
    return [(n["content"], _contents(n["children"])) for n in tree]


class TestTheParser:
    def test_under_a_bullet_the_code_goes_on_that_block(self):
        tree = parse_hierarchical_content("- note\n  ```js\n  a()\n  ```\n- after")
        assert _contents(tree) == [("note\n```js\na()\n```", []), ("after", [])]

    def test_on_a_bullet_line_it_is_a_block_of_its_own(self):
        tree = parse_hierarchical_content("- note\n\t- ```js\n\t  a()\n\t  ```\n- after")
        assert _contents(tree) == [("note", [("```js\na()\n```", [])]), ("after", [])]

    def test_the_indentation_inside_is_kept(self):
        tree = parse_hierarchical_content(
            "- note\n  ```py\n  def f():\n      return 1\n  ```")
        assert tree[0]["content"] == "note\n```py\ndef f():\n    return 1\n```"

    def test_bullets_and_headings_inside_are_code(self):
        tree = parse_hierarchical_content("- note\n  ```md\n  - item\n  # title\n  ```")
        assert _contents(tree) == [("note\n```md\n- item\n# title\n```", [])]

    def test_a_fence_on_a_bullet_line_inside_does_not_close(self):
        tree = parse_hierarchical_content("- note\n  ```md\n  - ```\n  x\n  ```")
        assert _contents(tree) == [("note\n```md\n- ```\nx\n```", [])]

    def test_tilde_fences_and_a_mixed_closer(self):
        tree = parse_hierarchical_content("- note\n  ~~~\n  x\n  ```")
        assert tree[0]["content"] == "note\n~~~\nx\n```"

    def test_a_property_after_the_code_stays_on_the_block(self):
        tree = parse_hierarchical_content("- note\n  ```\n  x\n  ```\n  k:: v")
        assert tree[0]["content"] == "note\n```\nx\n```\nk:: v"

    def test_the_structure_after_the_code_is_read_as_before(self):
        tree = parse_hierarchical_content("- a\n  ```\n  x\n  ```\n\t- b\n- c")
        assert _contents(tree) == [("a\n```\nx\n```", [("b", [])]), ("c", [])]

    def test_an_opener_nothing_closes_is_no_code_block(self):
        # It stays as it was; the check before the write refuses it.
        tree = parse_hierarchical_content("- note\n  ```\n  x")
        assert _contents(tree) == [("note", [("```", []), ("x", [])])]

    def test_a_first_line_code_block(self):
        tree = parse_hierarchical_content("```\nx\n```\n- after")
        assert _contents(tree) == [("```\nx\n```", []), ("after", [])]


def test_add_note_content_writes_the_code_block_as_one_block():
    api = page_graph_api(PageGraph({"Page A": [{"content": "start"}]}))
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, ["add-note-content", "--page", "Page A", "--content",
                                        "- note\n  ```js\n  a()\n  ```\n- after"])
    assert r.exit_code == 0, r.stderr
    blocks = api.graph.page_named("Page A")["blocks"]
    assert [b["content"] for b in blocks] == ["start", "note\n```js\na()\n```", "after"]
