"""The read-only analysis commands: do they run at all?

These six had no test. They cannot lose data, which is why they were never
urgent, but an exception in one of them is still a command that does not work
— and nothing would have noticed. This is deliberately a smoke test: it asserts
that each one runs, emits valid JSON and survives an empty graph, not what the
analysis concludes.
"""

import json
import os
import re
import time
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import journal_day, split_runner

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
         patch("logseq_cli.group.LogseqAPI", return_value=api_with_content()):
        result = split_runner().invoke(cli, ["--token", "X", command, *args, "--json"])
    assert result.exit_code == 0, result.stderr or result.stdout
    json.loads(result.stdout)


@pytest.mark.parametrize("command,args", COMMANDS, ids=[c for c, _ in COMMANDS])
def test_survives_an_empty_graph(command, args, tmp_path):
    """A fresh graph must not divide by zero or index into nothing."""
    cfg = tmp_path / "c.toml"
    cfg.write_text("", encoding="utf-8")
    with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(cfg)}, clear=False), \
         patch("logseq_cli.group.LogseqAPI", return_value=empty_api()):
        result = split_runner().invoke(cli, ["--token", "X", command, *args, "--json"])
    assert result.exit_code == 0, f"{command}: {result.exception!r} {result.stderr}"
    json.loads(result.stdout)


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
         patch("logseq_cli.group.LogseqAPI", return_value=api):
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


def run_result(api, tmp_path, *args):
    cfg = tmp_path / "c.toml"
    cfg.write_text("", encoding="utf-8")
    with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(cfg)}, clear=False), \
         patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, ["--token", "X", *args])


ELEVEN = {"NOW", "LATER", "TODO", "DOING", "IN-PROGRESS", "WAIT", "WAITING", "STARTED",
          "DONE", "CANCELED", "CANCELLED"}
TREE = [
    "TODO a", "WAITING b", "STARTED c", "DONE d", "DONE e", "CANCELED f",
    "note\n* [ ] a", "[ ] b", "TODO: c", "TODO\nnotes", "note\n[ ] c", "* [ ] d", "## [ ] e",
]
SIX_ROWS = [["TODO", 1], ["WAITING", 1], ["STARTED", 1], ["DONE", 2], ["CANCELED", 1]]


class TestAnalyzeGraphCountsOpenTasks:
    """Tasks are the blocks Logseq gives a marker, counted by one query.

    The count used to be a pattern over the page text, and it counted what only
    looked like a task. The mock here answers two things apart: the page tree,
    with the text of every block, and the answer of the task query, with the
    blocks that carry a marker. It applies no marker rule of its own, so a
    number that comes out right can only come from the answer of the query.
    """

    def _api(self, trees=None, rows=SIX_ROWS):
        trees = trees if trees is not None else {"P": TREE}
        api = MagicMock()
        api.get_all_pages.return_value = [{"originalName": n} for n in trees]
        api.get_page_blocks_tree.side_effect = lambda n: (
            None if trees[n] is None else
            [{"uuid": str(i), "content": c, "children": []} for i, c in enumerate(trees[n])])
        api.get_page.return_value = {"name": "P"}
        api.datascript_query.return_value = rows
        return api

    def test_counts_come_from_the_query_answer_not_from_text(self, tmp_path):
        d = run_json(self._api(), tmp_path, "analyze-graph")
        assert d["tasks"] == {"open": 3, "done": 2, "cancelled": 1,
                              "open_by_marker": {"STARTED": 1, "TODO": 1, "WAITING": 1}}
        assert list(d["tasks"]["open_by_marker"]) == ["STARTED", "TODO", "WAITING"]
        assert "total_todos" not in d and "checkboxes" not in d

    def test_the_query_names_exactly_the_eleven_markers(self, tmp_path):
        api = self._api({"P": TREE, "Q": ["plain"]})
        run_json(api, tmp_path, "analyze-graph")
        assert len(api.datascript_query.call_args_list) == 1
        query = api.datascript_query.call_args_list[0].args[0]
        assert ":block/marker" in query and "(count ?b)" in query
        asked = set(re.findall(r'"([A-Z-]+)"', re.search(r"#\{([^}]*)\}", query).group(1)))
        assert asked == ELEVEN

    def test_the_note_counts_blocks_that_start_with_a_box(self, tmp_path):
        result = run_result(self._api(), tmp_path, "analyze-graph", "--json")
        assert ('Note: 1 blocks start with "[ ]", which Logseq shows as text, '
                'not as a task or checkbox.') in result.stderr
        none = self._api({"P": ["TODO a", "plain\n[ ] b", "* [ ] c"]})
        assert "Note:" not in run_result(none, tmp_path, "analyze-graph", "--json").stderr
        many = self._api({"P": ["[x] a", "[X] b", "  [ ] c", "[ ]d"]})
        assert 'Note: 4 blocks start with "[ ]"' in \
            run_result(many, tmp_path, "analyze-graph", "--json").stderr

    def test_text_line_shows_the_open_markers_in_order(self, tmp_path):
        out = run_result(self._api(), tmp_path, "analyze-graph").stdout
        assert "Tasks: 3 open (1 STARTED, 1 TODO, 1 WAITING), 2 done, 1 cancelled" in out
        assert "Open TODOs:" not in out

    def test_no_open_tasks_drops_the_parenthesis(self, tmp_path):
        api = self._api(rows=[["DONE", 2]])
        d = run_json(api, tmp_path, "analyze-graph")
        assert d["tasks"]["open_by_marker"] == {}
        out = run_result(api, tmp_path, "analyze-graph").stdout
        assert "Tasks: 0 open, 2 done, 0 cancelled" in out

    def test_a_block_without_marker_does_not_count_whatever_the_text_says(self, tmp_path):
        d = run_json(self._api(rows=[]), tmp_path, "analyze-graph")
        assert d["tasks"] == {"open": 0, "done": 0, "cancelled": 0, "open_by_marker": {}}

    def test_open_tasks_equal_the_count_of_get_todos(self, tmp_path):
        """Both commands make their number from the same three blocks. A mock
        cannot show that the database answers both queries alike; the live runs
        do."""
        def answer(query):
            if "(pull ?b" in query:
                return [({"content": f"TODO {c}", "marker": "TODO", "uuid": f"u{c}"},
                         {"original-name": "P", "name": "p"}) for c in "abc"]
            return [["TODO", 3]]
        api = self._api()
        api.datascript_query.side_effect = answer
        assert run_json(api, tmp_path, "analyze-graph")["tasks"]["open"] == 3
        assert run_json(api, tmp_path, "get-todos", "--no-follow-refs")["count"] == 3

    def test_days_help_says_what_it_limits(self):
        out = split_runner().invoke(cli, ["analyze-graph", "--help"]).output
        assert "Recently updated" in " ".join(out.split())

    def test_open_markers_follow_tasks_order_not_the_alphabet(self, tmp_path):
        api = self._api({"P": ["NOW a", "IN-PROGRESS b", "DOING c"]},
                        rows=[["NOW", 1], ["IN-PROGRESS", 1], ["DOING", 1]])
        d = run_json(api, tmp_path, "analyze-graph")
        assert list(d["tasks"]["open_by_marker"]) == ["DOING", "NOW", "IN-PROGRESS"]
        out = run_result(api, tmp_path, "analyze-graph").stdout
        assert "Tasks: 3 open (1 DOING, 1 NOW, 1 IN-PROGRESS), 0 done, 0 cancelled" in out

    def test_a_page_without_a_tree_is_skipped(self, tmp_path):
        result = run_result(self._api({"P": None}, rows=[["TODO", 1]]), tmp_path,
                            "analyze-graph", "--json")
        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["tasks"] == {
            "open": 1, "done": 0, "cancelled": 0, "open_by_marker": {"TODO": 1}}
        assert "Note:" not in result.stderr

    def test_a_box_block_under_a_parent_is_found(self, tmp_path):
        api = self._api(rows=[])
        api.get_page_blocks_tree.side_effect = lambda n: [
            {"uuid": "1", "content": "parent", "children": [
                {"uuid": "2", "content": "[ ] nested", "children": []}]}]
        result = run_result(api, tmp_path, "analyze-graph", "--json")
        assert 'Note: 1 blocks start with "[ ]"' in result.stderr

    def test_days_limits_only_the_recently_updated_list(self, tmp_path):
        now = int(time.time() * 1000)
        api = self._api()
        api.get_all_pages.return_value = [
            {"originalName": "P", "updatedAt": now},
            {"originalName": "Q", "updatedAt": now - 40 * 86400 * 1000}]
        api.get_page_blocks_tree.side_effect = lambda n: []
        d = run_json(api, tmp_path, "analyze-graph", "--days", "30")
        assert [r["page"] for r in d["recently_updated"]] == ["P"]
        assert d["total_pages"] == 2 and d["tasks"]["open"] == 3

    def test_a_marker_outside_state_is_skipped(self, tmp_path):
        d = run_json(self._api(rows=[["TODO", 1], ["FOO", 7]]), tmp_path, "analyze-graph")
        assert d["tasks"]["open"] == 1 and d["tasks"]["open_by_marker"] == {"TODO": 1}
        assert "FOO" not in json.dumps(d)

    def test_a_query_answer_of_none_counts_zero(self, tmp_path):
        api = self._api(rows=None)
        assert run_json(api, tmp_path, "analyze-graph")["tasks"] == {
            "open": 0, "done": 0, "cancelled": 0, "open_by_marker": {}}
        assert "Tasks: 0 open, 0 done, 0 cancelled" in run_result(api, tmp_path,
                                                                  "analyze-graph").stdout


def _sent_query(tmp_path, request):
    api = empty_api()
    run_result(api, tmp_path, "smart-query", "--request", request, "--json")
    return api.datascript_query.call_args.args[0]


def _markers_in(query):
    return set(re.findall(r'"([A-Z-]+)"', query))


OPEN_MARKERS = {"NOW", "LATER", "TODO", "DOING", "IN-PROGRESS", "WAIT", "WAITING", "STARTED"}


class TestSmartQueryTaskPatterns:
    """The patterns for open and done tasks ask for what Logseq reads as such,
    not for a list of their own."""

    def test_tasks_pattern_asks_for_every_open_marker(self, tmp_path):
        query = _sent_query(tmp_path, "tasks")
        assert ":block/marker" in query
        assert _markers_in(query) == OPEN_MARKERS and "WAITING" in query

    # The keywords of the pattern are product data, German ones included: with
    # an English stand-in the test would not show that every word of it works.
    @pytest.mark.parametrize("word", ["todo", "task", "tasks", "incomplete", "pending",
                                      "aufgaben", "offene", "offen"])
    def test_every_keyword_of_the_pattern_reaches_it(self, tmp_path, word):
        assert _sent_query(tmp_path, word) == _sent_query(tmp_path, "tasks")
        assert _markers_in(_sent_query(tmp_path, word)) == OPEN_MARKERS

    def test_done_pattern_asks_for_exactly_done(self, tmp_path):
        assert _markers_in(_sent_query(tmp_path, "done")) == {"DONE"}


class TestFindKnowledgeGapsIgnoresArtefacts:
    """596 "orphans" in a real graph were almost all side effects.

    Logseq turns `#272` in a sentence into a page named "272", and a stray
    bracket into a page of its own. They are genuinely unreferenced, so the
    answer was true and useless: the one real gap was buried under hundreds of
    them, while "596 orphaned pages" reads as a call to action.
    """

    ARTEFACTS = [")", "-", "1", "272", "-AI", "2025_10_10", "2025-10-10",
                 "2025/10/10", "..."]

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
        from logseq_cli.commands.analysis import _is_incidental_page
        for name in ("3b82f6)", "508-workaround)", "Active)"):
            assert _is_incidental_page(name), name

    def test_a_name_with_balanced_brackets_is_kept(self):
        from logseq_cli.commands.analysis import _is_incidental_page
        assert not _is_incidental_page("Project (Phase 1)")

    def test_a_ticket_number_that_took_the_next_word_is_ignored(self):
        """"#123-Entwurf" in prose becomes a page of that name."""
        from logseq_cli.commands.analysis import _is_incidental_page
        assert _is_incidental_page("123-Entwurf")
        assert _is_incidental_page("456-Nachtrag")

    def test_a_real_term_starting_with_a_digit_is_kept(self):
        """Three digits or more, so "2-Faktor-Auth" is not caught by it."""
        from logseq_cli.commands.analysis import _is_incidental_page
        assert not _is_incidental_page("2-Faktor-Auth")
        assert not _is_incidental_page("3-Wege-Abgleich")

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

    "nicht erfolgreich" and "schmeckt nicht gut" both scored positive; in the
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
            {"originalName": "J", "journalDay": journal_day(), "journal?": True}]
        with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(cfg)}, clear=False), \
             patch("logseq_cli.group.LogseqAPI", return_value=api), \
             patch("logseq_cli.commands.analysis.get_page_content", return_value=text):
            r = split_runner().invoke(
                cli, ["--token", "X", "analyze-journal-patterns",
                      "--timeframe", "last 30 days", "--json"])
        assert r.exit_code == 0, r.stderr or r.stdout
        return json.loads(r.stdout)["mood_summary"]

    def test_a_negated_word_is_not_a_positive_signal(self, tmp_path):
        s = self._run("- der Kuchen schmeckt nicht gut", tmp_path)
        assert s["positive_signals"] == 0

    def test_a_stated_mood_counts(self, tmp_path):
        s = self._run("- stimmung: gut", tmp_path)
        assert s["positive_signals"] == 1

    def test_a_stated_negative_mood_counts(self, tmp_path):
        s = self._run("- mood: mies", tmp_path)
        assert s["negative_signals"] == 1

    def test_the_word_in_prose_alone_says_nothing(self, tmp_path):
        s = self._run("- der Kuchen war gut, das Rezept ist gut lesbar", tmp_path)
        assert s["positive_signals"] == 0
        assert s["negative_signals"] == 0
