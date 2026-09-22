"""Selecting a write target by text instead of by UUID.

Recorded use shows the detour this removes: 18 shell pipelines of the shape
``UUID=$(find-block ... --json | python3 -c ...)`` followed by a write, plus 37
find-block -> update-block and 11 find-block -> remove-block sequences.

The safety property matters more than the convenience: these commands overwrite
content, so an ambiguous selector must stop rather than pick a match. Guessing
rewrites one of several equally valid blocks and the caller cannot tell which.
set-todo-status already had this selector and did guess (it took candidates[0]);
that is fixed here too.
"""
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from logseq_cli.helpers import resolve_single_block

import click


def _api(matches, block=None):
    api = MagicMock()
    api.datascript_query.return_value = [[m] for m in matches]
    api.get_block.return_value = block or {"uuid": "00000000-0000-4000-8000-0000000000a2", "content": "old"}
    return api


ONE = [{"uuid": "00000000-0000-4000-8000-0000000000a2", "content": "**14:22** Entry"}]
TWO = [{"uuid": "00000000-0000-4000-8000-0000000000a2", "content": "Duplicate A"},
       {"uuid": "u-2", "content": "Duplicate B"}]


class TestResolveSingleBlock:
    def test_single_match_returns_uuid(self):
        assert resolve_single_block(_api(ONE), "14:22") == "00000000-0000-4000-8000-0000000000a2"

    def test_no_match_aborts(self):
        with pytest.raises(click.ClickException) as exc:
            resolve_single_block(_api([]), "nope")
        assert "No block matches" in str(exc.value)
        assert "Nothing was changed" in str(exc.value)

    def test_ambiguous_aborts_and_lists_candidates(self):
        with pytest.raises(click.ClickException) as exc:
            resolve_single_block(_api(TWO), "Duplicate")
        msg = str(exc.value)
        assert "2 blocks match" in msg
        assert "refusing to guess" in msg
        assert "00000000-0000-4000-8000-0000000000a2" in msg and "u-2" in msg

    def test_long_ambiguity_is_truncated_but_counted(self):
        many = [{"uuid": f"u-{i}", "content": f"Treffer {i}"} for i in range(14)]
        with pytest.raises(click.ClickException) as exc:
            resolve_single_block(_api(many), "Treffer")
        assert "... and 4 more" in str(exc.value)


class TestUpdateBlockWhereContent:
    def test_updates_the_single_match(self):
        api = _api(ONE)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "update-block", "--where-content", "14:22", "--content", "new"])
        assert r.exit_code == 0, r.output
        api.update_block.assert_called_once_with("00000000-0000-4000-8000-0000000000a2", "new", properties=None)

    def test_ambiguous_writes_nothing(self):
        api = _api(TWO)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "update-block", "--where-content", "Duplicate", "--content", "new"])
        assert r.exit_code == 1
        api.update_block.assert_not_called()

    def test_no_match_writes_nothing(self):
        api = _api([])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "update-block", "--where-content", "nope", "--content", "new"])
        assert r.exit_code == 1
        api.update_block.assert_not_called()

    def test_exactly_one_selector(self):
        api = _api(ONE)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            both = CliRunner().invoke(cli, [
                "update-block", "--id", "00000000-0000-4000-8000-0000000000a2", "--where-content", "x", "--content", "n"])
            neither = CliRunner().invoke(cli, ["update-block", "--content", "n"])
        for r in (both, neither):
            assert r.exit_code == 1
            assert "exactly one of" in r.output
        api.update_block.assert_not_called()

    def test_dry_run_writes_nothing(self):
        api = _api(ONE)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "update-block", "--where-content", "14:22", "--content", "new",
                "--dry-run"])
        assert r.exit_code == 0, r.output
        assert "[DRY RUN]" in r.output
        api.update_block.assert_not_called()

    def test_id_path_still_works(self):
        api = _api([])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "update-block", "--id", "00000000-0000-4000-8000-0000000000a2", "--content", "new"])
        assert r.exit_code == 0, r.output
        api.update_block.assert_called_once_with("00000000-0000-4000-8000-0000000000a2", "new", properties=None)
        # the only query is the property read; no content search on the --id path
        for call in api.datascript_query.call_args_list:
            assert ":block/properties-text-values" in call.args[0]


class TestSetTodoStatusAmbiguity:
    """Regression: it used to take candidates[0] and rewrite it silently."""

    def test_two_matching_todos_abort(self):
        api = _api([{"uuid": "u-A", "content": "TODO Report (A)"},
                    {"uuid": "u-B", "content": "TODO Report (B)"}])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "set-todo-status", "--content", "Report", "--page", "X",
                "--status", "DONE"])
        assert r.exit_code == 1
        assert "refusing to guess" in r.output
        api.update_block.assert_not_called()

    def test_todo_marker_still_disambiguates_prose(self):
        """A TODO plus a prose mention is not ambiguous: the marker decides."""
        api = _api([{"uuid": "u-A", "content": "TODO Write report"},
                    {"uuid": "u-B", "content": "see Write report above"}])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "set-todo-status", "--content", "Report", "--page", "X",
                "--status", "DONE"])
        assert r.exit_code == 0, r.output
        api.update_block.assert_called_once()
        assert api.update_block.call_args.args[0] == "u-A"


class TestInsertBlockQuiet:
    def test_quiet_drops_the_uuid_list_but_keeps_the_confirmation(self):
        from tests.conftest import fake_api
        api = fake_api(["u1", "u2", "u3"])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            loud = CliRunner().invoke(cli, [
                "insert-block", "--child-of", "p", "--tree", "- a\n- b\n- c"])
            api2 = fake_api(["u1", "u2", "u3"])
            with patch("logseq_cli.group.LogseqAPI", return_value=api2):
                quiet = CliRunner().invoke(cli, [
                    "insert-block", "--child-of", "p", "--tree", "- a\n- b\n- c",
                    "--quiet"])
        assert "Inserted 3 block(s)" in loud.output
        assert "uuid: u1" in loud.output
        assert "Inserted 3 block(s)" in quiet.output
        assert "uuid:" not in quiet.output
