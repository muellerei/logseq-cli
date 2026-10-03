"""``[safety] read_only``: every command that writes refuses before it sends anything.

Driven through the real client with ``requests.post`` replaced by a recorder
that refuses everything, checkEditing included: a command that gets past the
gate fails here for another reason, and the ``sent`` list says so. The tests
do not count on a mocked API method not being called.
"""
import json
import os
from pathlib import Path

import pytest

try:  # tomllib is stdlib from 3.11; 3.10 uses the tomli backport
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

from logseq_cli.api import LogseqAPI, _MUTATING_METHODS
from logseq_cli.cli import cli
from logseq_cli.config import ConfigError, check_safety, load_config, read_config
from logseq_cli.writerefused import ReadOnly
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble
from tests.test_dry_run_coverage import TestEveryWriteHasADryRun

WRITERS = sorted(TestEveryWriteHasADryRun._KNOWN_WRITERS)

# Only what click itself demands; the command bodies come after the gate.
REQUIRED = {
    "add-block-ref": ["--source-id", "x"],
    "add-journal-entry": ["--content", "x"],
    "add-note-content": ["--page", "P"],
    "copy-block": ["--id", "x", "--to-page", "P"],
    "create-page": ["--page", "P"],
    "delete-block": ["--id", "x"],
    "delete-page": ["--page", "P"],
    "move-block": ["--id", "x"],
    "remove-block": ["--id", "x"],
    "remove-property": ["--key", "k"],
    "rename-page": ["--page", "P", "--new-name", "Q"],
    "replace-text": ["--page", "P", "--find", "a", "--replace", "b"],
    "set-block-property": ["--id", "x", "--key", "k", "--value", "v"],
    "set-property": ["--page", "P", "--key", "k", "--value", "v"],
    "set-todo-status": ["--status", "DONE"],
}


@pytest.fixture
def sent(monkeypatch):
    """Every request the client tries to send; none gets an answer."""
    import logseq_cli.api

    requests_seen = []

    def post(url, json=None, **kwargs):
        requests_seen.append((json or {}).get("method"))
        raise RuntimeError("a request was sent")

    monkeypatch.setattr(logseq_cli.api.requests, "post", post)
    return requests_seen


@pytest.fixture
def double(monkeypatch):
    """The HTTP double, installed for this test."""
    return LogseqHttpDouble().install(monkeypatch)


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Write a config file and make it the one in use."""
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


def try_write(*extra):
    """A write that says what it would do, as JSON: the shape the refusals share."""
    return invoke("add-journal-block", "--content", "x", *extra, "--json")


ON = "[safety]\nread_only = true\n"


@pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
@pytest.mark.parametrize("name", WRITERS)
def test_every_writer_refuses_before_its_first_request(name, dry_run, config, sent):
    path = config(ON)
    result = invoke(name, *REQUIRED.get(name, []), *(["--dry-run"] if dry_run else []),
                    "--json")
    assert result.exit_code != 0
    error = error_of(result)
    assert error["reason"] == "read_only"
    assert error["source"] == ["config"]
    assert error["config_path"] == str(path)
    assert error["writes_landed"] == 0
    assert "Nothing was written." in error["error"]
    assert sent == []


def test_the_refusal_names_the_switch_in_plain_text(config, sent):
    path = config(ON)
    result = invoke("add-journal-block", "--content", "x")
    assert result.exit_code != 0
    assert result.stderr == (f"Error: Writes are off: read_only = true in [safety] of "
                             f"{path}. Nothing was written.\n")


class TestTheGateCoversEveryWriter:
    def test_commands_with_the_gate_are_the_commands_that_write(self):
        """A writer added without the gate fails here, not in the field."""
        scanned = set(TestEveryWriteHasADryRun()._writing_commands())
        gated = {name for name, command in cli.commands.items()
                 if getattr(command, "writes", False)}
        assert gated == scanned == TestEveryWriteHasADryRun._KNOWN_WRITERS

    @pytest.mark.parametrize("method", sorted(_MUTATING_METHODS))
    def test_the_network_refuses_a_write_method_on_any_path(self, method, config, sent):
        """A writer the scan misses still stops in ``_post``."""
        config(ON)
        with pytest.raises(ReadOnly):
            LogseqAPI(token="X")._post(method, [])
        assert sent == []


class TestItOnlyTightens:
    def test_env_false_does_not_loosen_the_config(self, config, sent, monkeypatch):
        config(ON)
        monkeypatch.setenv("LOGSEQ_CLI_READ_ONLY", "false")
        result = try_write()
        assert error_of(result)["reason"] == "read_only"
        assert error_of(result)["source"] == ["config"]

    def test_the_flag_alone_switches_writes_off(self, sent):
        result = invoke("--read-only", "add-journal-block", "--content", "x", "--json")
        error = error_of(result)
        assert error["reason"] == "read_only"
        assert error["source"] == ["flag"]
        assert error["config_path"] is None
        assert sent == []

    def test_the_flag_wins_over_a_config_that_says_false(self, config, sent):
        config("[safety]\nread_only = false\n")
        result = invoke("--read-only", "add-journal-block", "--content", "x", "--json")
        assert error_of(result)["source"] == ["flag"]

    def test_every_source_is_named(self, config, sent, monkeypatch):
        config(ON)
        monkeypatch.setenv("LOGSEQ_CLI_READ_ONLY", "1")
        result = invoke("--read-only", "add-journal-block", "--content", "x", "--json")
        assert error_of(result)["source"] == ["config", "env", "flag"]

    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_env_on(self, value, sent, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_READ_ONLY", value)
        result = try_write()
        assert error_of(result)["source"] == ["env"]

    def test_an_unknown_env_value_switches_writes_off_and_says_so(self, sent, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_READ_ONLY", "flase")
        result = invoke("add-journal-block", "--content", "x")
        assert result.exit_code != 0
        assert "Writes are off" in result.stderr
        assert "LOGSEQ_CLI_READ_ONLY='flase'" in result.stderr
        assert sent == []


class TestWhatStaysAsItWas:
    @pytest.mark.parametrize("name", WRITERS)
    def test_help_works_under_read_only(self, name, config):
        config(ON)
        result = invoke(name, "--help")
        assert result.exit_code == 0
        assert "Usage:" in result.stdout

    def test_a_usage_error_still_comes_before_the_refusal(self, config, sent):
        config(ON)
        result = invoke("set-block-property", "--key", "k", "--value", "v")
        assert result.exit_code == 2
        assert "Missing option" in result.stderr
        assert "read_only" not in result.stderr

    def test_a_command_that_only_reads_runs(self, config, double):
        double.add_page("Probe Page", ["alpha block"])
        config(ON)
        result = invoke("get-page", "--page", "Probe Page")
        assert result.exit_code == 0
        assert "alpha block" in result.stdout

    def test_without_the_switch_a_writer_goes_on(self, config, sent):
        config("[safety]\nread_only = false\n")
        result = invoke("add-journal-block", "--content", "x")
        assert "Writes are off" not in result.stderr
        assert sent, "the command should have reached the network"


def test_the_config_is_read_once_per_call_for_the_gate_and_the_network(config, monkeypatch, double):
    """The command gate and ``_post`` get one answer, from one read."""
    import logseq_cli.safety as safety
    config("[safety]\nread_only = false\n")
    reads = []
    real = safety.read_config
    monkeypatch.setattr(safety, "read_config", lambda *a, **kw: reads.append(1) or real(*a, **kw))
    result = invoke("create-page", "--page", "Fresh Page")
    assert result.exit_code == 0, result.stderr
    assert double.writes(), "the command should have written"
    assert len(reads) == 1


def test_the_network_asks_the_function_it_was_given_when_the_first_write_is_sent(sent):
    """The client takes the decision as a function and asks it late: the group
    builds the client ahead of --help and must not load the config for it."""
    asked = []

    def guard():
        asked.append(1)
        raise ReadOnly("stub says no", source=["config"], config_path=None)

    api = LogseqAPI(token="X", guard_write=guard)
    assert asked == []
    with pytest.raises(RuntimeError):     # a read: the recorder refuses the request
        api._post("logseq.Editor.getPage", [])
    assert asked == []
    with pytest.raises(ReadOnly, match="stub says no"):
        api._post("logseq.Editor.removeBlock", [])
    assert asked == [1]
    assert sent == ["logseq.Editor.getPage"]


class TestABrokenConfigStopsWriters:
    def test_a_config_that_does_not_parse_refuses_even_a_dry_run(self, config, sent):
        config("[safety\n")
        result = invoke("add-journal-block", "--content", "x", "--dry-run", "--json")
        assert result.exit_code != 0
        assert error_of(result)["reason"] == "config_error"
        assert sent == []

    def test_a_value_that_is_not_a_bool_refuses(self, config, sent):
        config('[safety]\nread_only = "yes"\n')
        result = try_write()
        error = error_of(result)
        assert error["reason"] == "config_error"
        assert "true or false" in error["error"]
        assert sent == []


class TestAMissingNamedFile:
    @pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
    def test_a_writer_refuses(self, dry_run, tmp_path, monkeypatch, sent):
        gone = tmp_path / "gone.toml"
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(gone))
        result = invoke("add-journal-block", "--content", "x", "--json",
                        *(["--dry-run"] if dry_run else []))
        error = error_of(result)
        assert result.exit_code != 0
        assert error["reason"] == "config_error"
        assert str(gone) in error["error"]
        assert sent == []

    def test_a_reader_runs(self, tmp_path, monkeypatch, double):
        double.add_page("Probe Page", ["alpha block"])
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(tmp_path / "gone.toml"))
        result = invoke("get-page", "--page", "Probe Page")
        assert result.exit_code == 0
        assert "alpha block" in result.stdout

    def test_no_variable_and_no_file_is_a_normal_run(self, sent):
        result = invoke("add-journal-block", "--content", "x")
        assert "config" not in result.stderr.lower()
        assert sent, "the command should have reached the network"


class TestSafetyIsCheckedStrictly:
    @pytest.mark.parametrize("text,hint", [
        ("[safety]\nreadonly = true\n", "did you mean `read_only`"),
        ("[safety]\nread-only = true\n", "did you mean `read_only`"),
        ("[safety]\nread_only = true\nfoo = 1\n", "unknown key `foo`"),
    ])
    def test_an_unknown_key_in_safety_names_the_key(self, text, hint, config, sent):
        config(text)
        result = try_write()
        error = error_of(result)
        assert error["reason"] == "config_error"
        assert hint in error["error"]
        assert sent == []

    @pytest.mark.parametrize("text", [
        "read_only = true\n",
        "[journal]\nread_only = true\n",
        "[saftey]\nread_only = true\n",
        "readonly = true\n",
    ])
    def test_a_safety_key_outside_safety_says_where_it_belongs(self, text, config, sent):
        config(text)
        result = try_write()
        error = error_of(result)
        assert error["reason"] == "config_error"
        assert "belongs under [safety]" in error["error"]
        assert sent == []

    @pytest.mark.parametrize("text", [
        "[journal]\ndefault_heading = \"## Log\"\n[journal.safety]\nread_only = true\n",
        "[graph.safety]\nread_only = true\n",
    ])
    def test_a_safety_table_nested_in_another_section_is_refused(self, text, config, sent):
        config(text)
        result = try_write()
        error = error_of(result)
        assert error["reason"] == "config_error"
        assert "[safety] is a top-level section" in error["error"]
        assert sent == []

    def test_the_other_sections_stay_tolerant(self):
        check_safety({"journal": {"whatever": 1}, "unknown_section": {"x": 2},
                      "top": 3, "_path": "c.toml"})

    def test_a_reader_is_not_stopped_by_a_slip_in_safety(self, config, double):
        double.add_page("Probe Page", ["alpha block"])
        config("[safety]\nreadonly = true\n")
        result = invoke("get-page", "--page", "Probe Page")
        assert result.exit_code == 0
        assert "alpha block" in result.stdout

    def test_a_reader_that_loads_the_config_warns_and_goes_on(self, config, capsys):
        config("[safety]\nreadonly = true\n")
        data = load_config()
        assert data["safety"] == {"readonly": True}
        assert "warning: " in capsys.readouterr().err

    def test_read_config_reads_and_says_nothing(self, config, capsys):
        config("[safety]\nreadonly = true\n")
        assert read_config()["safety"] == {"readonly": True}
        assert capsys.readouterr().err == ""

    def test_a_writer_says_it_once(self, config, sent):
        config("[safety]\nreadonly = true\n")
        result = invoke("add-journal-block", "--content", "x")
        assert result.stderr.count("unknown key `readonly`") == 1

    def test_a_config_without_a_slip_says_nothing(self, config, capsys):
        config(ON)
        load_config()
        assert capsys.readouterr().err == ""

    def test_a_safety_that_is_not_a_table(self):
        with pytest.raises(ConfigError, match="must be a table"):
            check_safety({"safety": True, "_path": "c.toml"})


# --- doctor and init ----------------------------------------------------------

@pytest.fixture
def healthy_doctor(monkeypatch):
    """A doctor run that is healthy but for what the config says."""
    from unittest.mock import MagicMock
    api = MagicMock()
    api.host, api.port, api.token = "127.0.0.1", "12315", "tok"
    api.base_url = "http://127.0.0.1:12315/api"
    api.call.return_value = {"currentGraph": "logseq_local_/graph"}
    api.get_all_pages.return_value = [{"name": "a"}]
    monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kwargs: api)
    monkeypatch.setattr("logseq_cli.commands.meta._port_has_listener",
                        lambda *a, **k: True)
    return api


def read_only_line(result):
    checks = json.loads(result.stdout)["checks"]
    return next(c for c in checks if c["check"] == "read_only")


class TestDoctorShowsTheState:
    def test_on_from_the_config_says_where(self, healthy_doctor, config):
        path = config(ON)
        result = invoke("doctor", "--json")
        assert result.exit_code == 0
        assert read_only_line(result)["detail"] == f"on (config {path})"
        assert json.loads(result.stdout)["healthy"] is True

    def test_on_from_env_and_flag(self, healthy_doctor, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_READ_ONLY", "1")
        result = invoke("--read-only", "doctor", "--json")
        assert read_only_line(result)["detail"] == "on (env, flag)"

    def test_off_without_a_safety_section_names_the_file(self, healthy_doctor, config):
        path = config('[journal]\ndefault_heading = "## Log"\n')
        result = invoke("doctor", "--json")
        assert result.exit_code == 0
        assert read_only_line(result)["detail"] == f"off (no [safety] in {path})"

    def test_off_without_any_config_file_says_so(self, healthy_doctor):
        result = invoke("doctor", "--json")
        assert result.exit_code == 0
        assert read_only_line(result)["detail"] == "off (no config file found)"

    def test_the_plain_text_says_writes_are_off(self, healthy_doctor, config):
        config(ON)
        result = invoke("doctor")
        assert result.exit_code == 0
        assert "read_only: on" in result.stdout
        assert "Ready to read; writes are off." in result.stdout

    def test_the_plain_text_when_writes_are_on(self, healthy_doctor):
        result = invoke("doctor")
        assert "Ready: reads and writes should work." in result.stdout

    def test_a_slip_in_safety_is_shown_and_writes_are_said_to_refuse(self, healthy_doctor, config):
        config("[safety]\nreadonly = true\n")
        result = invoke("doctor", "--json")
        line = read_only_line(result)["detail"]
        assert "unknown key `readonly`" in line
        assert "refuse" in line

    def test_an_unknown_section_is_a_note_not_a_failure(self, healthy_doctor, config):
        config("[jurnal]\nx = 1\n")
        result = invoke("doctor", "--json")
        assert result.exit_code == 0
        note = next(c for c in json.loads(result.stdout)["checks"]
                    if c["check"] == "config sections")
        assert "[jurnal]" in note["detail"]
        assert note["ok"] is None


class TestTheStateLineSaysWhichCase:
    def test_a_config_with_safety_false_says_so(self, healthy_doctor, config):
        path = config("[safety]\nread_only = false\n")
        result = invoke("doctor", "--json")
        assert read_only_line(result)["detail"] == f"off ([safety] read_only = false in {path})"

    def test_a_slip_leaves_the_closing_line_honest(self, healthy_doctor, config):
        config("[safety]\nreadonly = true\n")
        result = invoke("doctor")
        assert "Ready to read; commands that write refuse until the config is fixed." in result.stdout


def test_doctor_reads_the_config_once(healthy_doctor, config, monkeypatch):
    """The state of read_only comes from the config doctor already loaded."""
    import logseq_cli.commands.meta as meta
    import logseq_cli.safety as safety
    config(ON)
    reads = []
    real = meta.read_config
    monkeypatch.setattr(meta, "read_config", lambda *a, **kw: reads.append(1) or real(*a, **kw))
    monkeypatch.setattr(safety, "read_config", lambda *a, **kw: reads.append(1) or real(*a, **kw))
    invoke("doctor", "--json")
    assert len(reads) == 1


class TestDoctorDoesNotClaimOffWhenItCannotTell:
    def test_a_config_that_does_not_parse(self, healthy_doctor, config):
        config("[safety\n")
        line = read_only_line(invoke("doctor", "--json"))["detail"]
        assert line.startswith("unknown, commands that write refuse")

    def test_a_named_file_that_is_not_there(self, healthy_doctor, tmp_path, monkeypatch):
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(tmp_path / "gone.toml"))
        result = invoke("doctor", "--json")
        assert result.exit_code == 0
        assert read_only_line(result)["detail"].startswith("unknown, commands that write refuse")


class TestABrokenConfigIsNotAdvisedAway:
    def test_doctor_does_not_say_remove(self, healthy_doctor, config):
        config("[safety\n")
        result = invoke("doctor")
        assert result.exit_code != 0
        assert "Fix the config file" in result.stdout
        assert "remove" not in result.stdout.lower()

    def test_init_force_refuses_a_file_it_cannot_read_and_leaves_it(self, config):
        path = config("[safety\nread_only = true\n")
        before = path.read_text(encoding="utf-8")
        result = invoke("init", "--force", "--json")
        assert result.exit_code != 0
        assert error_of(result)["reason"] == "config_error"
        assert "drop its [safety] section" in error_of(result)["error"]
        assert path.read_text(encoding="utf-8") == before


class TestInitCannotLiftTheLimitByWritingElsewhere:
    """A new file at a searched path hides the one in use, or takes over from it later."""

    @staticmethod
    def setup():
        home = Path(os.environ["HOME"])
        old = home / ".logseq-cli.toml"
        old.write_text(ON, encoding="utf-8")
        return old, home / ".config" / "logseq-cli" / "config.toml"

    def test_a_new_file_in_front_of_the_config_in_use_carries_its_safety(self, double):
        old, new = self.setup()
        result = invoke("init", "--output", str(new))
        assert result.exit_code == 0, result.stderr
        assert tomllib.loads(new.read_text(encoding="utf-8"))["safety"] == {"read_only": True}
        assert f"Kept [safety] from {old}." in result.stderr
        assert old.read_text(encoding="utf-8") == ON

    def test_writes_stay_off_afterwards(self, double, sent):
        old, new = self.setup()
        invoke("init", "--output", str(new))
        result = try_write()
        assert error_of(result)["reason"] == "read_only"

    def test_a_new_file_behind_the_config_in_use_carries_its_safety(self, double):
        home = Path(os.environ["HOME"])
        active = home / ".config" / "logseq-cli" / "config.toml"
        active.parent.mkdir(parents=True)
        active.write_text(ON, encoding="utf-8")
        behind = home / ".logseq-cli.toml"
        result = invoke("init", "--output", str(behind))
        assert result.exit_code == 0, result.stderr
        assert f"Kept [safety] from {active}." in result.stderr
        active.unlink()
        assert error_of(try_write())["reason"] == "read_only"

    def test_a_file_that_is_not_searched_gets_nothing(self, double, tmp_path):
        old, _ = self.setup()
        elsewhere = tmp_path / "copy.toml"
        result = invoke("init", "--output", str(elsewhere))
        assert result.exit_code == 0
        assert "[safety]" not in elsewhere.read_text(encoding="utf-8")

    def test_a_config_in_use_that_cannot_be_read_stops_it(self, double):
        old, new = self.setup()
        old.write_text("[safety\n", encoding="utf-8")
        result = invoke("init", "--output", str(new), "--json")
        assert error_of(result)["reason"] == "config_error"
        assert not new.exists()


class TestInitForceKeepsSafety:
    def test_the_safety_section_survives_and_is_said(self, config, double):
        path = config('[journal]\ndefault_heading = "## Old"\n\n[safety]\nread_only = true\n')
        result = invoke("init", "--force")
        assert result.exit_code == 0, result.stderr
        text = path.read_text(encoding="utf-8")
        assert "## Old" not in text
        assert tomllib.loads(text)["safety"] == {"read_only": True}
        assert f"Kept [safety] from {path}." in result.stderr

    def test_a_config_without_safety_gets_none(self, config, double):
        path = config('[journal]\ndefault_heading = "## Old"\n')
        result = invoke("init", "--force")
        assert result.exit_code == 0
        assert "## Old" not in path.read_text(encoding="utf-8")
        assert "[safety]" not in path.read_text(encoding="utf-8")
        assert "Kept" not in result.stderr

    def test_a_safety_section_with_a_slip_stops_init(self, config, double):
        path = config('[safety]\nread_only = "yes"\n')
        before = path.read_text(encoding="utf-8")
        result = invoke("init", "--force", "--json")
        assert error_of(result)["reason"] == "config_error"
        assert "true or false" in error_of(result)["error"]
        assert path.read_text(encoding="utf-8") == before

    def test_the_preview_says_what_the_run_keeps(self, config, double):
        config(ON)
        result = invoke("init", "--dry-run")
        assert result.exit_code == 0
        assert "read_only = true" in result.stdout
        assert "Would keep [safety] from" in result.stderr
        assert "Kept" not in result.stderr


def test_the_example_files_use_only_sections_the_cli_reads():
    from logseq_cli.config import KNOWN_SECTIONS
    root = Path(__file__).resolve().parent.parent
    for name in ("config.example.toml", "config.example.de.toml", "config.example.minimal.toml"):
        sections = tomllib.loads((root / name).read_text(encoding="utf-8"))
        assert set(sections) <= set(KNOWN_SECTIONS), name
