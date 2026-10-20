#!/usr/bin/env bash
# Replace one block's text only if the block is still the one you read
#
# Reads the block with --json, takes its `hash`, and passes it back as
# --expect-hash. If the block changed in between (you typed in it, another
# tool wrote it), the write is refused with `precondition_failed` and nothing
# is written. The hash sees the block's content, not where it stands: a block
# moved since the read still passes.
#
# With a third argument it also sets `prio::` on the block, as a second write
# on the hash the first one returned: no read in between. The hash says what
# the first write left; if anyone changes the block after it, the second
# write is refused the same way.
#
# On a refusal this script stops. It never reads the block again and repeats
# the same text: the new text was decided on the old content, and a fresh hash
# would write that decision over the change that was made. Read the block
# again (get-block --json), decide again, then run the script again.
#
# Usage: ./safe-update-block.sh BLOCK_UUID "new text" [PRIO]
#        ./safe-update-block.sh 6650a1b2-0000-4000-8000-000000000001 "TODO ship the parser, fixed" high
# Requires: LOGSEQ_TOKEN or --token, and jq
# Prints the --json output of the last write.
# Exit:  0 written; 3 the block changed since it was read (nothing written by
#        the refused call; with PRIO, the text may already be written);
#        anything else: the call failed for another reason (see stderr)

set -euo pipefail

ID="${1:?Usage: $0 BLOCK_UUID \"new text\" [PRIO]}"
NEW="${2:?Usage: $0 BLOCK_UUID \"new text\" [PRIO]}"
PRIO="${3:-}"

ERR=$(mktemp)
OUT=$(mktemp)
trap 'rm -f "${ERR}" "${OUT}"' EXIT

refused() {
    if [[ "$(jq -r '.reason // empty' "${ERR}" 2>/dev/null)" == "precondition_failed" ]]; then
        echo "The block changed after it was read; nothing was written by this call." >&2
        echo "Read it again with: logseq-cli get-block --id ${ID} --json" >&2
        echo "and decide again on what it says now. Do not repeat this change with a fresh hash." >&2
        exit 3
    fi
    cat "${ERR}" >&2
    exit 1
}

# --no-children: the hash of the block alone is what update-block checks.
HASH=$(logseq-cli get-block --id "${ID}" --no-children --json | jq -er '.hash')

if ! logseq-cli update-block --id "${ID}" --content "${NEW}" \
    --expect-hash "${HASH}" --json >"${OUT}" 2>"${ERR}"; then
    refused
fi
if [[ -z "${PRIO}" ]]; then
    cat "${OUT}"
    exit 0
fi

# The hash the write left. Not `jq -e`: a missing field would end the script
# under set -e; with none, read the block as above.
HASH=$(jq -r '.hash // empty' "${OUT}")
if [[ -z "${HASH}" ]]; then
    HASH=$(logseq-cli get-block --id "${ID}" --no-children --json | jq -er '.hash')
fi

if ! logseq-cli set-block-property --id "${ID}" --key prio --value "${PRIO}" \
    --expect-hash "${HASH}" --json >"${OUT}" 2>"${ERR}"; then
    refused
fi
cat "${OUT}"
