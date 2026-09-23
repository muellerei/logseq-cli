"""replace-text inserts --replace literally unless --regex is given (#60).

--find was escaped without --regex, --replace was not: re.sub read it as a
template, so ``C:\\new`` wrote a line break (exit 0, and the read-back check
agreed, since the block held what had been computed), ``x\\dy`` ended in a
traceback and ``\\g<0>`` inserted the match. With --regex the template is the
point, but an invalid pattern or group reference was a traceback too.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli


def _api(*contents):
    api = MagicMock()
    api.get_page_blocks_tree.return_value = [
        {"uuid": f"b{i}", "content": c, "children": []} for i, c in enumerate(contents)]
    written = {}

    def _update(uuid, new_content, properties=None, replacing=None):
        written["content"] = new_content

    def _get_block(uuid, include_children=False):
        return {"uuid": uuid, "content": written.get("content", contents[0])}

    api.update_block.side_effect = _update
    api.get_block.side_effect = _get_block
    api._written = written
    return api


def _run(api, *args):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return CliRunner().invoke(cli, ["replace-text", "--page", "P", *args])


def _assert_refused(r, api, option):
    """Exit 1 through fail(), naming the option, with nothing written."""
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit), r.exception
    assert option in r.output
    api.update_block.assert_not_called()


class TestLiteralWithoutRegex:
    def test_backslash_n_stays_two_characters(self):
        api = _api("path X here")
        r = _run(api, "--find", "X", "--replace", "C:\\new")
        assert r.exit_code == 0, r.output
        assert api._written["content"] == "path C:\\new here"

    def test_unknown_escape_is_written_not_a_traceback(self):
        api = _api("a X b")
        r = _run(api, "--find", "X", "--replace", "x\\dy")
        assert r.exit_code == 0, r.output
        assert api._written["content"] == "a x\\dy b"

    def test_group_reference_is_text(self):
        api = _api("a X b")
        r = _run(api, "--find", "X", "--replace", "<\\g<0>>")
        assert r.exit_code == 0, r.output
        assert api._written["content"] == "a <\\g<0>> b"

    def test_dry_run_shows_the_literal_text(self):
        api = _api("path X here")
        r = _run(api, "--find", "X", "--replace", "C:\\new", "--dry-run", "--json")
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["matches"][0]["new"] == "path C:\\new here"
        api.update_block.assert_not_called()


class TestTemplateWithRegex:
    def test_group_reference_is_expanded(self):
        api = _api("key=value")
        r = _run(api, "--regex", "--find", r"(\w+)=(\w+)", "--replace", r"\2=\1")
        assert r.exit_code == 0, r.output
        assert api._written["content"] == "value=key"

    @pytest.mark.parametrize("extra", [[], ["--dry-run"]])
    def test_invalid_find_is_an_error_not_a_traceback(self, extra):
        api = _api("a (b")
        r = _run(api, "--regex", "--find", "(", "--replace", "x", *extra)
        _assert_refused(r, api, "--find")

    def test_find_beyond_re_limits_is_an_error_not_a_traceback(self):
        # OverflowError and RecursionError, not re.error. Under --json the
        # error is an object.
        for find in ("a{4294967296}", "(" * 2000 + ")" * 2000):
            api = _api("a")
            r = _run(api, "--regex", "--find", find, "--replace", "x", "--json")
            _assert_refused(r, api, "--find")
            assert "--find" in json.loads(r.output)["error"]

    @pytest.mark.parametrize("extra", [[], ["--dry-run"]])
    def test_missing_group_is_an_error_before_any_write(self, extra):
        # Two blocks match, and the template is refused before either is written.
        api = _api("a1", "a2")
        r = _run(api, "--regex", "--find", r"a(\d)", "--replace", r"\2", *extra)
        _assert_refused(r, api, "--replace")

    def test_unknown_group_name_is_an_error_not_a_traceback(self):
        # Python raises IndexError here, not re.error.
        api = _api("a")
        r = _run(api, "--regex", "--find", "(?P<k>a)", "--replace", "\\g<key>")
        _assert_refused(r, api, "--replace")

    def test_template_is_checked_without_a_match(self):
        api = _api("zzz")
        r = _run(api, "--regex", "--find", r"a(\d)", "--replace", r"\2")
        _assert_refused(r, api, "--replace")
