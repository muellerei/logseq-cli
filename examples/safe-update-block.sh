#!/usr/bin/env bash
# Replace one block's text only if the block is still the one you read
#
# Reads the block with --json, takes its `hash`, and passes it back as
# --expect-hash. If the block changed in between (you typed in it, another
# tool wrote it), the write is refused with `precondition_failed` and nothing
# is written. The hash sees the block's content, not where it stands: a block
# moved since the read still passes.
#
# On a refusal this script stops. It never reads the block again and repeats
# the same text: the new text was decided on the old content, and a fresh hash
# would write that decision over the change that was made. Read the block
# again (get-block --json), decide again, then run the script again.
#
# Usage: ./safe-update-block.sh BLOCK_UUID "new text"
#        ./safe-update-block.sh 6650a1b2-0000-4000-8000-000000000001 "TODO ship the parser, fixed"
# Requires: LOGSEQ_TOKEN or --token, and jq
# Exit:  0 written; 3 the block changed since it was read (nothing written);
#        anything else: the call failed for another reason (see stderr)

set -euo pipefail

ID="${1:?Usage: $0 BLOCK_UUID \"new text\"}"
NEW="${2:?Usage: $0 BLOCK_UUID \"new text\"}"

ERR=$(mktemp)
trap 'rm -f "${ERR}"' EXIT

# --no-children: the hash of the block alone is what update-block checks.
HASH=$(logseq-cli get-block --id "${ID}" --no-children --json | jq -er '.hash')

if logseq-cli update-block --id "${ID}" --content "${NEW}" \
    --expect-hash "${HASH}" --json 2>"${ERR}"; then
    exit 0
fi

if [[ "$(jq -r '.reason // empty' "${ERR}" 2>/dev/null)" == "precondition_failed" ]]; then
    echo "The block changed after it was read; nothing was written." >&2
    echo "Read it again with: logseq-cli get-block --id ${ID} --json" >&2
    echo "and decide again on what it says now. Do not repeat this change with a fresh hash." >&2
    exit 3
fi

cat "${ERR}" >&2
exit 1
