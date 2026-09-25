#!/usr/bin/env bash
# Export all pages as a JSON backup
# Usage: ./backup-graph.sh [output-dir]
# Requires: LOGSEQ_TOKEN or --token
# A page that cannot be exported is named on stderr, leaves no file behind,
# and makes the script exit non-zero: a backup with holes must not look whole.

set -euo pipefail

OUTDIR="${1:-./logseq-backup-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUTDIR"

echo "Fetching page list..."
PAGES=$(logseq-cli get-all-pages --json | python3 -c "
import sys, json
pages = json.load(sys.stdin)
for p in pages:
    name = p.get('originalName') or p.get('name', '')
    if name:
        print(name)
")

COUNT=0
FAILED=0
# Names already written in this run: two pages can sanitize to the same file
# name ("a/b" and "a_b"), and the second must neither overwrite nor delete the
# first. A list file rather than an associative array, for bash 3 on macOS.
SEEN=$(mktemp)
trap 'rm -f "$SEEN"' EXIT
TOTAL=$(echo "$PAGES" | wc -l | tr -d ' ')

echo "Exporting $TOTAL pages to $OUTDIR..."
# Not `echo | while`: the loop would run in a subshell and lose FAILED.
while IFS= read -r page; do
    COUNT=$((COUNT + 1))
    # Sanitize filename
    SAFE_NAME=$(echo "$page" | tr '/:*?"<>|' '_')
    TARGET="$OUTDIR/${SAFE_NAME}.json"
    if grep -qxF "$SAFE_NAME" "$SEEN"; then
        echo "" >&2
        echo "  could not export: $page (its file name is taken by another page)" >&2
        FAILED=$((FAILED + 1))
    elif logseq-cli get-page --name "$page" --json --no-backlinks > "$TARGET.part"; then
        mv "$TARGET.part" "$TARGET"
        echo "$SAFE_NAME" >> "$SEEN"
    else
        rm -f "$TARGET.part"
        echo "" >&2
        echo "  could not export: $page" >&2
        FAILED=$((FAILED + 1))
    fi
    printf "\r  %d/%d" "$COUNT" "$TOTAL"
done <<< "$PAGES"

echo ""
if [ "$FAILED" -gt 0 ]; then
    echo "Exported $((TOTAL - FAILED)) of $TOTAL pages to $OUTDIR/; $FAILED failed (named above)." >&2
    exit 1
fi
echo "Done. Exported $TOTAL pages to $OUTDIR/"
