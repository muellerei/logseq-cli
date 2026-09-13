"""Config loading, heading resolution, and the message for a missing setting."""

import os
from unittest.mock import patch

import pytest

from logseq_cli.config import (
    ConfigError,
    config_search_paths,
    get,
    load_config,
    require,
    resolve_heading,
)


class TestLoadConfig:
    def test_no_file_anywhere_is_not_an_error(self, tmp_path):
        with patch.dict(os.environ, {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)}, clear=False):
            os.environ.pop("LOGSEQ_CLI_CONFIG", None)
            assert load_config() == {}

    def test_reads_a_table(self, tmp_path):
        f = tmp_path / "c.toml"
        f.write_text('[graph]\nprojects_namespace = "projects/"\n', encoding="utf-8")
        cfg = load_config(f)
        assert cfg["graph"]["projects_namespace"] == "projects/"
        assert cfg["_path"] == str(f)

    def test_malformed_toml_raises_instead_of_being_ignored(self, tmp_path):
        f = tmp_path / "c.toml"
        f.write_text("[graph\nbroken", encoding="utf-8")
        with pytest.raises(ConfigError, match="not valid TOML"):
            load_config(f)

    def test_explicit_path_that_does_not_exist_raises(self, tmp_path):
        """A named file is an instruction, so not finding it is an error."""
        with pytest.raises(ConfigError, match="No config file at"):
            load_config(tmp_path / "absent.toml")

    def test_env_var_pointing_at_a_missing_file_warns_but_continues(self, tmp_path, capsys):
        """A stale variable must not take down commands that need no settings.

        Deleting or renaming the file the variable points at would otherwise
        make every command exit 1, including a plain journal write.
        """
        missing = tmp_path / "gone.toml"
        with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(missing)}, clear=False):
            assert load_config() == {}
        err = capsys.readouterr().err
        assert "LOGSEQ_CLI_CONFIG" in err
        assert str(missing) in err
        assert "continuing without" in err

    def test_missing_file_in_default_locations_is_silent(self, tmp_path, capsys):
        """Not having a config at all is the normal case, not worth a warning."""
        with patch.dict(os.environ, {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)}, clear=False):
            os.environ.pop("LOGSEQ_CLI_CONFIG", None)
            assert load_config() == {}
        assert capsys.readouterr().err == ""

    def test_env_var_wins_over_default_locations(self, tmp_path):
        f = tmp_path / "from-env.toml"
        f.write_text('[journal]\ndefault_heading = "## Env"\n', encoding="utf-8")
        with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(f)}, clear=False):
            assert config_search_paths() == [f]
            assert load_config()["journal"]["default_heading"] == "## Env"

    def test_xdg_config_home_is_honoured(self, tmp_path):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}, clear=False):
            os.environ.pop("LOGSEQ_CLI_CONFIG", None)
            assert config_search_paths()[0] == tmp_path / "logseq-cli" / "config.toml"


class TestGet:
    def test_missing_section_returns_default(self):
        assert get({}, "graph", "projects_namespace", "fallback") == "fallback"

    def test_present_value_wins(self):
        cfg = {"graph": {"projects_namespace": "p/"}}
        assert get(cfg, "graph", "projects_namespace", "fallback") == "p/"


class TestResolveHeading:
    def setup_method(self):
        os.environ.pop("LOGSEQ_JOURNAL_HEADING", None)

    def test_named_shortcut_resolves(self):
        cfg = {"journal": {"headings": {"tasks": "## Tasks"}}}
        assert resolve_heading(cfg, "tasks") == "## Tasks"

    def test_literal_heading_passes_through(self):
        cfg = {"journal": {"headings": {"tasks": "## Tasks"}}}
        assert resolve_heading(cfg, "## Log") == "## Log"

    def test_unknown_name_passes_through_unchanged(self):
        """A typo must not silently resolve to something else."""
        assert resolve_heading({}, "nosuchname") == "nosuchname"

    def test_env_var_beats_config_default(self):
        cfg = {"journal": {"default_heading": "## FromFile"}}
        with patch.dict(os.environ, {"LOGSEQ_JOURNAL_HEADING": "## FromEnv"}):
            assert resolve_heading(cfg, None) == "## FromEnv"

    def test_config_default_used_when_env_unset(self):
        cfg = {"journal": {"default_heading": "## FromFile"}}
        assert resolve_heading(cfg, None) == "## FromFile"

    def test_nothing_configured_means_top_level(self):
        assert resolve_heading({}, None) is None


class TestRequire:
    def test_returns_configured_value(self):
        cfg = {"graph": {"projects_namespace": "projects/"}}
        assert require(cfg, "graph", "projects_namespace", "x") == "projects/"

    def test_missing_value_names_setting_file_and_caller(self):
        with pytest.raises(ConfigError) as exc:
            require({"_path": "/tmp/c.toml"}, "graph", "projects_namespace",
                    "smart-query --request 'projects'")
        msg = str(exc.value)
        assert "projects_namespace" in msg
        assert "[graph]" in msg
        assert "/tmp/c.toml" in msg
        assert "smart-query" in msg
        assert "no default" in msg

    def test_empty_string_counts_as_missing(self):
        with pytest.raises(ConfigError):
            require({"graph": {"projects_namespace": ""}}, "graph",
                    "projects_namespace", "x")
