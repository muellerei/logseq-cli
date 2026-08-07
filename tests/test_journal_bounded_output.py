"""Output-bounding flags for the journal read commands.

get-journal-range over a month and get-journal-summary over a week both emit
six-figure character counts on a real graph — far past what fits comfortably in
an agent's context. --tail/--limit/--heading and --no-content narrow that.

Two invariants matter beyond the plain filtering:
  * --tail/--limit are applied BEFORE fetching, so skipped days cost no API call.
  * Truncation is never silent: a shortened result announces what it left out.
"""
import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


def _journal_pages(days):
    """Fake getAllPages payload: journalDay ints like 20260801."""
    return [{"originalName": f"2026-08-{d:02d}, day", "name": f"2026-08-{d:02d}, day",
             "journalDay": int(f"202608{d:02d}")} for d in days]


@pytest.fixture
def api(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("logseq_cli.cli.LogseqAPI", lambda **kwargs: mock)
    mock.get_all_pages.return_value = _journal_pages(range(1, 11))  # 1..10 Aug
    mock.get_page_blocks_tree.return_value = [
        {"uuid": "h", "content": "## Log",
         "children": [{"uuid": "c", "content": "**09:00** etwas"}]},
        {"uuid": "o", "content": "## Tasks",
         "children": [{"uuid": "t", "content": "TODO offen"}]},
    ]
    return mock


class TestJournalRangeTailLimit:
    def test_tail_keeps_newest_days(self, api):
        # split_runner: the truncation note goes to stderr and would otherwise
        # be interleaved into stdout by the default (stream-merging) runner.
        result = split_runner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-10",
            "--tail", "3", "--json"])
        assert result.exit_code == 0
        dates = [e["date"] for e in json.loads(result.stdout)]
        assert dates == ["2026-08-08", "2026-08-09", "2026-08-10"]

    def test_limit_keeps_oldest_days(self, api):
        result = split_runner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-10",
            "--limit", "2", "--json"])
        dates = [e["date"] for e in json.loads(result.stdout)]
        assert dates == ["2026-08-01", "2026-08-02"]

    def test_tail_skips_fetching_omitted_days(self, api):
        """The point of filtering before the fetch: omitted days cost nothing."""
        CliRunner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-10",
            "--tail", "2", "--json"])
        assert api.get_page_blocks_tree.call_count == 2

    def test_no_flag_fetches_everything(self, api):
        CliRunner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-10", "--json"])
        assert api.get_page_blocks_tree.call_count == 10

    def test_truncation_is_announced_on_stderr(self, api):
        result = split_runner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-10",
            "--tail", "3", "--json"])
        assert "3 of 10" in result.stderr
        assert "7 omitted" in result.stderr
        json.loads(result.stdout)  # stdout stays pure payload

    def test_no_truncation_note_when_nothing_omitted(self, api):
        """No note when the full range is returned — avoid crying wolf."""
        result = split_runner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-10",
            "--tail", "20", "--json"])
        assert "omitted" not in result.stderr

    def test_tail_and_limit_are_mutually_exclusive(self, api):
        result = CliRunner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-10",
            "--tail", "3", "--limit", "2"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    @pytest.mark.parametrize("flag", ["--tail", "--limit"])
    def test_zero_is_rejected(self, api, flag):
        result = CliRunner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-10",
            flag, "0"])
        assert result.exit_code != 0
        assert ">= 1" in result.output


class TestJournalRangeHeading:
    def test_heading_narrows_to_one_section(self, api):
        result = CliRunner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-02",
            "--heading", "## Log", "--json"])
        entries = json.loads(result.stdout)
        for entry in entries:
            contents = [b["content"] for b in entry["blocks"]]
            assert contents == ["## Log"]
            assert "## Tasks" not in contents

    def test_without_heading_all_sections_present(self, api):
        result = CliRunner().invoke(cli, [
            "get-journal-range", "--from", "2026-08-01", "--to", "2026-08-01", "--json"])
        contents = [b["content"] for b in json.loads(result.stdout)[0]["blocks"]]
        assert "## Log" in contents and "## Tasks" in contents


class TestJournalSummaryNoContent:
    def test_no_content_drops_bodies_but_keeps_length(self, api, monkeypatch):
        monkeypatch.setattr("logseq_cli.cli.get_page_content",
                            lambda api_, name: "x" * 500 + " [[Alice]]")
        result = CliRunner().invoke(cli, [
            "get-journal-summary", "--range", "this year", "--no-content", "--json"])
        payload = json.loads(result.stdout)
        assert payload["content_omitted"] is True
        for entry in payload["entries"]:
            assert "content" not in entry
            assert entry["content_length"] == 510
            assert entry["topics"] == ["Alice"]

    def test_default_still_includes_content(self, api, monkeypatch):
        monkeypatch.setattr("logseq_cli.cli.get_page_content",
                            lambda api_, name: "voller text")
        result = CliRunner().invoke(cli, [
            "get-journal-summary", "--range", "this year", "--json"])
        payload = json.loads(result.stdout)
        assert "content_omitted" not in payload
        assert payload["entries"][0]["content"] == "voller text"
