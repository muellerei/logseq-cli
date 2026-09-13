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
