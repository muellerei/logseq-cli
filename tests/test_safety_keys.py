"""``[safety]`` is worked out per key: a second limit rides on the first one's machinery.

The key list is widened here with an invented ``extra_limit``. Value check,
config/env/flag decision, the doctor line and ``init``'s copying of the table
must all pick it up without a line of their own; if one of them still names
``read_only``, the invented key is missed there.
"""
import json

import click
import pytest

try:  # tomllib is stdlib from 3.11; 3.10 uses the tomli backport
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

import logseq_cli.config
from logseq_cli.cli import cli
from logseq_cli.config import ConfigError, check_safety
from logseq_cli.safety import decide, describe
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


@pytest.fixture(autouse=True)
def two_keys(monkeypatch):
    monkeypatch.setattr(logseq_cli.config, "SAFETY_KEYS", ("read_only", "extra_limit"))
    monkeypatch.delenv("LOGSEQ_CLI_EXTRA_LIMIT", raising=False)


@pytest.fixture
def config(tmp_path, monkeypatch):
    def write(text):
        path = tmp_path / "config.toml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(path))
        return path
    return write


def invoke(*args):
    return split_runner().invoke(cli, ["--token", "X", *args])


class TestValueCheck:
    def test_a_second_bool_key_is_accepted(self):
        check_safety({"safety": {"extra_limit": True, "read_only": False}})

    @pytest.mark.parametrize("value", ["yes", 1, "true"])
    def test_a_wrongly_typed_second_key_is_refused(self, value):
        with pytest.raises(ConfigError, match=r"extra_limit must be true or false"):
            check_safety({"safety": {"extra_limit": value}})

    def test_the_first_key_is_still_checked(self):
        with pytest.raises(ConfigError, match=r"read_only must be true or false"):
            check_safety({"safety": {"read_only": "yes"}})


class TestDecision:
    def test_each_key_has_its_own_sources(self, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_EXTRA_LIMIT", "1")
        decision = decide({"safety": {"read_only": True}})
        assert decision.sources_of("read_only") == ("config",)
        assert decision.sources_of("extra_limit") == ("env",)

    def test_config_and_flag_reach_the_second_key(self):
        ctx = click.Context(cli, obj={"extra_limit_flag": True})
        with ctx:
            decision = decide({"safety": {"extra_limit": True}})
        assert decision.sources_of("extra_limit") == ("config", "flag")
        assert not decision.is_on("read_only")

    def test_an_unknown_env_value_counts_as_on_for_the_second_key(self, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_EXTRA_LIMIT", "flase")
        assert decide({}).is_on("extra_limit")

    def test_describe_names_the_key_and_the_source(self):
        decision = decide({"safety": {"extra_limit": True}, "_path": "/c.toml"})
        assert describe(decision, "extra_limit") == "on (config /c.toml)"
        assert describe(decision, "read_only") == "off ([safety] read_only = false in /c.toml)"


class TestDoctorAndInit:
    def test_doctor_has_a_line_per_key(self, config, monkeypatch):
        from unittest.mock import MagicMock
        api = MagicMock()
        api.host, api.port, api.token = "127.0.0.1", "12315", "tok"
        api.base_url = "http://127.0.0.1:12315/api"
        api.call.return_value = {"currentGraph": "logseq_local_/graph"}
        api.get_all_pages.return_value = [{"name": "a"}]
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kwargs: api)
        monkeypatch.setattr("logseq_cli.commands.meta._port_has_listener",
                            lambda *a, **k: True)
        config("[safety]\nextra_limit = true\n")
        checks = {c["check"]: c["detail"] for c in
                  json.loads(invoke("doctor", "--json").stdout)["checks"]}
        assert checks["extra_limit"].startswith("on (config ")
        assert checks["read_only"].startswith("off (")

    def test_init_carries_the_second_key_into_a_new_file(self, config, monkeypatch):
        LogseqHttpDouble().install(monkeypatch)
        path = config("[safety]\nextra_limit = true\n")
        result = invoke("init", "--force")
        assert result.exit_code == 0, result.stderr
        assert tomllib.loads(path.read_text(encoding="utf-8"))["safety"] == {"extra_limit": True}
