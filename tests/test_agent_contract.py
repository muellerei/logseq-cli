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
