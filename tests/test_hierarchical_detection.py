"""Tests for hierarchical content detection and block tree insertion."""

import pytest
from unittest.mock import MagicMock, call

from logseq_cli.helpers import (
    contains_hierarchical_content,
    has_flush_newline_bullets,
    parse_hierarchical_content,
    find_or_create_heading,
    insert_block_tree,
)


class TestHasFlushNewlineBullets:
    """Tests for the flush (non-indented) newline-bullet guard."""

    def test_flush_bullets_after_header_returns_true(self):
        """The exact silent-failure case: header + column-0 bullets."""
        content = "**09:16** Header\n- point a\n- point b"
        assert has_flush_newline_bullets(content) is True

    def test_single_line_returns_false(self):
        assert has_flush_newline_bullets("**14:30** just a log line") is False

    def test_leading_bullet_line1_only_returns_false(self):
        """A bullet on line 1 is fine; nothing flush follows."""
        assert has_flush_newline_bullets("- single item") is False

    def test_indented_sub_bullets_return_false(self):
        """Properly indented children are handled by the hierarchy path, not the guard."""
        content = "**17:00** Main\n\t- sub 1\n\t- sub 2"
        assert has_flush_newline_bullets(content) is False

    def test_space_indented_sub_bullets_return_false(self):
        content = "**17:00** Main\n  - sub 1"
        assert has_flush_newline_bullets(content) is False

    def test_plain_multiline_no_bullets_returns_false(self):
        assert has_flush_newline_bullets("line1\nline2\nline3") is False

    def test_flush_bullet_among_indented_returns_true(self):
        """One flush bullet is enough to trip the guard."""
        content = "**09:16** Header\n\t- indented\n- flush"
        assert has_flush_newline_bullets(content) is True


class TestContainsHierarchicalContent:
    """Tests for the auto-detection of hierarchical content."""

    def test_simple_text_returns_false(self):
        assert contains_hierarchical_content("einfacher text") is False

    def test_empty_string_returns_false(self):
        assert contains_hierarchical_content("") is False

    def test_single_bullet_no_hierarchy_returns_false(self):
        assert contains_hierarchical_content("- item") is False

    def test_newline_without_indent_returns_false(self):
        """Peer bullets (no indentation) are not hierarchy."""
        assert contains_hierarchical_content("line1\n- peer") is False

    def test_tab_indented_sub_bullet_returns_true(self):
        assert contains_hierarchical_content("line1\n\t- sub") is True

    def test_space_indented_sub_bullet_returns_true(self):
        assert contains_hierarchical_content("line1\n  - sub") is True

    def test_deep_hierarchy_returns_true(self):
        content = "- top\n\t- child\n\t\t- grandchild"
        assert contains_hierarchical_content(content) is True

    def test_multiline_without_bullets_returns_false(self):
        content = "line1\nline2\nline3"
        assert contains_hierarchical_content(content) is False

    def test_realistic_log_entry_returns_true(self):
        content = "**17:38** Intune-Strategie erarbeitet\n\t- Recherche: 9 Dokumente\n\t- Musterschreiben erstellt"
        assert contains_hierarchical_content(content) is True

    def test_literal_backslash_n_returns_false(self):
        """Literal \\n in string (not actual newline) should not match."""
        content = "text with \\n\\t- fake sub"
        assert contains_hierarchical_content(content) is False


class TestFindOrCreateHeading:
    """Tests for heading lookup and creation."""

    def test_finds_existing_heading(self):
        api = MagicMock()
        api.get_page_blocks_tree.return_value = [
            {"content": "## Log", "uuid": "abc-123"},
            {"content": "## Meeting", "uuid": "def-456"},
        ]
        result = find_or_create_heading(api, "test-page", "## Log")
        assert result == "abc-123"
        api.append_block_in_page.assert_not_called()

    def test_creates_missing_heading(self):
        api = MagicMock()
        api.get_page_blocks_tree.return_value = []
        api.append_block_in_page.return_value = {"uuid": "new-uuid"}
        result = find_or_create_heading(api, "test-page", "## Log")
        assert result == "new-uuid"
        api.append_block_in_page.assert_called_once_with("test-page", "## Log")

    def test_strips_whitespace_when_matching(self):
        api = MagicMock()
        api.get_page_blocks_tree.return_value = [
            {"content": "  ## Log  ", "uuid": "abc-123"},
        ]
        result = find_or_create_heading(api, "test-page", "## Log")
        assert result == "abc-123"

    def test_returns_none_on_total_failure(self):
        api = MagicMock()
        api.get_page_blocks_tree.return_value = []
        api.append_block_in_page.return_value = None
        result = find_or_create_heading(api, "test-page", "## Log")
        assert result is None


class TestInsertBlockTree:
    """Tests for recursive block tree insertion."""

    def test_single_block_no_children(self):
        api = MagicMock()
        api.insert_block.return_value = {"uuid": "block-1"}
        tree = [{"content": "hello", "children": []}]
        n = insert_block_tree(api, tree, "parent-uuid")
        assert n == 1
        api.insert_block.assert_called_once_with("parent-uuid", "hello", {"sibling": False})

    def test_block_with_children(self):
        api = MagicMock()
        api.insert_block.side_effect = [
            {"uuid": "block-1"},  # parent
            {"uuid": "block-2"},  # child
        ]
        tree = [{"content": "parent", "children": [
            {"content": "child", "children": []}
        ]}]
        n = insert_block_tree(api, tree, "heading-uuid")
        assert n == 2
        assert api.insert_block.call_count == 2
        api.insert_block.assert_any_call("heading-uuid", "parent", {"sibling": False})
        api.insert_block.assert_any_call("block-1", "child", {"sibling": False})

    def test_multiple_siblings(self):
        api = MagicMock()
        api.insert_block.side_effect = [
            {"uuid": "b1"}, {"uuid": "b2"}, {"uuid": "b3"}
        ]
        tree = [
            {"content": "one", "children": []},
            {"content": "two", "children": []},
            {"content": "three", "children": []},
        ]
        n = insert_block_tree(api, tree, "parent")
        assert n == 3

    def test_deep_nesting(self):
        api = MagicMock()
        api.insert_block.side_effect = [
            {"uuid": "l1"}, {"uuid": "l2"}, {"uuid": "l3"}
        ]
        tree = [{"content": "level1", "children": [
            {"content": "level2", "children": [
                {"content": "level3", "children": []}
            ]}
        ]}]
        n = insert_block_tree(api, tree, "root")
        assert n == 3


class TestParseHierarchicalContentIntegration:
    """Integration tests: parse + detect working together."""

    def test_detected_content_parses_correctly(self):
        content = "**17:00** Main entry\n\t- Sub point 1\n\t- Sub point 2"
        assert contains_hierarchical_content(content) is True
        tree = parse_hierarchical_content(content)
        assert len(tree) == 1
        assert tree[0]["content"] == "**17:00** Main entry"
        assert len(tree[0]["children"]) == 2
        assert tree[0]["children"][0]["content"] == "Sub point 1"
        assert tree[0]["children"][1]["content"] == "Sub point 2"

    def test_non_detected_content_still_parseable(self):
        """Even flat content can be parsed — it just produces flat tree."""
        content = "simple block"
        assert contains_hierarchical_content(content) is False
        tree = parse_hierarchical_content(content)
        assert len(tree) == 1
        assert tree[0]["content"] == "simple block"
        assert tree[0]["children"] == []
