"""#25: the uuid of a block just written or found, without a second call.

In real use ``add-journal-block`` ran 18 times without ``--json`` and was
followed four times by a ``find-block`` only to recover the uuid it had just
written; ``find-block | grep uuid | head -1 | awk`` appeared 8 times.
"""
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import fake_api, split_runner


def _japi():
    # Batch writes verify by reading back, which FakeGraph answers; the
    # heading's first batch uuid is j-insert, like the single-block path.
    api = fake_api(["j-insert", "j-second"])
    api.get_user_configs.return_value = {}
    api.get_page.return_value = {"name": "journal"}
    api.get_page_blocks_tree.return_value = [{"uuid": "j-heading", "content": "## Log", "children": []}]
    # Distinct per call, so printing the last uuid instead of the root fails.
    api.append_block_in_page.side_effect = [{"uuid": "j-append"}, {"uuid": "j-append-2"}]
    api.insert_block.return_value = {"uuid": "j-insert"}
    return api


def _run(api, args, runner=None):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return (runner or CliRunner()).invoke(cli, args)


class TestJournalWritersPrintTheRootUuid:
    @pytest.mark.parametrize("args, root", [
        (["add-journal-block", "--content", "entry", "--top-level"], "j-append"),
        (["add-journal-block", "--content", "a", "--content", "b", "--top-level"], "j-append"),
        (["add-journal-block", "--content", "entry", "--under-heading", "## Log"], "j-insert"),
        (["add-journal-block", "--content", "- a\n\t- b", "--under-heading", "## Log"], "j-insert"),
        (["add-journal-block", "--content", "entry", "--under-heading", "## Log",
          "--upsert-heading", "### New"], "j-insert"),
        (["add-journal-content", "--content", "- entry", "--top-level"], "j-append"),
    ])
    def test_plain_text_names_the_root_uuid(self, args, root):
        r = _run(_japi(), args + ["--date", "2026-06-04"])
        assert r.exit_code == 0, r.output
        lines = [ln for ln in r.output.splitlines() if ln.startswith("  uuid: ")]
        assert lines == [f"  uuid: {root}"]


class TestUuidLineComesBeforeThePreview:
    """The preview repeats the content, which may itself read "uuid: ...";
    the first uuid line has to be the real one, whatever the content says."""

    def test_add_journal_block(self):
        r = _run(_japi(), ["add-journal-block", "--content", "uuid: see ticket 42",
                           "--top-level", "--date", "2026-06-04"])
        assert r.exit_code == 0, r.output
        first = next(ln for ln in r.output.splitlines() if ln.startswith("  uuid: "))
        assert first == "  uuid: j-append"

    def test_insert_block(self):
        api = MagicMock()
        api.append_block_in_page.return_value = {"uuid": "ib-1"}
        r = _run(api, ["insert-block", "--page", "P", "--content", "uuid: see ticket 42"])
        assert r.exit_code == 0, r.output
        first = next(ln for ln in r.output.splitlines() if ln.startswith("  uuid: "))
        assert first == "  uuid: ib-1"


def _fapi(n):
    api = MagicMock()
    api.datascript_query.return_value = [
        [{"uuid": f"00000000-0000-4000-8000-00000000000{i}", "content": f"hit {i}",
          "page": {"original-name": "Page A", "name": "page a"}}] for i in range(n)]
    return api


class TestFindBlockUuidOnly:
    def test_bare_uuids_one_per_line(self):
        r = _run(_fapi(2), ["find-block", "--content", "hit", "--uuid-only"], split_runner())
        assert r.exit_code == 0, r.output
        assert r.stdout.splitlines() == [
            "00000000-0000-4000-8000-000000000000",
            "00000000-0000-4000-8000-000000000001"]

    def test_with_first_it_is_usable_in_command_substitution(self):
        r = _run(_fapi(3), ["find-block", "--content", "hit", "--first", "--uuid-only"],
                 split_runner())
        assert r.exit_code == 0, r.output
        assert r.stdout == "00000000-0000-4000-8000-000000000000\n"
        assert "omitted" in r.stderr  # what --first dropped is still said

    def test_no_match_fails_so_an_empty_value_cannot_flow_on(self):
        r = _run(_fapi(0), ["find-block", "--content", "hit", "--uuid-only"], split_runner())
        assert r.exit_code == 1
        assert r.stdout == ""
        assert "No blocks found" in r.stderr

    @pytest.mark.parametrize("other", ["--json", "--with-children"])
    def test_excludes_the_other_output_forms(self, other):
        api = _fapi(1)
        r = _run(api, ["find-block", "--content", "hit", "--uuid-only", other])
        assert r.exit_code != 0
        assert "--uuid-only" in r.output
        api.datascript_query.assert_not_called()

    def test_plain_output_without_the_flag_is_unchanged(self):
        r = _run(_fapi(0), ["find-block", "--content", "hit"])
        assert r.exit_code == 0
        assert "No blocks found." in r.output


class TestFindBlockExactlyOne:
    """--first picks one of several matches without a word on stdout; for a
    write target that is a guess, which resolve_single_block refuses too."""

    def test_one_match_passes(self):
        r = _run(_fapi(1), ["find-block", "--content", "hit", "--exactly-one", "--uuid-only"],
                 split_runner())
        assert r.exit_code == 0, r.output
        assert r.stdout == "00000000-0000-4000-8000-000000000000\n"

    def test_several_matches_fail_and_are_listed(self):
        r = _run(_fapi(2), ["find-block", "--content", "hit", "--exactly-one", "--uuid-only"],
                 split_runner())
        assert r.exit_code == 1
        assert r.stdout == ""
        assert "00000000-0000-4000-8000-000000000001" in r.stderr

    def test_no_match_fails(self):
        r = _run(_fapi(0), ["find-block", "--content", "hit", "--exactly-one"], split_runner())
        assert r.exit_code == 1
        assert r.stdout == ""

    def test_applies_to_the_plain_form_too(self):
        r = _run(_fapi(2), ["find-block", "--content", "hit", "--exactly-one"])
        assert r.exit_code == 1

    @pytest.mark.parametrize("other", [["--first"], ["--limit", "3"]])
    def test_excludes_first_and_limit(self, other):
        api = _fapi(1)
        r = _run(api, ["find-block", "--content", "hit", "--exactly-one", *other])
        assert r.exit_code != 0
        assert "--exactly-one" in r.output
        api.datascript_query.assert_not_called()
