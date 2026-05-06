#!/usr/bin/env bash
# Export a page as Logseq-compatible markdown
# Usage: ./export-page.sh "Page Name" > output.md
# Requires: LOGSEQ_TOKEN or --token

set -euo pipefail

PAGE="${1:?Usage: $0 \"Page Name\"}"

logseq-cli get-page --name "$PAGE" --format markdown --no-backlinks
