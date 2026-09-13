"""find-block --with-children: matches with their sub-blocks.

Recorded use shows the shape this replaces: 51 of 65 context-greps were
``get-page --heading "## Log" | grep -A<n> "14:57"`` - a subtree read expressed
as a text read with a guessed line count, which drags in whatever follows when
the guess is too high and truncates when it is too low. The datalog pull behind
find-block carries no children, so the subtree costs one extra read per match;
that fan-out is capped and the remainder reported rather than silently dropped.
"""
import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli, FIND_BLOCK_CHILDREN_LIMIT


def _api(matches, children_by_uuid=None):
    api = MagicMock()
    api.datascript_query.return_value = [[m] for m in matches]
    children_by_uuid = children_by_uuid or {}

    def _get_block(uuid, include_children=True):
        return {"uuid": uuid, "children": children_by_uuid.get(uuid, [])}

    api.get_block.side_effect = _get_block
    return api


HIT = {
    "uuid": "u-1",
    "content": "**14:22** Ticket 10 abgeschlossen",
    "page": {"original-name": "2026-08-21, friday"},
}
KIDS = [
    {"content": "**Implementation:** fix pushed", "children": [
        {"content": "=> state matches", "children": []},
    ]},
    {"content": "**Roadmap:** nachgezogen", "children": []},
]


class TestWithChildren:
    def test_children_are_printed_indented(self):
        api = _api([HIT], {"u-1": KIDS})
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "find-block", "--content", "14:22", "--with-children"])
        assert r.exit_code == 0, r.output
        assert "**Implementation:** fix pushed" in r.output
        assert "=> state matches" in r.output
        # the grandchild must sit deeper than the child, so the tree shape is
        # readable rather than a flat dump
        def indent_of(needle):
            line = next(l for l in r.output.splitlines() if needle in l)
            return len(line) - len(line.lstrip())
        assert indent_of("=> state matches") > indent_of("**Implementation:**")

    def test_without_flag_no_extra_read_and_no_children(self):
        api = _api([HIT], {"u-1": KIDS})
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, ["find-block", "--content", "14:22"])
        assert r.exit_code == 0, r.output
        assert "**Implementation:**" not in r.output
        api.get_block.assert_not_called()

    def test_head_is_not_truncated_when_expanding(self):
        """The 80-char preview would cut the head of a subtree in half."""
        long_hit = dict(HIT, content="X" * 200)
        api = _api([long_hit], {"u-1": []})
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "find-block", "--content", "X", "--with-children"])
        assert "X" * 200 in r.output

    def test_preview_still_truncates_without_the_flag(self):
        long_hit = dict(HIT, content="X" * 200)
        api = _api([long_hit])
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, ["find-block", "--content", "X"])
        assert "X" * 200 not in r.output
        assert "X" * 80 in r.output

    def test_json_output_carries_children(self):
        api = _api([HIT], {"u-1": KIDS})
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "find-block", "--content", "14:22", "--with-children", "--json"])
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data[0]["children"][0]["content"] == "**Implementation:** fix pushed"

    def test_fanout_is_capped_and_the_remainder_named(self):
        n = FIND_BLOCK_CHILDREN_LIMIT + 7
        hits = [dict(HIT, uuid=f"u-{i}") for i in range(n)]
        api = _api(hits, {f"u-{i}": [] for i in range(n)})
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "find-block", "--content", "x", "--with-children"])
        assert r.exit_code == 0, r.output
        assert api.get_block.call_count == FIND_BLOCK_CHILDREN_LIMIT
        assert "7 further match(es) not expanded" in r.output

    def test_first_limits_before_expanding(self):
        hits = [dict(HIT, uuid=f"u-{i}") for i in range(5)]
        api = _api(hits, {f"u-{i}": KIDS for i in range(5)})
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "find-block", "--content", "x", "--first", "--with-children"])
        assert r.exit_code == 0, r.output
        assert api.get_block.call_count == 1

    def test_match_without_uuid_does_not_crash(self):
        api = _api([{"content": "no uuid here"}])
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "find-block", "--content", "no uuid", "--with-children"])
        assert r.exit_code == 0, r.output
        api.get_block.assert_not_called()
