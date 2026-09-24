"""Tests for normalize_heading — heading matching tolerance.

Regression anchor: a heading block addressed by a ((block-ref)) stores its
properties (id::, collapsed::, ...) on the lines after the heading text, e.g.
``## Focus Topics W24\nid:: <uuid>\ncollapsed:: true``. These must still match
the bare ``## Focus Topics W24`` query — otherwise get-page --heading silently
returns nothing for exactly the headings that matter most.
"""
from logseq_cli.headings import normalize_heading


def _match(stored, query):
    return normalize_heading(stored) == normalize_heading(query)


class TestBlockProperties:
    def test_id_property_on_next_line_still_matches(self):
        stored = "## Focus Topics W24\nid:: fedcba98-7654-3210-fedc-ba9876543210"
        assert _match(stored, "## Focus Topics W24")

    def test_multiple_properties_still_match(self):
        stored = "## Focus Topics W24\nid:: fedcba98-7654-3210-fedc-ba9876543210\ncollapsed:: true"
        assert _match(stored, "## Focus Topics W24")


class TestRendererMacros:
    def test_renderer_suffix_still_stripped(self):
        assert _match("## Tasks {{renderer :todomaster}}", "## Tasks")


class TestNoFalsePositives:
    def test_double_colon_in_heading_text_preserved(self):
        # A literal '::' that is part of the heading text must NOT be stripped.
        assert normalize_heading("## Setup:: Phase 1") == "## Setup:: Phase 1"

    def test_distinct_headings_do_not_match(self):
        assert not _match("## Focus Topics KW23", "## Focus Topics W24")

    def test_empty_text(self):
        assert normalize_heading("") == ""
        assert normalize_heading(None) == ""
