#!/bin/bash
# Export all Logseq pages as individual JSON files
# Usage: ./export-all-pages.sh [output-dir]
# A page that cannot be exported is named on stderr and leaves no file; the
# script then exits non-zero.

set -uo pipefail

OUTPUT_DIR="${1:-export}"
mkdir -p "$OUTPUT_DIR"

echo "Exporting pages to $OUTPUT_DIR/"
PAGES=$(logseq-cli get-all-pages --json | jq -r '.[].name') || exit 1
EXPORTED=0
FAILED=0
# Two pages can map to the same file name ("a/b" and "a_b"); the second must
# not overwrite or delete the first. A list file, for bash 3 on macOS.
SEEN=$(mktemp)
trap 'rm -f "$SEEN"' EXIT
while read -r page; do
    safe_name=$(echo "$page" | tr '/' '_')
    target="$OUTPUT_DIR/${safe_name}.json"
    if grep -qxF "$safe_name" "$SEEN"; then
        echo "  could not export: $page (its file name is taken by another page)" >&2
        FAILED=$((FAILED + 1))
    elif logseq-cli get-page --name "$page" --json > "$target.part"; then
        mv "$target.part" "$target"
        echo "$safe_name" >> "$SEEN"
        echo "  $page"
        EXPORTED=$((EXPORTED + 1))
    else
        rm -f "$target.part"
        echo "  could not export: $page" >&2
        FAILED=$((FAILED + 1))
    fi
done <<< "$PAGES"

echo "Done. $EXPORTED pages exported, $FAILED failed."
[ "$FAILED" -eq 0 ]
