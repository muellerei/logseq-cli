"""preorder_blocks: every block of a tree, depth first, the way getPageBlocksTree hands it back."""
from unittest.mock import MagicMock

from logseq_cli.lookup import get_page_content, page_text
from logseq_cli.outlinetext import preorder_blocks as walk_blocks
from logseq_cli.render import process_blocks


def _b(content, *children):
    return {"content": content, "children": list(children)}


def _order(tree):
    return [b["content"] for b in walk_blocks(tree)]


def test_yields_every_block_depth_first():
    tree = [_b("A", _b("B", _b("C")), _b("D")), _b("E")]
    assert _order(tree) == ["A", "B", "C", "D", "E"]


def test_a_block_without_children_key_is_a_leaf():
    assert _order([{"content": "A"}]) == ["A"]


def test_children_none_is_a_leaf():
    assert _order([{"content": "A", "children": None}]) == ["A"]


def test_empty_tree_yields_nothing():
    assert walk_blocks([]) == []


def test_walks_deeper_than_two_levels():
    tree = [_b("1", _b("2", _b("3", _b("4"))))]
    assert _order(tree) == ["1", "2", "3", "4"]


def test_page_text_of_an_empty_or_missing_tree_is_empty():
    assert page_text([]) == "" and page_text(None) == ""


def test_page_text_is_what_process_blocks_makes_of_the_tree():
    tree = [{"uuid": "1", "content": "A", "children": [
        {"uuid": "2", "content": "B", "children": []}]}]
    assert page_text(tree) == process_blocks(tree) != ""


def test_get_page_content_and_page_text_give_the_same_answer():
    """One source for "an empty tree is an empty text"; None tells a second copy apart."""
    tree = [{"uuid": "1", "content": "A", "children": []}]
    for answer in (tree, [], None):
        api = MagicMock()
        api.get_page_blocks_tree.return_value = answer
        assert get_page_content(api, "P") == page_text(answer)
