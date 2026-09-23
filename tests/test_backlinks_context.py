"""get-backlinks --with-context: which block does the linking, not just which page.

The linking blocks already arrive in the API response — getPageLinkedReferences
answers ``[page, [block, ...]]`` pairs — and ``extract_backlink_names`` drops
everything but the name. A caller that wants to know *why* a page links back has
to fetch and search each page again, which is the read the response had already
paid for.

Behind a flag, because the plain listing is a documented shape: the tests in
test_get_backlinks_batch pin it, and a page with many backlinks would otherwise
grow the default output by the length of every linking block.
"""
import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli
from logseq_cli.commands.pages import _extract_backlink_context
from tests.conftest import split_runner


def _api(refs_by_page):
    """refs_by_page: {page: [(linking_page, [block_content, ...]), ...]}"""
    api = MagicMock()

    def _refs(page_name):
        return [[{"originalName": ln},
                 [{"uuid": f"u{i}", "content": c} for i, c in enumerate(blocks)]]
                for ln, blocks in refs_by_page.get(page_name, [])]

    api.get_page_linked_references.side_effect = _refs
    return api


def _run(args, api):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return CliRunner().invoke(cli, args)


class TestWithContext:
    def test_json_carries_the_linking_blocks(self):
        api = _api({"Alice": [("Journal", ["met [[Alice]] at noon"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context", "--json"], api)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        entry = data["backlinks"][0]
        assert entry["page"] == "Journal"
        assert entry["blocks"][0]["content"] == "met [[Alice]] at noon"

    def test_plain_output_shows_the_block(self):
        api = _api({"Alice": [("Journal", ["met [[Alice]] at noon"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context"], api)
        assert result.exit_code == 0, result.output
        assert "met [[Alice]] at noon" in result.output

    def test_block_uuid_is_included(self):
        """So a caller can act on the block, not just read it."""
        api = _api({"Alice": [("Journal", ["met [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context", "--json"], api)
        data = json.loads(result.output)
        assert data["backlinks"][0]["blocks"][0]["uuid"] == "u0"

    def test_several_blocks_on_one_page(self):
        api = _api({"Alice": [("Journal", ["first [[Alice]]", "second [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context", "--json"], api)
        data = json.loads(result.output)
        assert len(data["backlinks"][0]["blocks"]) == 2

    def test_output_is_bounded(self):
        """Same promise as every other read: a busy page must not blow the budget."""
        many = [f"mention {i} [[Alice]]" for i in range(50)]
        api = _api({"Alice": [("Journal", many)]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "3", "--json"], api)
        data = json.loads(result.output)
        assert len(data["backlinks"][0]["blocks"]) == 3
        assert data["backlinks"][0]["withheld"] == 47

    def test_properties_blocks_are_not_context(self):
        """A key:: value block is the page's own metadata, not a mention."""
        api = _api({"Alice": [("Journal", ["tags:: people", "met [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context", "--json"], api)
        data = json.loads(result.output)
        contents = [b["content"] for b in data["backlinks"][0]["blocks"]]
        assert "met [[Alice]]" in contents
        assert "tags:: people" not in contents


class TestWithoutContextIsUnchanged:
    """The default shape is documented and pinned elsewhere; this guards it here."""

    def test_default_json_is_a_list_of_names(self):
        api = _api({"Alice": [("Journal", ["met [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--json"], api)
        data = json.loads(result.output)
        assert data["backlinks"] == ["Journal"]

    def test_default_plain_output_has_no_block_text(self):
        api = _api({"Alice": [("Journal", ["met [[Alice]] at noon"])]})
        result = _run(["get-backlinks", "--name", "Alice"], api)
        assert "at noon" not in result.output
        assert "<- Journal" in result.output


class TestLimitRejectsNegativeValues:
    """`--limit -1` used to answer with less data and a count larger than the page held.

    The cap is applied as a slice and the withheld count was derived from the
    cap, so a negative value broke both halves at once: ``blocks[:-1]`` drops
    the *last* block instead of capping, and ``len(blocks) - (-1)`` exceeds
    what exists. Three blocks came back as two, with "4 more not shown".

    ``0`` is a valid value here and means "keep all", which is what makes the
    wrong input reachable: a caller who knows that reaches for ``-1`` as "all
    the more so". So the boundary is ``< 0``, not ``< 1``.
    """

    def test_negative_limit_is_rejected(self):
        api = _api({"Alice": [("Journal", ["a [[Alice]]", "b [[Alice]]", "c [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "-1"], api)
        assert result.exit_code == 1
        assert "--limit" in result.output
        assert "0 or greater" in result.output

    def test_the_refusal_goes_to_stderr_and_stdout_stays_empty(self):
        """stdout is payload; a caller piping it into a parser gets nothing else."""
        api = _api({"Alice": [("Journal", ["a [[Alice]]"])]})
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(cli, ["get-backlinks", "--name", "Alice",
                                                 "--with-context", "--limit", "-1"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert "0 or greater" in result.stderr

    def test_the_error_is_json_when_json_was_asked_for(self):
        """The command speaks JSON, so its refusal has to as well."""
        api = _api({"Alice": [("Journal", ["a [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "-1", "--json"], api)
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert "--limit" in payload["error"]

    def test_zero_still_keeps_every_block(self):
        """The documented meaning of 0 survives the guard."""
        api = _api({"Alice": [("Journal", ["a [[Alice]]", "b [[Alice]]", "c [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "0", "--json"], api)
        assert result.exit_code == 0
        entry = json.loads(result.output)["backlinks"][0]
        assert len(entry["blocks"]) == 3
        assert "withheld" not in entry

    def test_one_is_accepted_and_caps(self):
        """The value just above the boundary is ordinary, not an edge case."""
        api = _api({"Alice": [("Journal", ["a [[Alice]]", "b [[Alice]]", "c [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "1", "--json"], api)
        assert result.exit_code == 0
        entry = json.loads(result.output)["backlinks"][0]
        assert len(entry["blocks"]) == 1
        assert entry["withheld"] == 2

    def test_no_page_is_read_before_the_input_is_refused(self):
        """A bad value must not cost an API call."""
        api = _api({"Alice": [("Journal", ["a [[Alice]]"])]})
        _run(["get-backlinks", "--name", "Alice", "--with-context",
              "--limit", "-1"], api)
        api.get_page_linked_references.assert_not_called()

    def test_the_guard_also_covers_batch_mode(self):
        """--name is repeatable; the check belongs before the loop, not inside it."""
        api = _api({"Alice": [("Journal", ["a [[Alice]]"])],
                    "Bob": [("Journal", ["b [[Bob]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--name", "Bob",
                       "--with-context", "--limit", "-2"], api)
        assert result.exit_code == 1
        api.get_page_linked_references.assert_not_called()


class TestWithheldCannotExceedWhatExists:
    """The count is derived from what was kept, not from what was requested.

    The guard alone would close the reported defect, but it leaves the count
    computed against `limit` — a number the caller supplies. Deriving it from
    the kept blocks instead makes the two halves of the output unable to
    disagree, whatever value reaches the function. `get-journal-range` has
    computed its `omitted` this way all along.
    """

    def test_withheld_plus_shown_equals_what_the_page_held(self):
        api = _api({"Alice": [("Journal", [f"m{i} [[Alice]]" for i in range(9)])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "4", "--json"], api)
        entry = json.loads(result.output)["backlinks"][0]
        assert len(entry["blocks"]) + entry["withheld"] == 9

    def test_properties_blocks_are_not_counted_as_withheld(self):
        """They were never candidates, so they cannot be "left out" either."""
        api = _api({"Alice": [("Journal", ["tags:: people", "a [[Alice]]",
                                           "b [[Alice]]", "c [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "2", "--json"], api)
        entry = json.loads(result.output)["backlinks"][0]
        assert len(entry["blocks"]) == 2
        assert entry["withheld"] == 1

    def test_a_limit_above_the_block_count_withholds_nothing(self):
        api = _api({"Alice": [("Journal", ["a [[Alice]]", "b [[Alice]]"])]})
        result = _run(["get-backlinks", "--name", "Alice", "--with-context",
                       "--limit", "50", "--json"], api)
        entry = json.loads(result.output)["backlinks"][0]
        assert "withheld" not in entry

    def test_the_count_holds_even_if_a_negative_value_reaches_the_function(self):
        """The second layer, tested where it actually is.

        The CLI guard stops negative values today, so every test above passes
        with the count still derived from ``limit``. This one calls the
        extractor directly: it is the only place the defence is visible, and
        without it the fix would be a guard with an untested claim behind it.
        """
        refs = [[{"originalName": "Journal"},
                 [{"uuid": f"u{i}", "content": f"m{i}"} for i in range(3)]]]
        for limit in (-1, -2, -3):
            entry = _extract_backlink_context(refs, limit)[0]
            shown, withheld = len(entry["blocks"]), entry.get("withheld", 0)
            assert shown + withheld == 3, (
                f"--limit {limit}: {shown} shown + {withheld} withheld "
                "does not add up to the 3 blocks the page held"
            )

    def test_the_plain_text_note_matches_the_json_count(self):
        """Both formats report the same truncation or the pair is a lie."""
        api = _api({"Alice": [("Journal", [f"m{i} [[Alice]]" for i in range(9)])]})
        plain = _run(["get-backlinks", "--name", "Alice", "--with-context",
                      "--limit", "4"], api)
        as_json = _run(["get-backlinks", "--name", "Alice", "--with-context",
                        "--limit", "4", "--json"], api)
        withheld = json.loads(as_json.output)["backlinks"][0]["withheld"]
        assert f"... {withheld} more not shown" in plain.output
