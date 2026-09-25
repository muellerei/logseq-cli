#!/bin/bash
# Daily TODO overview - suitable for cronjob
# Usage: ./daily-todos.sh
# Cronjob: 0 9 * * * /path/to/daily-todos.sh >> ~/logseq-todos.log

set -o pipefail

echo "=== TODO Overview $(date +%Y-%m-%d) ==="
# With pipefail a failed call is not mistaken for an empty list. The error
# goes to stderr, which cron mails rather than appending to the log, and the
# script exits non-zero so the failure is not a quiet gap in the log.
# Each result is a row holding one block: [[{...}], ...].
if ! logseq-cli smart-query --request "tasks" --json | \
    jq -r '.results[][0] | "- [\(.marker // "TODO")] \(.content)"'; then
    echo "(the query failed)"
    echo "daily-todos: the query failed; the error is above on stderr" >&2
    exit 1
fi
echo ""
