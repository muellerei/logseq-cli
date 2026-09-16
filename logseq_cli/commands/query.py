import datetime

import click

from logseq_cli.config import load_config, require
from logseq_cli.datalog import edn_keyword, edn_string, page_name_literal
from logseq_cli.group import cli
from logseq_cli.output import handle_connection_error, output


def _extract_param_from_request(req_lower, keywords, original_request):
    """Extract a dynamic parameter value from a natural language request.

    Removes matched keywords from the request to isolate the parameter value.
    Example: "links to Alice" with keyword "links to" -> "Alice"
    """
    remaining = original_request.strip()
    remaining_lower = req_lower.strip()
    # Remove matched keywords (longest first to avoid partial removal)
    for kw in sorted(keywords, key=len, reverse=True):
        idx = remaining_lower.find(kw)
        if idx != -1:
            remaining = remaining[:idx] + remaining[idx + len(kw):]
            remaining_lower = remaining_lower[:idx] + remaining_lower[idx + len(kw):]
    result = remaining.strip().strip('"').strip("'").strip()
    return result if result else None

def _print_results(results):
    """Print query results in human-readable format (max 20 items)."""
    if not isinstance(results, list):
        return
    for i, item in enumerate(results[:20]):
        if isinstance(item, list) and len(item) > 0:
            block = item[0]
            if isinstance(block, dict):
                name = block.get("name") or block.get("original-name") or block.get("content", "")[:80]
                click.echo(f"  {i+1}. {name}")
            else:
                click.echo(f"  {i+1}. {block}")
        elif isinstance(item, dict):
            name = item.get("name") or item.get("originalName") or item.get("content", "")[:80]
            click.echo(f"  {i+1}. {name}")
        else:
            click.echo(f"  {i+1}. {item}")

@cli.command("smart-query", epilog="""\b
Examples:
  logseq-cli --token TOKEN smart-query --request "offene aufgaben"
  logseq-cli --token TOKEN smart-query --request '[:find ?n :where [?p :block/name ?n]]' --advanced
Note:
  Without --advanced: keyword-template match (fragile for complex queries).
  With --advanced: raw Datalog passes through untouched.
""")
@click.option("--request", required=True, help="Natural language query request (or raw Datalog with --advanced)")
@click.option("--include-query", is_flag=True, help="Include the generated Datalog query in output")
@click.option("--advanced", is_flag=True, help="Pass --request as raw Datalog query (bypass template matching)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def smart_query(ctx, request, include_query, advanced, as_json):
    """Run smart Datalog queries via pattern matching on request keywords.

    Use --advanced to pass a raw Datalog query string directly via --request,
    bypassing all template matching. Without --advanced, natural language in
    --request is matched against pre-built query templates.
    """
    api = ctx.obj["api"]
    req_lower = request.lower()

    # --advanced mode: pass raw Datalog query directly to datascript_query.
    # This is the one place that does NOT go through the datalog build layer,
    # and that is correct: --advanced is the documented raw pass-through for
    # arbitrary Datalog. Do not "fix" it to route through edn_string.
    if advanced:
        query_str = request
        description = "Advanced (raw Datalog query)"
        # A rejected query raises DatalogQueryError, caught by the decorator: a
        # query that never ran must exit non-zero, not report an empty result.
        results = api.datascript_query(query_str)

        result_data = {
            "request": request,
            "matched_template": "advanced",
            "description": description,
            "results_count": len(results) if isinstance(results, list) else 0,
            "results": results,
        }
        if include_query:
            result_data["query"] = query_str

        if as_json:
            output(result_data, True)
        else:
            click.echo(f"Query: {description}")
            if include_query:
                click.echo(f"Datalog: {query_str}")
            click.echo(f"Results: {result_data['results_count']}\n")
            _print_results(results)
        return

    # Pre-built query templates
    query_templates = {
        "recent": {
            "keywords": ["recent", "latest", "new", "last modified", "updated", "kürzlich", "zuletzt", "letzte", "neueste"],
            "query": '[:find (pull ?p [*]) :where [?p :block/name] [?p :block/updated-at ?u] [(> ?u {timestamp})]]',
            "description": "Recently modified pages",
        },
        "referenced": {
            "keywords": ["most referenced", "popular", "top pages", "most linked"],
            "query": '[:find ?name (count ?b) :where [?b :block/content ?c] [?p :block/name ?name] [(clojure.string/includes? ?c ?name)]]',
            "description": "Most referenced pages",
        },
        "tasks": {
            "keywords": ["todo", "task", "tasks", "incomplete", "pending", "aufgaben", "offene", "offen"],
            "query": '[:find (pull ?b [*]) :where [?b :block/marker ?m] [(contains? #{"TODO" "LATER" "NOW" "DOING"} ?m)]]',
            "description": "Open tasks",
        },
        "done": {
            "keywords": ["done", "completed", "finished", "erledigt", "fertig", "abgeschlossen"],
            "query": '[:find (pull ?b [*]) :where [?b :block/marker "DONE"]]',
            "description": "Completed tasks",
        },
        "journal": {
            "keywords": ["journal", "diary", "daily", "tagebuch"],
            "query": '[:find (pull ?p [*]) :where [?p :block/journal? true]]',
            "description": "Journal pages",
        },
        "properties": {
            "keywords": ["property", "properties", "type"],
            "query": '[:find (pull ?b [*]) :where [?b :block/properties ?p] [(not-empty ?p)]]',
            "description": "Blocks with properties",
        },
        "scheduled": {
            "keywords": ["scheduled", "deadline", "due", "geplant", "fällig", "termin"],
            "query": '[:find (pull ?b [*]) :where (or [?b :block/scheduled ?d] [?b :block/deadline ?d])]',
            "description": "Scheduled/deadline blocks",
        },
        "empty": {
            "keywords": ["empty", "blank", "no content", "leer", "ohne inhalt"],
            "query": '[:find (pull ?p [*]) :where [?p :block/name ?n] (not [?b :block/page ?p] [?b :block/content ?c] [(not= ?c "")])]',
            "description": "Empty pages",
        },
        "links-to": {
            "keywords": ["links to", "references", "mentions", "verlinkt", "referenziert"],
            "query": (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/refs ?target] [?target :block/name {page_name}]]'
            ),
            "description": "Blocks linking to {page_name}",
            "extract_param": "page_name",
        },
        "created-today": {
            "keywords": ["created today", "today", "heute erstellt"],
            "query": (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/created-at ?c] [(> ?c {today_start})]]'
            ),
            "description": "Blocks created today",
        },
        "tagged": {
            "keywords": ["tagged", "tag", "hashtag", "getaggt", "markiert"],
            "query": (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/content ?c] [(clojure.string/includes? ?c {tag_name})]]'
            ),
            "description": "Blocks tagged with #{tag_name}",
            "extract_param": "tag_name",
        },
        "long-content": {
            "keywords": ["long", "detailed", "ausfuehrlich", "ausführlich"],
            "query": (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/content ?c] [(count ?c) ?len] [(> ?len 300)]]'
            ),
            "description": "Blocks with long content (>300 chars)",
        },
        "persons": {
            "keywords": ["person", "persons", "people", "personen", "kollegen"],
            # Built from config: which property marks a person page is the
            # user's own convention, not something the CLI can know.
            "query": None,
            "needs_config": ("graph", "person_property"),
            "description": "All person pages",
        },
        "projects": {
            "keywords": ["project", "projects", "projekte"],
            # Built from config: the namespace that marks project pages differs
            # per graph, so there is no default to fall back on.
            "query": None,
            "needs_config": ("graph", "projects_namespace"),
            "description": "All project pages",
        },
    }

    # Match query template
    best_match = None
    best_score = 0

    for key, template in query_templates.items():
        score = sum(1 for kw in template["keywords"] if kw in req_lower)
        if score > best_score:
            best_score = score
            best_match = key

    if not best_match:
        # Default: content search across all blocks, then fall back to page name search
        best_match = "content-search"

    if best_match == "content-search":
        # Improved fallback: search block content, then page names as last resort
        search_term = request.strip()
        content_query = (
            '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
            ' :where [?b :block/content ?c]'
            f' [(clojure.string/includes? ?c {edn_string(search_term)})]]'
        )
        # A real error (connection down, rejected query) must surface via the
        # decorator, not be turned into a page-name search: that would answer a
        # different question with exit 0. The fallback is for the fachliche
        # case only, no content hits, so it keys off an empty result.
        results = api.datascript_query(content_query)
        if results:
            query_used = content_query
            description = f"Content search for '{search_term}'"
        else:
            # No content hits: try page names as a last resort.
            pages = api.get_all_pages()
            results = [
                p for p in pages
                if req_lower in (p.get("name") or "").lower()
            ]
            query_used = f"(page name search for '{request}')"
            description = "Page name search (no content match)"
    else:
        template = query_templates[best_match]
        query_str = template["query"]

        # Templates that describe the user's own graph carry no query of their
        # own: it is built here from config. A missing setting raises and exits
        # non-zero rather than querying for a guessed namespace, which would
        # return an empty list that looks exactly like "no projects".
        if query_str is None:
            section, key = template["needs_config"]
            cfg = load_config()
            if key == "projects_namespace":
                prefix = require(cfg, section, key,
                                 f"smart-query --request {request!r}")
                query_str = (
                    '[:find (pull ?p [*]) :where [?p :block/name ?n] '
                    f'[(clojure.string/starts-with? ?n {edn_string(str(prefix).lower())})]]'
                )
            else:
                prop = require(cfg, section, key,
                               f"smart-query --request {request!r}")
                value = require(cfg, section, "person_value",
                                f"smart-query --request {request!r}")
                # Same shape as query-pages-by-property: the value may be
                # stored as a scalar or inside a collection. Not a live defect
                # while person_property points at a scalar-valued key, but it
                # is configurable - aim it at one Logseq stores as a list and
                # the query would quietly return too few.
                literal = edn_string(str(value))
                query_str = (
                    '[:find (pull ?p [*]) :where [?p :block/name] '
                    '[?p :block/properties ?props] '
                    f'[(get ?props :{edn_keyword(str(prop))}) ?t] '
                    f'(or [(= ?t {literal})] [(contains? ?t {literal})])]'
                )

        # Handle timestamp placeholder
        if "{timestamp}" in query_str:
            ts = int((datetime.datetime.now() - datetime.timedelta(days=7)).timestamp() * 1000)
            query_str = query_str.replace("{timestamp}", str(ts))

        # Handle today_start placeholder
        if "{today_start}" in query_str:
            today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            ts = int(today.timestamp() * 1000)
            query_str = query_str.replace("{today_start}", str(ts))

        # Handle dynamic parameter extraction (page_name, tag_name)
        extract_param = template.get("extract_param")
        if extract_param and f"{{{extract_param}}}" in query_str:
            param_value = _extract_param_from_request(req_lower, template["keywords"], request)
            if not param_value:
                param_value = request.strip()
            # Each template's placeholder needs the build function that matches
            # its query position, and the two known ones differ on purpose:
            # links-to queries :block/name (stored lowercased), tagged queries
            # :block/content (user spelling). Sending links-to through
            # edn_string would keep the case bug that lost 1569 backlinks.
            if best_match == "links-to":
                literal = page_name_literal(param_value)
            elif best_match == "tagged":
                literal = edn_string("#" + param_value)
            else:
                literal = edn_string(param_value)
            query_str = query_str.replace(f"{{{extract_param}}}", literal)
            description = template["description"].replace(f"{{{extract_param}}}", param_value)
        else:
            description = template["description"]

        # A rejected query raises DatalogQueryError (caught by the decorator):
        # a query that never ran must fail loud, not report zero hits.
        results = api.datascript_query(query_str)
        query_used = query_str

    result_data = {
        "request": request,
        "matched_template": best_match,
        "description": description,
        "results_count": len(results) if isinstance(results, list) else 0,
        "results": results,
    }
    if include_query:
        result_data["query"] = query_used

    if as_json:
        output(result_data, True)
    else:
        click.echo(f"Query: {description}")
        if include_query:
            click.echo(f"Datalog: {query_used}")
        click.echo(f"Results: {result_data['results_count']}\n")
        _print_results(results)
