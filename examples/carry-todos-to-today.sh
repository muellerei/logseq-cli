#!/usr/bin/env bash
# Carry open tasks older than N days into today's journal, as ((block-refs))
#
# Meant for a morning cron job: no agent, no tokens. Each task is written as
# a ref under one heading, so it can be ticked off on today's page and the
# marker changes on the original, which stays where it was written. A task
# that already stands on the target day, written there or carried there
# before, is left out, so running it twice adds nothing.
#
# Writing a ref gives its source block an id:: line if it has none, the way
# Logseq's editor does; that is the only change to the old tasks. Tasks on
# ordinary pages are not picked up, since only journal days have a date.
#
# Usage: ./carry-todos-to-today.sh [DAYS] [--write]     (default: 7, dry run)
# Env:   HEADING (default "## Carried over"), DATE (target day, default today),
#        MATCH (only tasks whose text matches this regex)
# Requires: LOGSEQ_TOKEN or --token, and jq

set -euo pipefail

DAYS=7
WRITE=""
for arg in "$@"; do
    case "${arg}" in
        --write) WRITE=1 ;;
        *) DAYS="${arg}" ;;
    esac
done
HEADING="${HEADING:-## Carried over}"
DATE="${DATE:-$(date +%Y-%m-%d)}"

# BSD date (macOS) and GNU date disagree on relative dates; try both.
CUTOFF=$(date -v-"${DAYS}"d +%Y-%m-%d 2>/dev/null \
    || date -d "${DAYS} days ago" +%Y-%m-%d)

MATCH_ARGS=()
if [[ -n "${MATCH:-}" ]]; then
    MATCH_ARGS=(--match "${MATCH}")
fi

# Split from the assignment so a failing call stops the script (see
# carried-over-todos.sh). ${arr[@]+...} keeps an empty array safe under set -u
# in bash 3.2, which macOS still ships. The payloads reach jq on stdin rather
# than as arguments, which a large graph could push past the length limit.
OLD=""
OLD=$(logseq-cli get-todos --to "${CUTOFF}" ${MATCH_ARGS[@]+"${MATCH_ARGS[@]}"} --json)
ALREADY=""
ALREADY=$(logseq-cli get-todos --from "${DATE}" --to "${DATE}" \
    ${MATCH_ARGS[@]+"${MATCH_ARGS[@]}"} --json)

UUIDS=()
while IFS= read -r uuid; do
    [[ -n "${uuid}" ]] && UUIDS+=("${uuid}")
done < <(printf '%s\n%s\n' "${ALREADY}" "${OLD}" | jq -rs '
    (.[0].todos | map(.uuid)) as $skip
    | .[1].todos[] | select(.uuid as $u | $skip | index($u) | not) | .uuid')

if [[ ${#UUIDS[@]} -eq 0 ]]; then
    echo "No open task older than ${DAYS} days that is not on ${DATE} already."
    exit 0
fi

CONTENT_ARGS=()
for uuid in "${UUIDS[@]}"; do
    CONTENT_ARGS+=(--content "((${uuid}))")
done

MODE=(--dry-run)
if [[ -n "${WRITE}" ]]; then
    MODE=()
fi

logseq-cli add-journal-block --date "${DATE}" --under-heading "${HEADING}" \
    "${CONTENT_ARGS[@]}" ${MODE[@]+"${MODE[@]}"}

if [[ -z "${WRITE}" ]]; then
    echo "Dry run: ${#UUIDS[@]} task(s). Run again with --write to add them."
fi
