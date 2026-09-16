"""Property values are stored as a scalar OR inside a collection.

Logseq decides which, and the page does not show it: on one real graph `team`
was `"Core"` on two pages and `["Core"]` on ten others. A query comparing
with equality alone matched the two and dropped the rest without a word — the
shape of failure this project otherwise refuses, a plausible count that gets
believed.

These tests pin both halves: the query has to reach both storage forms, and a
collection has to print the way the page spells it, not as Python's repr.
"""

import json as _json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli, _format_property_value


def _api(rows):
    api = MagicMock()
    api.datascript_query.return_value = rows
    return api


def _run(args, rows):
    api = _api(rows)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        result = CliRunner().invoke(cli, ["--token", "T"] + args)
    return result, api


def _page(name, props):
    return [{"name": name.lower(), "original-name": name, "properties": props}]


class TestTheQueryReachesBothForms:
    def test_the_query_asks_for_the_scalar_and_the_collection(self):
        _, api = _run(
            ["query-pages-by-property", "--key", "team", "--value", "Core"],
            [_page("Alice", {"team": ["Core"]})],
        )
        query = api.datascript_query.call_args[0][0]
        assert '(= ?v "Core")' in query, "scalar form dropped"
        assert '(contains? ?v "Core")' in query, "collection form dropped"

    def test_smart_query_asks_for_both_too(self, tmp_path):
        # person_property is configurable; pointed at a list-valued key it
        # would hit the same wall.
        cfg = tmp_path / "c.toml"
        cfg.write_text('[graph]\nperson_property = "type"\nperson_value = "Person"\n',
                       encoding="utf-8")
        api = _api([])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            CliRunner().invoke(
                cli, ["--token", "T", "smart-query", "--request", "personen"],
                env={"LOGSEQ_CLI_CONFIG": str(cfg)})
        query = api.datascript_query.call_args[0][0]
        assert '(= ?t "Person")' in query
        assert '(contains? ?t "Person")' in query


class TestTheValuePrintsLikeThePage:
    def test_a_single_element_collection_loses_its_brackets(self):
        assert _format_property_value(["Core"]) == "Core"

    def test_several_values_read_as_a_list_a_person_would_write(self):
        assert _format_property_value(["Alpha", "Beta"]) == "Alpha, Beta"

    def test_a_scalar_is_untouched(self):
        assert _format_property_value("Core") == "Core"

    def test_a_number_survives(self):
        assert _format_property_value(3) == "3"

    def test_the_listing_does_not_print_python_syntax(self):
        result, _ = _run(
            ["query-pages-by-property", "--key", "team"],
            [_page("Alice", {"team": ["Core"]})],
        )
        assert "team:: Core" in result.output
        assert "['Core']" not in result.output

    def test_json_carries_the_same_value(self):
        result, _ = _run(
            ["query-pages-by-property", "--key", "team", "--json"],
            [_page("Alice", {"team": ["Core"]})],
        )
        assert _json.loads(result.output)["pages"][0]["value"] == "Core"
