#!/usr/bin/env bash
# Tasks standing in the last N days, longest-carried first
#
# A task carried forward by ((block-ref)) appears in each journal it was pulled
# into, so `references` counts how many days it has been taken along and
# `references_withheld` how many of those fall outside the window asked about.
# Their sum answers "how long have I been moving this?", which the page a task
# lives on cannot: that only says when it was first written down.
#
# Usage: ./carried-over-todos.sh [DAYS]        (default: 14)
# Requires: LOGSEQ_TOKEN or --token, and jq

set -euo pipefail

DAYS="${1:-14}"
# BSD date (macOS) and GNU date disagree on relative dates; try both.
FROM=$(date -v-"${DAYS}"d +%Y-%m-%d 2>/dev/null \
    || date -d "${DAYS} days ago" +%Y-%m-%d)
TO=$(date +%Y-%m-%d)

# One read, reused below. --refs-limit 0 lifts the per-task cap so every
# occurrence inside the window is counted; it does not widen the window, so
# days before ${FROM} stay in references_withheld — which is what makes the
# total meaningful rather than just the visible part.
# Declared before assignment on purpose: `local`/`export` on the same line as
# a command substitution swallows its exit status, and so does a bare
# assignment under `set -e` in some shells. Split, the failure propagates and
# the script stops instead of reporting an empty week.
PAYLOAD=""
PAYLOAD=$(logseq-cli get-todos --from "${FROM}" --to "${TO}" --refs-limit 0 --json)

echo "=== Tasks standing between ${FROM} and ${TO} ==="
echo

echo "${PAYLOAD}" | jq -r '
  .todos
  | map(. + {
      days_seen: ((.references // []) | length),
      days_outside: (.references_withheld // 0)
    })
  | sort_by(-(.days_seen + .days_outside), .content)
  | .[]
  | "\(.marker) \(.content | split("\n")[0] | .[0:60])\n" +
    "    first written: \(.page)\n" +
    (if (.days_seen + .days_outside) == 0
     then "    not carried — written on the day it stands\n"
     else "    carried into \(.days_seen + .days_outside) journal(s), " +
          "\(.days_seen) in this window\n"
     end)
'

echo "${PAYLOAD}" | jq -r '
  (.todos | length) as $total
  | (.todos | map(select(((.references // []) | length)
                         + (.references_withheld // 0) > 0)) | length) as $carried
  | "=== \($total) task(s) stood in this window, \($carried) carried over from "
    + "earlier days ==="
'
