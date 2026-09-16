#!/usr/bin/env python3
"""Check relative links and heading anchors across the Markdown files.

Every link between the documents here is a claim that a file and a heading
exist. Nothing verifies that, and a rename breaks it silently -- the link still
looks like a link.

Run it over the repository root:

    python3 scripts/check-links.py .

External links (http, https, mailto) are not checked: that needs the network,
and a 404 somewhere else is not the same class of error as a reference to our
own file that no longer resolves. Images count as links -- a missing one is a
break the same way, and `![alt](x.png)` differs from a link only by a `!`.
"""
import pathlib
import re
import sys
import urllib.parse

# Generated trees hold Markdown too, and reading them reports failures nobody
# else can reproduce: `.pytest_cache/README.md` is written by pytest. These are
# skipped wherever they appear, since a directory named `.git` is never ours at
# any depth.
SKIP_ANYWHERE = {".git", ".pytest_cache", ".venv", "venv", "node_modules"}

# `local/` is different: it is gitignored and exists only on one machine, but
# the name is ordinary enough that a tracked `docs/local/` is plausible. Only
# the one at the root is skipped -- matching it at any depth would silently
# drop real documentation.
SKIP_AT_ROOT = {"local"}

EXTERNAL = ("http://", "https://", "mailto:")

# A link target may carry a title: [text](file.md "Title"). Splitting it off
# here keeps it out of the path; without that, the title was read as part of
# the filename and every titled link came back broken.
LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*(<[^>]*>|[^\s)]+)(?:\s+\"[^\"]*\"|\s+'[^']*')?\s*\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")

# [text][label] resolves through a definition line elsewhere in the file. The
# definition is what points at a file, so that is what gets checked; a label
# with no definition is a break of its own.
REF_LINK_RE = re.compile(r"\[[^\]]*\]\[([^\]]*)\]")
REF_DEF_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(<[^>]*>|\S+)", re.MULTILINE)

# A heading is not the only thing that can be linked to: an <a name> or an id
# attribute makes an anchor too, and rejecting those would push contributors
# away from a technique the renderer supports.
HTML_ANCHOR_RE = re.compile(r"<[^>]*\b(?:name|id)\s*=\s*[\"']([^\"']+)[\"']")

# Markdown that *shows* link syntax is not Markdown that *has* a link. This
# project documents Markdown graphs, so examples in fenced blocks and inline
# code are the normal case -- checking them turns documentation into a failure.
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")


def anchor(heading):
    """Slug a heading the way GitHub does.

    The rule that is easy to get wrong: punctuation is dropped *before* spaces
    are turned into hyphens, and the surrounding spaces stay. So an em dash in
    "007 - Write the rules down" leaves two spaces behind and the anchor gets
    **two** hyphens, not one. A first version collapsed them and reported an
    intact link as broken.
    """
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return text.replace(" ", "-")


def anchors(text):
    """Every fragment this file can be linked to.

    Two headings that slug the same do not collide: GitHub appends `-1`, `-2`
    to the later ones, so `#fixed-1` is a working link into a CHANGELOG with
    repeated section headings. Counting them here is what makes that link
    resolve instead of being reported as broken.
    """
    found = set()
    seen = {}
    # Deliberately the raw text: a heading may consist entirely of inline code
    # (`### `[journal.headings]``), and stripping code would delete the heading
    # along with it, so a link that resolves on GitHub would read as broken.
    for line in text.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            slug = anchor(heading.group(1))
            count = seen.get(slug, 0)
            found.add(slug if count == 0 else f"{slug}-{count}")
            seen[slug] = count + 1
        found.update(HTML_ANCHOR_RE.findall(line))
    return found


def markdown_files(root):
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts
        if SKIP_ANYWHERE.isdisjoint(parts) and parts[0] not in SKIP_AT_ROOT:
            yield path


def strip_code(text):
    """Blank out fenced blocks and inline code, keeping line structure intact.

    Replacing rather than deleting keeps every other offset where it was, so
    what is left still lines up with the file it came from.
    """
    out = []
    fence = None
    for line in text.splitlines():
        marker = FENCE_RE.match(line)
        if fence:
            out.append("")
            if marker and marker.group(1) == fence:
                fence = None
            continue
        if marker:
            fence = marker.group(1)
            out.append("")
            continue
        out.append(INLINE_CODE_RE.sub("", line))
    return "\n".join(out)


def read(path):
    """Read a file, or report why it could not be read instead of crashing.

    A checker that dies on one unreadable file tells a contributor less than a
    stack trace's worth of nothing: the run stops before reaching the files
    that were fine.
    """
    try:
        return path.read_text(encoding="utf-8"), None
    except (UnicodeDecodeError, OSError) as exc:
        return None, type(exc).__name__


def check_target(path, text, target):
    """Resolve one link target; return a reason if it does not hold, else None."""
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if target.startswith(EXTERNAL) or "://" in target:
        return None
    file_part, _, fragment = target.partition("#")
    # A path may be percent-encoded in the link and plain on disk.
    file_part = urllib.parse.unquote(file_part)
    fragment = urllib.parse.unquote(fragment)
    if file_part:
        referenced = (path.parent / file_part).resolve()
        if not referenced.exists():
            return "no such file"
        if referenced.suffix != ".md":
            # Anchors cannot be checked in a non-Markdown file.
            return None
        other, error = read(referenced)
        if other is None:
            return f"unreadable target ({error})"
    else:
        other = text
    if fragment and fragment not in anchors(other):
        return "no such anchor"
    return None


def check_file(path, text):
    broken = []
    body = strip_code(text)
    definitions = {m.group(1).lower(): m.group(2) for m in REF_DEF_RE.finditer(body)}
    for match in LINK_RE.finditer(body):
        reason = check_target(path, text, match.group(2))
        if reason:
            broken.append((path, match.group(2), reason))
    for match in REF_LINK_RE.finditer(body):
        label = match.group(1).lower()
        if not label:
            continue
        if label not in definitions:
            broken.append((path, f"[{match.group(1)}]", "no such link definition"))
            continue
        reason = check_target(path, text, definitions[label])
        if reason:
            broken.append((path, definitions[label], reason))
    return broken


def check(root):
    broken = []
    files = list(markdown_files(root))
    for path in files:
        text, error = read(path)
        if text is None:
            broken.append((path, "", f"unreadable ({error})"))
            continue
        broken.extend(check_file(path, text))
    return files, broken


def main():
    if len(sys.argv) > 2:
        print("usage: check-links.py [ROOT]", file=sys.stderr)
        return 2
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    # Without this a mistyped path reports "0 files, 0 broken links" and exits
    # green -- a check that silently verified nothing.
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    files, broken = check(root)
    print(f"{len(files)} file(s), {len(broken)} broken link(s)")
    for path, target, reason in broken:
        where = path.relative_to(root)
        print(f"  {where}: {target} -- {reason}" if target else f"  {where}: {reason}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
