"""Tests for --dry-run on the remaining write commands.

The invariant is the same one :mod:`tests.test_destructive_dry_run` asserts for
the cascading commands: with --dry-run, no mutating API call is made. These
seven overwrite or rewrite in place rather than cascade, so what a preview must
add is the *old* state — the marker being replaced, the property value about to
be overwritten, the pages whose ``[[links]]`` a rename would rewrite.

Validation must survive the preview too: a --dry-run that swallows "block not
found" or "ambiguous selector" would report a write that could never succeed.
"""
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


MUTATING = ("update_block", "remove_block", "delete_page", "rename_page",
            "insert_block", "append_block_in_page", "insert_batch_block",
            "create_page", "upsert_block_property", "remove_block_property")


def _assert_no_mutation(api):
    """No mutating API method may have been called."""
    called = [name for name in MUTATING if getattr(api, name).called]
    assert not called, f"mutating calls during --dry-run: {called}"


@pytest.fixture
def api():
    """A MagicMock LogseqAPI injected into the CLI context."""
    mock = MagicMock()
    with patch("logseq_cli.cli.LogseqAPI", return_value=mock):
        yield mock


def _json_payload(result):
    """Parse the JSON object a --json run wrote to stdout."""
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# set-todo-status
# ---------------------------------------------------------------------------
class TestSetTodoStatusDryRun:
    def test_dry_run_shows_marker_change_and_does_not_write(self, api):
        api.get_block.return_value = {"uuid": "u1", "content": "TODO Write the report"}
        result = CliRunner().invoke(cli, ["set-todo-status", "--id", "u1",
                                          "--status", "DONE", "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "TODO -> DONE" in result.output
        assert "Write the report" in result.output
        _assert_no_mutation(api)

    def test_json_reports_both_markers(self, api):
        api.get_block.return_value = {"uuid": "u1", "content": "TODO Write the report"}
        result = CliRunner().invoke(cli, ["set-todo-status", "--id", "u1",
                                          "--status", "DOING", "--dry-run", "--json"])
        assert result.exit_code == 0
        payload = _json_payload(result)
        assert payload["dry_run"] is True
        assert payload["old_marker"] == "TODO"
        assert payload["new_marker"] == "DOING"
        assert payload["new"] == "DOING Write the report"
        _assert_no_mutation(api)

    def test_without_dry_run_writes(self, api):
        api.get_block.return_value = {"uuid": "u1", "content": "TODO Write the report"}
        result = CliRunner().invoke(cli, ["set-todo-status", "--id", "u1",
                                          "--status", "DONE"])
        assert result.exit_code == 0
        api.update_block.assert_called_once_with("u1", "DONE Write the report")

    def test_missing_block_still_fails_under_dry_run(self, api):
        api.get_block.return_value = None
        result = CliRunner().invoke(cli, ["set-todo-status", "--id", "nope",
                                          "--status", "DONE", "--dry-run"])
        assert result.exit_code == 1
        assert "not found" in result.output
        _assert_no_mutation(api)

    def test_ambiguous_content_still_aborts_under_dry_run(self, api):
        # Two TODO blocks match: the command refuses to guess, preview or not.
        api.datascript_query.return_value = [
            [{"uuid": "a", "content": "TODO Ship the release"}],
            [{"uuid": "b", "content": "TODO Ship the docs"}],
        ]
        result = split_runner().invoke(cli, ["set-todo-status", "--content", "Ship",
                                             "--page", "Project Alpha",
                                             "--status", "DONE", "--dry-run"])
        assert result.exit_code == 1
        assert "refusing to guess" in result.stderr
        _assert_no_mutation(api)


# ---------------------------------------------------------------------------
# set-property
# ---------------------------------------------------------------------------
class TestSetPropertyDryRun:
    def test_dry_run_shows_old_and_new_value(self, api):
        api.get_page_blocks_tree.return_value = [
            {"uuid": "b1", "properties": {"team": "Core"}}]
        result = CliRunner().invoke(cli, ["set-property", "--name", "Alice",
                                          "--key", "team", "--value", "Platform",
                                          "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "Core" in result.output
        assert "Platform" in result.output
        _assert_no_mutation(api)

    def test_dry_run_marks_a_new_property_as_unset(self, api):
        api.get_page_blocks_tree.return_value = [{"uuid": "b1", "properties": {}}]
        result = CliRunner().invoke(cli, ["set-property", "--name", "Alice",
                                          "--key", "role", "--value", "Engineer",
                                          "--dry-run"])
        assert result.exit_code == 0
        assert "(not set)" in result.output
        _assert_no_mutation(api)

    def test_json_reports_old_value_and_dry_run(self, api):
        api.get_page_blocks_tree.return_value = [
            {"uuid": "b1", "properties": {"team": "Core"}}]
        result = CliRunner().invoke(cli, ["set-property", "--name", "Alice",
                                          "--key", "team", "--value", "Platform",
                                          "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["dry_run"] is True
        assert payload["old_value"] == "Core"
        assert payload["value"] == "Platform"
        assert payload["existed"] is True
        _assert_no_mutation(api)

    def test_without_dry_run_writes(self, api):
        api.get_page_blocks_tree.return_value = [{"uuid": "b1", "properties": {}}]
        result = CliRunner().invoke(cli, ["set-property", "--name", "Alice",
                                          "--key", "team", "--value", "Platform"])
        assert result.exit_code == 0
        api.upsert_block_property.assert_called_once_with("b1", "team", "Platform")

    def test_missing_page_still_fails_under_dry_run(self, api):
        api.get_page_blocks_tree.return_value = []
        result = split_runner().invoke(cli, ["set-property", "--name", "Ghost Page",
                                             "--key", "team", "--value", "Platform",
                                             "--dry-run"])
        assert result.exit_code == 1
        assert "not found" in result.stderr
        _assert_no_mutation(api)


# ---------------------------------------------------------------------------
# remove-property
# ---------------------------------------------------------------------------
class TestRemovePropertyDryRun:
    def test_dry_run_names_the_value_that_would_go(self, api):
        api.get_page_blocks_tree.return_value = [
            {"uuid": "b1", "properties": {"team": "Core"}}]
        result = CliRunner().invoke(cli, ["remove-property", "--name", "Alice",
                                          "--key", "team", "--dry-run"])
        assert result.exit_code == 0
        assert "Would remove 'team'" in result.output
        assert "Core" in result.output
        _assert_no_mutation(api)

    def test_dry_run_says_so_when_the_property_is_absent(self, api):
        # The live call succeeds silently on a key that was never there, so a
        # misspelled --key would otherwise read as a successful removal.
        api.get_page_blocks_tree.return_value = [
            {"uuid": "b1", "properties": {"team": "Core"}}]
        result = CliRunner().invoke(cli, ["remove-property", "--name", "Alice",
                                          "--key", "typo", "--dry-run"])
        assert result.exit_code == 0
        assert "is not set" in result.output
        assert "nothing would be removed" in result.output
        _assert_no_mutation(api)

    def test_json_reports_not_present(self, api):
        api.get_page_blocks_tree.return_value = [{"uuid": "b1", "properties": {}}]
        result = CliRunner().invoke(cli, ["remove-property", "--name", "Alice",
                                          "--key", "typo", "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["dry_run"] is True
        assert payload["status"] == "not_present"
        assert payload["present"] is False
        _assert_no_mutation(api)

    def test_dry_run_on_a_block_target(self, api):
        api.get_block.return_value = {"uuid": "b9", "properties": {"prio": 1}}
        result = CliRunner().invoke(cli, ["remove-property", "--id", "b9",
                                          "--key", "prio", "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["id"] == "b9"
        assert payload["status"] == "would_remove"
        assert payload["value"] == 1
        _assert_no_mutation(api)

    def test_without_dry_run_writes(self, api):
        api.get_page_blocks_tree.return_value = [
            {"uuid": "b1", "properties": {"team": "Core"}}]
        result = CliRunner().invoke(cli, ["remove-property", "--name", "Alice",
                                          "--key", "team"])
        assert result.exit_code == 0
        api.remove_block_property.assert_called_once_with("b1", "team")

    def test_missing_block_still_fails_under_dry_run(self, api):
        api.get_block.return_value = None
        result = split_runner().invoke(cli, ["remove-property", "--id", "nope",
                                             "--key", "prio", "--dry-run"])
        assert result.exit_code == 1
        assert "Block not found" in result.stderr
        _assert_no_mutation(api)


# ---------------------------------------------------------------------------
# set-block-property
# ---------------------------------------------------------------------------
class TestSetBlockPropertyDryRun:
    def test_dry_run_shows_old_and_new_value(self, api):
        api.get_block.return_value = {"uuid": "b1", "properties": {"prio": 1}}
        result = CliRunner().invoke(cli, ["set-block-property", "--id", "b1",
                                          "--key", "prio", "--value", "3",
                                          "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "was: 1" in result.output
        assert "now: 3" in result.output
        _assert_no_mutation(api)

    def test_json_reports_old_value_and_dry_run(self, api):
        api.get_block.return_value = {"uuid": "b1", "properties": {"prio": 1}}
        result = CliRunner().invoke(cli, ["set-block-property", "--id", "b1",
                                          "--key", "prio", "--value", "3",
                                          "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["dry_run"] is True
        assert payload["old_value"] == 1
        # --value 3 is coerced to an int, same as on the live path.
        assert payload["value"] == 3
        assert payload["existed"] is True
        _assert_no_mutation(api)

    def test_without_dry_run_writes_without_reading_first(self, api):
        result = CliRunner().invoke(cli, ["set-block-property", "--id", "b1",
                                          "--key", "prio", "--value", "3"])
        assert result.exit_code == 0
        api.upsert_block_property.assert_called_once_with("b1", "prio", 3)
        # The extra read exists only for the preview; the write path is unchanged.
        api.get_block.assert_not_called()

    def test_missing_block_fails_under_dry_run(self, api):
        # The live path cannot notice a typo'd UUID (upsert answers the same
        # either way); the preview reads the block and does.
        api.get_block.return_value = None
        result = split_runner().invoke(cli, ["set-block-property", "--id", "nope",
                                             "--key", "prio", "--value", "3",
                                             "--dry-run"])
        assert result.exit_code == 1
        assert "Block not found" in result.stderr
        _assert_no_mutation(api)


# ---------------------------------------------------------------------------
# rename-page
# ---------------------------------------------------------------------------
class TestRenamePageDryRun:
    def test_dry_run_counts_and_lists_referencing_pages(self, api):
        api.get_page.return_value = {"name": "project alpha"}
        api.get_page_linked_references.return_value = [
            [{"originalName": "Alice"}, []],
            [{"originalName": "Bob"}, []],
            [{"originalName": "Carol"}, []],
        ]
        result = CliRunner().invoke(cli, ["rename-page", "--name", "Project Alpha",
                                          "--new-name", "Project Beta", "--dry-run"])
        assert result.exit_code == 0
        assert "Project Alpha" in result.output
        assert "Project Beta" in result.output
        assert "would be rewritten: 3" in result.output
        assert "Alice" in result.output and "Carol" in result.output
        _assert_no_mutation(api)

    def test_json_reports_reference_count(self, api):
        api.get_page.return_value = {"name": "project alpha"}
        api.get_page_linked_references.return_value = [[{"originalName": "Alice"}, []]]
        result = CliRunner().invoke(cli, ["rename-page", "--name", "Project Alpha",
                                          "--new-name", "Project Beta",
                                          "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["dry_run"] is True
        assert payload["old_name"] == "Project Alpha"
        assert payload["new_name"] == "Project Beta"
        assert payload["referencing_page_count"] == 1
        assert payload["referencing_pages"] == ["Alice"]
        _assert_no_mutation(api)

    def test_backlink_failure_reports_unknown_not_zero(self, api):
        # Reporting 0 for a failed lookup would understate the blast radius of
        # the single most far-reaching command in the CLI.
        api.get_page.return_value = {"name": "project alpha"}
        api.get_page_linked_references.side_effect = RuntimeError("API unavailable")
        result = split_runner().invoke(cli, ["rename-page", "--name", "Project Alpha",
                                             "--new-name", "Project Beta", "--dry-run"])
        assert result.exit_code == 0
        assert "unknown" in result.stdout
        assert "could not read backlinks" in result.stderr
        _assert_no_mutation(api)

    def test_without_dry_run_writes(self, api):
        api.get_page.return_value = {"name": "project alpha"}
        result = CliRunner().invoke(cli, ["rename-page", "--name", "Project Alpha",
                                          "--new-name", "Project Beta"])
        assert result.exit_code == 0
        api.rename_page.assert_called_once_with("Project Alpha", "Project Beta")

    def test_missing_page_still_fails_under_dry_run(self, api):
        api.get_page.return_value = None
        result = split_runner().invoke(cli, ["rename-page", "--name", "Ghost Page",
                                             "--new-name", "Project Beta", "--dry-run"])
        assert result.exit_code == 1
        assert "not found" in result.stderr
        _assert_no_mutation(api)


# ---------------------------------------------------------------------------
# add-block-ref
# ---------------------------------------------------------------------------
class TestAddBlockRefDryRun:
    def test_dry_run_names_source_page_and_heading(self, api):
        api.get_page.return_value = {"name": "Project Alpha"}
        api.get_block.return_value = {"uuid": "src-uuid", "content": "TODO Ship it"}
        api.get_page_blocks_tree.return_value = [{"uuid": "h1", "content": "## Tasks"}]
        result = CliRunner().invoke(cli, ["add-block-ref", "--source-id", "src-uuid",
                                          "--page", "Project Alpha",
                                          "--under-heading", "## Tasks", "--dry-run"])
        assert result.exit_code == 0
        assert "((src-uuid))" in result.output
        assert "TODO Ship it" in result.output
        assert "Project Alpha" in result.output
        assert "## Tasks" in result.output
        _assert_no_mutation(api)

    def test_dry_run_warns_when_the_source_block_is_missing(self, api):
        # A ref to a non-existent block renders as nothing; the live path never
        # checks, so this is the preview's own contribution.
        api.get_page.return_value = {"name": "Project Alpha"}
        api.get_block.return_value = None
        api.get_page_blocks_tree.return_value = []
        result = CliRunner().invoke(cli, ["add-block-ref", "--source-id", "gone",
                                          "--page", "Project Alpha", "--dry-run"])
        assert result.exit_code == 0
        assert "not found" in result.output
        _assert_no_mutation(api)

    def test_dry_run_does_not_create_a_missing_heading(self, api):
        api.get_page.return_value = {"name": "Project Alpha"}
        api.get_block.return_value = {"uuid": "src-uuid", "content": "TODO Ship it"}
        api.get_page_blocks_tree.return_value = [{"uuid": "h1", "content": "## Notes"}]
        result = CliRunner().invoke(cli, ["add-block-ref", "--source-id", "src-uuid",
                                          "--page", "Project Alpha",
                                          "--under-heading", "## Tasks",
                                          "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["dry_run"] is True
        assert payload["would_create_heading"] is True
        assert payload["source_exists"] is True
        _assert_no_mutation(api)

    def test_dry_run_does_not_create_a_missing_journal_page(self, api):
        api.get_page.return_value = None
        api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
        api.get_block.return_value = {"uuid": "src-uuid", "content": "TODO Ship it"}
        api.get_page_blocks_tree.return_value = []
        result = CliRunner().invoke(cli, ["add-block-ref", "--source-id", "src-uuid",
                                          "--journal-date", "2026-04-23",
                                          "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["would_create_page"] is True
        _assert_no_mutation(api)

    def test_without_dry_run_writes(self, api):
        api.get_page.return_value = {"name": "Project Alpha"}
        api.get_page_blocks_tree.return_value = [{"uuid": "h1", "content": "## Tasks"}]
        api.insert_block.return_value = {"uuid": "new-uuid"}
        result = CliRunner().invoke(cli, ["add-block-ref", "--source-id", "src-uuid",
                                          "--page", "Project Alpha",
                                          "--under-heading", "## Tasks"])
        assert result.exit_code == 0
        api.insert_block.assert_called_once_with("h1", "((src-uuid))", {"sibling": False})


# ---------------------------------------------------------------------------
# add-note-content
# ---------------------------------------------------------------------------
class TestAddNoteContentDryRun:
    def test_dry_run_reports_page_heading_and_block_count(self, api):
        api.get_page.return_value = {"name": "Project Alpha"}
        api.get_page_blocks_tree.return_value = [{"uuid": "h1", "content": "## Roadmap"}]
        result = CliRunner().invoke(cli, ["add-note-content", "--page", "Project Alpha",
                                          "--content", "- Phase 2\n\t- Kickoff",
                                          "--under-heading", "## Roadmap", "--dry-run"])
        assert result.exit_code == 0
        # Two blocks: the root and its child, counted from the same parse the
        # live path uses.
        assert "Would add 2 block(s)" in result.output
        assert "## Roadmap" in result.output
        assert "Project Alpha" in result.output
        _assert_no_mutation(api)

    def test_json_reports_counts_and_dry_run(self, api):
        api.get_page.return_value = {"name": "Project Alpha"}
        api.get_page_blocks_tree.return_value = [{"uuid": "h1", "content": "## Roadmap"}]
        result = CliRunner().invoke(cli, ["add-note-content", "--page", "Project Alpha",
                                          "--content", "Body text",
                                          "--under-heading", "## Roadmap",
                                          "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["dry_run"] is True
        assert payload["page"] == "Project Alpha"
        assert payload["blocks_added"] == 1
        assert payload["would_create_page"] is False
        assert payload["would_create_heading"] is False
        _assert_no_mutation(api)

    def test_dry_run_does_not_create_the_page(self, api):
        api.get_page.return_value = None
        api.get_page_blocks_tree.return_value = []
        result = CliRunner().invoke(cli, ["add-note-content", "--page", "Project Alpha",
                                          "--content", "Body text",
                                          "--dry-run", "--json"])
        payload = _json_payload(result)
        assert payload["would_create_page"] is True
        _assert_no_mutation(api)

    def test_without_dry_run_writes(self, api):
        api.get_page.return_value = {"name": "Project Alpha"}
        api.append_block_in_page.return_value = {"uuid": "new-uuid"}
        result = CliRunner().invoke(cli, ["add-note-content", "--page", "Project Alpha",
                                          "--content", "Body text"])
        assert result.exit_code == 0
        api.append_block_in_page.assert_called_once_with("Project Alpha", "Body text")

    def test_no_create_still_fails_under_dry_run(self, api):
        api.get_page.return_value = None
        result = split_runner().invoke(cli, ["add-note-content", "--page", "Ghost Page",
                                             "--content", "Body text",
                                             "--no-create", "--dry-run"])
        assert result.exit_code == 1
        assert "Use --create" in result.stderr
        _assert_no_mutation(api)

    def test_bad_property_pair_still_fails_under_dry_run(self, api):
        api.get_page.return_value = {"name": "Project Alpha"}
        result = split_runner().invoke(cli, ["add-note-content", "--page", "Project Alpha",
                                             "--content", "Body text",
                                             "--property", "no-equals-sign", "--dry-run"])
        assert result.exit_code == 1
        assert "expected KEY=VALUE" in result.stderr
        _assert_no_mutation(api)


class TestDryRunNeverCreatesTheJournalPage:
    """A preview that brings a page into existence is not a preview.

    add-journal-block created the journal page before reaching its dry-run
    check, so `--dry-run` on a day with no journal yet left one behind — in
    both the single and the batch path.
    """

    def _api(self):
        api = MagicMock()
        api.get_page.return_value = None          # journal page does not exist
        api.get_user_configs.return_value = {}
        api.get_page_blocks_tree.return_value = []
        api.insert_block.return_value = {"uuid": "n"}
        api.insert_batch_block.return_value = [{"uuid": "n"}]
        return api

    def _run(self, api, *args):
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            return split_runner().invoke(cli, ["--token", "X", "add-journal-block", *args])

    def test_single_content_does_not_create_the_page(self):
        api = self._api()
        result = self._run(api, "--content", "Entry", "--dry-run")
        assert result.exit_code == 0
        api.create_page.assert_not_called()

    def test_batch_content_does_not_create_the_page(self):
        """The batch path had its own create_page call, further up."""
        api = self._api()
        result = self._run(api, "--content", "A", "--content", "B", "--dry-run")
        assert result.exit_code == 0
        api.create_page.assert_not_called()

    def test_the_preview_says_the_page_would_be_created(self):
        """Not creating it is only half the job: it has to be reported."""
        result = self._run(self._api(), "--content", "Entry", "--dry-run")
        assert "would be created" in result.stdout

    def test_json_carries_the_flag(self):
        result = self._run(self._api(), "--content", "Entry", "--dry-run", "--json")
        assert json.loads(result.stdout)["would_create_page"] is True

    def test_a_real_run_still_creates_the_page(self):
        """The deferral must not turn into a missing write.

        Asserts on create_page rather than the exit code: this fixture has no
        heading to insert under, so the run reports a failed insert afterwards.
        What matters here is that the page itself was created.
        """
        api = self._api()
        self._run(api, "--content", "Entry", "--top-level")
        api.create_page.assert_called_once()

    def test_an_existing_page_is_not_reported_as_new(self):
        api = self._api()
        api.get_page.return_value = {"name": "sep 13th, 2026"}
        result = self._run(api, "--content", "Entry", "--dry-run")
        assert "would be created" not in result.stdout


class TestEveryWriteHasADryRun:
    """The README promises "--dry-run on everything that writes".

    Nothing held it to that. ``create-page`` shipped without one and the gap
    survived because every test here names the commands it checks, so a command
    that was never named was never missed. This walks the registry instead: a
    new write command is covered the moment it is added.

    A command counts as writing if it calls a mutating API method. That is read
    off the source rather than declared in a list here, so the two cannot drift
    apart the way a hand-kept inventory would.
    """

    # The wrappers in api.py that mutate the graph, by the name the CLI calls.
    _MUTATING_CALLS = (
        "create_page", "delete_page", "rename_page",
        "append_block_in_page", "insert_block", "insert_batch_block",
        "update_block", "remove_block", "move_block", "replace_text",
        "upsert_block_property", "remove_block_property",
    )

    @staticmethod
    def _command_bodies():
        """Map each command function name to its source text.

        Read from the module file rather than via ``inspect``: every command is
        wrapped by ``handle_connection_error``, and ``functools.wraps`` copies
        enough metadata that ``getsource`` hands back the wrapper for all of
        them. Splitting the file on top-level ``def`` keeps this independent of
        the decorator stack.
        """
        import re
        import logseq_cli.cli as cli_module

        source = pathlib.Path(cli_module.__file__).read_text(encoding="utf-8")
        bodies = {}
        starts = [(m.start(), m.group(1))
                  for m in re.finditer(r"^def ([a-z_][a-z0-9_]*)\(", source, re.M)]
        for index, (offset, name) in enumerate(starts):
            end = starts[index + 1][0] if index + 1 < len(starts) else len(source)
            bodies[name] = source[offset:end]
        return bodies

    def _writing_commands(self):
        from logseq_cli.cli import cli as root

        bodies = self._command_bodies()
        writing = {}
        for name, command in root.commands.items():
            func = command.callback
            while hasattr(func, "__wrapped__"):
                func = func.__wrapped__
            source = bodies.get(func.__name__, "")
            if any(f"api.{call}(" in source for call in self._MUTATING_CALLS):
                writing[name] = command
        return writing

    def test_the_scan_finds_the_known_writers(self):
        """Guards the guard: a scan that finds nothing would pass silently."""
        found = self._writing_commands()
        for expected in ("create-page", "delete-page", "rename-page",
                         "update-block", "insert-block"):
            assert expected in found, (
                f"{expected} writes but the scan missed it — the detection is "
                f"broken, not the commands. Found: {sorted(found)}"
            )

    def test_every_writing_command_offers_dry_run(self):
        missing = []
        for name, command in self._writing_commands().items():
            flags = {opt for param in command.params
                     for opt in getattr(param, "opts", ())}
            if "--dry-run" not in flags:
                missing.append(name)
        assert not missing, (
            "these commands write but have no --dry-run, while the README "
            f"promises one on every write: {sorted(missing)}"
        )

    def test_add_journal_entry_dry_run_creates_no_page(self):
        """The preview ran after the journal page had been created.

        A --dry-run that writes is worse than none: it is the run people reach
        for to find out what would happen. Caught by hand while adding the flag,
        which is why it is asserted rather than assumed — the scan above only
        sees that the option exists.
        """
        api = MagicMock()
        api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
        api.get_page.return_value = None  # journal page not there yet
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = CliRunner().invoke(
                cli, ["add-journal-entry", "--content", "Entry", "--dry-run"])
        assert result.exit_code == 0, result.output
        api.create_page.assert_not_called()
        api.append_block_in_page.assert_not_called()
