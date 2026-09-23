"""The flat one-block write paths must fail as loudly as the tree paths.

`require_insert` was added because Logseq answers a failed write with HTTP 200 +
`null`, and it was applied thoroughly to the tree/helper paths. The flat paths in
cli.py called `api.*` directly and slipped past it, so the same command reported
success or aborted depending on whether the content happened to carry a tab:

    add-journal-block --content "**14:30** X"           -> exit 0, nothing written
    add-journal-block --content "**14:30** X\n\t- Detail" -> exit 1

Every command guarded here writes into the journal or the block-ref network, so a
silent miss means a log entry or a TODO link that looks present and is not.
"""
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli


def _dead_api():
    """API that accepts every write and persists none, as Logseq does on a
    page it has not loaded."""
    api = MagicMock()
    api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    api.get_page.return_value = {"name": "journal"}
    api.get_page_blocks_tree.return_value = [
        {"uuid": "head", "content": "## Log", "children": []}]
    api.get_block.return_value = {"uuid": "head", "content": "## Log", "children": [],
                                  "page": {"id": 1}}
    api.insert_block.return_value = None
    api.append_block_in_page.return_value = None
    api.create_page.return_value = None
    return api


def _live_api(uuid="new-uuid"):
    api = _dead_api()
    api.insert_block.return_value = {"uuid": uuid}
    api.append_block_in_page.return_value = {"uuid": uuid}
    api.create_page.return_value = {"name": "p"}
    return api


class TestAddJournalBlockFlat:
    """The single-block path: the default shape of an auto-logged entry."""

    def test_under_heading_fails_loudly(self):
        api = _dead_api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-block", "--under-heading", "## Log",
                "--content", "**14:30** Entry"])
        assert r.exit_code == 1
        assert "Added" not in r.output

    def test_top_level_fails_loudly(self):
        api = _dead_api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-block", "--content", "**14:30** Entry", "--top-level"])
        assert r.exit_code == 1
        assert "Added" not in r.output

    def test_json_mode_does_not_report_a_phantom_block(self):
        api = _dead_api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-block", "--under-heading", "## Log",
                "--content", "**14:30** X", "--json"])
        assert r.exit_code == 1

    def test_successful_write_still_reports(self):
        api = _live_api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-block", "--under-heading", "## Log",
                "--content", "**14:30** Entry"])
        assert r.exit_code == 0, r.output
        assert "Added block to journal" in r.output


class TestAddBlockRef:
    def test_failed_ref_is_not_reported_as_added(self):
        api = _dead_api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-block-ref", "--source-id", "src", "--page", "P",
                "--under-heading", "## Log"])
        assert r.exit_code == 1
        assert "Added block-ref" not in r.output
        assert "did not create the block-ref" in r.output

    def test_successful_ref_reports_its_uuid(self):
        api = _live_api("ref-uuid")
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-block-ref", "--source-id", "src", "--page", "P",
                "--under-heading", "## Log"])
        assert r.exit_code == 0, r.output
        assert "ref-uuid" in r.output


class TestAddJournalEntry:
    def test_count_comes_from_writes_not_from_line_count(self):
        """One line lands, two do not: it must not claim three."""
        api = _dead_api()
        api.append_block_in_page.side_effect = [{"uuid": "u1"}, None, None]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-entry", "--multi-block", "--content", "A\nB\nC"])
        assert r.exit_code == 1
        assert "Added 3 block(s)" not in r.output
        assert "already written and remain" in r.output  # names the partial state

    def test_as_block_failure_aborts(self):
        api = _dead_api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-entry", "--content", "Text"])
        assert r.exit_code == 1

    def test_all_lines_land(self):
        api = _dead_api()
        api.append_block_in_page.side_effect = [
            {"uuid": "u1"}, {"uuid": "u2"}, {"uuid": "u3"}]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-entry", "--multi-block", "--content", "A\nB\nC"])
        assert r.exit_code == 0, r.output
        assert "Added 3 block(s)" in r.output


class TestCreatePageWithContent:
    # create-page refuses a page that already exists, so a run that is meant to
    # reach the write path has to start from an absent one. _dead_api answers
    # get_page with a page, which is the existing case.
    def test_failed_content_write_aborts(self):
        api = _dead_api()
        api.get_page.return_value = None
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "create-page", "--name", "New", "--content", "Text"])
        assert r.exit_code == 1

    def test_page_without_content_is_unaffected(self):
        api = _dead_api()
        api.get_page.return_value = None
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, ["create-page", "--name", "New"])
        assert r.exit_code == 0, r.output
        api.append_block_in_page.assert_not_called()


class TestReplaceTextVerifiesByReading:
    """updateBlock answers null either way, so the count must come from a read."""

    def _api(self, after_content):
        api = _dead_api()
        api.get_page_blocks_tree.return_value = [
            {"uuid": "b1", "content": "old here", "children": []}]
        api.get_block.return_value = {"uuid": "b1", "content": after_content}
        return api

    def test_write_that_did_not_land_is_reported(self):
        api = self._api("old here")  # unchanged: the update never took
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "old", "--replace", "new"])
        assert r.exit_code == 1
        assert "did not reach the graph" in r.output

    def test_write_that_landed_is_counted(self):
        api = self._api("new here")
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "old", "--replace", "new"])
        assert r.exit_code == 0, r.output
        assert "Replaced 1 block(s)" in r.output

    def test_dry_run_does_not_read_back_or_write(self):
        api = self._api("old here")
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "old", "--replace", "new",
                "--dry-run"])
        assert r.exit_code == 0, r.output
        assert "Would replace 1 block(s)" in r.output
        api.update_block.assert_not_called()
