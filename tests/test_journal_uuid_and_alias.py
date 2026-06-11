"""Tests for uuid return on journal-creation commands and the --name alias
parity across page-name commands."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli


def _japi():
    api = MagicMock()
    api.get_user_configs.return_value = {}
    api.get_page.return_value = {"name": "journal"}  # exists -> no create
    api.append_block_in_page.return_value = {"uuid": "j-append"}
    api.insert_block.return_value = {"uuid": "j-insert"}
    return api


class TestJournalUuidReturn:
    def test_add_journal_content_returns_uuid(self):
        api = _japi()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-content", "--content", "- entry",
                "--top-level", "--date", "2026-06-04", "--json",
            ])
        assert r.exit_code == 0, r.output
        d = json.loads(r.output)
        assert d["uuid"] == "j-append"
        assert d["uuids"] == ["j-append"]

    def test_add_journal_block_single_returns_uuid(self):
        api = _japi()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-block", "--content", "entry",
                "--top-level", "--date", "2026-06-04", "--json",
            ])
        assert r.exit_code == 0, r.output
        d = json.loads(r.output)
        assert d["uuid"] == "j-append"
        assert d["uuids"] == ["j-append"]

    def test_add_journal_block_batch_returns_uuids(self):
        api = _japi()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-block", "--content", "a", "--content", "b",
                "--top-level", "--date", "2026-06-04", "--json",
            ])
        assert r.exit_code == 0, r.output
        d = json.loads(r.output)
        assert d["blocks_added"] == 2
        assert d["uuids"] == ["j-append", "j-append"]


class TestNameAlias:
    """--name must be accepted wherever --page is (no 'no such option' / usage error)."""

    def test_find_block_accepts_name(self):
        api = MagicMock()
        api.datascript_query.return_value = []
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "find-block", "--content", "x", "--name", "SomePage", "--json",
            ])
        assert r.exit_code == 0, r.output  # would be 2 if --name were unknown

    def test_insert_block_accepts_name(self):
        api = MagicMock()
        api.append_block_in_page.return_value = {"uuid": "ib"}
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "insert-block", "--name", "SomePage", "--content", "x", "--json",
            ])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["uuid"] == "ib"
