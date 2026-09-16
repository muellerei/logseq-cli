#!/usr/bin/env python3
"""Check relative links and heading anchors across the Markdown files.

Every link between the documents here is a claim that a file and a heading
exist. Nothing verifies that, and a rename breaks it silently -- the link still
looks like a link.

Run it over the repository root:

    python3 scripts/check-links.py .

External links (http, https, mailto) are not checked: that needs the network,
and a 404 somewhere else is not the same class of error as a reference to our
own file that no longer resolves.
"""
import pathlib
import re
import sys

# Generated or untracked trees hold Markdown too, and a checker that reads them
# reports failures nobody else can reproduce: `local/` is gitignored and exists
# only on one machine, `.pytest_cache/README.md` is written by pytest. Both were
# in scope in an earlier version, which is how a run over the repository came
# back with a broken link that no contributor could have seen.
SKIP_DIRS = {".git", "local", ".pytest_cache", ".venv", "venv", "node_modules"}

EXTERNAL = ("http://", "https://", "mailto:")

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


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
    return {anchor(m.group(1)) for m in (HEADING_RE.match(line) for line in text.splitlines()) if m}


def markdown_files(root):
    for path in sorted(root.rglob("*.md")):
        if SKIP_DIRS.isdisjoint(path.parts):
            yield path


def check(root):
    broken = []
    files = list(markdown_files(root))
    for path in files:
        text = path.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(text):
            target = match.group(2)
            if target.startswith(EXTERNAL):
                continue
            file_part, _, fragment = target.partition("#")
            if file_part:
                referenced = (path.parent / file_part).resolve()
                if not referenced.exists():
                    broken.append((path, target, "no such file"))
                    continue
                # A link into a non-Markdown file cannot be checked for anchors.
                other = referenced.read_text(encoding="utf-8") if referenced.suffix == ".md" else None
            else:
                other = text
            if fragment and other is not None and fragment not in anchors(other):
                broken.append((path, target, "no such anchor"))
    return files, broken


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    files, broken = check(root)
    print(f"{len(files)} file(s), {len(broken)} broken link(s)")
    for path, target, reason in broken:
        print(f"  {path.relative_to(root)}: {target} -- {reason}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
