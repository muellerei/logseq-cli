"""`doctor` must tell the failure modes apart, not just say "not ready".

Motivating incident (2026-08-05): the Logseq process was running but nothing
listened on port 12315. Working out whether reads could fall back to the
filesystem or writes had to be held took seven manual diagnosis steps. The
value of this command is entirely in the distinction it draws:

  app not running  !=  app running but API off  !=  API up but token rejected

Each needs a different fix, so each gets its own remedy line.
"""
import json
from unittest.mock import MagicMock

import pytest
import requests
from click.testing import CliRunner

from logseq_cli.cli import cli


def _http_error(code):
    resp = MagicMock()
    resp.status_code = code
    err = requests.HTTPError(f"HTTP {code}")
    err.response = resp
    return err


@pytest.fixture
def api(monkeypatch):
    mock = MagicMock()
    mock.host, mock.port = "127.0.0.1", "12315"
    mock.base_url = "http://127.0.0.1:12315/api"
    mock.token = "tok"
    monkeypatch.setattr("logseq_cli.cli.LogseqAPI", lambda **kwargs: mock)
    return mock


@pytest.fixture
def listener(monkeypatch):
    """Control whether the port appears open."""
    def set_state(open_):
        monkeypatch.setattr("logseq_cli.cli._port_has_listener",
                            lambda *a, **k: open_)
    return set_state


@pytest.fixture
def process(monkeypatch):
    def set_state(running):
        monkeypatch.setattr("logseq_cli.cli._logseq_process_running",
                            lambda: running)
    return set_state


class TestHealthy:
    def test_all_green_exits_0(self, api, listener):
        listener(True)
        api.call.return_value = {"currentGraph": "logseq_local_/graph"}
        api.get_all_pages.return_value = [{"name": "a"}, {"name": "b"}]
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "Ready" in result.output

    def test_json_reports_healthy(self, api, listener):
        listener(True)
        api.call.return_value = {"currentGraph": "g"}
        api.get_all_pages.return_value = [{"name": "a"}]
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        payload = json.loads(result.stdout)
        assert payload["healthy"] is True
        assert "remedy" not in payload
        assert payload["graph"] == "g"


class TestFailureModesAreDistinguished:
    def test_app_not_running(self, api, listener, process):
        listener(False)
        process(False)
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert "not running" in payload["remedy"]

    def test_app_running_but_api_off(self, api, listener, process):
        """The 2026-08-05 case — must not be conflated with 'app not running'."""
        listener(False)
        process(True)
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert "HTTP API" in payload["remedy"]
        assert "not running" not in payload["remedy"]

    def test_unknown_process_state_still_advises(self, api, listener, process):
        listener(False)
        process(None)
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 1
        assert "remedy" in json.loads(result.stdout)

    def test_missing_token_differs_from_wrong_token(self, api, listener):
        listener(True)
        api.token = ""
        api.call.side_effect = _http_error(401)
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 1
        assert "No token was supplied" in json.loads(result.stdout)["remedy"]

    def test_wrong_token(self, api, listener):
        listener(True)
        api.token = "falsch"
        api.call.side_effect = _http_error(401)
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 1
        assert "rejected the token" in json.loads(result.stdout)["remedy"]

    def test_api_up_but_no_graph_loaded(self, api, listener):
        listener(True)
        api.call.return_value = {}
        api.get_all_pages.return_value = []
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 1
        assert "no pages" in json.loads(result.stdout)["remedy"]


class TestRobustness:
    def test_never_crashes_on_unexpected_error(self, api, listener):
        """doctor is the tool you reach for when things are broken."""
        listener(True)
        api.call.side_effect = RuntimeError("boom")
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1
        assert "Not ready" in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_survives_connection_error(self, api, listener):
        listener(True)
        api.call.side_effect = requests.ConnectionError("refused")
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["healthy"] is False


class TestRuntimeChecks:
    """doctor reports the runtime it is running on.

    An installation problem otherwise surfaces later as something unrelated:
    an ImportError in the middle of a command, or a config file that never
    loads because no TOML parser is present.
    """

    def test_python_and_packages_are_reported(self, api, listener):
        listener(True)
        api.call.return_value = {"currentGraph": "g"}
        api.get_all_pages.return_value = [{"name": "a"}]
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        checks = {c["check"]: c for c in json.loads(result.stdout)["checks"]}
        assert checks["python"]["ok"] is True
        assert checks["python"]["detail"].startswith("3.")
        assert checks["packages"]["ok"] is True
        for lib in ("click", "requests"):
            assert lib in checks["packages"]["detail"]

    def test_missing_package_fails_with_a_remedy(self, api, listener, monkeypatch):
        """A broken install must not look like a healthy one."""
        import logseq_cli.cli as cli_mod

        real = cli_mod.import_module

        def fake(name, *a, **kw):
            if name == "requests":
                raise ImportError("boom")
            return real(name, *a, **kw)

        monkeypatch.setattr(cli_mod, "import_module", fake)
        listener(True)
        api.call.return_value = {"currentGraph": "g"}
        api.get_all_pages.return_value = [{"name": "a"}]
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        payload = json.loads(result.stdout)
        packages = next(c for c in payload["checks"] if c["check"] == "packages")
        assert packages["ok"] is False
        assert "requests" in packages["detail"]
        assert payload["healthy"] is False
        assert "remedy" in payload


class TestGraphKind:
    """Which Logseq generation is on the other end.

    A DB graph (2.x) answers the same API but keeps a different data model:
    the fields these commands read are not there, so reads come back empty
    rather than failing. Empty is the same shape an empty graph has, which
    leaves the user comparing their own graph against a result that cannot
    tell them why. `doctor` names the kind so that question is answered where
    it is asked.

    The rule is Logseq's own (deps/db/src/logseq/db/sqlite/util.cljs,
    `db-based-graph?`): the graph url starts with `logseq_db_` for a DB graph,
    `logseq_local_` for a file graph.
    """

    def test_a_file_graph_is_reported_as_supported(self, api, listener):
        listener(True)
        api.call.return_value = {"currentGraph": "logseq_local_/Users/x/notes"}
        api.get_all_pages.return_value = [{"name": "a", "originalName": "A"}]
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        checks = {c["check"]: c for c in json.loads(result.stdout)["checks"]}
        assert checks["graph kind"]["ok"] is True
        assert "file" in checks["graph kind"]["detail"].lower()
        assert json.loads(result.stdout)["healthy"] is True

    def test_a_db_graph_fails_and_says_why(self, api, listener):
        """The whole point: not "0 pages", but which Logseq this is."""
        listener(True)
        api.call.return_value = {"currentGraph": "logseq_db_my-notes"}
        api.get_all_pages.return_value = [{"id": 1, "title": "A"}]
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        payload = json.loads(result.stdout)
        checks = {c["check"]: c for c in payload["checks"]}
        assert checks["graph kind"]["ok"] is False
        assert "2.x" in checks["graph kind"]["detail"]
        assert payload["healthy"] is False
        assert "remedy" in payload
        assert "0.10" in payload["remedy"] or "file" in payload["remedy"].lower()

    def test_a_db_graph_says_so_in_plain_output_too(self, api, listener):
        listener(True)
        api.call.return_value = {"currentGraph": "logseq_db_my-notes"}
        api.get_all_pages.return_value = []
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1
        assert "graph kind" in result.output
        assert "2.x" in result.output

    def test_an_unknown_url_shape_does_not_claim_to_know(self, api, listener):
        """Neither prefix: report it as undetermined rather than guess.

        A wrong "file graph, all good" is worse than no answer, because it
        rules out the one cause the user should be looking at.
        """
        listener(True)
        api.call.return_value = {"currentGraph": "something-else"}
        api.get_all_pages.return_value = [{"name": "a"}]
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        payload = json.loads(result.stdout)
        checks = {c["check"]: c for c in payload["checks"]}
        assert checks["graph kind"]["ok"] is None
        assert payload["healthy"] is True, "an undetermined kind must not fail a working setup"

    def test_a_missing_graph_url_reports_undetermined(self, api, listener):
        listener(True)
        api.call.return_value = {}
        api.get_all_pages.return_value = [{"name": "a"}]
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        checks = {c["check"]: c for c in json.loads(result.stdout)["checks"]}
        assert checks["graph kind"]["ok"] is None

    def test_the_kind_is_not_claimed_when_the_api_never_answered(self, api, listener, process):
        """No answer is not evidence about the graph kind."""
        listener(False)
        process(False)
        result = CliRunner().invoke(cli, ["doctor", "--json"])
        checks = {c["check"] for c in json.loads(result.stdout)["checks"]}
        assert "graph kind" not in checks
