#!/bin/bash
# Export all Logseq pages as individual JSON files
# Usage: ./export-all-pages.sh [output-dir]

OUTPUT_DIR="${1:-export}"
mkdir -p "$OUTPUT_DIR"

echo "Exporting pages to $OUTPUT_DIR/"
logseq-cli get-all-pages --json | jq -r '.[].name' | while read -r page; do
    safe_name=$(echo "$page" | tr '/' '_')
    logseq-cli get-page --name "$page" --json > "$OUTPUT_DIR/${safe_name}.json" 2>/dev/null
    echo "  $page"
done

echo "Done. $(ls "$OUTPUT_DIR" | wc -l | tr -d ' ') pages exported."
