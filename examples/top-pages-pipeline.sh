#!/bin/bash
# Pipeline: the first 20 pages by name, and graph statistics
# Usage: ./top-pages-pipeline.sh

set -eo pipefail

echo "=== First 20 Pages by Name (journals excluded) ==="
# Limited in jq rather than with `head`: head closing the pipe early would
# end jq with SIGPIPE, which pipefail reports as a failure.
logseq-cli get-all-pages --json | \
    jq -r '[.[] | select(.["journal?"] != true)] | sort_by(.name) | .[:20][].name'

echo ""
echo "=== Graph Statistics ==="
logseq-cli get-all-pages --json | \
    jq '{
        total_pages: length,
        journals: [.[] | select(.["journal?"] == true)] | length,
        regular: [.[] | select(.["journal?"] != true)] | length
    }'
