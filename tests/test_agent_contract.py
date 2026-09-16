"""The contract a non-interactive caller relies on.

Any agent driving this CLI from a shell assumes three things: payload on stdout,
errors on stderr, and a non-zero exit when something failed. Two paths broke that
and are guarded here, because both hit a caller at first contact:

- transport errors (Logseq down, wrong token) were printed as prose even with
  `--json`, so an agent parsing stderr got unparseable text exactly where it
  needed a reason;
- `get-block` with an unknown UUID printed `null` on stdout and exited 0, which
  reads as a successful empty block rather than a miss.
"""
import json
from unittest.mock import MagicMock, patch

import requests
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


def _http_error(status):
    resp = MagicMock()
    resp.status_code = status
    resp.text = '{"error":"Unauthorized"}'
    return requests.HTTPError(response=resp)


class TestTransportErrorsAreStructured:
    def test_connection_error_is_json_with_reason(self):
        api = MagicMock()
        api.get_page.side_effect = requests.ConnectionError()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-page", "--name", "X", "--json"])
        assert r.exit_code == 1
        payload = json.loads(r.stderr)
        assert payload["reason"] == "connection_refused"
        assert r.stdout == ""

    def test_auth_error_names_the_token(self):
        api = MagicMock()
        api.get_page.side_effect = _http_error(401)
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-page", "--name", "X", "--json"])
        assert r.exit_code == 1
        payload = json.loads(r.stderr)
        assert payload["status_code"] == 401
        assert "token" in payload["hint"].lower()

    def test_without_json_the_message_stays_prose(self):
        api = MagicMock()
        api.get_page.side_effect = requests.ConnectionError()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-page", "--name", "X"])
        assert r.exit_code == 1
        assert r.stderr.startswith("Error: Cannot connect")
        assert "{" not in r.stderr

    def test_errors_never_reach_stdout(self):
        """stdout must stay parseable as payload, whatever went wrong."""
        api = MagicMock()
        api.get_page.side_effect = _http_error(500)
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-page", "--name", "X", "--json"])
        assert r.stdout == ""


class TestGetBlockNotFound:
    def test_unknown_uuid_fails_instead_of_printing_null(self):
        api = MagicMock()
        api.get_block.return_value = None
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-block", "--id", "nope", "--json"])
        assert r.exit_code == 1
        assert r.stdout.strip() != "null"
        payload = json.loads(r.stderr)
        assert payload["exists"] is False

    def test_known_uuid_still_returns_the_block(self):
        api = MagicMock()
        api.get_block.return_value = {"uuid": "u-1", "content": "Text", "children": []}
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-block", "--id", "u-1", "--json"])
        assert r.exit_code == 0, r.output
        assert json.loads(r.stdout)["content"] == "Text"

    def test_text_mode_also_fails(self):
        api = MagicMock()
        api.get_block.return_value = None
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-block", "--id", "nope"])
        assert r.exit_code == 1
        assert "not found" in r.stderr.lower()


class TestQueryErrorsMeetTheContract:
    """HTTP 200 with an error body must satisfy the same contract as a
    transport error: structured JSON on stderr, empty stdout, non-zero exit.

    Logseq reports a broken datalog query this way, so without this guard the
    path "API answered, but the query never ran" falls outside the contract
    and surfaces as an empty result or a traceback.
    """

    def test_query_error_body_is_structured_like_transport_errors(self):
        error_resp = MagicMock()
        error_resp.status_code = 200
        error_resp.json.return_value = {"error": "Cannot parse clause"}
        error_resp.raise_for_status = MagicMock()
        with patch("logseq_cli.api.requests.post", return_value=error_resp):
            r = split_runner().invoke(cli, ["get-todos", "--json"])
        assert r.exit_code == 1
        assert r.stdout == ""
        payload = json.loads(r.stderr)
        assert payload["reason"] == "datalog_query_failed"


class TestShippedExamplesReadTheRealPayload:
    """An example that mis-reads the payload teaches the mistake it makes.

    `examples/weekly-todos.sh` read `data.get('tasks', [])` from the day of the
    initial import, while `get-todos --json` has always answered `{"todos": …}`.
    The default swallowed it: the script printed "Total: 0 open tasks" against
    any graph, which reads as an empty week rather than as a broken script.
    """

    def test_every_example_reads_a_key_the_cli_emits(self):
        """Checks every shipped example, not just the one that was wrong.

        Scanning the directory rather than a list means a new example is
        covered the day it is added, without anyone remembering to extend
        this test.
        """
        import pathlib
        import re

        payload_keys = {"todos", "count", "repeating_excluded"}
        examples = sorted((pathlib.Path(__file__).parent.parent
                           / "examples").glob("*.sh"))
        assert examples, "no example scripts found"

        checked = []
        for script_path in examples:
            script = script_path.read_text()
            if "get-todos" not in script:
                continue
            # Only top-level access counts: Python's data['x'] / data.get('x'),
            # and jq expressions rooted at the payload. A field read inside a
            # todo (.content, .page, .references) is a different contract,
            # held by the get-todos tests.
            read_keys = set(re.findall(r"data(?:\.get\(|\[)['\"](\w+)['\"]", script))
            read_keys |= {m for m in re.findall(r"^\s*\.(\w+)", script, re.M)}
            read_keys |= set(re.findall(r"\(\.(\w+)\s*\|\s*length\)", script))
            unknown = read_keys - payload_keys
            assert not unknown, (
                f"{script_path.name} reads keys get-todos never emits: {unknown}")
            checked.append(script_path.name)
        assert checked, "no example exercises get-todos any more"

    def test_get_todos_json_still_uses_those_keys(self):
        """Pins the other half: the example is only right while this holds."""
        api = MagicMock()
        api.datascript_query.return_value = []
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-todos", "--json"])
        assert r.exit_code == 0, r.stdout
        assert set(json.loads(r.stdout)) == {"todos", "count"}
