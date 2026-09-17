"""The two probes ``doctor`` uses to tell one outage from another.

``test_doctor.py`` patches both of these out. That is right for what it tests —
how ``doctor`` reports a given answer — but it means the probes themselves were
never run: making either return ``None`` left all 842 tests green.

What they decide is the distinction the docstring calls the one that costs the
most time by hand: "Logseq is not running" versus "Logseq runs, but its HTTP
API is off". Answer it wrong and ``doctor`` sends the user to start an
application that is already open, or into the settings of one that is closed.

``_port_has_listener`` is tested against a real socket on localhost — bound and
closed within the test, never reaching the network. ``_logseq_process_running``
is tested against a stubbed ``pgrep``, since a real one would answer differently
depending on whether the machine running the suite happens to have Logseq open.
"""

import socket
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.commands.meta import _logseq_process_running, _port_has_listener


@pytest.fixture
def bound_port():
    """A real listening socket on an ephemeral port, closed afterwards."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    yield str(sock.getsockname()[1])
    sock.close()


@pytest.fixture
def free_port():
    """A port number that nothing is listening on."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return str(port)


class TestPortProbe:
    def test_true_when_something_listens(self, bound_port):
        assert _port_has_listener("127.0.0.1", bound_port) is True

    def test_false_when_nothing_listens(self, free_port):
        assert _port_has_listener("127.0.0.1", free_port, timeout=0.5) is False

    def test_answer_is_a_bool_not_none(self, free_port):
        """``doctor`` branches on this; ``None`` would read as "no listener"."""
        assert isinstance(_port_has_listener("127.0.0.1", free_port, timeout=0.5), bool)

    @pytest.mark.parametrize("port", ["not-a-port", "", "99999999"])
    def test_unusable_port_is_false_not_an_exception(self, port):
        """doctor runs this before anything validates the configured port."""
        assert _port_has_listener("127.0.0.1", port, timeout=0.5) is False

    def test_unresolvable_host_is_false_not_an_exception(self):
        assert _port_has_listener("no-such-host.invalid", "12315", timeout=0.5) is False


class TestProcessProbe:
    """Three answers, and the third is not a failure: unknown is a real state."""

    def _run(self, returncodes, has_pgrep=True):
        calls = iter(returncodes)

        def fake_run(*args, **kwargs):
            return MagicMock(returncode=next(calls))

        with patch("shutil.which", return_value="/usr/bin/pgrep" if has_pgrep else None), \
             patch("subprocess.run", side_effect=fake_run):
            return _logseq_process_running()

    def test_true_when_pgrep_finds_the_process(self):
        assert self._run([0]) is True

    def test_lowercase_pattern_is_tried_too(self):
        """Capitalised on macOS, lowercase on Linux; both must count."""
        assert self._run([1, 0]) is True

    def test_false_when_no_pattern_matches(self):
        assert self._run([1, 1]) is False

    def test_none_when_pgrep_is_absent(self):
        """Unknown, not "not running" — the two lead to different advice."""
        assert self._run([], has_pgrep=False) is None

    def test_none_when_pgrep_fails(self):
        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch("subprocess.run", side_effect=OSError("boom")):
            assert _logseq_process_running() is None

    def test_none_when_pgrep_times_out(self):
        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired("pgrep", 5)):
            assert _logseq_process_running() is None

    def test_unknown_is_distinguishable_from_not_running(self):
        """``doctor`` gives different remedies for False and None."""
        assert self._run([1, 1]) is False
        assert self._run([], has_pgrep=False) is None
        assert self._run([1, 1]) is not self._run([], has_pgrep=False)
