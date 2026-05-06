#!/usr/bin/env bash
# Add a timestamped log entry to today's journal
# Usage: ./morning-log.sh "Started working on feature X"
# Requires: LOGSEQ_TOKEN or --token, optionally LOGSEQ_JOURNAL_HEADING

set -euo pipefail

CONTENT="${1:?Usage: $0 \"log message\"}"
TIMESTAMP=$(date +%H:%M)

logseq-cli add-journal-block --content "**${TIMESTAMP}** ${CONTENT}"
