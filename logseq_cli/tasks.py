"""What a task is, and how its marker is read and swapped.

Logseq decides what a task is with its parser (mldoc, 1.5.7 in Logseq
0.10.15), never with a pattern over text; this module holds the one place the
CLI states that rule: the eleven markers mldoc reads, their grouping into
three states, and how a marker is found at the start of a block's text and
replaced. Measured against mldoc 1.5.7 and Logseq 0.10.15.

Nothing here talks to Logseq, so all of it is tested without a mock; the
layering tests keep it that way (ADR 0003).
"""
import re

from logseq_cli.blocktext import attached_line_mask, property_line_mask
from logseq_cli.datalog import edn_string

# The words mldoc reads as :block/marker, in the order a list of tasks is
# shown: what is going on first, finished and cancelled last. STARTED is read
# by mldoc, although Logseq's own marker-pattern does not name it.
ORDER = ("DOING", "NOW", "IN-PROGRESS", "STARTED", "TODO", "LATER", "WAIT", "WAITING",
         "DONE", "CANCELED", "CANCELLED")

# A repeating task has the state of its marker: TODO with a repeater is open,
# DONE with a repeater is done. There is no extra rule for repeaters.
STATE = {
    "DOING": "open", "NOW": "open", "IN-PROGRESS": "open", "STARTED": "open",
    "TODO": "open", "LATER": "open", "WAIT": "open", "WAITING": "open",
    "DONE": "done",
    "CANCELED": "cancelled", "CANCELLED": "cancelled",
}

# The three states, in the order ORDER meets them: derived, so a state added
# to STATE reaches every reader of it.
STATES = tuple(dict.fromkeys(STATE[m] for m in ORDER))

# What Logseq offers in its own UI: every marker but STARTED.
FRONTEND_MARKERS = tuple(m for m in ORDER if m != "STARTED")

# Which marker changes move Logseq's clock, as (old marker, new marker), None
# for a block without one. The pair decides, not the target: WAIT -> DOING and
# DONE -> NOW leave the text alone. Read from Logseq 0.10.15, not measured:
# with-marker-time in handler/editor.cljs and clock-in/clock-out in
# util/clock.cljs; only with time tracking on, Logseq's default. The last two
# starting pairs count only while the block has no logbook yet. Known gaps:
# Logseq judges that on the stored block's body, a double on the text after
# the write, and Logseq reads the new marker with its own marker-pattern
# instead of mldoc.
CLOCK_IN_STEPS = (
    (None, "DOING"), (None, "NOW"), ("TODO", "DOING"), ("LATER", "NOW"),
    ("NOW", "NOW"), ("DOING", "DOING"),
)
CLOCK_OUT_STEPS = (
    ("DOING", "TODO"), ("NOW", "LATER"), ("DOING", "DONE"), ("NOW", "DONE"),
)

# Derived from ORDER, longest first, so WAITING is tried before WAIT.
_WORD = "|".join(re.escape(m) for m in sorted(ORDER, key=len, reverse=True))

# A heading prefix: hashes, then spaces only. mldoc 1.5.7 also reads
# "##<TAB>TODO x" as a task; this is stricter on purpose, because with_marker
# writes on what these functions read.
_PREFIX = r"#+ +"

# Measured with mldoc 1.5.7: a marker counts at the start of the text (after
# an optional heading prefix), in capitals, followed by a space or the end of
# the text. "TODO\nnotes" and "TODO\tx" are no tasks, "TODO \nnotes" is one.
_MARKER_RE = re.compile(rf"^({_PREFIX})?({_WORD})(?: |\Z)")
_BEFORE_NEWLINE_RE = re.compile(rf"^(?:{_PREFIX})?({_WORD})\n")
_BOX_RE = re.compile(r"\s*\[[ xX]\]")
_VAR_RE = re.compile(r"^\?[A-Za-z][A-Za-z0-9_-]*\Z")


def markers_in(states) -> list:
    """The markers of the given states, in ORDER, however the states come.

    Raises ValueError for a state that is none of open, done, cancelled.
    """
    states = set(states)
    unknown = states - set(STATE.values())
    if unknown:
        raise ValueError(f"unknown task state: {sorted(unknown)[0]!r}")
    return [m for m in ORDER if STATE[m] in states]


def marker_of(text: str):
    """The marker mldoc reads at the start of a block's text, or None."""
    m = _MARKER_RE.match(text)
    return m.group(2) if m else None


def marker_before_newline(text: str):
    """The marker when it is directly followed by a line break, else None.

    mldoc reads no task there ("TODO\\nnotes"), but the text is one a marker
    change would have to treat with care.
    """
    m = _BEFORE_NEWLINE_RE.match(text)
    return m.group(1) if m else None


def with_marker(text: str, marker: str) -> str:
    """``text`` with its marker replaced by ``marker``.

    The heading prefix stays as written; the spaces and tabs after the old
    marker become one space, none at the end of the text. A line break is
    never replaced, and only the first line changes: split over the whole
    text, a line break counts as the blank after the marker, and "TODO\\nnotes"
    came out joined as "DONE notes". Raises ValueError for a marker outside ORDER and for a
    text without a marker: a swap must not put a marker on a block that is no
    task.
    """
    if marker not in ORDER:
        raise ValueError(f"unknown marker: {marker!r}")
    m = _MARKER_RE.match(text)
    if not m:
        raise ValueError("text has no marker to replace")
    head = m.group(1) or ""
    rest = text[m.end(2):].lstrip(" \t")
    return head + marker + ("" if rest == "" else " " + rest)


def starts_with_box(content: str) -> bool:
    """Whether the first line opens with a checkbox: ``[ ]``, ``[x]``, ``[X]``."""
    return bool(_BOX_RE.match(content.split("\n", 1)[0]))


def marker_clause(var: str, markers) -> str:
    """A Datalog clause that holds for the given markers, in the given order.

    Raises ValueError for a variable that is not ``?name``, an empty list (an
    empty set would match nothing, silently) and a marker outside ORDER.
    """
    if not _VAR_RE.match(var):
        raise ValueError(f"not a Datalog variable: {var!r}")
    markers = list(markers)
    if not markers:
        raise ValueError("no markers")
    for m in markers:
        if m not in ORDER:
            raise ValueError(f"unknown marker: {m!r}")
    return f'[(contains? #{{{" ".join(edn_string(m) for m in markers)}}} {var})]'


def task_text(content: str, marker) -> str:
    """The text of a task: ``content`` without its properties, its
    SCHEDULED/DEADLINE lines and its logbook drawer, and without the marker.

    Those lines are metadata of the task, not the task: left in, a repeating
    task reported on stderr printed its own timestamp line and a ``:LOGBOOK:``
    fragment instead of what it says. Which lines they are is blocktext's rule
    (attached_line_mask, property_line_mask): an unclosed drawer opener is
    text, and nothing inside a code block counts. Which word the marker is
    comes from the DB marker (``marker``), not from a list kept here: the list
    had drifted and missed CANCELED and WAIT. Without a marker nothing is
    removed.
    """
    raw = content.split("\n")
    drop = [a or p for a, p in zip(attached_line_mask(raw), property_line_mask(raw))]
    clean = "\n".join(line for line, d in zip(raw, drop) if not d).strip()
    if marker:
        # The DB marker has decided; mldoc also reads the marker after "#" plus
        # a tab, which marker_of refuses on purpose (it is used for writing).
        # A heading prefix stays, hashes and blanks only: "\s" would take a line
        # break and a no-break space, which mldoc does not.
        clean = re.sub(rf"^(#+[ \t]+)?{re.escape(marker)}(?:\s+|$)", r"\1", clean).rstrip()
    return clean
