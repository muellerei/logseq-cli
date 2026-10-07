"""What keeps a command from writing: ``[safety] read_only`` and its friends.

Each key of ``[safety]`` is a bool switch that only tightens, and every one is
worked out the same way from config, environment and flag. The environment
variable and the flag key are derived from the key (``read_only`` has
``LOGSEQ_CLI_READ_ONLY`` and ``read_only_flag``), so a key added to
``config.SAFETY_KEYS`` is decided, described and kept by ``init`` without a
line of its own here.

One function decides which switches are on and where each came from
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

import click

from logseq_cli import config as _config
from logseq_cli.config import (
    CONFIG_ENV_VAR, ConfigError, check_safety, config_search_paths, named_config_missing,
    read_config,
)
from logseq_cli.notes import print_note
from logseq_cli.writerefused import PreconditionRequired, ReadOnly

_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no", "off", "")
_CACHE = "logseq_cli.safety.decision"


def env_var(key: str) -> str:
    """The environment variable that switches ``key`` on: ``LOGSEQ_CLI_READ_ONLY``."""
    return f"LOGSEQ_CLI_{key.upper()}"


def flag_key(key: str) -> str:
    """Where the group's flag for ``key`` leaves its value in the click context."""
    return f"{key}_flag"


def flag_name(key: str) -> str:
    """The group option that switches ``key`` on: ``--read-only``."""
    return "--" + key.replace("_", "-")


def safety_keys() -> tuple:
    """The keys ``[safety]`` knows, in the order they are shown."""
    return _config.SAFETY_KEYS


@dataclass(frozen=True)
class WriteDecision:
    """Which ``[safety]`` switches are on, and which of config, env and flag set each."""
    by_key: tuple = ()          # ((key, (source, ...)), ...): every key, () when off
    config_path: str | None = None
    has_safety: bool = False    # the config file has a [safety] section

    def sources_of(self, key: str) -> tuple:
        return dict(self.by_key).get(key, ())

    def is_on(self, key: str) -> bool:
        return bool(self.sources_of(key))


def _obj() -> dict | None:
    """The click context's ``obj`` dict, or None outside a click call."""
    ctx = click.get_current_context(silent=True)
    return ctx.obj if ctx is not None and isinstance(ctx.obj, dict) else None


def _env_on(key: str) -> bool:
    name = env_var(key)
    raw = os.environ.get(name)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    print_note(f"warning: {name}={raw!r} is not a value it knows; "
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
    """Which switches are on, from a loaded config, the environment and the flags.

    The one place the three are put together, per key; ``write_decision`` (the gate)
    and ``doctor`` both come here. Raises ConfigError for a ``[safety]`` that
    cannot be trusted.
    """
    check_safety(config)
    section = config.get("safety") or {}
    by_key = []
    for key in safety_keys():
        sources = []
        if section.get(key) is True:
            sources.append("config")
        if _env_on(key):
            sources.append("env")
        if (_obj() or {}).get(flag_key(key)):
            sources.append("flag")
        by_key.append((key, tuple(sources)))
    return WriteDecision(tuple(by_key), config.get("_path"), "safety" in config)


def write_decision() -> WriteDecision:
    """Which switches are on, and from where. Worked out once per call.

    The flags are the group's, read from the click context. The
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


def refusal_reason(key: str, source: str, path) -> str:
    """One source of a switch, put in words for a refusal."""
    return {
        "config": f"{key} = true in [safety] of {path}",
        "env": f"{env_var(key)} is set",
        "flag": f"{flag_name(key)} was passed",
    }[source]


def _state_reason(source: str, path) -> str:
    """One source of a switch, put in words for the line doctor shows."""
    return f"config {path}" if source == "config" else source


def guard_write() -> None:
    """Raise :class:`ReadOnly` when writes are off."""
    decision = write_decision()
    sources = decision.sources_of("read_only")
    if not sources:
        return
    reasons = [refusal_reason("read_only", name, decision.config_path) for name in sources]
    raise ReadOnly(f"Writes are off: {'; '.join(reasons)}.",
                   source=list(sources), config_path=decision.config_path)


@dataclass(frozen=True)
class Owed:
    """What a call owes when it changes a block it read.

    ``options`` are the ones that would do, any one of them. ``always`` names
    why the call owes it whatever ``require_preconditions`` says (``"--next"``),
    or is ``None`` when only the switch makes it owe.
    """
    options: tuple
    always: str | None = None


def _param_name(option: str) -> str:
    return option.lstrip("-").replace("-", "_")


def guard_preconditions(command, params: dict) -> None:
    """Raise :class:`PreconditionRequired` when the call owes a precondition it was not given.

    The one place the obligation is checked, right behind :func:`guard_write`
    (a call that cannot write is refused as that, whatever it left out) and
    before the command's first request. ``command.owes`` is asked with the
    parsed parameters, so the answer is the call's, not the command's: it may
    owe nothing for one set of options and something for another. A command
    without ``owes`` owes nothing.
    """
    owes = getattr(command, "owes", None)
    owed = owes(params) if owes is not None else None
    if owed is None:
        return
    require_precondition(owed, any(params.get(_param_name(option)) is not None
                                   for option in owed.options))


def require_precondition(owed: Owed, given: bool) -> None:
    """Raise :class:`PreconditionRequired` when ``owed`` is not met by ``given``.

    Behind :func:`guard_preconditions`, and called by a command whose obligation
    is only known once it has read something (``add-journal-block
    --upsert-heading`` owes after it has picked the block it replaces), so the
    refusal and its wording stay in one place.
    """
    if given:
        return
    decision = write_decision()
    sources = decision.sources_of("require_preconditions")
    if not (sources or owed.always):
        return
    options = " or ".join(owed.options)
    if owed.always:
        why = f"{owed.always} needs one"
    else:
        why = "; ".join(refusal_reason("require_preconditions", name, decision.config_path)
                        for name in sources)
    raise PreconditionRequired(
        f"A precondition is required: {why}. Pass {options}.",
        source=list(sources), config_path=decision.config_path if sources else None,
        options=list(owed.options))


class WriteCommand(click.Command):
    """A command that writes: it carries ``writes = True`` where it is registered.

    ``output.handle_connection_error``, which every command that writes runs
    inside, asks for the mark once click has parsed the options and before the
    command's first request, and refuses through :func:`guard_write`. So
    ``--help`` and usage errors come as before, ``--json`` gets the refusal as
    JSON whatever order the decorators are in (there is only the one), and
    ``--dry-run`` is refused too: a preview that says "would write" when the
    real run cannot would lie about it.

    ``owes`` is the precondition the call owes (:func:`guard_preconditions`):
    a function from the parsed parameters to an :class:`Owed`, or ``None``.
    """
    writes = True

    def __init__(self, *args, owes=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owes = owes


def render_safety(section: dict) -> str:
    """The ``[safety]`` table as TOML text, for a file that is written anew.

    Only bools, which is all ``check_safety`` lets through.
    """
    lines = ["[safety]"]
    lines += [f"{key} = {'true' if value else 'false'}" for key, value in section.items()]
    return "\n".join(lines) + "\n"


def describe(decision: WriteDecision, key: str) -> str:
    """One line for doctor: the state of ``key`` and where it comes from."""
    if decision.is_on(key):
        return "on (" + ", ".join(_state_reason(name, decision.config_path)
                                  for name in decision.sources_of(key)) + ")"
    if decision.config_path is None:
        return "off (no config file found)"
    if not decision.has_safety:
        return f"off (no [safety] in {decision.config_path})"
    return f"off ([safety] {key} = false in {decision.config_path})"


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
