#!/bin/bash
# Daily TODO overview - suitable for cronjob
# Usage: ./daily-todos.sh
# Cronjob: 0 9 * * * /path/to/daily-todos.sh >> ~/logseq-todos.log

echo "=== TODO Overview $(date +%Y-%m-%d) ==="
logseq-cli smart-query --request "tasks" --json 2>/dev/null | \
    jq -r '.results[] | "- [\(.marker // "TODO")] \(.content)"' 2>/dev/null || \
    echo "(No results or Logseq not running)"
echo ""
