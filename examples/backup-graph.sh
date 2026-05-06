#!/usr/bin/env bash
# Export all pages as a JSON backup
# Usage: ./backup-graph.sh [output-dir]
# Requires: LOGSEQ_TOKEN or --token

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
TOTAL=$(echo "$PAGES" | wc -l | tr -d ' ')

echo "Exporting $TOTAL pages to $OUTDIR..."
echo "$PAGES" | while IFS= read -r page; do
    COUNT=$((COUNT + 1))
    # Sanitize filename
    SAFE_NAME=$(echo "$page" | tr '/:*?"<>|' '_')
    logseq-cli get-page --name "$page" --json --no-backlinks > "$OUTDIR/${SAFE_NAME}.json" 2>/dev/null || true
    printf "\r  %d/%d" "$COUNT" "$TOTAL"
done

echo ""
echo "Done. Exported to $OUTDIR/"
