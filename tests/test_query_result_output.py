"""The text output of ``smart-query``, which nothing exercised.

``_print_results`` is what a user sees without ``--json``; returning ``None``
from it left all 842 tests green. Its four branches exist because Datalog does
not answer in one shape: a query may return rows wrapping a block, bare rows,
plain maps, or scalars, and each has a different place to look for a name.

A branch that picks the wrong field does not fail — it prints a blank line
where a page name belongs, numbered as if a result were there.

The 20-item cap is part of the contract too. It is what keeps a query matching
a thousand blocks from filling a terminal, and it is invisible in the count
line above the list, so a silent change of it misleads twice.
"""

import pytest

from logseq_cli.commands.query import _print_results


def _render(results):
    """Run the printer and hand back the lines it produced."""
    import click

    lines = []
    original = click.echo
    click.echo = lambda msg="", **kw: lines.append(str(msg))
    try:
        _print_results(results)
    finally:
        click.echo = original
    return lines


class TestResultShapes:
    """Datalog answers in several shapes; each must find the name."""

    def test_row_wrapping_a_page_map(self):
        assert _render([[{"name": "alpha"}]]) == ["  1. alpha"]

    def test_row_wrapping_a_block_falls_back_to_content(self):
        assert _render([[{"content": "some block text"}]]) == ["  1. some block text"]

    def test_row_prefers_name_over_content(self):
        assert _render([[{"name": "alpha", "content": "ignored"}]]) == ["  1. alpha"]

    def test_row_accepts_the_hyphenated_key(self):
        """Datalog answers ``:block/original-name``, not ``originalName``."""
        assert _render([[{"original-name": "Alpha"}]]) == ["  1. Alpha"]

    def test_bare_map_uses_the_camelcase_key(self):
        """The HTTP API's own shape, as getAllPages returns it."""
        assert _render([{"originalName": "Alpha"}]) == ["  1. Alpha"]

    def test_row_wrapping_a_scalar(self):
        assert _render([["just a string"]]) == ["  1. just a string"]

    def test_bare_scalar(self):
        assert _render(["just a string"]) == ["  1. just a string"]

    def test_numbering_counts_from_one(self):
        assert _render(["a", "b", "c"]) == ["  1. a", "  2. b", "  3. c"]


class TestGuards:
    def test_non_list_prints_nothing(self):
        """A failed query answers a dict; it must not be rendered as results."""
        assert _render({"error": "bad query"}) == []

    def test_empty_list_prints_nothing(self):
        assert _render([]) == []

    def test_empty_row_is_skipped_not_crashed(self):
        assert _render([[]]) == ["  1. []"]


class TestOutputIsCapped:
    """Twenty items, whatever the result count says."""

    def test_more_than_twenty_is_truncated(self):
        lines = _render([f"item{i}" for i in range(50)])
        assert len(lines) == 20
        assert lines[-1] == "  20. item19"

    def test_exactly_twenty_is_complete(self):
        assert len(_render([f"item{i}" for i in range(20)])) == 20

    def test_long_content_is_cut_to_eighty_characters(self):
        long = "x" * 200
        assert _render([{"content": long}]) == [f"  1. {'x' * 80}"]
