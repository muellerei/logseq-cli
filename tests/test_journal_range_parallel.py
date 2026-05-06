"""Tests for parallel fetching in get-journal-range."""

import json as _json
import threading
import time as _time
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli


def _make_journal_pages(dates):
    """Build a get_all_pages return value with one journal page per date string."""
    pages = []
    for d in dates:
        # date string YYYY-MM-DD -> journalDay int
        jd = int(d.replace("-", ""))
        pages.append({
            "name": d.lower(),
            "originalName": d,
            "journalDay": jd,
        })
    return pages


class TestJournalRangeParallel:
    def test_results_sorted_by_date_despite_completion_order(self, monkeypatch):
        """Random per-page latency must not affect output ordering."""
        dates = [f"2026-04-{d:02d}" for d in range(20, 25)]
        api = MagicMock()
        api.get_all_pages.return_value = _make_journal_pages(dates)
        api.get_page_linked_references.return_value = []

        # Latency: later dates resolve faster, earlier slower
        latencies = {d: (5 - i) * 0.01 for i, d in enumerate(dates)}

        def slow_blocks(page_name):
            _time.sleep(latencies.get(page_name, 0))
            return [{"content": f"block for {page_name}", "uuid": f"u-{page_name}", "children": []}]

        api.get_page_blocks_tree.side_effect = slow_blocks

        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "get-journal-range",
                "--from", "2026-04-20",
                "--to", "2026-04-24",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        out_dates = [e["date"] for e in data]
        assert out_dates == sorted(out_dates)
        assert out_dates == dates

    def test_single_day_range(self):
        api = MagicMock()
        api.get_all_pages.return_value = _make_journal_pages(["2026-04-20"])
        api.get_page_blocks_tree.return_value = [
            {"content": "x", "uuid": "u1", "children": []}
        ]
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "get-journal-range",
                "--from", "2026-04-20",
                "--to", "2026-04-20",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert len(data) == 1
        assert data[0]["date"] == "2026-04-20"

    def test_failing_day_does_not_abort_range(self):
        dates = [f"2026-04-{d:02d}" for d in range(20, 23)]
        api = MagicMock()
        api.get_all_pages.return_value = _make_journal_pages(dates)
        api.get_page_linked_references.return_value = []

        def maybe_fail(page_name):
            if page_name == "2026-04-21":
                raise RuntimeError("simulated error")
            return [{"content": f"ok for {page_name}", "uuid": f"u-{page_name}", "children": []}]

        api.get_page_blocks_tree.side_effect = maybe_fail

        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "get-journal-range",
                "--from", "2026-04-20",
                "--to", "2026-04-22",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        # We must still see all 3 dates in the output
        out_dates = [e["date"] for e in data]
        assert out_dates == dates
        # The failed day must be marked as error
        bad = [e for e in data if e["date"] == "2026-04-21"][0]
        assert bad.get("error") is not None

    def test_workers_env_respected(self, monkeypatch):
        """LOGSEQ_CLI_RANGE_WORKERS=1 forces sequential execution."""
        monkeypatch.setenv("LOGSEQ_CLI_RANGE_WORKERS", "1")
        dates = [f"2026-04-{d:02d}" for d in range(20, 23)]
        api = MagicMock()
        api.get_all_pages.return_value = _make_journal_pages(dates)
        api.get_page_blocks_tree.return_value = []

        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "get-journal-range",
                "--from", "2026-04-20",
                "--to", "2026-04-22",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert [e["date"] for e in data] == dates

    def test_ten_day_range_order(self):
        dates = [f"2026-04-{d:02d}" for d in range(10, 20)]
        api = MagicMock()
        api.get_all_pages.return_value = _make_journal_pages(dates)
        api.get_page_blocks_tree.return_value = []
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "get-journal-range",
                "--from", "2026-04-10",
                "--to", "2026-04-19",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert [e["date"] for e in data] == dates
