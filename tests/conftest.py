"""Shared test helpers."""
from click.testing import CliRunner


def split_runner():
    """CliRunner that captures stderr separately from stdout.

    Click <8.2 needs ``mix_stderr=False`` for that; in 8.2+ the streams are
    always separate and the argument was removed. Support both so assertions
    about "payload on stdout, errors on stderr" keep working across versions.
    """
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()
