"""Tests for hierarchical content detection and block tree insertion."""

from unittest.mock import MagicMock

from logseq_cli.helpers import (
    contains_hierarchical_content,
    parse_hierarchical_content,
    find_or_create_heading,
)


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


class TestParseHierarchicalPropertyLines:
    """Property lines (key:: value) must merge into the preceding block,
    never become standalone blocks (they'd render as broken content)."""

    def test_indented_property_line_merges_into_parent(self):
        tree = parse_hierarchical_content("- ## Plan\n\tcollapsed:: true")
        assert len(tree) == 1
        assert tree[0]["content"] == "## Plan\ncollapsed:: true"
        assert tree[0]["children"] == []

    def test_bulleted_property_line_merges(self):
        tree = parse_hierarchical_content("- ## Plan\n\t- collapsed:: true")
        assert len(tree) == 1
        assert tree[0]["content"] == "## Plan\ncollapsed:: true"
        assert tree[0]["children"] == []

    def test_property_merges_into_last_created_block(self):
        tree = parse_hierarchical_content(
            "- Head\n\t- Child\n\tid:: fedcba98-0000-0000-0000-000000000000")
        assert tree[0]["content"] == "Head"
        assert tree[0]["children"][0]["content"] == (
            "Child\nid:: fedcba98-0000-0000-0000-000000000000")

    def test_multiple_property_lines_merge_in_order(self):
        tree = parse_hierarchical_content(
            "- ## Heading\n\tid:: abc\n\tcollapsed:: true\n\t- child")
        assert tree[0]["content"] == "## Heading\nid:: abc\ncollapsed:: true"
        assert len(tree[0]["children"]) == 1
        assert tree[0]["children"][0]["content"] == "child"

    def test_leading_property_line_stays_a_block(self):
        """No preceding block to merge into: keep old behavior."""
        tree = parse_hierarchical_content("type:: Person\n- ## Kontakt")
        assert tree[0]["content"] == "type:: Person"
        assert tree[1]["content"] == "## Kontakt"

    def test_normal_content_with_double_colon_midline_not_merged(self):
        """Only lines *starting* with key:: are property lines."""
        tree = parse_hierarchical_content("- A\n- Siehe key:: value Doku")
        assert len(tree) == 2

    def test_timestamp_entry_unaffected(self):
        tree = parse_hierarchical_content("**09:30** Log line\n\t- Detail")
        assert tree[0]["content"] == "**09:30** Log line"
        assert tree[0]["children"][0]["content"] == "Detail"
