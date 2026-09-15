"""LOGSEQ_PORT is validated, so a bad value names itself.

Without this, a typo travels into the URL and surfaces one step later as
"no listener" — the same message a correct port gets when Logseq is simply
not running. Two causes, one message.
"""

import pytest

from logseq_cli.api import InvalidPortError, LogseqAPI


class TestPortValidation:
    @pytest.mark.parametrize("value", ["nonsense", "12315x", "", " ", "1.5", "12 315"])
    def test_a_non_numeric_port_is_rejected(self, monkeypatch, value):
        monkeypatch.setenv("LOGSEQ_PORT", value)
        with pytest.raises(InvalidPortError):
            LogseqAPI(token="x")

    @pytest.mark.parametrize("value", ["0", "-1", "65536", "99999"])
    def test_a_port_outside_the_range_is_rejected(self, monkeypatch, value):
        monkeypatch.setenv("LOGSEQ_PORT", value)
        with pytest.raises(InvalidPortError):
            LogseqAPI(token="x")

    def test_the_message_names_the_value_and_the_range(self, monkeypatch):
        monkeypatch.setenv("LOGSEQ_PORT", "nonsense")
        with pytest.raises(InvalidPortError) as excinfo:
            LogseqAPI(token="x")
        text = str(excinfo.value)
        assert "nonsense" in text, "the rejected value must be quoted back"
        assert "LOGSEQ_PORT" in text, "the setting to fix must be named"
        assert "65535" in text, "the accepted range must be stated"

    @pytest.mark.parametrize("value", ["1", "12315", "8080", "65535"])
    def test_a_valid_port_passes(self, monkeypatch, value):
        monkeypatch.setenv("LOGSEQ_PORT", value)
        api = LogseqAPI(token="x")
        assert api.port == value

    def test_whitespace_around_a_valid_port_is_accepted(self, monkeypatch):
        """A trailing newline is what a shell pipeline leaves behind; the
        value is usable, so rejecting it would be pedantry."""
        monkeypatch.setenv("LOGSEQ_PORT", " 12315\n")
        api = LogseqAPI(token="x")
        assert api.port == "12315"

    def test_an_explicit_argument_is_validated_too(self, monkeypatch):
        """The constructor argument wins over the environment, so it must
        carry the same check — otherwise the guard depends on which of the
        two paths a caller happens to take."""
        monkeypatch.delenv("LOGSEQ_PORT", raising=False)
        with pytest.raises(InvalidPortError):
            LogseqAPI(token="x", port="nonsense")

    def test_the_message_names_the_source_the_value_came_from(self, monkeypatch):
        """Naming LOGSEQ_PORT for a value passed as --port sends the reader
        to a setting that is not the one in effect."""
        monkeypatch.setenv("LOGSEQ_PORT", "12315")
        with pytest.raises(InvalidPortError) as excinfo:
            LogseqAPI(token="x", port="99999")
        assert "--port" in str(excinfo.value)
        assert "LOGSEQ_PORT" not in str(excinfo.value)

    def test_an_unset_port_uses_the_default(self, monkeypatch):
        monkeypatch.delenv("LOGSEQ_PORT", raising=False)
        api = LogseqAPI(token="x")
        assert api.port == "12315"

    def test_a_full_api_url_bypasses_the_port(self, monkeypatch):
        """LOGSEQ_API_URL replaces the assembled URL, so the port never
        reaches the request and a leftover bad value must not block the run.

        The case is real: LOGSEQ_PORT tends to stay set in a shell profile
        long after someone switched to a full URL. Rejecting it there would
        fail a run over a value the run does not use.
        """
        monkeypatch.setenv("LOGSEQ_PORT", "nonsense")
        monkeypatch.setenv("LOGSEQ_API_URL", "http://example.invalid/api")
        api = LogseqAPI(token="x")
        assert api.base_url == "http://example.invalid/api"
