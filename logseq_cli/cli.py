import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib import import_module
from importlib.metadata import version as _pkg_version, PackageNotFoundError
from pathlib import Path

import click
import requests

from logseq_cli.api import LogseqAPI, InvalidPortError
from logseq_cli.config import (
    ConfigError,
    config_search_paths,
    get,
    load_config,
    require,
    resolve_heading,
)
from logseq_cli.datalog import (
    edn_keyword,
    edn_string,
    page_name_literal,
)
from logseq_cli.helpers import (
    parse_repeater,
    next_occurrence,
    escape_regex,
    journal_day_to_date,
    format_journal_date,
    parse_date_keyword,
    parse_date_range,
    process_blocks,
    get_page_content,
    find_backlinks,
    parse_hierarchical_content,
    parse_tree_input,
    read_content_file,
    require_content,
    contains_hierarchical_content,
    reject_unsupported_multiline,
    MultilineContentError,
    has_flush_newline_bullets,
    has_mixed_indentation,
    normalize_indentation,
    find_heading,
    find_or_create_heading,
    PROPERTY_LINE_RE,
    insert_block_tree_with_uuids,
    insert_block_tree_as_siblings,
    insert_block_tree_as_first_children,
    insert_block_tree_at_page_top,
    collect_block_ids,
    invalid_block_ids,
    block_id_property,
    insert_formatted_content_with_uuids,
    block_uuid_from_result,
    require_insert,
    move_block_verified,
    find_blocks_by_content,
    resolve_single_block,
    parse_property_pairs,
    apply_block_properties,
    coerce_property_value,
    uuid_fields,
    normalize_heading,
    extract_page_links,
    extract_topics,
    strip_title_heading,
    is_journal_date,
    count_blocks,
)
from logseq_cli.group import cli, resolve_version
from logseq_cli.commands import journal  # noqa: F401  imported for registration
from logseq_cli.commands import edit  # noqa: F401  imported for registration
from logseq_cli.commands import pages  # noqa: F401  imported for registration
from logseq_cli.commands import analysis  # noqa: F401  imported for registration
from logseq_cli.commands import meta  # noqa: F401  imported for registration
from logseq_cli.commands import query  # noqa: F401  imported for registration
from logseq_cli.commands import properties  # noqa: F401  imported for registration
from logseq_cli.commands import todos  # noqa: F401  imported for registration
from logseq_cli.commands import blocks  # noqa: F401  imported for registration
from logseq_cli.output import fail, handle_connection_error, output
from logseq_cli.render import (
    BLOCK_REF_RE, blocks_to_markdown, blocks_with_ids, count_unresolved_refs,
    extract_backlink_names, extract_section, is_properties_block,
    resolve_refs_in_blocks,
)








# A text replacement must skip property lines: rewriting an id:: line breaks
# every ((block-ref)) to that block, irreversibly. Regex shared via helpers.

# find-block --with-children costs one extra read per match (the datalog pull
# carries no children), so the fan-out is capped and the remainder reported.












# ---------------------------------------------------------------------------
# 1. get-all-pages
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# 2. get-page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. get-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3b. find-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. search-pages
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5. get-backlinks
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. get-journal-summary
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 6b. get-journal-range
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. analyze-graph
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 8. find-knowledge-gaps
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# 9. analyze-journal-patterns
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# 10. smart-query
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 11. suggest-connections
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 12. create-page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 13. add-journal-entry
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 14. add-journal-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 15. add-journal-content
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 16. add-note-content
# ---------------------------------------------------------------------------


# --- Block editing commands ---






# `remove-block` is the canonical name (Logseq's API verb is removeBlock), but
# `delete-page` sits right next to it, so `delete-block` is the single most common
# wrong guess. Register it as an alias so the guess works instead of erroring out.






# ---------------------------------------------------------------------------
# 20b. add-block-ref
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# 21. get-todos
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 21b. set-todo-status
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 22. get-properties
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 23. set-property
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 24. remove-property
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 25. set-block-property
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 26. rename-page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 27. delete-page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 28. query-pages-by-property
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 29. copy-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 29b. move-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 30. get-page-stats
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 31. doctor
# ---------------------------------------------------------------------------
# Logseq's own rule, from deps/db/src/logseq/db/sqlite/util.cljs:
#     (defn db-based-graph? [graph-name]
#       (when graph-name (string/starts-with? graph-name db-version-prefix)))
# with the two prefixes defined in deps/common/src/logseq/common/config.cljs
# as "logseq_db_" and "logseq_local_". Taken from there rather than inferred
# from an observed response, so the rule rests on the definition both kinds are
# built from.
#
# Not used: logseq.App.checkCurrentIsDbGraph. It is exported in 2.x
# (src/main/logseq/api.cljs) and is the direct answer, but 0.10.15 does not
# carry it — it answers `MethodNotExist: check_current_is_db_graph`, checked
# against the running server. The prefix is the one signal both lines share.
#
# Also not used: logseq.App.getInfo().supportDb. It reads like the flag for
# this, and is not: the implementation returns a hardcoded `true`
# (src/main/logseq/api/app.cljs), meaning "this build can open DB graphs",
# not "this graph is one". On 0.10.15 getInfo does not exist at all.
#
# Rejected as signals, measured against a 1845-page graph: `file` is set on
# 962 pages and `format` on 22, so neither separates the two kinds — they
# only look like they would.






















def main():
    cli()


if __name__ == "__main__":
    main()
