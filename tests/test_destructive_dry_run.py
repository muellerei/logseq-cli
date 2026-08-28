"""Tests for --dry-run on destructive commands and the delete-page force gate.

The invariant under test: with --dry-run, no mutating API call is made. These
commands cascade (remove-block takes children with it, copy-block --remove
deletes the source), so a preview that lies is worse than no preview.
"""
import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


MUTATING = ("update_block", "remove_block", "delete_page",
            "insert_block", "append_block_in_page")


def _assert_no_mutation(api):
    """No mutating API method may have been called."""
    for name in MUTATING:
        method = getattr(api, name, None)
        if method is not None:
            assert not method.called, f"{name} was called during --dry-run"


@pytest.fixture
def api(monkeypatch):
    """A MagicMock LogseqAPI injected into the CLI context."""
    mock = MagicMock()
    monkeypatch.setattr("logseq_cli.cli.LogseqAPI", lambda **kwargs: mock)
    return mock


class TestUpdateBlockDryRun:
    def test_dry_run_does_not_write(self, api):
        api.get_block.return_value = {"uuid": "u1", "content": "old text"}
        result = CliRunner().invoke(cli, ["update-block", "--id", "u1",
                                          "--content", "new text", "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "old text" in result.output
        assert "new text" in result.output
        _assert_no_mutation(api)

    def test_without_dry_run_writes(self, api):
        api.get_block.return_value = {"uuid": "u1", "content": "old text"}
        result = CliRunner().invoke(cli, ["update-block", "--id", "u1",
                                          "--content", "new text"])
        assert result.exit_code == 0
        api.update_block.assert_called_once_with("u1", "new text", properties=None)

    def test_missing_block_errors_as_json_on_stderr(self, api):
        api.get_block.return_value = None
        result = split_runner().invoke(cli, ["update-block", "--id", "nope",
                                             "--content", "x", "--json"],
                                       catch_exceptions=False)
        assert result.exit_code == 1
        # stdout must stay clean; the error object goes to stderr.
        assert result.stdout == ""
        payload = json.loads(result.stderr)
        assert payload["error"].startswith("Block not found")
        assert payload["id"] == "nope"


class TestRemoveBlockDryRun:
    def test_dry_run_counts_descendants(self, api):
        api.get_block.return_value = {
            "uuid": "root", "content": "Parent",
            "children": [
                {"uuid": "c1", "content": "Kind A"},
                {"uuid": "c2", "content": "Kind B",
                 "children": [{"uuid": "g1", "content": "Enkel"}]},
            ],
        }
        result = CliRunner().invoke(cli, ["remove-block", "--id", "root",
                                          "--dry-run", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["descendants"] == 3      # c1, c2, g1
        assert payload["blocks_removed"] == 4   # + root
        assert payload["dry_run"] is True
        _assert_no_mutation(api)

    def test_dry_run_with_no_children(self, api):
        api.get_block.return_value = {"uuid": "solo", "content": "Alone"}
        result = CliRunner().invoke(cli, ["remove-block", "--id", "solo",
                                          "--dry-run", "--json"])
        payload = json.loads(result.stdout)
        assert payload["descendants"] == 0
        assert payload["blocks_removed"] == 1
        _assert_no_mutation(api)

    def test_without_dry_run_removes(self, api):
        api.get_block.return_value = {"uuid": "root", "content": "Parent"}
        result = CliRunner().invoke(cli, ["remove-block", "--id", "root"])
        assert result.exit_code == 0
        api.remove_block.assert_called_once_with("root")


class TestCopyBlockDryRun:
    def test_dry_run_does_not_write(self, api):
        api.get_block.return_value = {
            "uuid": "root", "content": "Parent",
            "children": [{"uuid": "c1", "content": "Kind"}],
        }
        result = CliRunner().invoke(cli, ["copy-block", "--id", "root",
                                          "--to-page", "Target", "--dry-run", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["blocks"] == 2
        assert payload["action"] == "copy"
        assert payload["removes_source"] is False
        _assert_no_mutation(api)

    def test_dry_run_move_flags_source_removal(self, api):
        api.get_block.return_value = {"uuid": "root", "content": "Parent"}
        result = CliRunner().invoke(cli, ["copy-block", "--id", "root",
                                          "--to-page", "Target", "--remove",
                                          "--dry-run", "--json"])
        payload = json.loads(result.stdout)
        assert payload["action"] == "move"
        assert payload["removes_source"] is True
        _assert_no_mutation(api)


class TestDeletePageGate:
    """--json must never act as an implicit --force (clig.dev separates the two)."""

    def test_dry_run_does_not_delete(self, api):
        api.get_page.return_value = {"name": "X"}
        api.get_page_blocks_tree.return_value = [{"uuid": "b1", "content": "a"}]
        result = CliRunner().invoke(cli, ["delete-page", "--name", "X", "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        _assert_no_mutation(api)

    def test_json_without_force_refuses_when_not_a_tty(self, api):
        api.get_page.return_value = {"name": "X"}
        api.get_page_blocks_tree.return_value = [{"uuid": "b1", "content": "a"}]
        # CliRunner supplies a non-TTY stdin, i.e. the agent/script case.
        result = split_runner().invoke(cli, ["delete-page", "--name", "X", "--json"])
        assert result.exit_code == 1
        assert result.stdout == ""
        payload = json.loads(result.stderr)
        assert "--force" in payload["error"]
        _assert_no_mutation(api)

    def test_force_deletes_without_prompt(self, api):
        api.get_page.return_value = {"name": "X"}
        api.get_page_blocks_tree.return_value = [{"uuid": "b1", "content": "a"}]
        result = CliRunner().invoke(cli, ["delete-page", "--name", "X", "--force"])
        assert result.exit_code == 0
        api.delete_page.assert_called_once_with("X")

    def test_missing_page_errors(self, api):
        api.get_page.return_value = None
        result = CliRunner().invoke(cli, ["delete-page", "--name", "Nope", "--force"])
        assert result.exit_code == 1
        _assert_no_mutation(api)
