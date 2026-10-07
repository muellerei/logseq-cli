"""``[safety] require_preconditions``: a command that changes a block it read
refuses without a precondition, before its first request.

The obligation hangs on the call, not on the command (``WriteCommand(owes=...)``),
and is checked in one place, right behind the write gate. Driven through the real
client with ``requests.post`` replaced by a recorder that refuses everything: a
command that gets past the gate fails for another reason, and ``sent`` says so.
"""
import json

import click
import pytest

from logseq_cli.cli import cli
from logseq_cli.output import handle_connection_error
from logseq_cli.safety import Owed, WriteCommand
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

TASK = "00000000-0000-4000-8000-0000000000a1"
ON = "[safety]\nrequire_preconditions = true\n"


@pytest.fixture
def sent(monkeypatch):
    """Every request the client tries to send; none gets an answer."""
    import logseq_cli.api

    seen = []

    def post(url, json=None, **kwargs):
        seen.append((json or {}).get("method"))
        raise RuntimeError("a request was sent")

    monkeypatch.setattr(logseq_cli.api.requests, "post", post)
    return seen


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


def error_of(result):
    return json.loads(result.stderr)


def set_status(*extra, flags=()):
    return invoke(*flags, "set-todo-status", "--id", TASK, "--status", "DONE", *extra, "--json")


class TestTheSwitch:
    def test_without_a_precondition_the_command_refuses_before_a_request(self, config, sent):
        path = config(ON)
        result = set_status()
        assert result.exit_code != 0
        error = error_of(result)
        assert error["reason"] == "precondition_required"
        assert error["source"] == ["config"]
        assert error["config_path"] == str(path)
        assert error["options"] == ["--expect-hash", "--expect-marker"]
        assert error["writes_landed"] == 0
        assert "require_preconditions = true" in error["error"]
        assert "--expect-hash" in error["error"] and "--expect-marker" in error["error"]
        assert error["error"].count("Nothing was written.") == 1
        assert sent == []

    def test_the_refusal_reads_as_a_sentence_in_plain_text(self, config, sent):
        path = config(ON)
        result = invoke("set-todo-status", "--id", TASK, "--status", "DONE")
        assert result.exit_code != 0
        assert result.stderr == (
            f"Error: A precondition is required: require_preconditions = true in [safety] of "
            f"{path}. Pass --expect-hash or --expect-marker. Nothing was written.\n")

    def test_a_matching_marker_gets_the_call_past_the_gate(self, config, monkeypatch):
        config(ON)
        LogseqHttpDouble.installed(monkeypatch, {
            "Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]})
        result = set_status("--expect-marker", "todo")
        assert result.exit_code == 0, result.stderr

    def test_a_hash_is_a_precondition_too(self, config, monkeypatch):
        # The gate lets it through; the check then finds the hash wrong.
        config(ON)
        LogseqHttpDouble.installed(monkeypatch, {
            "Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]})
        result = set_status("--expect-hash", "0123456789ab")
        assert error_of(result)["reason"] == "precondition_failed"

    def test_off_by_default(self, monkeypatch):
        LogseqHttpDouble.installed(monkeypatch, {
            "Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]})
        result = set_status()
        assert result.exit_code == 0, result.stderr

    def test_a_false_in_the_config_is_off(self, config, monkeypatch):
        config("[safety]\nrequire_preconditions = false\n")
        LogseqHttpDouble.installed(monkeypatch, {
            "Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]})
        assert set_status().exit_code == 0

    def test_help_and_usage_errors_come_before_the_refusal(self, config, sent):
        config(ON)
        assert invoke("set-todo-status", "--help").exit_code == 0
        result = invoke("set-todo-status", "--id", TASK)
        assert result.exit_code == 2 and "Missing option" in result.stderr


class TestItOnlyTightens:
    def test_the_flag_alone_switches_it_on(self, sent):
        result = set_status(flags=("--require-preconditions",))
        error = error_of(result)
        assert error["reason"] == "precondition_required"
        assert error["source"] == ["flag"]
        assert error["config_path"] is None
        assert sent == []

    def test_env_alone_switches_it_on(self, sent, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_REQUIRE_PRECONDITIONS", "1")
        assert error_of(set_status())["source"] == ["env"]

    def test_env_false_does_not_loosen_the_config(self, config, sent, monkeypatch):
        config(ON)
        monkeypatch.setenv("LOGSEQ_CLI_REQUIRE_PRECONDITIONS", "false")
        error = error_of(set_status())
        assert error["reason"] == "precondition_required"
        assert error["source"] == ["config"]

    def test_a_config_that_says_false_is_tightened_by_the_flag(self, config, sent):
        config("[safety]\nrequire_preconditions = false\n")
        assert error_of(set_status(flags=("--require-preconditions",)))["source"] == ["flag"]

    def test_every_source_is_named(self, config, sent, monkeypatch):
        config(ON)
        monkeypatch.setenv("LOGSEQ_CLI_REQUIRE_PRECONDITIONS", "yes")
        error = error_of(set_status(flags=("--require-preconditions",)))
        assert error["source"] == ["config", "env", "flag"]
        assert "LOGSEQ_CLI_REQUIRE_PRECONDITIONS is set" in error["error"]
        assert "--require-preconditions was passed" in error["error"]

    def test_there_is_no_flag_that_loosens_it(self):
        options = [opt for p in cli.params for opt in p.opts]
        assert "--require-preconditions" in options
        assert "--no-require-preconditions" not in options

    def test_an_unknown_env_value_counts_as_on_and_says_so(self, sent, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_REQUIRE_PRECONDITIONS", "flase")
        result = invoke("set-todo-status", "--id", TASK, "--status", "DONE")
        assert result.exit_code != 0
        assert "A precondition is required" in result.stderr
        assert "LOGSEQ_CLI_REQUIRE_PRECONDITIONS='flase'" in result.stderr
        assert sent == []


class TestFailClosed:
    @pytest.mark.parametrize("value", ["'yes'", "1"])
    def test_a_value_that_is_no_bool_is_a_config_error(self, config, sent, value):
        config(f"[safety]\nrequire_preconditions = {value}\n")
        error = error_of(set_status())
        assert error["reason"] == "config_error"
        assert "require_preconditions must be true or false" in error["error"]
        assert sent == []

    def test_a_missing_named_config_refuses_the_command(self, tmp_path, monkeypatch, sent):
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(tmp_path / "gone.toml"))
        error = error_of(set_status())
        assert error["reason"] == "config_error"
        assert sent == []

    def test_a_misspelt_key_is_refused_with_a_hint(self, config, sent):
        config("[safety]\nrequire_precondition = true\n")
        error = error_of(set_status())
        assert error["reason"] == "config_error"
        assert "did you mean `require_preconditions`" in error["error"]

    def test_the_key_outside_safety_is_refused(self, config, sent):
        config("require-preconditions = true\n")
        error = error_of(set_status())
        assert error["reason"] == "config_error"
        assert "belongs under [safety]" in error["error"]


class TestRankOverTheWriteGate:
    def test_read_only_wins_over_a_missing_precondition(self, config, sent):
        config("[safety]\nread_only = true\nrequire_preconditions = true\n")
        error = error_of(set_status())
        assert error["reason"] == "read_only"
        assert sent == []

    def test_read_only_wins_when_the_flags_ask_for_both(self, sent):
        error = error_of(set_status(flags=("--read-only", "--require-preconditions")))
        assert error["reason"] == "read_only"


class TestOwesIsDecidedPerCall:
    """A synthetic command, so the rules are tested without a real command that has them."""

    @pytest.fixture
    def synthetic(self, monkeypatch):
        def make(owes):
            @click.command("zz-synthetic", cls=WriteCommand, owes=owes)
            @click.option("--expect-hash", "expect_hash", default=None)
            @click.option("--mode", default="free")
            @click.option("--json", "as_json", is_flag=True)
            @handle_connection_error
            def zz(expect_hash, mode, as_json):
                click.echo("body ran")

            monkeypatch.setitem(cli.commands, "zz-synthetic", zz)
        return make

    def test_a_command_without_owes_never_owes_anything(self, synthetic, config):
        synthetic(None)
        config(ON)
        result = invoke("zz-synthetic")
        assert result.exit_code == 0 and "body ran" in result.stdout

    def test_owes_is_asked_with_the_parsed_parameters(self, synthetic, config):
        seen = []

        def owes(params):
            seen.append(dict(params))
            return Owed(("--expect-hash",)) if params["mode"] == "change" else None

        synthetic(owes)
        config(ON)
        free = invoke("zz-synthetic", "--mode", "free")
        assert free.exit_code == 0 and "body ran" in free.stdout
        refused = invoke("zz-synthetic", "--mode", "change", "--json")
        assert error_of(refused)["reason"] == "precondition_required"
        assert "body ran" not in refused.stdout
        assert seen[-1]["mode"] == "change" and seen[-1]["expect_hash"] is None
        met = invoke("zz-synthetic", "--mode", "change", "--expect-hash", "abc")
        assert met.exit_code == 0 and "body ran" in met.stdout

    def test_what_is_owed_is_not_asked_for_when_the_switch_is_off(self, synthetic):
        synthetic(lambda params: Owed(("--expect-hash",)))
        result = invoke("zz-synthetic")
        assert result.exit_code == 0 and "body ran" in result.stdout

    def test_always_owes_without_the_switch(self, synthetic):
        synthetic(lambda params: Owed(("--expect-hash",), always="--next"))
        result = invoke("zz-synthetic", "--json")
        error = error_of(result)
        assert result.exit_code != 0
        assert error["reason"] == "precondition_required"
        assert error["source"] == []
        assert "--next" in error["error"]
        assert "require_preconditions" not in error["error"]
        assert "body ran" not in result.stdout

    def test_always_is_met_by_the_option(self, synthetic):
        synthetic(lambda params: Owed(("--expect-hash",), always="--next"))
        result = invoke("zz-synthetic", "--expect-hash", "abc")
        assert result.exit_code == 0 and "body ran" in result.stdout

    def test_always_still_gives_way_to_read_only(self, synthetic):
        synthetic(lambda params: Owed(("--expect-hash",), always="--next"))
        assert error_of(invoke("--read-only", "zz-synthetic", "--json"))["reason"] == "read_only"


def test_every_command_with_a_hash_option_carries_owes():
    """A command that takes ``--expect-hash`` or ``--expect-tree-hash`` and forgets
    ``owes`` would stay free under the switch. ``add-journal-block`` is the one
    exception: its upsert-heading mode owes after it has picked its block, in its body."""
    missing = []
    for name, command in sorted(cli.commands.items()):
        options = {opt for p in command.params for opt in p.opts}
        if options & {"--expect-hash", "--expect-tree-hash"}:
            if getattr(command, "owes", None) is None:
                missing.append(name)
    assert missing == []


class TestDoctor:
    def _doctor(self, monkeypatch, *args):
        from unittest.mock import MagicMock
        api = MagicMock()
        api.host, api.port, api.token = "127.0.0.1", "12315", "tok"
        api.base_url = "http://127.0.0.1:12315/api"
        api.call.return_value = {"currentGraph": "logseq_local_/graph"}
        api.get_all_pages.return_value = [{"name": "a"}]
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kwargs: api)
        monkeypatch.setattr("logseq_cli.commands.meta._port_has_listener", lambda *a, **k: True)
        result = invoke(*args, "doctor", "--json")
        return {c["check"]: c["detail"] for c in json.loads(result.stdout)["checks"]}, result

    def test_it_shows_the_switch_and_where_it_comes_from(self, config, monkeypatch):
        path = config(ON)
        checks, _ = self._doctor(monkeypatch)
        assert checks["require_preconditions"] == f"on (config {path})"

    def test_it_shows_the_flag_as_the_source(self, config, monkeypatch):
        config("[safety]\nread_only = false\n")
        checks, _ = self._doctor(monkeypatch, "--require-preconditions")
        assert checks["require_preconditions"] == "on (flag)"

    def test_it_shows_off(self, config, monkeypatch):
        path = config("[safety]\nread_only = false\n")
        checks, _ = self._doctor(monkeypatch)
        assert checks["require_preconditions"] == (
            f"off ([safety] require_preconditions = false in {path})")

    def test_a_broken_config_makes_it_unknown(self, config, monkeypatch):
        config("[safety]\nrequire_preconditions = 'yes'\n")
        checks, _ = self._doctor(monkeypatch)
        assert checks["require_preconditions"].startswith(
            "unknown, commands that write refuse until this is fixed:")


class TestUpdateBlockOwesAHash:
    def test_it_refuses_without_one_before_a_request(self, config, sent):
        config(ON)
        result = invoke("update-block", "--id", TASK, "--content", "new", "--json")
        error = error_of(result)
        assert error["reason"] == "precondition_required"
        assert error["options"] == ["--expect-hash"]
        assert sent == []

    def test_off_by_default(self, monkeypatch):
        LogseqHttpDouble.installed(monkeypatch, {"Probe Page": [{"content": "note", "uuid": TASK}]})
        result = invoke("update-block", "--id", TASK, "--content", "new", "--json")
        assert result.exit_code == 0, result.stderr
