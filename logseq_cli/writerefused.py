"""The ways a write can end without being done or proven.

Each type carries a ``reason`` for ``--json`` and the fields an agent needs
to act on it; the error handler in ``output`` turns any of them into the
same error object. Not click.ClickException: under ``--json`` that prints
neither JSON nor a reason (measured), so a block open in the editor and a
write Logseq ignored would read the same.

A leaf module, importing nothing from the package: ``api`` and
``strictinsert`` raise these and ``output`` catches them, and so
``strictinsert`` and ``output`` depend on the error types, not on the HTTP
client. ``api`` imports the names, so ``from logseq_cli.api import
WriteNotVerified`` works.

No type has a ``writes_landed`` field: the handler adds that from the API's
own count, for every type alike, and a field of the same name would collide
with it.
"""


class WriteRefused(Exception):
    """A write the CLI did not do, or cannot show Logseq did.

    Never raised itself: it has no ``reason``, only each subclass does.
    ``message`` is the whole sentence; whether earlier writes of the call
    landed is added by the error handler, not here.
    """

    reason: str

    def __init__(self, message: str, **fields):
        super().__init__(message)
        self.fields = fields    # go to the --json error object


class EditorOpen(WriteRefused):
    """The block is open in Logseq's editor. Fields: block, page."""
    reason = "open_in_editor"


class BlockChanged(WriteRefused):
    """The block no longer holds the text the caller read and meant to replace.
    Field: block."""
    reason = "block_changed"


class BlockNotFound(WriteRefused):
    """The block the caller meant is gone, found out before anything was
    written. Field: id (or content and page, for a block picked by its text)."""
    reason = "block_not_found"


class EditorStateUnknown(WriteRefused):
    """checkEditing answered something not understood. Field: answer."""
    reason = "editor_state_unknown"


class LogseqWriteError(WriteRefused):
    """Logseq answered a write with an error object. Fields: method, logseq_message."""
    reason = "logseq_error"


class PageExists(WriteRefused):
    """createPage on a page that exists would drop the properties. Field: page."""
    reason = "page_exists"


class PreconditionFailed(WriteRefused):
    """The block no longer is what the caller said it read. Fields: the
    ``expected_*`` the call gave, ``actual_marker`` (None for a block without
    one) and ``first_line``. Never the current hash: with it, a caller could
    repeat its old text in one step and write over what the check protects."""
    reason = "precondition_failed"


class PreconditionRequired(WriteRefused):
    """The call changes a block it read and gave no precondition, which it owes.
    Fields: source (a list of "config", "env", "flag"; empty when the command
    asks for it whatever the switch says), config_path (None when no config
    file was found), options (the ones, any of which would do)."""
    reason = "precondition_required"


class RenameRefused(WriteRefused):
    """renamePage onto an empty or taken name. Fields: old, new, why ("empty" | "exists")."""
    reason = "rename_refused"


class ReadOnly(WriteRefused):
    """Writes are switched off. Fields: source (a list of "config", "env",
    "flag"), config_path (None when no config file was found)."""
    reason = "read_only"


class WriteNotVerified(WriteRefused):
    """The write did not show in Logseq. Fields: method, target, expected, got."""
    reason = "write_not_verified"


def partial_state(landed: int) -> str:
    """What a retry would meet, from the count of writes that landed.

    No rollback (design note "Exit status"): writes before the refusal stay,
    and a retry of the whole command would write them again.
    """
    if landed:
        return (f"{landed} earlier write(s) in this call landed and remain "
                "(no rollback); check before retrying.")
    return "Nothing was written."
