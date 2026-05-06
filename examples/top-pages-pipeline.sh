#!/bin/bash
# Pipeline: Top 10 most referenced pages
# Usage: ./top-pages-pipeline.sh

echo "=== Top 10 Pages by Reference Count ==="
logseq-cli get-all-pages --json | \
    jq -r '[.[] | select(.["journal?"] != true)] | sort_by(.name) | .[].name' | \
    head -20

echo ""
echo "=== Graph Statistics ==="
logseq-cli get-all-pages --json | \
    jq '{
        total_pages: length,
        journals: [.[] | select(.["journal?"] == true)] | length,
        regular: [.[] | select(.["journal?"] != true)] | length
    }'
