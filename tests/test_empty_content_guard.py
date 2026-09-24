"""Writing commands must reject empty ``--content`` instead of writing a blank block.

Motivating incident (2026-09-14): a journal entry was written with a loop of
``insert-block --child-of UUID --content "$(cat $S/$f.md)"``. The files did not
exist. The shell printed ``cat: ...: No such file or directory`` on stderr but
still exited 0, so each substitution collapsed to an empty string and the CLI
wrote three empty blocks into the graph, reporting ``Inserted block child of
<uuid>...`` for every one of them. The damage was invisible in the exit code
and only surfaced when the journal was read back.

``read_content_file`` had guarded this for ``--content-file`` since it was
added ("effectively empty file, so the caller fails before any write"). The
same reasoning applies to text arriving on the command line, where a failed
substitution is *more* likely, not less -- this closes that asymmetry.

``update-block`` is the sharpest case: there an empty value does not add a
blank block but erases the text of an existing one.
"""
import pytest
import click

from tests.conftest import split_runner, fake_api
from logseq_cli.cli import cli
from logseq_cli.cliinput import require_content


BLOCK = "abcdef12-3456-7890-abcd-ef1234567890"

# Empty, and the three shapes a failed shell substitution actually produces.
BLANK = ["", "   ", "\n", "\t\n  "]


class TestRequireContent:
    @pytest.mark.parametrize("value", BLANK)
    def test_blank_rejected(self, value):
        with pytest.raises(click.BadParameter) as exc:
            require_content(value)
        assert "--content is empty" in str(exc.value)

    def test_option_name_is_reported(self):
        with pytest.raises(click.BadParameter) as exc:
            require_content("", option="--tree")
        assert "--tree is empty" in str(exc.value)

    def test_real_content_passes_through_unchanged(self):
        # Leading/trailing whitespace is content's own business; only a value
        # that is *entirely* blank is rejected.
        assert require_content("  **09:00** a note  ") == "  **09:00** a note  "

    def test_zero_is_content(self):
        assert require_content("0") == "0"


class TestWriteCommandsRejectBlank:
    """No blank value may reach the API, on any writing command."""

    @pytest.mark.parametrize("value", BLANK)
    def test_insert_block(self, monkeypatch, value):
        api = fake_api(["u1"])
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
        result = split_runner().invoke(
            cli, ["--token", "t", "insert-block", "--child-of", BLOCK, "--content", value]
        )
        assert result.exit_code != 0
        assert "--content is empty" in result.stderr
        api.insert_block.assert_not_called()
        api.insert_batch_block.assert_not_called()

    @pytest.mark.parametrize("value", BLANK)
    def test_update_block_does_not_erase(self, monkeypatch, value):
        api = fake_api(["u1"])
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
        result = split_runner().invoke(
            cli, ["--token", "t", "update-block", "--id", BLOCK, "--content", value]
        )
        assert result.exit_code != 0
        assert "--content is empty" in result.stderr
        api.update_block.assert_not_called()

    @pytest.mark.parametrize("value", BLANK)
    def test_add_journal_block(self, monkeypatch, value):
        api = fake_api(["u1"])
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
        result = split_runner().invoke(
            cli, ["--token", "t", "add-journal-block", "--content", value]
        )
        assert result.exit_code != 0
        assert "--content is empty" in result.stderr

    @pytest.mark.parametrize("value", BLANK)
    def test_add_journal_content(self, monkeypatch, value):
        api = fake_api(["u1"])
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
        result = split_runner().invoke(
            cli, ["--token", "t", "add-journal-content", "--content", value]
        )
        assert result.exit_code != 0
        assert "--content is empty" in result.stderr

    def test_batch_rejects_when_one_value_is_blank(self, monkeypatch):
        # The repeatable form is the exact shape of the incident: a loop where
        # only some substitutions fail. One blank value must fail the call
        # rather than write the good ones and a blank alongside them.
        api = fake_api(["u1", "u2"])
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
        result = split_runner().invoke(
            cli, ["--token", "t", "add-journal-block",
                  "--content", "real content", "--content", ""]
        )
        assert result.exit_code != 0
        assert "--content is empty" in result.stderr

    @pytest.mark.parametrize("value", BLANK)
    def test_dry_run_also_rejects(self, monkeypatch, value):
        # --dry-run reports the plan; a plan to write a blank block is not one
        # worth previewing.
        api = fake_api(["u1"])
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
        result = split_runner().invoke(
            cli, ["--token", "t", "insert-block", "--child-of", BLOCK,
                  "--content", value, "--dry-run"]
        )
        assert result.exit_code != 0
        assert "--content is empty" in result.stderr


class TestRealContentStillWrites:
    """The guard must not cost the working path anything."""

    def test_insert_block_writes(self, monkeypatch):
        api = fake_api(["u1"])
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
        result = split_runner().invoke(
            cli, ["--token", "t", "insert-block", "--child-of", BLOCK,
                  "--content", "**09:00** a real entry"]
        )
        assert result.exit_code == 0, result.stderr
        assert "Inserted block" in result.stdout

    def test_update_block_writes(self, monkeypatch):
        api = fake_api(["u1"])
        api.get_block.side_effect = None
        api.get_block.return_value = {"uuid": BLOCK, "content": "alt", "properties": {}}
        monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
        result = split_runner().invoke(
            cli, ["--token", "t", "update-block", "--id", BLOCK, "--content", "neu"]
        )
        assert result.exit_code == 0, result.stderr
        api.update_block.assert_called_once()
