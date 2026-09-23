"""How every command speaks: results on stdout, failures on stderr.

Kept apart from the commands so that one answer to "what does --json look
like" serves all of them, and so a command module cannot quietly grow its own.
"""
import functools
import json
import sys

import click
import requests

from logseq_cli.api import DatalogQueryError
from logseq_cli.config import ConfigError
from logseq_cli.datalog import InvalidKeywordError
from logseq_cli.blocktext import IdLineError, SplitBlockError


def handle_connection_error(func):
    """Catch transport-level errors and report them like every other failure.

    These two are what a caller hits first: Logseq not running, or a wrong
    token. Reporting them as prose while ``--json`` was asked for would hand an
    agent unparseable text exactly at first contact, so they go through
    :func:`fail`, which honours ``--json`` and keeps errors on stderr.

    ``as_json`` is read from the wrapped command's kwargs; Click passes every
    option by name, so it is there whenever the command declares the flag.

    ``functools.wraps`` carries ``__module__`` and ``__wrapped__`` across, not
    only the name and the docstring. ``tests/test_dry_run_coverage.py`` unwraps
    each callback and parses the module that ``__module__`` names; a wrapper
    built by hand reports the module that defines *this* decorator instead, so
    once the commands live elsewhere the scan would look in the wrong file and
    find no writing command at all.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        as_json = bool(kwargs.get("as_json"))
        try:
            return func(*args, **kwargs)
        except requests.ConnectionError:
            fail(
                "Cannot connect to Logseq API. "
                "Is Logseq running with the HTTP API enabled?",
                as_json=as_json,
                reason="connection_refused",
            )
        except requests.HTTPError as e:
            status = e.response.status_code
            hint = ("Check --token: Logseq rejected it." if status in (401, 403)
                    else None)
            fail(
                f"HTTP {status} - {e.response.text}",
                as_json=as_json,
                reason="http_error",
                status_code=status,
                **({"hint": hint} if hint else {}),
            )
        except DatalogQueryError as e:
            # Not a transport error: the connection is healthy, Logseq rejected
            # the query itself. A distinct reason keeps agents from running
            # doctor (which reports OK) and falling back to the filesystem.
            fail(
                str(e),
                as_json=as_json,
                reason="datalog_query_failed",
                query=e.query,
            )
        except ConfigError as e:
            # Nothing was sent and nothing is wrong with Logseq: a setting that
            # describes the user's graph is missing or their config is broken.
            # Its own reason keeps an agent from retrying or blaming the
            # connection; the message names the setting and the file.
            fail(
                str(e),
                as_json=as_json,
                reason="config_error",
            )
        except SplitBlockError as e:
            # Refused before the write: the text would not come back from the
            # page file as the block written (#47). A usage error, like the
            # other refusals of --content, with the line for an agent to fix.
            fail(
                str(e),
                as_json=as_json,
                exit_code=2,
                reason="splits_into_blocks",
                line=e.line,
                kind=e.kind,
            )
        except IdLineError as e:
            # Refused before the write, like a line that splits the block: the
            # text would give the block another uuid (#56).
            fail(
                str(e),
                as_json=as_json,
                exit_code=2,
                reason="id_line",
                line=e.line,
            )
        except InvalidKeywordError as e:
            # The connection is healthy and no query was sent; the input was
            # rejected before building. A distinct reason keeps this out of the
            # "connection down" path an agent would otherwise take.
            fail(
                str(e),
                as_json=as_json,
                reason="invalid_property_key",
            )
    return wrapper


def json_text(data) -> str:
    """``data`` as the JSON every command prints, without the final newline.

    Split out for the reads that measure their output before printing it
    (``--max-chars``): they must measure the same text :func:`output` prints.
    """
    return json.dumps(data, indent=2, default=str)


def output(data, as_json: bool, human_formatter=None):
    """Output data as JSON or human-readable text."""
    if as_json:
        click.echo(json_text(data))
    elif human_formatter:
        click.echo(human_formatter(data))
    else:
        click.echo(data)


def fail(message: str, as_json: bool = False, exit_code: int = 1, **fields):
    """Report an error and exit with ``exit_code`` (never returns).

    Errors always go to **stderr**, never stdout — stdout stays reserved for
    payload, so a caller parsing stdout as JSON is never handed an error object
    where data was expected. With ``--json`` the error is emitted as a JSON
    object (``{"error": ..., ...fields}``) so agents can parse it structurally
    instead of scraping prose; without it, a plain ``Error: ...`` line.

    ``fields`` adds context keys (e.g. ``id=...``, ``page=...``) to the JSON form.
    """
    if as_json:
        payload = {"error": message, **fields}
        click.echo(json.dumps(payload, indent=2, default=str), err=True)
    else:
        click.echo(f"Error: {message}", err=True)
    sys.exit(exit_code)
