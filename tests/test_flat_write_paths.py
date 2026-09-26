"""The flat one-block write paths must fail as loudly as the tree paths.

A check of the returned uuid was added because Logseq answers a failed write
with HTTP 200 + `null`, and it was applied thoroughly to the tree/helper paths.
The flat paths in cli.py called `api.*` directly and slipped past it, so the
same command reported success or aborted depending on whether the content
happened to carry a tab:

    add-journal-block --content "**14:30** X"           -> exit 0, nothing written
    add-journal-block --content "**14:30** X\n\t- Detail" -> exit 1

Every command guarded here writes into the journal or the block-ref network, so a
silent miss means a log entry or a TODO link that looks present and is not.

The check sits in LogseqAPI now (spec 030), so a failed insert is run against
the HTTP double, where the real API sees Logseq's `null`; a method mock would
never raise.
"""
import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

JOURNAL = ["--date", "2099-01-05"]


def _dead_graph(monkeypatch, *, from_call=1):
    """Logseq answering insertBlock and appendBlockInPage with ``null`` and
    writing nothing, as it does on a page it has not loaded; with
    ``from_call`` the calls before it still land."""
    double = LogseqHttpDouble()
    double.add_page("2099-01-05, Monday", [{"content": "## Log", "children": ["logged"]}])
    double.add_page("P", ["## Refs", "src block"])
    for method in ("insertBlock", "appendBlockInPage"):
        double.set_mode(method, "noop", from_call=from_call)
    return double.install(monkeypatch)


def _run_json(args):
    """The command under --json: its result and the error object on stderr."""
    r = split_runner().invoke(cli, ["--token", "t", *args, "--json"])
    start = r.stderr.find("{")
    return r, (json.loads(r.stderr[start:]) if start >= 0 else None)


def _dead_api():
    """API that accepts every write and persists none, as Logseq does on a
    page it has not loaded. For updateBlock, which has no proof in LogseqAPI
    yet; an insert here would not raise as the real method does."""
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

    def test_under_heading_fails_loudly(self, monkeypatch):
        _dead_graph(monkeypatch)
        r = CliRunner().invoke(cli, [
            "--token", "t", "add-journal-block", *JOURNAL, "--under-heading", "## Log",
            "--content", "**14:30** Entry"])
        assert r.exit_code == 1
        assert "Added" not in r.output
        assert "did not show in Logseq" in r.output

    def test_top_level_fails_loudly(self, monkeypatch):
        _dead_graph(monkeypatch)
        r = CliRunner().invoke(cli, [
            "--token", "t", "add-journal-block", *JOURNAL, "--content", "**14:30** Entry",
            "--top-level"])
        assert r.exit_code == 1
        assert "Added" not in r.output
        assert "did not show in Logseq" in r.output

    def test_json_mode_does_not_report_a_phantom_block(self, monkeypatch):
        _dead_graph(monkeypatch)
        r, error = _run_json(["add-journal-block", *JOURNAL, "--under-heading", "## Log",
                              "--content", "**14:30** X"])
        assert r.exit_code == 1
        assert r.stdout == ""
        assert (error["reason"], error["method"]) == ("write_not_verified", "insertBlock")

    def test_successful_write_still_reports(self):
        api = _live_api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-journal-block", "--under-heading", "## Log",
                "--content", "**14:30** Entry"])
        assert r.exit_code == 0, r.output
        assert "Added block to journal" in r.output


class TestAddBlockRef:
    def test_failed_ref_is_not_reported_as_added(self, monkeypatch):
        double = _dead_graph(monkeypatch)
        r, error = _run_json(["add-block-ref", "--source-id", double.uuid_of("src block"),
                              "--page", "P", "--under-heading", "## Refs"])
        assert r.exit_code == 1
        assert "Added block-ref" not in r.output
        assert (error["reason"], error["method"]) == ("write_not_verified", "insertBlock")
        assert error["target"] == double.uuid_of("## Refs")

    def test_successful_ref_reports_its_uuid(self):
        api = _live_api("ref-uuid")
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "add-block-ref", "--source-id", "src", "--page", "P",
                "--under-heading", "## Log"])
        assert r.exit_code == 0, r.output
        assert "ref-uuid" in r.output


class TestAddJournalEntry:
    def test_count_comes_from_writes_not_from_line_count(self, monkeypatch):
        """One line lands, two do not: it must not claim three."""
        _dead_graph(monkeypatch, from_call=2)
        r, error = _run_json(["add-journal-entry", *JOURNAL, "--multi-block",
                              "--content", "A\nB\nC"])
        assert r.exit_code == 1
        assert "Added 3 block(s)" not in r.output
        # Names the partial state.
        assert error["writes_landed"] == 1
        assert "1 earlier write(s) in this call landed and remain" in error["error"]

    def test_as_block_failure_aborts(self, monkeypatch):
        _dead_graph(monkeypatch)
        r, error = _run_json(["add-journal-entry", *JOURNAL, "--content", "Text"])
        assert r.exit_code == 1
        assert error["reason"] == "write_not_verified"
        assert "Nothing was written." in error["error"]

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
    def test_failed_content_write_aborts(self, monkeypatch):
        _dead_graph(monkeypatch)
        r, error = _run_json(["create-page", "--name", "New", "--content", "Text"])
        assert r.exit_code == 1
        assert (error["reason"], error["method"]) == ("write_not_verified", "appendBlockInPage")
        # The page was created before the text failed, and stays.
        assert error["writes_landed"] == 1

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

    def test_write_that_did_not_land_fails_under_json_too(self):
        """--json exited 0 here, with the miss only in a `failed` field (#93)."""
        api = self._api("old here")
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "old", "--replace", "new",
                "--json"])
        assert r.exit_code != 0
        assert json.loads(r.stdout)["failed"] == ["b1"]
        error = json.loads(r.stderr)
        assert error["reason"] == "write_not_verified"
        assert error["failed"] == ["b1"]

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
