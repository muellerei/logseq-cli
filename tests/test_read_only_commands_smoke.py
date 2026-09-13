"""The read-only analysis commands: do they run at all?

These six had no test. They cannot lose data, which is why they were never
urgent, but an exception in one of them is still a command that does not work
— and nothing would have noticed. This is deliberately a smoke test: it asserts
that each one runs, emits valid JSON and survives an empty graph, not what the
analysis concludes.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner

COMMANDS = [
    ("analyze-graph", []),
    ("get-all-pages", []),
    ("get-page-stats", ["--name", "Page A"]),
    ("search-pages", ["--query", "Page"]),
    ("find-knowledge-gaps", []),
    ("suggest-connections", []),
]


def api_with_content():
    api = MagicMock()
    api.get_all_pages.return_value = [
        {"originalName": "Page A", "name": "page a"},
        {"originalName": "Page B", "name": "page b"},
    ]
    api.get_page.return_value = {"name": "Page A"}
    api.get_page_blocks_tree.return_value = [
        {"uuid": "1", "content": "- some text linking [[Page B]]", "children": []}
    ]
    api.search.return_value = {"blocks": []}
    api.datascript_query.return_value = []
    api.get_page_linked_references.return_value = []
    return api


def empty_api():
    api = MagicMock()
    api.get_all_pages.return_value = []
    api.get_page.return_value = None
    api.get_page_blocks_tree.return_value = []
    api.search.return_value = {"blocks": []}
    api.datascript_query.return_value = []
    api.get_page_linked_references.return_value = []
    return api


@pytest.mark.parametrize("command,args", COMMANDS, ids=[c for c, _ in COMMANDS])
def test_runs_and_emits_json(command, args, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("", encoding="utf-8")
    with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(cfg)}, clear=False), \
         patch("logseq_cli.cli.LogseqAPI", return_value=api_with_content()):
        result = split_runner().invoke(cli, ["--token", "X", command, *args, "--json"])
    assert result.exit_code == 0, result.stderr or result.stdout
    json.loads(result.stdout)


@pytest.mark.parametrize("command,args", COMMANDS, ids=[c for c, _ in COMMANDS])
def test_survives_an_empty_graph(command, args, tmp_path):
    """A fresh graph must not divide by zero or index into nothing."""
    cfg = tmp_path / "c.toml"
    cfg.write_text("", encoding="utf-8")
    with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(cfg)}, clear=False), \
         patch("logseq_cli.cli.LogseqAPI", return_value=empty_api()):
        result = split_runner().invoke(cli, ["--token", "X", command, *args, "--json"])
    assert result.exception is None or isinstance(result.exception, SystemExit), \
        f"{command} raised {result.exception!r}"


def graph_with(pages_topics):
    """A graph where each page links the given topics."""
    api = MagicMock()
    api.get_all_pages.return_value = [{"originalName": n} for n in pages_topics]
    api.get_page_blocks_tree.side_effect = lambda n: [
        {"uuid": "1", "content": f"[[{t}]]", "children": []}
        for t in pages_topics.get(n, [])
    ]
    api.get_page.return_value = {"name": "x"}
    api.search.return_value = {"blocks": []}
    api.datascript_query.return_value = []
    api.get_page_linked_references.return_value = []
    return api


def run_json(api, tmp_path, *args):
    cfg = tmp_path / "c.toml"
    cfg.write_text("", encoding="utf-8")
    with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(cfg)}, clear=False), \
         patch("logseq_cli.cli.LogseqAPI", return_value=api):
        result = split_runner().invoke(cli, ["--token", "X", *args, "--json"])
    assert result.exit_code == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


class TestSuggestConnectionsRanksEvidenceNotCoincidence:
    """Jaccard alone put the thinnest evidence on top.

    Two pages linking the same single page scored 1/1 = 1.0 and outranked a
    pair sharing 35 topics out of 38 — and --min-confidence 0.5 then filtered
    the good pairs out and kept the coincidences, working against its purpose.
    """

    NOISE_AND_SUBSTANCE = {
        "P1": ["Shared Topic"], "P2": ["Shared Topic"],          # one shared topic each
        "P3": ["X"], "P4": ["X"],
        "Strong A": [f"T{i}" for i in range(12)],    # eleven shared
        "Strong B": [f"T{i}" for i in range(11)],
    }

    def test_a_single_shared_topic_is_not_a_connection(self, tmp_path):
        d = run_json(graph_with(self.NOISE_AND_SUBSTANCE), tmp_path,
                     "suggest-connections")
        pairs = {(s["page_a"], s["page_b"]) for s in d["suggestions"]}
        assert ("P1", "P2") not in pairs
        assert ("P3", "P4") not in pairs

    def test_the_substantial_pair_survives(self, tmp_path):
        d = run_json(graph_with(self.NOISE_AND_SUBSTANCE), tmp_path,
                     "suggest-connections")
        assert d["suggestions"][0]["page_a"] == "Strong A"
        assert len(d["suggestions"][0]["shared_topics"]) == 11

    def test_min_shared_is_adjustable(self, tmp_path):
        """Lowering it brings the coincidences back, deliberately."""
        d = run_json(graph_with(self.NOISE_AND_SUBSTANCE), tmp_path,
                     "suggest-connections", "--min-shared", "1")
        assert d["total_found"] > 1

    def test_ties_on_confidence_prefer_more_shared_topics(self, tmp_path):
        topics = {
            "A": ["a", "b"], "B": ["a", "b"],                    # 2 shared, 1.0
            "C": [f"t{i}" for i in range(6)],
            "D": [f"t{i}" for i in range(6)],                    # 6 shared, 1.0
        }
        d = run_json(graph_with(topics), tmp_path,
                     "suggest-connections", "--min-shared", "2")
        assert len(d["suggestions"][0]["shared_topics"]) == 6


class TestAnalyzeGraphCountsOpenTasks:
    """total_todos matched "todo" anywhere, case-insensitively.

    That counted "Todo-Liste" in prose and the TODO inside a DONE block's
    logbook line, so the number was neither the open tasks nor all of them.
    """

    def _api(self, text):
        api = MagicMock()
        api.get_all_pages.return_value = [{"originalName": "P"}]
        api.get_page_blocks_tree.return_value = [
            {"uuid": "1", "content": text, "children": []}]
        api.get_page.return_value = {"name": "P"}
        return api

    def test_markers_count(self, tmp_path):
        d = run_json(self._api("- TODO one\n- LATER two\n- [ ] three"),
                     tmp_path, "analyze-graph")
        assert d["total_todos"] == 3

    def test_prose_does_not_count(self, tmp_path):
        d = run_json(self._api("- the Todo-Liste is long\n- => todo later maybe"),
                     tmp_path, "analyze-graph")
        assert d["total_todos"] == 0

    def test_a_logbook_line_does_not_count(self, tmp_path):
        """`State "DONE" from "TODO"` is history, not an open task."""
        d = run_json(self._api('- DONE shipped\n  :LOGBOOK:\n  * State "DONE" from "TODO"\n  :END:'),
                     tmp_path, "analyze-graph")
        assert d["total_todos"] == 0

    def test_an_empty_bracket_pair_in_prose_does_not_count(self, tmp_path):
        """A checkbox is `- [ ]` at the start of a block, not `[ ]` anywhere.

        The marker half of this pattern was anchored to the line but the
        checkbox half was not, so an empty pair inside running text counted:
        a code snippet (`tags = [ ]`), an empty markdown link, a table cell.
        A graph with no tasks at all reported three of them.

        analyze-journal-patterns requires the bullet and was already right;
        the two counters measure the same thing and must agree.
        """
        text = ("- tags = [ ] means an empty list\n"
                "- see [ ](https://example.com)\n"
                "- a sentence about [ ] brackets")
        d = run_json(self._api(text), tmp_path, "analyze-graph")
        assert d["total_todos"] == 0


class TestFindKnowledgeGapsIgnoresArtefacts:
    """596 "orphans" in a real graph were almost all side effects.

    Logseq turns `#272` in a sentence into a page named "272", and a stray
    bracket into a page of its own. They are genuinely unreferenced, so the
    answer was true and useless: the one real gap was buried under hundreds of
    them, while "596 orphaned pages" reads as a call to action.
    """

    ARTEFACTS = [")", "-", "1", "272", "-AI", "2025_10_10", "..."]

    def _graph(self, extra=(), trees=None):
        names = [*self.ARTEFACTS, "Real Knowledge Page", *extra]
        api = MagicMock()
        api.get_all_pages.return_value = [
            {"originalName": n, "name": n.lower()} for n in names]
        trees = trees or {}
        api.get_page_blocks_tree.side_effect = lambda n: [
            {"uuid": "1", "content": trees[n], "children": []}] if n in trees else []
        api.get_page.return_value = {"name": "x"}
        return api

    def test_artefacts_are_not_reported_as_orphans(self, tmp_path):
        d = run_json(self._graph(), tmp_path, "find-knowledge-gaps")
        for name in self.ARTEFACTS:
            assert name not in d["orphaned_pages"], name

    def test_prose_fragments_dragged_in_by_brackets_are_ignored(self):
        """"#Active)" and "3b82f6)" come from parentheses in a sentence."""
        from logseq_cli.cli import _is_incidental_page
        for name in ("3b82f6)", "508-workaround)", "Active)"):
            assert _is_incidental_page(name), name

    def test_a_name_with_balanced_brackets_is_kept(self):
        from logseq_cli.cli import _is_incidental_page
        assert not _is_incidental_page("Projekt (Phase 1)")

    def test_a_ticket_number_that_took_the_next_word_is_ignored(self):
        """"#272-Designentscheidung" in prose becomes a page of that name."""
        from logseq_cli.cli import _is_incidental_page
        assert _is_incidental_page("272-Designentscheidung")
        assert _is_incidental_page("149-Rekursionsrisiko")

    def test_a_real_term_starting_with_a_digit_is_kept(self):
        """Three digits or more, so "2-Faktor-Auth" is not caught by it."""
        from logseq_cli.cli import _is_incidental_page
        assert not _is_incidental_page("2-Faktor-Auth")
        assert not _is_incidental_page("4-Level-Struktur")

    def test_a_real_page_is_still_reported(self, tmp_path):
        """The filter must not swallow the finding it exists to surface."""
        d = run_json(self._graph(), tmp_path, "find-knowledge-gaps")
        assert "Real Knowledge Page" in d["orphaned_pages"]

    def test_a_date_in_file_form_is_not_a_missing_page(self, tmp_path):
        """[[2025_10_10]] is a journal spelled differently, not a gap."""
        api = self._graph(trees={"Real Knowledge Page": "- see [[2025_10_11]]"})
        d = run_json(api, tmp_path, "find-knowledge-gaps")
        assert all("2025_10_11" not in m["page"] for m in d["missing_pages"])

    def test_an_empty_page_with_a_written_namesake_is_not_a_gap(self, tmp_path):
        """An empty `Alpha` next to a written `projects/Alpha` is an anchor."""
        api = self._graph(
            extra=["Alpha", "projects/Alpha"],
            trees={"projects/Alpha": "- " + ("content " * 60),
                   "Real Knowledge Page": "- links [[Alpha]] " * 3})
        d = run_json(api, tmp_path, "find-knowledge-gaps", "--min-refs", "1")
        assert all(u["page"] != "Alpha" for u in d["underdeveloped_pages"])


class TestMoodCountsStatementsNotWords:
    """Counting every positive word measured technical prose, not mood.

    "nicht zufrieden" and "läuft nicht gut" both scored positive; in the
    journal this was checked against, 16% of positive hits were negations —
    concentrated in the sentences that actually carry a judgement.
    """

    CONFIG = ('[analysis]\nmood_positive = ["gut"]\n'
              'mood_negative = ["mies"]\nmood_labels = ["stimmung", "mood"]\n')

    def _run(self, text, tmp_path):
        cfg = tmp_path / "c.toml"
        cfg.write_text(self.CONFIG, encoding="utf-8")
        api = MagicMock()
        api.get_all_pages.return_value = [
            {"originalName": "J", "journalDay": 20260910, "journal?": True}]
        with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(cfg)}, clear=False), \
             patch("logseq_cli.cli.LogseqAPI", return_value=api), \
             patch("logseq_cli.cli.get_page_content", return_value=text):
            r = split_runner().invoke(
                cli, ["--token", "X", "analyze-journal-patterns",
                      "--timeframe", "last 30 days", "--json"])
        assert r.exit_code == 0, r.stderr or r.stdout
        return json.loads(r.stdout)["mood_summary"]

    def test_a_negated_word_is_not_a_positive_signal(self, tmp_path):
        s = self._run("- heute war nicht gut und ich bin nicht zufrieden", tmp_path)
        assert s["positive_signals"] == 0

    def test_a_stated_mood_counts(self, tmp_path):
        s = self._run("- stimmung: gut", tmp_path)
        assert s["positive_signals"] == 1

    def test_a_stated_negative_mood_counts(self, tmp_path):
        s = self._run("- mood: mies", tmp_path)
        assert s["negative_signals"] == 1

    def test_the_word_in_prose_alone_says_nothing(self, tmp_path):
        s = self._run("- der Build lief gut durch, Code ist gut lesbar", tmp_path)
        assert s["positive_signals"] == 0
        assert s["negative_signals"] == 0
