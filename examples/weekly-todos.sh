#!/usr/bin/env bash
# List all open TODOs, grouped by page
# Usage: ./weekly-todos.sh [--status DOING]
# Requires: LOGSEQ_TOKEN or --token

set -euo pipefail

echo "=== Open Tasks ==="
logseq-cli get-todos --status TODO --status DOING "$@"
echo ""
echo "=== Count ==="
logseq-cli get-todos --status TODO --status DOING --json "$@" | python3 -c "
import sys, json
data = json.load(sys.stdin)
tasks = data['todos']
print(f'Total: {len(tasks)} open tasks')
pages = {}
for t in tasks:
    p = t.get('page', 'unknown')
    pages[p] = pages.get(p, 0) + 1
for p, c in sorted(pages.items(), key=lambda x: -x[1])[:10]:
    print(f'  {c:3d}  {p}')
"
