"""A datalog query that never ran must not look like a query with no hits.

Logseq answers a broken datalog query with HTTP 200 and ``{"error": ...}`` in
the body instead of an error status. Before this, ``datascript_query`` handed
that dict to its callers unchanged: ``get-todos`` crashed unpacking it,
``find-block`` fabricated a match (the letter ``e`` from the key ``error``),
and every ``try/except`` around a query was dead code because nothing was
thrown. On a tool for finding notes, a false "nothing found" (or worse, a
fabricated "exactly one found" on a write path) is the failure that costs
trust.

Regression guard for that whole class of failure, not just one command.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.api import LogseqAPI, DatalogQueryError
from logseq_cli.datalog import (
    InvalidKeywordError,
    edn_keyword,
    edn_string,
    page_name_literal,
)
from logseq_cli.cli import cli
from tests.conftest import split_runner


QUERY_ERROR = {"error": "Cannot parse clause, expected (fn-call ...)"}


def _response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def _post_failing_datalog(url, json=None, headers=None, timeout=None):
    """Fake transport: datalog queries fail, every other method answers empty."""
    if json and json.get("method") == "logseq.DB.datascriptQuery":
        return _response(QUERY_ERROR)
    return _response([])


class TestDatascriptQueryRaises:
    """The API layer must turn the error dict into an exception."""

    def test_error_dict_raises_with_message_and_query(self):
        api = LogseqAPI(token="x")
        query = "[:find ?x :where KAPUTT]"
        with patch("logseq_cli.api.requests.post", return_value=_response(QUERY_ERROR)):
            with pytest.raises(DatalogQueryError) as exc:
                api.datascript_query(query)
        # Structured attributes, not just text: the JSON error output and the
        # tests need the fields without parsing a message string.
        assert "Cannot parse clause" in exc.value.api_message
        assert exc.value.query == query

    def test_empty_list_is_a_valid_zero_hit_result(self):
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", return_value=_response([])):
            assert api.datascript_query("[:find ?x :where [?x :block/name]]") == []

    def test_success_list_passes_through_unchanged(self):
        payload = [[{"name": "alice"}], [{"name": "bob"}]]
        api = LogseqAPI(token="x")
        with patch("logseq_cli.api.requests.post", return_value=_response(payload)):
            assert api.datascript_query("[:find (pull ?p [:block/name]) :where [?p :block/name]]") == payload


class TestCommandsWithoutOwnHandler:
    """The four commands with no try/except around their query.

    After the API layer starts throwing, these rely entirely on
    ``handle_connection_error`` catching ``DatalogQueryError``. Without that,
    they print a naked traceback while the suite stays green: exactly the
    intermediate state these tests exist to forbid.
    """

    COMMANDS = {
        "get-todos": ["get-todos", "--json"],
        "find-block": ["find-block", "--content", "irgendwas", "--json"],
        "update-block --where-content": [
            "update-block", "--where-content", "Alt", "--page", "Seite",
            "--content", "Neu", "--json",
        ],
        "set-todo-status --content": [
            "set-todo-status", "--content", "Task", "--page", "Seite",
            "--status", "DONE", "--json",
        ],
    }

    @pytest.mark.parametrize("label", COMMANDS)
    def test_query_error_is_reported_not_raised(self, label):
        args = self.COMMANDS[label]
        with patch("logseq_cli.api.requests.post", side_effect=_post_failing_datalog):
            r = split_runner().invoke(cli, args)
        assert r.exit_code != 0
        # A clean failure exits via fail(); anything else is a traceback.
        assert r.exception is None or isinstance(r.exception, SystemExit), (
            f"{label} leaked {type(r.exception).__name__}: {r.exception}"
        )
        assert r.stdout == ""
        payload = json.loads(r.stderr)
        assert payload["reason"] == "datalog_query_failed"
        assert "Cannot parse clause" in payload["error"]

    def test_find_block_does_not_fabricate_a_match(self):
        """helpers.py:855 turned the error dict into the single hit ['e'].

        Over resolve_single_block that fed the write path ("exactly one
        found" on a block nobody meant), so the fabricated hit is the worst
        real outcome of the error dict, not the crash.
        """
        with patch("logseq_cli.api.requests.post", side_effect=_post_failing_datalog):
            r = split_runner().invoke(cli, ["find-block", "--content", "x", "--json"])
        assert r.exit_code != 0
        assert r.stdout == ""


class TestEdnString:
    """The build layer, tested directly: no API involved."""

    def test_quote_is_escaped(self):
        assert edn_string('mit "Zitat"') == '"mit \\"Zitat\\""'

    def test_trailing_backslash_cannot_close_the_literal(self):
        """The bypass from the finding: input ending in a backslash."""
        assert edn_string("endet auf \\") == '"endet auf \\\\"'

    def test_backslash_before_quote_keeps_the_order(self):
        r"""Backslash first, then quote. Input a\"b must come out as
        a\\\"b; the swapped order produces a\\"b and reopens the bypass."""
        assert edn_string('a\\"b') == '"a\\\\\\"b"'

    def test_empty_string(self):
        assert edn_string("") == '""'

    def test_umlauts_pass_unchanged(self):
        assert edn_string("Gespräch über Lösungen") == '"Gespräch über Lösungen"'

    def test_newline_becomes_an_edn_escape(self):
        assert edn_string("a\nb") == '"a\\nb"'
        assert edn_string("a\rb\tc") == '"a\\rb\\tc"'


class TestEdnKeyword:
    ACCEPTED = ["type", "journal?", "exclude-from-graph-view", "last-updated",
                "lastUpdated"]
    REJECTED = ["type) ?v] [?p", "x] [?p", "a b", "a:b", "a.b", "a/b", ""]

    @pytest.mark.parametrize("key", ACCEPTED)
    def test_real_keys_are_accepted(self, key):
        assert edn_keyword(key) == key

    @pytest.mark.parametrize("key", REJECTED)
    def test_injection_shapes_are_rejected(self, key):
        with pytest.raises(InvalidKeywordError):
            edn_keyword(key)

    def test_the_message_names_the_value(self):
        with pytest.raises(InvalidKeywordError) as exc:
            edn_keyword("type) ?v] [?p")
        assert "type) ?v] [?p" in str(exc.value)


class TestPageNameLiteral:
    def test_lowercases_because_block_name_is_stored_lowercased(self):
        assert page_name_literal("Alice") == '"alice"'

    def test_quotes_in_page_names_do_not_break(self):
        assert page_name_literal('Sei"te') == '"sei\\"te"'


class QueryRecorder:
    """Fake API that records the datalog query instead of running it.

    FakeGraph in conftest models write paths and does not fit here; this is the
    same stance (a fake standing in for the real API) on a different object:
    what matters for injection is the query string that would be sent.
    """

    def __init__(self, result=None):
        self.queries = []
        self._result = result if result is not None else []

    def datascript_query(self, query):
        self.queries.append(query)
        return self._result

    def get_all_pages(self):
        return []


# Payloads that would break out of the literal if the value were interpolated
# raw. The check is on the generated query: the injected clause must appear
# only inside a quoted literal, never as bare datalog syntax.
INJECTION = 'x" [?leak :block/name ?n] [(clojure.string/includes? ?c "'
BACKSLASH_BYPASS = 'x\\'


class TestInjectionPerCaller:
    """One test per rewritten call site, checking the built query."""

    def test_find_block_content(self):
        from logseq_cli.helpers import find_blocks_by_content
        rec = QueryRecorder()
        find_blocks_by_content(rec, INJECTION)
        q = rec.queries[0]
        # The whole payload appears only as one escaped literal: the injected
        # clause is inside quotes, so it never becomes bare datalog syntax.
        assert edn_string(INJECTION) in q
        assert q.count('"') % 2 == 0

    def test_find_block_content_backslash_bypass(self):
        from logseq_cli.helpers import find_blocks_by_content
        rec = QueryRecorder()
        find_blocks_by_content(rec, BACKSLASH_BYPASS)
        q = rec.queries[0]
        # A trailing backslash must not escape the closing quote.
        assert q.count('"') % 2 == 0
        assert edn_string(BACKSLASH_BYPASS) in q

    def test_find_block_page_scoped(self):
        from logseq_cli.helpers import find_blocks_by_content
        rec = QueryRecorder()
        find_blocks_by_content(rec, "text", page='Sei"te [?x :block/name ?y]')
        q = rec.queries[0]
        # The malicious page name appears only as one escaped literal.
        assert page_name_literal('Sei"te [?x :block/name ?y]') in q

    def test_find_block_regex_page_scoped(self):
        from logseq_cli.helpers import find_blocks_by_content
        rec = QueryRecorder()
        find_blocks_by_content(rec, ".*", page='Sei"te', use_regex=True)
        assert page_name_literal('Sei"te') in rec.queries[0]

    def test_get_todos_markers(self):
        # Markers are a fixed whitelist, but still routed through edn_string,
        # so the built query carries them as proper string literals.
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            r = split_runner().invoke(cli, ["get-todos", "--status", "TODO", "--json"])
        assert r.exit_code == 0, r.output
        assert rec.queries, "no query was built"
        assert edn_string("TODO") in rec.queries[0]

    def test_smart_query_content_search(self):
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            split_runner().invoke(cli, ["smart-query", "--request", INJECTION, "--json"])
        assert rec.queries, "no query was built"
        q = rec.queries[0]
        assert edn_string(INJECTION) in q
        assert q.count('"') % 2 == 0

    def test_smart_query_links_to_lowercases(self):
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            split_runner().invoke(cli, ["smart-query", "--request", "links to Alice", "--json"])
        assert rec.queries, "no query was built"
        # The case bug: :block/name is stored lowercased. Must query "alice".
        assert '"alice"' in rec.queries[0]
        assert '"Alice"' not in rec.queries[0]

    def test_smart_query_tagged_wraps_hash_in_literal(self):
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            split_runner().invoke(cli, ["smart-query", "--request", "tagged foo", "--json"])
        assert rec.queries, "no query was built"
        q = rec.queries[0]
        # The # is text inside the literal, not a datalog construct.
        assert '"#foo"' in q

    def test_query_pages_by_property_value(self):
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            split_runner().invoke(
                cli,
                ["query-pages-by-property", "--key", "type",
                 "--value", INJECTION, "--json"],
            )
        assert rec.queries, "no query was built"
        assert edn_string(INJECTION) in rec.queries[0]


class TestStep4ErrorHandling:
    """The try/except blocks that used to swallow query errors now surface
    them: exit != 0, message on stderr, no substitute result."""

    def test_advanced_query_error_exits_nonzero(self):
        with patch("logseq_cli.api.requests.post", side_effect=_post_failing_datalog):
            r = split_runner().invoke(
                cli, ["smart-query", "--request", "[:find ?x :where KAPUTT]",
                      "--advanced", "--json"])
        assert r.exit_code != 0
        payload = json.loads(r.stderr)
        assert payload["reason"] == "datalog_query_failed"
        assert r.stdout == ""

    def test_template_query_error_exits_nonzero(self):
        # A template query (not advanced) that the API rejects must fail loud.
        with patch("logseq_cli.api.requests.post", side_effect=_post_failing_datalog):
            r = split_runner().invoke(
                cli, ["smart-query", "--request", "scheduled blocks", "--json"])
        assert r.exit_code != 0
        payload = json.loads(r.stderr)
        assert payload["reason"] == "datalog_query_failed"

    def test_rejected_property_key_has_its_own_reason(self):
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            r = split_runner().invoke(
                cli, ["query-pages-by-property", "--key", "type) ?v] [?p",
                      "--json"])
        assert r.exit_code != 0
        assert not rec.queries, "a rejected key must never build a query"
        payload = json.loads(r.stderr)
        assert payload["reason"] == "invalid_property_key"
        assert "type) ?v] [?p" in payload["error"]
        assert r.stdout == ""

    def test_valid_property_key_still_works(self):
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            r = split_runner().invoke(
                cli, ["query-pages-by-property", "--key", "type", "--json"])
        assert r.exit_code == 0, r.output
        assert rec.queries


class TestPropertyKeyCasing:
    """query-pages-by-property must find a page whether the user types the
    camelCase key (as Logseq displays it) or the kebab-case key (as datalog
    stores it). Before this, camelCase found nothing without any hint.
    """

    def test_camelcase_key_query_contains_kebab_form(self):
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            r = split_runner().invoke(
                cli, ["query-pages-by-property", "--key", "excludeFromGraphView",
                      "--json"])
        assert r.exit_code == 0, r.output
        assert rec.queries, "no query was built"
        q = rec.queries[0]
        # The kebab form datalog actually stores must be queried.
        assert "exclude-from-graph-view" in q

    def test_kebab_key_still_works(self):
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            r = split_runner().invoke(
                cli, ["query-pages-by-property", "--key", "exclude-from-graph-view",
                      "--json"])
        assert r.exit_code == 0, r.output
        assert "exclude-from-graph-view" in rec.queries[0]

    def test_both_forms_are_tried_for_a_camelcase_key(self):
        """A camelCase key must match both spellings, since a foreign graph
        might store either. Both appear in the built query."""
        rec = QueryRecorder(result=[])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            split_runner().invoke(
                cli, ["query-pages-by-property", "--key", "techStack", "--json"])
        q = rec.queries[0]
        assert "tech-stack" in q
        assert "techStack" in q

    def test_value_is_read_despite_kebab_keys_in_pull(self):
        """The pull returns kebab keys; a camelCase --key must still read the
        value off the page, not an empty string."""
        page = {"name": "P", "original-name": "P",
                "properties": {"tech-stack": "Python"}}
        rec = QueryRecorder(result=[[page]])
        with patch("logseq_cli.cli.LogseqAPI", return_value=rec):
            r = split_runner().invoke(
                cli, ["query-pages-by-property", "--key", "techStack", "--json"])
        assert r.exit_code == 0, r.output
        data = json.loads(r.stdout)
        assert data["pages"][0]["value"] == "Python"


class TestContentSearchFallback:
    """smart-query's content-search must fall back to a page-name search on
    zero hits, but let a real error (connection down, rejected query) surface
    instead of silently answering a different question.
    """

    def test_query_error_is_not_swallowed_by_the_fallback(self):
        # A rejected query used to be turned into a page-name search with
        # exit 0; it must now fail loud via the decorator.
        with patch("logseq_cli.api.requests.post", side_effect=_post_failing_datalog):
            r = split_runner().invoke(
                cli, ["smart-query", "--request", "irgendein freier suchtext", "--json"])
        assert r.exit_code != 0
        payload = json.loads(r.stderr)
        assert payload["reason"] == "datalog_query_failed"
        assert r.stdout == ""

    def test_zero_hits_falls_back_to_page_name_search(self):
        # Content search returns [], so the page-name fallback runs.
        call = {"n": 0}

        def _post(url, json=None, headers=None, timeout=None):
            method = json.get("method") if json else None
            if method == "logseq.DB.datascriptQuery":
                return _response([])  # no content hits
            if method == "logseq.Editor.getAllPages":
                return _response([{"name": "Freetext Page", "original-name": "Freetext Page"}])
            return _response([])

        with patch("logseq_cli.api.requests.post", side_effect=_post):
            r = split_runner().invoke(
                cli, ["smart-query", "--request", "freitext", "--json"])
        assert r.exit_code == 0, r.output
        data = json.loads(r.stdout)
        assert "Page name search" in data["description"]

    def test_content_hits_do_not_trigger_the_fallback(self):
        hit = [{"content": "hat freitext drin", "uuid": "u1",
                "page": {"original-name": "S", "name": "s"}}]

        def _post(url, json=None, headers=None, timeout=None):
            method = json.get("method") if json else None
            if method == "logseq.DB.datascriptQuery":
                return _response([hit])
            return _response([])

        with patch("logseq_cli.api.requests.post", side_effect=_post):
            r = split_runner().invoke(
                cli, ["smart-query", "--request", "freitext", "--json"])
        assert r.exit_code == 0, r.output
        data = json.loads(r.stdout)
        assert "Content search" in data["description"]
