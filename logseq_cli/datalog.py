"""Build datalog literals from user values.

The single place where a user-supplied value may enter a datalog query. Three
functions because the queries in this repo have exactly three value positions:
a string literal, a keyword after ``:``, and a page-name literal (which Logseq
stores lowercased). ``smart-query --advanced`` is the one documented exception
and stays a raw pass-through.

No imports from ``api`` or ``cli``: this module must stay purely testable.
"""
import re


class InvalidKeywordError(ValueError):
    """A property key was rejected by the keyword whitelist.

    Distinct from a failed query on purpose: the connection is healthy and no
    query was sent, so this must never read as "nothing found" or "Logseq
    down".
    """

    ALLOWED = "letters, digits, and - _ ? ! * + < > ="

    def __init__(self, value: str):
        super().__init__(
            f"Invalid property key {value!r}: allowed are {self.ALLOWED}."
        )
        self.value = value


# Keywords sit outside any string literal, so escaping cannot help there; only
# a character-class check can. The class is cut to the real stock (35 property
# keys in the reference graph, including `journal?`), not guessed. Uppercase
# stays allowed for foreign graphs and the camelCase view of getAllPages.
_KEYWORD_RE = re.compile(r"^[A-Za-z0-9_?!*+<>=-]+$")


def edn_string(value: str) -> str:
    """Return ``value`` as an EDN string literal, quotes included.

    Backslash is escaped FIRST, then the quote. The order looks swappable but
    is not: quoting first would leave the escaping backslashes themselves
    unescaped, which is exactly the bypass (input ending in ``\\``) this
    function exists to close. Control characters become EDN escapes so they
    cannot break the query across lines.

    The surrounding quotes are part of the return value so a caller cannot
    forget them.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def edn_keyword(value: str) -> str:
    """Return ``value`` for use after ``:`` in a keyword position.

    Raises :class:`InvalidKeywordError` for anything outside the whitelist;
    the message names the rejected value so a caller sees what to fix.
    """
    if not _KEYWORD_RE.match(value):
        raise InvalidKeywordError(value)
    return value


def page_name_literal(value: str) -> str:
    """Return a page name as an EDN string literal for ``:block/name``.

    Logseq stores ``:block/name`` lowercased, so the lowercasing here is a
    domain rule, not an escaping detail: querying with the user's spelling
    finds nothing (``"Alice"`` 0 hits, ``"alice"`` 1569 hits on the reference
    graph).
    """
    return edn_string(value.lower())
