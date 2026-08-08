"""--content-file / --tree-file: block content from a file instead of --content.

Two problems this solves, both hit in real journal use:

1. Shell quoting. ``--content "$(cat file)"`` breaks on an apostrophe in the
   text, and the reflex fix (stripping special characters) mangles umlauts.
   Reading the file directly removes the shell from the path entirely.
2. Multiple flush ``- `` roots. ``add-journal-block --content`` rejects those
   via ``has_flush_newline_bullets`` because inline they would silently become
   one block with raw newline bullets. From a file the whole text is parsed as
   a tree, so flush roots are legitimate siblings and the guard must not fire.
"""
import json as _json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from tests.conftest import split_runner
from logseq_cli.cli import cli
from logseq_cli.helpers import read_content_file

import click


# ---------- read_content_file ----------------------------------------------

class TestReadContentFile:
    def test_reads_utf8_verbatim(self, tmp_path):
        f = tmp_path / "entry.md"
        f.write_text("**09:00** Größe geprüft\n\t- Alice' Hinweis", encoding="utf-8")
        assert read_content_file(str(f)) == "**09:00** Größe geprüft\n\t- Alice' Hinweis"

    def test_strips_only_trailing_newlines(self, tmp_path):
        f = tmp_path / "entry.md"
        f.write_text("- a\n\t- b\n\n", encoding="utf-8")
        assert read_content_file(str(f)) == "- a\n\t- b"

    def test_missing_file_is_bad_parameter(self, tmp_path):
        with pytest.raises(click.BadParameter) as exc:
            read_content_file(str(tmp_path / "nope.md"))
        assert "not found" in str(exc.value)

    def test_directory_is_bad_parameter(self, tmp_path):
        with pytest.raises(click.BadParameter) as exc:
            read_content_file(str(tmp_path))
        assert "directory" in str(exc.value)

    def test_empty_file_is_bad_parameter(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("   \n\n", encoding="utf-8")
        with pytest.raises(click.BadParameter) as exc:
            read_content_file(str(f))
        assert "empty" in str(exc.value)

    def test_non_utf8_is_bad_parameter(self, tmp_path):
        f = tmp_path / "latin.md"
        f.write_bytes(b"Gr\xf6\xdfe")  # latin-1, invalid UTF-8
        with pytest.raises(click.BadParameter) as exc:
            read_content_file(str(f))
        assert "UTF-8" in str(exc.value)


# ---------- CLI fixture -----------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("logseq_cli.cli.LogseqAPI", lambda **kwargs: mock)
    mock.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    mock.get_page.return_value = {"name": "journal"}
    mock.get_page_blocks_tree.return_value = [
        {"uuid": "head", "content": "## Log", "children": []}]
    # Existing child under the heading, so --upsert-heading finds a target
    mock.get_block.return_value = {
        "uuid": "head", "content": "## Log",
        "children": [{"uuid": "fl", "content": "### [[Carol]]"}]}
    counter = {"n": 0}

    def _uuid(*args, **kwargs):
        counter["n"] += 1
        return {"uuid": f"u{counter['n']}"}

    mock.insert_block.side_effect = _uuid
    mock.append_block_in_page.side_effect = _uuid
    return mock


# ---------- add-journal-block --content-file --------------------------------

class TestAddJournalBlockContentFile:
    def test_multiple_flush_roots_with_children_are_siblings(self, api, tmp_path):
        """The exact case the guard rejects inline: 3 roots, 2 children each."""
        f = tmp_path / "eod.md"
        f.write_text(
            "- ### Daily Summary\n"
            "\t- Punkt A\n"
            "\t- Punkt B\n"
            "- ### Response Tracking\n"
            "\t- Bob +0.3\n"
            "\t- Alice 0.0\n"
            "- ### Offene TODOs\n"
            "\t- Deployment analysis\n"
            "\t- Book time off\n",
            encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f)])
        assert result.exit_code == 0, result.output
        assert "Added 9 block(s)" in result.output
        # 3 roots under the heading, each with 2 children
        roots = [c for c in api.insert_block.call_args_list
                 if c.args[0] == "head"]
        assert len(roots) == 3
        assert roots[0].args[1] == "### Daily Summary"
        assert roots[2].args[1] == "### Offene TODOs"

    def test_guard_does_not_fire_for_file_input(self, api, tmp_path):
        """Flush bullets from a file must not raise the --content UsageError."""
        f = tmp_path / "flat.md"
        f.write_text("- eins\n- zwei\n- drei", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--content-file", str(f)])
        assert result.exit_code == 0, result.output
        assert "ohne Einrückung" not in result.output
        assert "Added 3 block(s)" in result.output

    def test_guard_still_fires_for_inline_content(self, api):
        """The inline path keeps its protection, and now points at the file flag."""
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--content", "Kopf\n- eins\n- zwei"])
        assert result.exit_code != 0
        assert "ohne Einrückung" in result.output
        assert "--content-file" in result.output

    def test_single_root_with_children(self, api, tmp_path):
        f = tmp_path / "one.md"
        f.write_text("**09:16** Kopf\n\t- Detail A\n\t\t- Tiefer", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f)])
        assert result.exit_code == 0, result.output
        assert "Added 3 block(s)" in result.output

    def test_special_characters_survive(self, api, tmp_path):
        """Apostrophes, quotes and umlauts reach the API unmangled."""
        f = tmp_path / "umlaut.md"
        f.write_text("**14:30** Alice' \"Größe\" geprüft: Straße, Übergabe", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f)])
        assert result.exit_code == 0, result.output
        written = api.insert_block.call_args_list[0].args[1]
        assert written == "**14:30** Alice' \"Größe\" geprüft: Straße, Übergabe"

    def test_top_level_without_heading(self, api, tmp_path):
        f = tmp_path / "top.md"
        f.write_text("- eins\n\t- kind\n- zwei", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--top-level", "--content-file", str(f)])
        assert result.exit_code == 0, result.output
        assert "Added 3 block(s)" in result.output
        appended = [c.args[1] for c in api.append_block_in_page.call_args_list]
        assert appended == ["eins", "zwei"]

    def test_dry_run_counts_all_blocks_without_writing(self, api, tmp_path):
        f = tmp_path / "dry.md"
        f.write_text("- a\n\t- a1\n- b\n\t- b1", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "[DRY RUN]" in result.output
        assert "4 block(s)" in result.output
        api.insert_block.assert_not_called()

    def test_json_output(self, api, tmp_path):
        """JSON payload stays on stdout; the 'Hierarchical content' note goes to stderr."""
        f = tmp_path / "j.md"
        f.write_text("- a\n\t- a1", encoding="utf-8")
        result = split_runner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f), "--json"])
        assert result.exit_code == 0, result.output
        payload = _json.loads(result.stdout)
        assert payload["blocks_added"] == 2
        assert payload["uuid"] == "u1"

    def test_date_flag_still_applies(self, api, tmp_path):
        f = tmp_path / "d.md"
        f.write_text("- Nachtrag", encoding="utf-8")
        result = split_runner().invoke(cli, [
            "add-journal-block", "--date", "2026-08-03",
            "--content-file", str(f), "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout)["page"] == "2026-08-03"


class TestAddJournalBlockFlagValidation:
    def test_content_and_content_file_are_mutually_exclusive(self, api, tmp_path):
        f = tmp_path / "x.md"
        f.write_text("- a", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--content", "inline", "--content-file", str(f)])
        assert result.exit_code != 0
        assert "not both" in result.output
        api.insert_block.assert_not_called()

    def test_neither_flag_is_an_error(self, api):
        result = CliRunner().invoke(cli, ["add-journal-block"])
        assert result.exit_code != 0
        assert "--content" in result.output

    def test_missing_file_fails_before_any_write(self, api, tmp_path):
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--content-file", str(tmp_path / "gone.md")])
        assert result.exit_code != 0
        assert "not found" in result.output
        api.insert_block.assert_not_called()
        api.append_block_in_page.assert_not_called()

    def test_empty_file_fails_before_any_write(self, api, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("\n\n", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--content-file", str(f)])
        assert result.exit_code != 0
        assert "empty" in result.output
        api.insert_block.assert_not_called()

    def test_no_preserve_is_rejected(self, api, tmp_path):
        """--no-preserve would collapse the tree into one block with raw '- '
        markers, and the inline guard that catches that is skipped for files."""
        f = tmp_path / "eod.md"
        f.write_text("- ### Kopf\n\t- Punkt A\n\t- Punkt B", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f), "--no-preserve"])
        assert result.exit_code != 0
        assert "unvereinbar" in result.output
        api.insert_block.assert_not_called()
        api.append_block_in_page.assert_not_called()

    def test_no_preserve_still_works_with_inline_content(self, api):
        """The rejection is specific to --content-file, not a global ban."""
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content", "ein  langer   Satz", "--no-preserve"])
        assert result.exit_code == 0, result.output
        assert api.insert_block.call_args_list[0].args[1] == "ein langer Satz"


class TestUpsertHeadingKeepsAllRoots:
    """--upsert-heading used to process tree[0] only and silently drop the rest,
    while still reporting count_blocks(tree) as written. With --content-file,
    multiple flush roots are the advertised normal case, so the loss would be
    routine rather than exotic."""

    def test_further_roots_become_siblings(self, api, tmp_path):
        f = tmp_path / "u.md"
        f.write_text("- ### [[Carol]]\n\t- Punkt A\n- ### Zweiter\n\t- Punkt B",
                     encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--upsert-heading", "### [[Carol]]", "--content-file", str(f)])
        assert result.exit_code == 0, result.output
        assert "Added 4 block(s)" in result.output
        written = [c.args[1] for c in api.insert_block.call_args_list]
        assert written == ["Punkt A", "### Zweiter", "Punkt B"]
        assert api.update_block.call_args_list[0].args[1] == "### [[Carol]]"

    def test_flat_file_keeps_every_line(self, api, tmp_path):
        """A file with no indentation at all still has N roots, not one."""
        f = tmp_path / "flat.md"
        f.write_text("- eins\n- zwei\n- drei", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--upsert-heading", "### [[Carol]]", "--content-file", str(f)])
        assert result.exit_code == 0, result.output
        assert "Added 3 block(s)" in result.output
        assert api.update_block.call_args_list[0].args[1] == "eins"
        assert [c.args[1] for c in api.insert_block.call_args_list] == ["zwei", "drei"]

    def test_reported_count_matches_actual_writes(self, api, tmp_path):
        f = tmp_path / "u.md"
        f.write_text("- A\n\t- a1\n- B\n\t- b1\n- C", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--upsert-heading", "### [[Carol]]", "--content-file", str(f)])
        assert "Added 5 block(s)" in result.output
        # 1 update (root A) + 4 inserts = 5 blocks touched
        assert len(api.update_block.call_args_list) == 1
        assert len(api.insert_block.call_args_list) == 4


class TestPartialWriteIsNamed:
    """A mid-tree write failure leaves earlier blocks in place. Claiming
    "Nothing was written" invites a retry and thus duplicates."""

    def test_message_names_the_partial_state(self, api, tmp_path):
        calls = {"n": 0}

        def fail_on_fourth(*args, **kwargs):
            calls["n"] += 1
            return None if calls["n"] == 4 else {"uuid": f"u{calls['n']}"}

        api.insert_block.side_effect = fail_on_fourth
        f = tmp_path / "u.md"
        f.write_text("- ### A\n\t- a1\n\t- a2\n- ### B\n\t- b1", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f)])
        assert result.exit_code == 1
        assert "3 block(s) were already written and remain" in result.output
        assert "Nothing was written" not in result.output
        assert "duplicate" in result.output

    def test_upsert_aborts_instead_of_reporting_phantom_blocks(self, api, tmp_path):
        """The upsert path used insert_block_tree (non-strict, int-returning)
        and then reported count_blocks(tree). A failed write was invisible:
        exit 0, "Added 4 block(s)", 2 actually written."""
        api.insert_block.side_effect = None
        api.insert_block.return_value = None
        f = tmp_path / "u.md"
        f.write_text("- ### [[Carol]]\n\t- Punkt A\n\t\t- Detail A1\n\t\t- Detail A2",
                     encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--upsert-heading", "### [[Carol]]", "--content-file", str(f)])
        assert result.exit_code == 1
        assert "Added" not in result.output
        assert "1 block(s) were already written" in result.output

    def test_upsert_success_count_matches_real_writes(self, api, tmp_path):
        f = tmp_path / "u.md"
        f.write_text("- ### [[Carol]]\n\t- Punkt A\n\t\t- Detail A1\n\t\t- Detail A2",
                     encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--upsert-heading", "### [[Carol]]", "--content-file", str(f)])
        assert result.exit_code == 0, result.output
        real = len(api.update_block.call_args_list) + len(api.insert_block.call_args_list)
        assert real == 4
        assert "Added 4 block(s)" in result.output

    def test_batch_page_top_counts_across_content_values(self, api):
        """insert_block_tree_at_page_top started its counter at 0 per --content
        value, so a failure in the second value claimed "Nothing was written"
        while the first value's blocks were already on the page."""
        calls = {"n": 0}

        def fail_on_third(*args, **kwargs):
            calls["n"] += 1
            return None if calls["n"] == 3 else {"uuid": f"u{calls['n']}"}

        api.append_block_in_page.side_effect = fail_on_third
        api.insert_block.side_effect = fail_on_third
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--top-level",
            "--content", "A\n\t- a1", "--content", "B\n\t- b1"])
        assert result.exit_code == 1
        assert "2 block(s) were already written" in result.output
        assert "Nothing was written" not in result.output

    def test_page_top_append_failure_is_not_reported_as_success(self, api, tmp_path):
        """insert_formatted_content_with_uuids was the last inserter without a
        strict contract: a page Logseq has not loaded answers every append with
        HTTP 200 + null, the None UUIDs were counted, and the command printed
        "Added N block(s)" with exit 0 for an entry that never existed."""
        api.append_block_in_page.side_effect = None
        api.append_block_in_page.return_value = None
        f = tmp_path / "top.md"
        f.write_text("- ### Kopf\n\t- Punkt A", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--top-level", "--content-file", str(f)])
        assert result.exit_code == 1
        assert "Added" not in result.output
        assert "Nothing was written" in result.output

    def test_heading_fallback_failure_is_not_reported_as_success(self, api, tmp_path):
        """Same path, reached via the 'could not create heading' fallback."""
        api.get_page_blocks_tree.return_value = []
        api.append_block_in_page.side_effect = None
        api.append_block_in_page.return_value = None
        f = tmp_path / "top.md"
        f.write_text("- ### Kopf\n\t- Punkt A", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f)])
        assert result.exit_code == 1
        assert "Added" not in result.output

    def test_first_block_failure_still_says_nothing_written(self, api, tmp_path):
        """With zero blocks written, the original wording is the correct one."""
        api.insert_block.side_effect = None
        api.insert_block.return_value = None
        f = tmp_path / "u.md"
        f.write_text("- ### A\n\t- a1", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content-file", str(f)])
        assert result.exit_code == 1
        assert "Nothing was written" in result.output


# ---------- insert-block --tree-file ----------------------------------------

class TestInsertBlockTreeFile:
    def test_tab_indented_text_from_file(self, api, tmp_path):
        f = tmp_path / "tree.md"
        f.write_text("- root\n\t- child A\n\t- child B", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "insert-block", "--child-of", "parent-uuid", "--tree-file", str(f)])
        assert result.exit_code == 0, result.output
        assert "3" in result.output

    def test_json_from_file(self, api, tmp_path):
        f = tmp_path / "tree.json"
        f.write_text(_json.dumps([{"content": "a", "children": [{"content": "b"}]}]),
                     encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "insert-block", "--child-of", "parent-uuid", "--tree-file", str(f)])
        assert result.exit_code == 0, result.output
        contents = [c.args[1] for c in api.insert_block.call_args_list]
        assert contents == ["a", "b"]

    def test_tree_and_tree_file_are_mutually_exclusive(self, api, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("- a", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "insert-block", "--child-of", "p", "--tree", "- a", "--tree-file", str(f)])
        assert result.exit_code != 0
        assert "not both" in result.output
        api.insert_block.assert_not_called()

    def test_content_and_tree_file_are_mutually_exclusive(self, api, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("- a", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "insert-block", "--child-of", "p", "--content", "x", "--tree-file", str(f)])
        assert result.exit_code != 0
        assert "not both" in result.output
        api.insert_block.assert_not_called()

    def test_missing_file_fails_before_any_write(self, api, tmp_path):
        result = CliRunner().invoke(cli, [
            "insert-block", "--child-of", "p", "--tree-file", str(tmp_path / "gone.md")])
        assert result.exit_code != 0
        assert "not found" in result.output
        api.insert_block.assert_not_called()

    def test_dry_run_does_not_write(self, api, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("- a\n\t- b", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "insert-block", "--child-of", "p", "--tree-file", str(f), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "DRY RUN" in result.output
        api.insert_block.assert_not_called()
