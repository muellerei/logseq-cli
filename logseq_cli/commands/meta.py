import re
import sys
from collections import Counter
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

import click
import requests

from logseq_cli.config import ConfigError, config_search_paths, get, load_config
from logseq_cli.group import cli, resolve_version
from logseq_cli.helpers import normalize_heading
from logseq_cli.output import fail, handle_connection_error, output


_DB_GRAPH_PREFIX = "logseq_db_"

_FILE_GRAPH_PREFIX = "logseq_local_"

def _graph_kind(graph_url):
    """Classify the current graph from its url.

    Returns ``(kind, ok, detail)`` where kind is ``"db"``, ``"file"`` or
    ``None``. An unrecognised or absent url yields ``ok=None``: a wrong
    "file graph, all good" is worse than no answer, because it rules out the
    one cause the reader should be looking at.
    """
    if not isinstance(graph_url, str) or not graph_url:
        return None, None, "could not be determined (no graph url in the API answer)"
    if graph_url.startswith(_DB_GRAPH_PREFIX):
        return "db", False, "Logseq 2.x (DB/SQLite) — not supported by this CLI"
    if graph_url.startswith(_FILE_GRAPH_PREFIX):
        return "file", True, "file-based (Markdown) graph — supported"
    return None, None, f"could not be determined from {graph_url!r}"

def _port_has_listener(host: str, port: str, timeout: float = 2.0) -> bool:
    """True if something accepts TCP connections on host:port."""
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False

def _logseq_process_running() -> "bool | None":
    """True/False if a Logseq desktop process is detectable, None if unknown.

    Best-effort and platform-dependent: used only to tell "app not running" from
    "app running but its HTTP API is off", which is the distinction that costs
    the most time to work out by hand.
    """
    import shutil
    import subprocess
    if not shutil.which("pgrep"):
        return None
    try:
        for pattern in ("Logseq", "logseq"):
            res = subprocess.run(["pgrep", "-x", pattern],
                                 capture_output=True, timeout=5)
            if res.returncode == 0:
                return True
        return False
    except (OSError, subprocess.SubprocessError):
        return None

@cli.command("init", epilog="""\b
Examples:
  logseq-cli --token TOKEN init --dry-run
  logseq-cli --token TOKEN init
  logseq-cli --token TOKEN init --output ./config.toml --force
Note:
  Reads the graph, never writes to it. Suggestions are counted, not guessed:
  each one comes with how many of the recent journals actually use it, so a
  section you abandoned years ago does not end up in your config.
""")
@click.option("--output", "out_path", default=None,
              help="Where to write (default: the first config search path)")
@click.option("--days", default=120, show_default=True, type=int,
              help="How many of the most recent journals to look at (1 or greater)")
@click.option("--force", is_flag=True, help="Overwrite an existing config file")
@click.option("--dry-run", "dry_run", is_flag=True, help="Print what would be written")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def init_config(ctx, out_path, days, force, dry_run, as_json):
    """Suggest a config file from what your graph actually contains."""
    api = ctx.obj["api"]

    # Sliced off the front of the journals, so a negative value drops the
    # oldest one instead of limiting the sample: the suggestion would rest on
    # a quietly different set of journals than the one asked for. 0 is refused
    # rather than allowed, because looking at no journals still writes a config
    # - one built on no evidence, under the message "No journals found - is the
    # right graph open?", which blames the graph for what the flag did.
    if days < 1:
        fail("--days must be 1 or greater.", as_json)

    target = Path(out_path).expanduser() if out_path else config_search_paths()[0]
    if target.exists() and not (force or dry_run):
        fail(f"{target} already exists. Pass --force to overwrite it, "
             "or --dry-run to see what would be written.",
             as_json=as_json, reason="config_exists")

    pages = api.get_all_pages() or []
    journals = [p for p in pages
                if p.get("journalDay") or p.get("journal-day") or p.get("journal?")]
    # Most recent first: a section abandoned years ago must not outvote the one
    # in use now, which counting the whole history would let it do.
    journals.sort(key=lambda p: p.get("journalDay") or p.get("journal-day") or 0,
                  reverse=True)
    journals = journals[:days]

    heading_counts = Counter()
    for page in journals:
        name = page.get("originalName") or page.get("original-name") or page.get("name")
        if not name:
            continue
        seen = set()
        for block in _walk_blocks(api.get_page_blocks_tree(name) or []):
            text = (block.get("content") or "").strip()
            if text.startswith("#"):
                seen.add(normalize_heading(text))
        heading_counts.update(seen)

    namespaces = Counter()
    prop_values = Counter()
    for page in pages:
        name = (page.get("originalName") or page.get("original-name")
                or page.get("name") or "")
        if "/" in name:
            namespaces[name.split("/", 1)[0] + "/"] += 1
        props = page.get("properties") or {}
        if isinstance(props, dict):
            for value in _as_list(props.get("type")):
                prop_values[str(value)] += 1

    total = len(journals)
    suggestions = {
        "journals_examined": total,
        "headings": heading_counts.most_common(8),
        "namespaces": namespaces.most_common(5),
        "person_values": prop_values.most_common(5),
    }
    toml_text = _render_config(heading_counts, namespaces, prop_values, total)

    if as_json:
        output({"target": str(target), "written": False if dry_run else None,
                "suggestions": suggestions, "config": toml_text}, True)
        if dry_run:
            return
    else:
        click.echo(f"Looked at {total} journal page(s).")
        if not total:
            click.echo("  No journals found — is the right graph open?")
        for heading, count in heading_counts.most_common(8):
            click.echo(f"  {count:4}/{total}  {heading}")
        for ns, count in namespaces.most_common(5):
            click.echo(f"  {count:4} pages under  {ns}")
        for value, count in prop_values.most_common(5):
            click.echo(f"  {count:4} pages with   type:: {value}")
        click.echo()

    if dry_run:
        if not as_json:
            click.echo(f"[DRY RUN] Would write {target}:\n")
            click.echo(toml_text)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(toml_text, encoding="utf-8")
    if not as_json:
        click.echo(f"Wrote {target}")
        click.echo("Review it: these are counts from your graph, not certainties.")

def _walk_blocks(blocks):
    """Yield every block in a tree, depth first."""
    for block in blocks:
        yield block
        yield from _walk_blocks(block.get("children") or [])

def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]

def _slug(heading: str) -> str:
    """A short name for a heading, usable as a TOML key."""
    text = re.sub(r"^#+\s*", "", heading)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()
    return text or "section"

def _tie_note(counts, what: str) -> list:
    """Name the runners-up when the count cannot separate them.

    `Counter.most_common` breaks a tie by insertion order, so whichever page
    the API happened to return first would decide — and the comment written
    next to the winner ("10 pages live under this prefix") reads as evidence
    while hiding that something else scored exactly the same. In the graph
    this was found in, two namespaces had ten pages each and the wrong one
    was picked, after which `smart-query` returned ten confident non-results.

    Returns comment lines, or nothing when there is a clear winner.
    """
    ranked = counts.most_common()
    if not ranked:
        return []
    top_count = ranked[0][1]
    rivals = [name for name, count in ranked[1:] if count == top_count]
    if not rivals:
        return []
    return [f"# just as common, and possibly the {what} you want: "
            + ", ".join(str(r) for r in rivals),
            "# counting cannot tell them apart — pick the right one yourself"]

def _render_config(headings, namespaces, prop_values, total) -> str:
    """Build the config text, commenting out anything that is a guess."""
    lines = [
        "# Written by `logseq-cli init` from the graph it found.",
        "# The counts say how many of the recent journals use each heading;",
        "# check them, they are evidence rather than certainty.",
        "",
        "[journal]",
    ]
    ranked = headings.most_common(8)
    # Several sections can appear in every journal, and then the count alone
    # does not say which one prose goes under. Prefer a plain top-level
    # heading: one that is not a link to a page ("## [[Meeting]]" collects
    # meetings) and not a sub-heading, which is where notes usually live.
    def _is_plain_top_level(h: str) -> bool:
        return h.startswith("## ") and not h.startswith("### ") and "[[" not in h

    default_pick = next(
        ((h, c) for h, c in ranked if _is_plain_top_level(h)),
        ranked[0] if ranked else None,
    )
    if default_pick:
        top, count = default_pick
        lines.append(f'# in {count} of {total} journals')
        lines += _tie_note(
            Counter({h: c for h, c in ranked if _is_plain_top_level(h)}),
            "section")
        lines.append(f'default_heading = "{top}"')
    else:
        lines.append('# No headings found; journal writes go in at top level.')
        lines.append('# default_heading = "## Log"')

    lines += ["", "[journal.headings]",
              "# The key is yours to choose; the value must match the graph exactly."]
    used = set()
    for heading, count in ranked:
        key = _slug(heading)
        while key in used:
            key += "_"
        used.add(key)
        lines.append(f'{key} = "{heading}"  # {count}/{total}')

    lines += ["", "[graph]"]
    if namespaces:
        ns, count = namespaces.most_common(1)[0]
        lines.append(f"# {count} pages live under this prefix")
        lines += _tie_note(namespaces, "namespace")
        lines.append(f'projects_namespace = "{ns}"')
    else:
        lines.append("# No namespaced pages found. Without this setting,")
        lines.append('# `smart-query --request "projects"` reports it as missing.')
        lines.append('# projects_namespace = "projects/"')

    if prop_values:
        value, count = prop_values.most_common(1)[0]
        lines.append(f"# {count} pages carry type:: {value}")
        lines += _tie_note(prop_values, "type:: value")
        lines.append('person_property = "type"')
        lines.append(f'person_value = "{value}"')
    else:
        lines.append("# No type:: properties found.")
        lines.append('# person_property = "type"')
        lines.append('# person_value = "Person"')

    lines += [
        "",
        "# [analysis] is not guessed: which words carry mood in your journal is",
        "# not something a count can tell. The defaults are English; see",
        "# docs/configuration.md and config.example.toml.",
        "",
    ]
    return "\n".join(lines)

@cli.command("doctor", epilog="""\b
Examples:
  logseq-cli --token TOKEN doctor
  logseq-cli --token TOKEN doctor --json
Note:
  Read-only. Exit 0 = ready to read and write, 1 = something is wrong.
  Distinguishes "Logseq not running" from "running but HTTP API off" and
  from "API up but token rejected" - each needs a different fix.
""")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def doctor(ctx, as_json):
    """Check connectivity, auth and graph access in one call."""
    api = ctx.obj["api"]
    checks = []
    remedy = None

    def add(name, ok, detail):
        checks.append({"check": name, "ok": ok, "detail": detail})

    # 0. The runtime itself. Everything below assumes the CLI is installed
    #    correctly; when it is not, the failure surfaces later as something
    #    unrelated (an ImportError mid-command, a config that never loads).
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 10)
    add("python", py_ok,
        py + ("" if py_ok else "  (3.10 or newer required)"))

    missing = []
    versions = []
    for mod, label in (("click", "click"), ("requests", "requests")):
        try:
            import_module(mod)
        except Exception:  # noqa: BLE001 - any import failure means "not usable"
            missing.append(label)
            continue
        # Ask the installed metadata rather than the module: click deprecated
        # its __version__ attribute and drops it in 9.1, and a doctor that
        # warns about the library it is checking is not much of a doctor.
        try:
            versions.append(f"{label} {_pkg_version(mod)}")
        except PackageNotFoundError:  # pragma: no cover - importable but no dist
            versions.append(label)
    # The TOML parser is stdlib from 3.11 and the tomli backport before that;
    # either is fine, only having neither is a problem, and only for configs.
    try:
        import_module("tomllib")
        versions.append("tomllib (stdlib)")
    except ModuleNotFoundError:
        try:
            versions.append(f"tomli {import_module('tomli').__version__}")
        except Exception:  # noqa: BLE001
            missing.append("tomli (needed on Python 3.10 to read a config file)")
    add("packages", not missing,
        ", ".join(versions) if not missing else "missing: " + ", ".join(missing))
    if missing:
        remedy = remedy or 'Reinstall the package: pip install -e ".[dev]"'

    # 1. Is anything listening? Separates "app closed" from "API disabled",
    #    the exact ambiguity that turned a real outage into a manual hunt.
    listener = _port_has_listener(api.host, api.port)
    add("port", listener,
        f"{api.host}:{api.port} " + ("accepting connections" if listener else "no listener"))

    if not listener:
        proc = _logseq_process_running()
        if proc is True:
            add("process", False,
                "Logseq is running but nothing listens on the API port")
            remedy = ("Logseq runs, but its HTTP API is off or bound elsewhere. "
                      "Enable it in Logseq: Settings -> Features -> HTTP APIs Server, "
                      "then start the server and confirm the port.")
        elif proc is False:
            add("process", False, "no Logseq process found")
            remedy = "Logseq is not running. Start it, then enable the HTTP API server."
        else:
            add("process", None, "process state unknown (pgrep unavailable)")
            remedy = (f"Nothing listens on {api.host}:{api.port}. Check that Logseq runs "
                      "and its HTTP API server is enabled.")

    # 2. Token: only meaningful once the port answers.
    token_set = bool(api.token)
    if listener:
        add("token", token_set,
            "token provided" if token_set else "no token (--token or LOGSEQ_TOKEN)")

    # 3. Live API call. This is what actually proves usability.
    graph = None
    if listener:
        try:
            configs = api.call("logseq.App.getUserConfigs")
            add("api", True, "API responded")
            if isinstance(configs, dict):
                graph = configs.get("currentGraph") or configs.get("preferredWorkflow")
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            add("api", False, f"HTTP {code}")
            if code == 401:
                # Distinguish "none supplied" from "supplied but wrong": the
                # first is a missing flag, the second a wrong value.
                remedy = (
                    "No token was supplied. Pass the value from Logseq's API "
                    "settings via --token or the LOGSEQ_TOKEN env var."
                    if not token_set else
                    "The API rejected the token. Check that it matches the value "
                    "in Logseq: Settings -> Features -> HTTP APIs Server."
                )
            else:
                remedy = f"API answered HTTP {code}. Check the Logseq API settings."
        except requests.RequestException as e:
            add("api", False, f"{type(e).__name__}: {e}")
            remedy = "Port is open but the API did not answer. Is another service on that port?"
        except Exception as e:  # noqa: BLE001 - doctor must never crash
            add("api", False, f"{type(e).__name__}: {e}")
            remedy = "Unexpected error talking to the API."

    # 3b. Graph kind. A 2.x (DB) graph answers this same API, so reachability
    # proves nothing about whether the reads below will mean anything: it keeps
    # a different data model, and the fields these commands ask for are simply
    # absent. That surfaces as empty names and empty lists — the exact shape an
    # empty graph has, which sends people looking at their own notes for a
    # cause that is one version number away.
    if graph is not None or any(c["check"] == "api" and c["ok"] for c in checks):
        kind, kind_ok, kind_detail = _graph_kind(graph)
        add("graph kind", kind_ok, kind_detail)
        if kind == "db":
            remedy = (
                "This is a Logseq 2.x (DB) graph, which this CLI does not "
                "support: it stores the graph in SQLite under a different data "
                "model, so reads return nothing rather than failing. Use a "
                "file-based (Markdown) graph on the 0.10.x line."
            )

    # 4. Graph read: proves a graph is actually loaded, not just the API alive.
    if any(c["check"] == "api" and c["ok"] for c in checks):
        try:
            pages = api.get_all_pages()
            count = len(pages) if isinstance(pages, list) else 0
            add("graph", count > 0, f"{count} page(s) visible")
            if count == 0:
                remedy = "API works but no pages are visible. Is a graph open in Logseq?"
        except Exception as e:  # noqa: BLE001
            add("graph", False, f"{type(e).__name__}: {e}")
            remedy = "API works but the graph could not be read."

    # Config last: it says nothing about whether Logseq is reachable, so it is
    # reported with ok=None and cannot turn a working setup into a failed one.
    # Without it most commands are fine; the point is to name the few that are
    # not, before the user hits one and wonders why it found nothing.
    try:
        cfg = load_config()
        configured = [
            key for section, key in (
                ("graph", "projects_namespace"),
                ("graph", "person_property"),
            )
            if get(cfg, section, key)
        ]
        if not cfg:
            add("config", None,
                "no config file; commands that need one will say so "
                "(see docs/configuration.md)")
        elif configured:
            add("config", True, f"{cfg['_path']} ({', '.join(configured)})")
        else:
            add("config", None,
                f"{cfg['_path']} carries no [graph] settings; "
                "smart-query for projects or people will report them missing")
    except ConfigError as e:
        # A broken config is worth failing on: the user meant to configure
        # something and it is not being applied.
        add("config", False, str(e).split("\n")[0])
        remedy = remedy or "Fix the config file, or remove it to run without one."

    healthy = all(c["ok"] for c in checks if c["ok"] is not None)

    result = {
        "healthy": healthy,
        "endpoint": api.base_url,
        "version": resolve_version(),
        "checks": checks,
    }
    if graph:
        result["graph"] = graph
    if remedy:
        result["remedy"] = remedy

    if as_json:
        output(result, True)
    else:
        click.echo(f"logseq-cli {result['version']}  ->  {api.base_url}")
        for c in checks:
            mark = "ok  " if c["ok"] else ("??  " if c["ok"] is None else "FAIL")
            click.echo(f"  [{mark}] {c['check']}: {c['detail']}")
        if graph:
            click.echo(f"  graph: {graph}")
        click.echo()
        if healthy:
            click.echo("Ready: reads and writes should work.")
        else:
            click.echo("Not ready.")
            if remedy:
                click.echo(f"  {remedy}")

    if not healthy:
        sys.exit(1)
