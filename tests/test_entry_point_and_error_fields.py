"""Two assertions left over after the mutation sweep, both real.

**The script entry point.** ``pyproject.toml`` installs the console script as
``logseq_cli.cli:cli``, so ``main()`` is not on the installed path at all. It
exists for ``python logseq_cli/cli.py``, the way someone runs the tool from a
checkout without installing it. Nothing covered that: emptying ``main()`` left
the whole suite green while the direct invocation fell silent.

**The structured fields on the input errors.** ``DatalogQueryError`` carries
``api_message`` and ``query`` as attributes because ``output.py`` reads
``e.query`` to build the JSON error payload -- its docstring says so. Its two
siblings follow the same shape with ``value``, and nothing reads them yet, so
removing the assignment changed nothing a test could see.

They are kept, not deleted: a JSON error naming the offending value is the
reason the fields exist, and the three classes are meant to answer alike. That
intent is what is pinned here, so the next JSON error path finds the field
still there.
"""

import subprocess
import sys
import pathlib

import pytest

from logseq_cli.api import InvalidPortError
from logseq_cli.datalog import InvalidKeywordError, edn_keyword


REPO = pathlib.Path(__file__).resolve().parent.parent


class TestScriptEntryPoint:
    """``python logseq_cli/cli.py`` must work from a plain checkout."""

    def test_direct_invocation_prints_the_version(self):
        result = subprocess.run(
            [sys.executable, str(REPO / "logseq_cli" / "cli.py"), "--version"],
            capture_output=True, text=True, timeout=60, cwd=REPO,
        )
        assert result.returncode == 0, result.stderr
        assert "logseq-cli" in result.stdout

    def test_direct_invocation_offers_help(self):
        result = subprocess.run(
            [sys.executable, str(REPO / "logseq_cli" / "cli.py"), "--help"],
            capture_output=True, text=True, timeout=60, cwd=REPO,
        )
        assert result.returncode == 0, result.stderr
        assert "get-page" in result.stdout

    def test_main_delegates_to_the_click_group(self):
        """main() must call cli(); an empty body breaks only the checkout path."""
        from unittest.mock import patch

        import logseq_cli.cli as cli_mod

        with patch.object(cli_mod, "cli") as group:
            cli_mod.main()
        group.assert_called_once_with()


class TestErrorsCarryTheOffendingValue:
    """Structured fields, so a JSON error can name what was rejected."""

    def test_invalid_keyword_keeps_the_value(self):
        with pytest.raises(InvalidKeywordError) as exc:
            edn_keyword("type) ?v] [?p")
        assert exc.value.value == "type) ?v] [?p"

    def test_invalid_keyword_message_still_names_it(self):
        """The field is additional to the message, not instead of it."""
        with pytest.raises(InvalidKeywordError) as exc:
            edn_keyword("bad key")
        assert "bad key" in str(exc.value)
        assert exc.value.value == "bad key"

    def test_invalid_port_keeps_value_and_source(self):
        err = InvalidPortError("not-a-port", source="LOGSEQ_PORT")
        assert err.value == "not-a-port"
        assert err.source == "LOGSEQ_PORT"
        assert "not-a-port" in str(err)

    def test_datalog_query_error_keeps_both_fields(self):
        """The one field an existing caller reads, pinned beside the others."""
        from logseq_cli.api import DatalogQueryError

        err = DatalogQueryError("syntax error", "[:find ?x]")
        assert err.api_message == "syntax error"
        assert err.query == "[:find ?x]"
