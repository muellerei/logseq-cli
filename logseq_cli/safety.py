"""What keeps a command from writing: ``[safety] read_only`` and its friends.

One function decides whether writes are off and where the switch came from
(:func:`decide`); everything else asks it, the checks through
:func:`write_decision`. The commands that write ask through
:class:`WriteCommand`, before their first request; ``LogseqAPI._post`` asks
again before any write method leaves the process, which catches a writer the
command scan in the tests would miss. Both get the same answer: it is worked
out once per call and kept in the click context.

The switch only tightens. A config with ``read_only = true`` stays on when the
environment says ``false`` or the flag is missing, and there is no
``--no-read-only``: a way to loosen it would be a way for whoever is being
limited to do so.

It guards against an agent that makes a mistake, not one that sets out to get
around it: the token, ``curl`` and the Markdown files stay within its reach.

A module of its own: it needs the config, the notes and the refusal type, and
``api`` and every command module need it.
"""
import os
from dataclasses import dataclass
from typing import NamedTuple

import click

from logseq_cli.config import (
    CONFIG_ENV_VAR, ConfigError, check_safety, config_search_paths, named_config_missing,
    read_config,
)
from logseq_cli.notes import print_note
from logseq_cli.writerefused import ReadOnly

ENV_VAR = "LOGSEQ_CLI_READ_ONLY"

_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no", "off", "")
_CACHE = "logseq_cli.safety.decision"
FLAG_KEY = "read_only_flag"


@dataclass(frozen=True)
class WriteDecision:
    """Whether writes are off, and which of config, env and flag switched them."""
    sources: tuple = ()
    config_path: str | None = None
    has_safety: bool = False    # the config file has a [safety] section

    @property
    def read_only(self) -> bool:
        return bool(self.sources)


def _obj() -> dict | None:
    """The click context's ``obj`` dict, or None outside a click call."""
    ctx = click.get_current_context(silent=True)
    return ctx.obj if ctx is not None and isinstance(ctx.obj, dict) else None


def _env_on() -> bool:
    raw = os.environ.get(ENV_VAR)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    print_note(f"warning: {ENV_VAR}={raw!r} is not a value it knows; "
               "treating it as on (unknown means off-limits).")
    return True


def refuse_missing_named_config() -> None:
    """A command that writes does not carry on without the file it was pointed at.

    ``read_config`` warns and goes on for a stale ``LOGSEQ_CLI_CONFIG``, which
    suits a read. For a write the file may hold the limit, and the variable
    is the one way to name it: a deleted or mistyped path would lift it.
    """
    if named := named_config_missing():
        raise ConfigError(
            f"{CONFIG_ENV_VAR} points at {named}, which does "
            "not exist. Commands that write do not run without the limits that "
            "file may hold; fix the path or unset the variable.")


def decide(config: dict) -> WriteDecision:
    """Are writes off, from a loaded config, the environment and the flag.

    The one place the three are put together; ``write_decision`` (the gate)
    and ``doctor`` both come here. Raises ConfigError for a ``[safety]`` that
    cannot be trusted.
    """
    check_safety(config)
    sources = []
    if (config.get("safety") or {}).get("read_only") is True:
        sources.append("config")
    if _env_on():
        sources.append("env")
    if (_obj() or {}).get(FLAG_KEY):
        sources.append("flag")
    return WriteDecision(tuple(sources), config.get("_path"), "safety" in config)


def write_decision() -> WriteDecision:
    """Are writes off, and from where. Worked out once per call.

    The flag is the group's ``--read-only``, read from the click context. The
    answer is kept in that context, so the command gate and the network check
    see the same one and the config is read once. Without a context (the
    client used from a script) nothing is kept and the flag is off.
    """
    obj = _obj()
    if obj is not None and _CACHE in obj:
        return obj[_CACHE]
    refuse_missing_named_config()
    decision = decide(read_config())
    if obj is not None:
        obj[_CACHE] = decision
    return decision


class _Words(NamedTuple):
    refusal: str    # in the message of the refusal
    state: str      # in the line doctor shows


# How each source is put in words.
_WORDING = {
    "config": _Words("read_only = true in [safety] of {path}", "config {path}"),
    "env": _Words(f"{ENV_VAR} is set", "env"),
    "flag": _Words("--read-only was passed", "flag"),
}


def guard_write() -> None:
    """Raise :class:`ReadOnly` when writes are off."""
    decision = write_decision()
    if not decision.read_only:
        return
    reasons = [_WORDING[name].refusal.format(path=decision.config_path)
               for name in decision.sources]
    raise ReadOnly(f"Writes are off: {'; '.join(reasons)}.",
                   source=list(decision.sources), config_path=decision.config_path)


class WriteCommand(click.Command):
    """A command that writes: it carries ``writes = True`` where it is registered.

    ``output.handle_connection_error``, which every command that writes runs
    inside, asks for the mark once click has parsed the options and before the
    command's first request, and refuses through :func:`guard_write`. So
    ``--help`` and usage errors come as before, ``--json`` gets the refusal as
    JSON whatever order the decorators are in (there is only the one), and
    ``--dry-run`` is refused too: a preview that says "would write" when the
    real run cannot would lie about it.
    """
    writes = True


def render_safety(section: dict) -> str:
    """The ``[safety]`` table as TOML text, for a file that is written anew.

    Only bools, which is all ``check_safety`` lets through.
    """
    lines = ["[safety]"]
    lines += [f"{key} = {'true' if value else 'false'}" for key, value in section.items()]
    return "\n".join(lines) + "\n"


def describe(decision: WriteDecision) -> str:
    """One line for doctor: the state of ``read_only`` and where it comes from."""
    if decision.read_only:
        return "on (" + ", ".join(_WORDING[name].state.format(path=decision.config_path)
                                  for name in decision.sources) + ")"
    if decision.config_path is None:
        return "off (no config file found)"
    if not decision.has_safety:
        return f"off (no [safety] in {decision.config_path})"
    return f"off ([safety] read_only = false in {decision.config_path})"


def safety_to_keep(target, active):
    """Which config's ``[safety]`` a file written at ``target`` must carry.

    Dropping it would lift a limit through a command that is allowed under
    read_only. That is the file being overwritten, or, for a new file at any
    searched path, the config in use (``active``). In front of it the new file
    hides it; behind it, it takes over once the one in front is moved or
    deleted, and without ``[safety]`` the limit would go without a word.
    Answers ``(that file, its [safety] table or None)``, or ``(None, None)``.
    A file that cannot be read cannot be kept: ConfigError.
    """
    if target.is_file():
        source = target
    elif active is not None and target.resolve() in [
            p.resolve() for p in config_search_paths()]:
        source = active
    else:
        return None, None
    config = read_config(source)
    check_safety(config)
    return source, config.get("safety")
