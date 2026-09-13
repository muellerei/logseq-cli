"""`logseq-cli init` — suggesting a config from what the graph contains.

The risk here is not a crash: it is a plausible-looking suggestion that is
wrong. A section abandoned years ago still sits in hundreds of old journals,
and counting the whole history would rank it above the one in daily use. So
these tests care mostly about *which* value is proposed, and about never
overwriting a config the user already wrote.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import tomllib
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


def graph(recent_headings, old_headings=(), *, recent=120, old=200,
          namespaces=0, person_pages=0):
    """A fake graph: `recent` new journals and `old` older ones."""
    pages = [{"originalName": f"2026-09-{i:04d}", "journalDay": 20260000 + (10000 - i)}
             for i in range(recent)]
    pages += [{"originalName": f"2023-01-{i:04d}", "journalDay": 20230000 + i}
              for i in range(old)]
    pages += [{"originalName": f"projects/P{i}"} for i in range(namespaces)]
    pages += [{"originalName": f"Person {i}", "properties": {"type": "Person"}}
              for i in range(person_pages)]

    def tree(name):
        headings = recent_headings if name.startswith("2026") else old_headings
        return [{"content": h, "children": []} for h in headings]

    api = MagicMock()
    api.get_all_pages.return_value = pages
    api.get_page_blocks_tree.side_effect = tree
    return api


def run(api, *args):
    with patch("logseq_cli.cli.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, ["--token", "X", "init", *args])


def written_config(result):
    body = result.stdout.split("Would write", 1)[1].split(":\n", 1)[1]
    return tomllib.loads(body)


class TestSuggestionsFollowRecentUse:
    def test_a_section_that_was_abandoned_is_not_suggested(self):
        """The old journals outnumber the new ones; recency has to win."""
        api = graph(["## Log"], ["## Notes"], recent=120, old=200)
        cfg = written_config(run(api, "--dry-run"))
        assert cfg["journal"]["default_heading"] == "## Log"
        assert "## Notes" not in cfg["journal"]["headings"].values()

    def test_counts_are_shown_so_a_suggestion_can_be_checked(self):
        api = graph(["## Log"])
        out = run(api, "--dry-run").stdout
        assert "120/120" in out
        assert "## Log" in out

    def test_equally_common_sections_are_named_rather_than_silently_dropped(self):
        """Counting cannot separate them, and they serve different purposes."""
        api = graph(["## Tasks", "## Log"])
        out = run(api, "--dry-run").stdout
        assert "just as common" in out

    def test_a_page_link_heading_is_not_the_default(self):
        """"## [[Meeting]]" collects meetings; prose does not go there."""
        api = graph(["## [[Meeting]]", "## Log"])
        cfg = written_config(run(api, "--dry-run"))
        assert cfg["journal"]["default_heading"] == "## Log"

    def test_namespace_and_person_value_come_from_the_graph(self):
        api = graph(["## Log"], namespaces=10, person_pages=25)
        cfg = written_config(run(api, "--dry-run"))
        assert cfg["graph"]["projects_namespace"] == "projects/"
        assert cfg["graph"]["person_value"] == "Person"

    def test_nothing_to_find_leaves_settings_commented_out(self):
        """A guess would be worse than an empty section here."""
        api = graph(["## Log"], namespaces=0, person_pages=0)
        cfg = written_config(run(api, "--dry-run"))
        assert "projects_namespace" not in cfg.get("graph", {})
        assert "person_value" not in cfg.get("graph", {})

    def test_output_is_valid_toml_and_loadable(self, tmp_path):
        api = graph(["## Log", "## [[Meeting]]"], namespaces=3, person_pages=2)
        target = tmp_path / "c.toml"
        run(api, "--output", str(target))
        from logseq_cli.config import load_config
        assert load_config(target)["journal"]["default_heading"] == "## Log"


class TestWriting:
    def test_writes_the_file(self, tmp_path):
        target = tmp_path / "nested" / "config.toml"
        result = run(graph(["## Log"]), "--output", str(target))
        assert result.exit_code == 0
        assert target.exists()

    def test_refuses_to_overwrite_without_force(self, tmp_path):
        target = tmp_path / "config.toml"
        target.write_text("# mine\n", encoding="utf-8")
        result = run(graph(["## Log"]), "--output", str(target))
        assert result.exit_code != 0
        assert target.read_text(encoding="utf-8") == "# mine\n"
        assert "--force" in (result.stderr or "") + result.stdout

    def test_force_overwrites(self, tmp_path):
        target = tmp_path / "config.toml"
        target.write_text("# mine\n", encoding="utf-8")
        result = run(graph(["## Log"]), "--output", str(target), "--force")
        assert result.exit_code == 0
        assert "# mine" not in target.read_text(encoding="utf-8")

    def test_dry_run_writes_nothing(self, tmp_path):
        target = tmp_path / "config.toml"
        result = run(graph(["## Log"]), "--output", str(target), "--dry-run")
        assert result.exit_code == 0
        assert not target.exists()

    def test_json_carries_the_config_and_the_evidence(self, tmp_path):
        target = tmp_path / "config.toml"
        result = run(graph(["## Log"]), "--output", str(target), "--dry-run", "--json")
        payload = json.loads(result.stdout)
        assert "config" in payload
        assert payload["suggestions"]["journals_examined"] == 120


class TestEmptyGraph:
    def test_no_journals_is_reported_not_crashed(self, tmp_path):
        api = graph([], recent=0, old=0)
        result = run(api, "--output", str(tmp_path / "c.toml"), "--dry-run")
        assert result.exit_code == 0
        assert "No journals found" in result.stdout


class TestTiesAreNamedNotDecided:
    """A tie broken by insertion order is a coin toss presented as a finding.

    Found against a real graph: two namespaces with ten pages each, the wrong
    one written into the config with "# 10 pages live under this prefix" next
    to it, after which smart-query returned ten confident non-results.
    """

    def test_namespace_tie_names_the_rival(self):
        api = graph(["## Log"])
        pages = api.get_all_pages.return_value
        pages += [{"originalName": f"alpha/P{i}"} for i in range(10)]
        pages += [{"originalName": f"beta/P{i}"} for i in range(10)]
        out = run(api, "--dry-run").stdout
        assert "just as common" in out
        assert "beta/" in out or "alpha/" in out
        assert "counting cannot tell them apart" in out

    def test_clear_namespace_winner_gets_no_note(self):
        api = graph(["## Log"])
        pages = api.get_all_pages.return_value
        pages += [{"originalName": f"alpha/P{i}"} for i in range(10)]
        pages += [{"originalName": f"beta/P{i}"} for i in range(2)]
        cfg = written_config(run(api, "--dry-run"))
        assert cfg["graph"]["projects_namespace"] == "alpha/"
        assert "just as common, and possibly the namespace" not in run(api, "--dry-run").stdout

    def test_person_value_tie_names_the_rival(self):
        api = graph(["## Log"])
        pages = api.get_all_pages.return_value
        pages += [{"originalName": f"A{i}", "properties": {"type": "Person"}} for i in range(5)]
        pages += [{"originalName": f"B{i}", "properties": {"type": "Contact"}} for i in range(5)]
        out = run(api, "--dry-run").stdout
        assert "type:: value you want" in out

    def test_the_file_still_parses_with_the_notes_in_it(self):
        """The notes are comments; they must not break the TOML."""
        api = graph(["## Log"])
        pages = api.get_all_pages.return_value
        pages += [{"originalName": f"alpha/P{i}"} for i in range(10)]
        pages += [{"originalName": f"beta/P{i}"} for i in range(10)]
        cfg = written_config(run(api, "--dry-run"))
        assert cfg["graph"]["projects_namespace"] in ("alpha/", "beta/")
