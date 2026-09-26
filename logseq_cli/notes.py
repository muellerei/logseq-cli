"""Notes on stderr, held back under ``--json`` until the command ends.

A note says something beside the result: an ``id::`` line dropped, a key
stored under another spelling, a result cut short. Under ``--json`` stderr
holds the error object when a command fails, and a note printed before it
made that stderr no JSON: every write can still be refused after its notes
were printed (a block open in the editor, a write Logseq did not do).

So while ``output.handle_connection_error`` runs a command under ``--json``,
:func:`print_note` holds the notes. A failure through ``output.fail`` carries them
in the error object as ``notes``; otherwise they are printed when the command
ends, after its result. Without ``--json`` a note goes out at once, as before.

A leaf module, importing nothing from the package, so that the helpers that
print notes (``outlinetext``, ``blockprops``, ``config``) need not import
``output``.
"""
import click

_HELD = "logseq_cli.notes.held"


def print_note(message: str) -> None:
    """Print ``message`` on stderr, or hold it while notes are held."""
    ctx = click.get_current_context(silent=True)
    held = ctx.meta.get(_HELD) if ctx is not None else None
    if held is None:
        click.echo(message, err=True)
    else:
        held.append(message)


def hold_notes() -> None:
    """Hold every note from here until :func:`release_notes`."""
    ctx = click.get_current_context(silent=True)
    if ctx is not None:
        ctx.meta.setdefault(_HELD, [])


def take_notes() -> list:
    """The notes held so far, which are then no longer held for printing."""
    ctx = click.get_current_context(silent=True)
    held = ctx.meta.get(_HELD) if ctx is not None else None
    if not held:
        return []
    taken = list(held)
    held.clear()
    return taken


def release_notes() -> None:
    """Print the notes still held, and stop holding."""
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return
    for message in ctx.meta.pop(_HELD, None) or []:
        click.echo(message, err=True)
