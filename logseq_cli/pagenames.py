"""Which page a name means: the one Logseq would open for it.

Logseq's HTTP API does not resolve an alias. ``getPage`` on one answers with
the alias's own entity, a stub without blocks; every read of it came back
empty with exit 0, and every write landed on a page of its own that Logseq
does not show under that name (measured, 0.10.15, file graph).

Logseq's UI decides by ``get-redirect-page-name`` (``frontend/db/model.cljs``):
a name whose page is empty or a placeholder, and that another page names in
its own ``alias::`` property, means that page; any other name means itself.
This module makes the same decision, so one name never means two pages. Where
it differs, it says so: two pages claiming one alias is refused here, where
Logseq silently takes the first.
"""
import unicodedata
from typing import NamedTuple

from logseq_cli.datalog import page_name_literal


class PageRef(NamedTuple):
    """A page name as given, and the page it means."""
    requested: str
    page: str
    redirected: bool = False

    def fields(self, key: str = "page") -> dict:
        """The JSON fields naming it: ``key`` as asked, ``alias_of`` if redirected.

        ``key`` stays the caller's name, so a caller who matches results to
        the names it asked for keeps finding them; ``alias_of`` says where the
        read or write went.
        """
        if self.redirected:
            return {key: self.requested, "alias_of": self.page}
        return {key: self.requested}


class AmbiguousAliasError(Exception):
    """More than one page names this alias in its ``alias::`` property."""

    def __init__(self, name: str, candidates: list):
        self.name = name
        self.candidates = candidates
        super().__init__(
            f"'{name}' is an alias of more than one page: "
            f"{', '.join(repr(c) for c in candidates)}. Name the page itself.")


class AliasError(Exception):
    """A command that takes a page's own name was given an alias of it."""

    def __init__(self, ref: PageRef, command: str):
        self.ref = ref
        super().__init__(
            f"'{ref.requested}' is an alias of '{ref.page}'. {command} takes the "
            f"page's own name, since it cannot be undone and an alias leaves "
            f"open which of the two is meant.")


def empty_or_placeholder(tree) -> bool:
    """Logseq's ``page-empty-or-dummy?``: no block, or one block holding ``""``.

    An alias Logseq made from ``alias::`` has no block; a page that existed
    before it became an alias, created empty, has one empty block (both
    measured). A page with anything written on it is a page of its own, and
    Logseq does not redirect it either.
    """
    if not tree:
        return True
    return (len(tree) == 1 and tree[0].get("content", "") == ""
            and not tree[0].get("children"))


def alias_sources(api, name: str) -> list:
    """Pages whose own ``alias::`` property names ``name``, by original name.

    ``:block/alias`` alone is not enough: Logseq links every page of an alias
    group with every other, and a second alias that once got a file of its own
    matches too. The property on the source page is what Logseq's
    ``get-alias-source-page`` checks, and it held in every state measured.
    The value comes back as a list; lower-casing is done here, since
    ``clojure.string/lower-case`` is unknown to the query engine (measured).
    Both sides in NFC, as Logseq's ``page-name-sanity-lc`` compares them.
    """
    rows = api.datascript_query(
        f"[:find ?sn ?v :where [?a :block/name {page_name_literal(name)}]"
        f" [?s :block/alias ?a] [?s :block/properties ?pr]"
        f" [(get ?pr :alias) ?v] [?s :block/original-name ?sn]]") or []
    wanted = _comparable(name)
    sources = set()
    for row in rows:
        source, values = row[0], row[1]
        values = values if isinstance(values, (list, tuple, set)) else [values]
        if any(_comparable(str(v)) == wanted for v in values):
            sources.add(source)
    return sorted(sources)


def _comparable(name: str) -> str:
    return unicodedata.normalize("NFC", name).lower()


def resolve_page(api, name: str) -> PageRef:
    """The page ``name`` means, as Logseq's UI would open it.

    A page with a file or with page properties costs one ``getPage``, a cached
    read many commands make anyway. A page without either, in practice an empty
    one (every page with text had a file, measured), is read as a block tree,
    and only an empty one or a placeholder costs one query more.
    Raises :class:`AmbiguousAliasError` when two pages claim the alias.
    """
    stub = api.get_page(name)
    if not stub:
        return PageRef(name, name)
    # Neither an alias Logseq made nor a page created empty has a file, and
    # page properties sit on a first block with text in it (measured): such a
    # page is not empty, and a large one is not read whole to find that out.
    # A file emptied down to one blank block would be missed here, and means
    # itself as it did before aliases were followed.
    if isinstance(stub, dict) and (stub.get("file") or stub.get("properties")):
        return PageRef(name, name)
    if not empty_or_placeholder(api.get_page_blocks_tree(name)):
        return PageRef(name, name)
    # By the name Logseq reports: getPage normalises further than lower().
    stored = stub.get("name") if isinstance(stub, dict) else None
    sources = alias_sources(api, stored or name)
    if not sources:
        return PageRef(name, name)
    if len(sources) > 1:
        raise AmbiguousAliasError(name, sources)
    return PageRef(name, sources[0], redirected=True)


def refuse_alias(ref: PageRef, command: str) -> None:
    """For delete-page and rename-page: an alias is refused, naming the page."""
    if ref.redirected:
        raise AliasError(ref, command)
