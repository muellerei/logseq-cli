import click
import datetime
import re

from logseq_cli.group import cli
from collections import Counter, defaultdict
from logseq_cli.config import get, load_config
from logseq_cli.helpers import (
    extract_topics,
    format_journal_date,
    get_page_content,
    journal_day_to_date,
    parse_date_range,
)
from logseq_cli.output import fail, handle_connection_error, output


def _project_pattern(tag_prefix: str, explicit_tags=None) -> "re.Pattern[str]":
    """Match project mentions, as a tag or as a page link.

    ``#projects/alpha`` and ``[[projects/alpha]]`` name the same project, and a
    graph that namespaces project pages tends to contain both, so matching only
    the tag form undercounts. Group 1 is the project name either way.

    ``explicit_tags`` is for graphs that do not namespace at all: those names
    cannot be inferred from a prefix, so they are listed. They match as a tag
    and as a link for the same reason the namespaced form does — a graph that
    writes ``[[Alpha]]`` in its journals and ``#Alpha`` in passing means the
    same project both times, and counting only one of them undercounts. In the
    journal this was measured against, the flat link outnumbered the flat tag
    by two orders of magnitude, so tags alone would have found nothing.
    """
    prefix = tag_prefix.lstrip("#")
    alts = [
        r"#" + re.escape(prefix) + r"(\S+)",
        r"\[\[" + re.escape(prefix) + r"([^\]]+)\]\]",
    ]
    for raw in explicit_tags or []:
        name = str(raw).strip().lstrip("#")
        if name:
            escaped = re.escape(name)
            alts.append(r"#(" + escaped + r")\b")
            alts.append(r"\[\[(" + escaped + r")\]\]")
    return re.compile("|".join(alts), re.IGNORECASE)

def _word_pattern(words) -> "re.Pattern[str]":
    """Case-insensitive whole-word alternation over a list of words.

    Words come from config, so they are escaped: a user writing "c++" or a
    stray "(" must not turn into a broken or surprising pattern. \\b around a
    word that starts or ends with a non-word character would never match, so
    the boundary is applied per word only where it can bite.
    """
    parts = []
    for raw in words:
        w = str(raw).strip()
        if not w:
            continue
        esc = re.escape(w)
        left = r"\b" if w[0].isalnum() or w[0] == "_" else ""
        right = r"\b" if w[-1].isalnum() or w[-1] == "_" else ""
        parts.append(f"{left}{esc}{right}")
    if not parts:
        # Matches nothing, rather than an empty alternation that matches
        # everywhere and would report every block as a mood hit.
        return re.compile(r"(?!)")
    return re.compile("|".join(parts), re.IGNORECASE)

@cli.command("analyze-graph", epilog="""\b
Example:
  logseq-cli --token TOKEN analyze-graph --days 30
Note:
  Requires Logseq running — no filesystem fallback possible.
""")
@click.option("--days", default=None, type=int, help="Limit to pages modified in last N days (1 or greater)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def analyze_graph(ctx, days, as_json):
    """Analyze the knowledge graph structure."""
    api = ctx.obj["api"]

    # A window, not a cap: a negative value moves the cutoff into the future,
    # so "recently updated" silently empties and the report answers a question
    # nobody asked. 0 puts the cutoff at this moment and is refused for the
    # same reason - it can only ever report pages edited in the future.
    if days is not None and days < 1:
        fail("--days must be 1 or greater.", as_json)

    pages = api.get_all_pages()

    # Open tasks only, and only where Logseq puts a marker: at the start of a
    # block. Matching "todo" anywhere, case-insensitively, counted "Todo-Liste"
    # in prose and the "TODO" inside a DONE block's logbook line, so the number
    # was neither the open tasks nor all of them.
    todo_pattern = re.compile(
        # The bullet may repeat: get_page_content prefixes each block with
        # "- ", so a block that already starts with one arrives as "- - TODO".
        # The checkbox needs its bullet for the same reason the markers need
        # the line anchor: a bare "[ ]" occurs in code snippets, empty
        # markdown links and table cells, none of which are tasks.
        r"(?i:- \[ \])|^(?:\s*-\s*)*(?:TODO|DOING|NOW|LATER|WAITING|IN-PROGRESS)\b",
        re.MULTILINE)
    link_pattern = re.compile(r"\[\[(.*?)\]\]")

    # Days filter: cutoff timestamp in milliseconds
    cutoff_ms = None
    if days is not None:
        cutoff_dt = datetime.datetime.now() - datetime.timedelta(days=days)
        cutoff_ms = int(cutoff_dt.timestamp() * 1000)

    page_names = set()
    journal_count = 0
    total_todos = 0
    reference_count = Counter()
    adjacency = defaultdict(set)
    recently_updated = []

    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        page_names.add(name.lower())
        if page.get("journalDay") or page.get("journal-day") or page.get("journal?"):
            journal_count += 1

    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        updated_at = page.get("updatedAt") or page.get("updated-at") or 0

        # Track recently updated pages when --days is set
        if cutoff_ms and updated_at >= cutoff_ms:
            updated_str = datetime.datetime.fromtimestamp(updated_at / 1000).strftime('%Y-%m-%d %H:%M')
            recently_updated.append({"page": name, "updated": updated_str, "updated_at": updated_at})

        try:
            content = get_page_content(api, name)
        except Exception:
            content = ""

        # count TODOs
        todos = todo_pattern.findall(content)
        total_todos += len(todos)

        # extract links
        links = link_pattern.findall(content)
        for link in links:
            reference_count[link] += 1
            adjacency[name.lower()].add(link.lower())
            adjacency[link.lower()].add(name.lower())

    # Sort recently updated by timestamp descending
    recently_updated.sort(key=lambda x: x["updated_at"], reverse=True)

    # BFS clusters
    visited = set()
    clusters = []

    for node in adjacency:
        if node in visited:
            continue
        cluster = set()
        queue = [node]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            cluster.add(current)
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)
        if len(cluster) > 1:
            clusters.append(sorted(cluster))

    top_referenced = reference_count.most_common(15)

    result = {
        "total_pages": len(pages),
        "journal_pages": journal_count,
        "non_journal_pages": len(pages) - journal_count,
        "total_todos": total_todos,
        "top_referenced": [{"page": p, "refs": c} for p, c in top_referenced],
        "clusters": len(clusters),
        "largest_cluster": len(clusters[0]) if clusters else 0,
    }
    if days is not None:
        result["recently_updated"] = [{"page": r["page"], "updated": r["updated"]} for r in recently_updated[:30]]

    if as_json:
        output(result, True)
    else:
        click.echo("=== Graph Analysis ===\n")
        click.echo(f"Total pages:     {result['total_pages']}")
        click.echo(f"Journal pages:   {result['journal_pages']}")
        click.echo(f"Content pages:   {result['non_journal_pages']}")
        click.echo(f"Open TODOs:      {result['total_todos']}")
        click.echo(f"Clusters:        {result['clusters']}")
        if clusters:
            click.echo(f"Largest cluster: {result['largest_cluster']} pages")
        if days is not None and recently_updated:
            click.echo(f"\nRecently Updated (last {days} days): {len(recently_updated)} pages")
            for r in recently_updated[:30]:
                click.echo(f"  {r['page']} ({r['updated']})")
        click.echo(f"\nTop Referenced Pages:")
        for item in result["top_referenced"]:
            click.echo(f"  {item['page']}: {item['refs']} refs")

def _is_incidental_page(name: str) -> bool:
    """True for pages that exist as a side effect, not as knowledge.

    Logseq turns `#272` in a sentence into a page called "272", and a stray
    bracket or dash into a page of its own. Those are real pages with no
    incoming links, so they answer "orphaned" truthfully and drown the answer:
    in the graph this was measured against, 596 orphans were almost entirely
    of this kind. Dates written in file-name form are the same story from the
    other side — they look like missing pages but are journals under another
    spelling.
    """
    stripped = name.strip()
    if len(stripped) < 3:
        return True
    if not any(c.isalpha() for c in stripped):
        return True
    if re.fullmatch(r"[\W_]+", stripped):
        return True
    # A name opening with punctuation is a tag that swallowed one: "#-AI"
    if not (stripped[0].isalnum() or stripped[0] in "_@"):
        return True
    # 2025_10_10, 2025-10-10, 2025/10/10 — a journal, not a gap
    if re.fullmatch(r"\d{4}[-_/]\d{1,2}[-_/]\d{1,2}", stripped):
        return True
    # An unclosed bracket dragged in from prose: "#Active)", "3b82f6)".
    if stripped.endswith(")") and "(" not in stripped:
        return True
    # A ticket number that took the next word with it: "#272-Designentscheidung"
    # comes from "#272-Designentscheidung" in a sentence. Three digits or more,
    # so that "2-Faktor-Auth" and "4-Level-Struktur" — real terms — survive.
    if re.match(r"\d{3,}-", stripped):
        return True
    return False

def _has_richer_namesake(name_lower: str, page_names: dict, content_of) -> bool:
    """True if some namespaced page shares this name and actually has content.

    An empty `Alpha` with 185 references sits next to
    `projects/Alpha` with 8806 words: the bare page is an anchor for
    the name, not a gap in the notes. Reporting it as underdeveloped sends the
    reader to write something that is already written next door.
    """
    for other_lower, other_original in page_names.items():
        if other_lower == name_lower:
            continue
        tail = other_lower.rsplit("/", 1)[-1]
        if tail != name_lower:
            continue
        try:
            if len((content_of(other_original) or "").strip()) > 200:
                return True
        except Exception:  # noqa: BLE001 - unreadable page proves nothing
            continue
    return False

@cli.command("find-knowledge-gaps", epilog="""\b
Example:
  logseq-cli --token TOKEN find-knowledge-gaps --min-refs 3 --include-orphans
""")
@click.option("--min-refs", default=2, type=int, help="Min references for underdeveloped detection")
@click.option("--include-orphans/--no-orphans", default=True, help="Include orphaned pages")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def find_knowledge_gaps(ctx, min_refs, include_orphans, as_json):
    """Find missing, underdeveloped, and orphaned pages."""
    api = ctx.obj["api"]
    pages = api.get_all_pages()

    link_pattern = re.compile(r"\[\[(.*?)\]\]")
    page_names = {}  # lowercase -> original name
    incoming_refs = Counter()
    page_content_lengths = {}

    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        page_names[name.lower()] = name

    # Pass 1: collect all references and content lengths
    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        try:
            content = get_page_content(api, name)
        except Exception:
            content = ""
        page_content_lengths[name.lower()] = len(content)
        links = link_pattern.findall(content)
        for link in links:
            incoming_refs[link.lower()] += 1

    # Missing pages: referenced but don't exist
    missing = []
    for ref_lower, count in incoming_refs.items():
        if ref_lower in page_names:
            continue
        if _is_incidental_page(ref_lower):
            continue
        missing.append({"page": ref_lower, "references": count})
    missing.sort(key=lambda x: x["references"], reverse=True)

    # Underdeveloped: exists, has refs, but very short content
    underdeveloped = []
    for name_lower, original in page_names.items():
        refs = incoming_refs.get(name_lower, 0)
        length = page_content_lengths.get(name_lower, 0)
        if refs >= min_refs and length < 100:
            if _is_incidental_page(original):
                continue
            # An empty page whose namespaced twin is written is an anchor for
            # the name, not a gap. Only checked here, where the list is short.
            if _has_richer_namesake(name_lower, page_names,
                                    lambda n: get_page_content(api, n)):
                continue
            underdeveloped.append({
                "page": original,
                "references": refs,
                "content_length": length,
            })
    underdeveloped.sort(key=lambda x: x["references"], reverse=True)

    # Orphaned: zero incoming references (exclude journals)
    orphans = []
    if include_orphans:
        journal_pages = set()
        for page in pages:
            if page.get("journalDay") or page.get("journal-day") or page.get("journal?"):
                name = page.get("originalName") or page.get("name", "")
                journal_pages.add(name.lower())

        for name_lower, original in page_names.items():
            if name_lower in journal_pages:
                continue
            if _is_incidental_page(original):
                continue
            if incoming_refs.get(name_lower, 0) == 0:
                orphans.append(original)
        orphans.sort(key=str.lower)

    result = {
        "missing_pages": missing[:20],
        "underdeveloped_pages": underdeveloped[:20],
        "orphaned_pages": orphans[:30] if include_orphans else [],
        "summary": {
            "missing": len(missing),
            "underdeveloped": len(underdeveloped),
            "orphaned": len(orphans) if include_orphans else "n/a",
        },
    }

    if as_json:
        output(result, True)
    else:
        click.echo("=== Knowledge Gaps ===\n")
        click.echo(f"Missing pages (referenced but don't exist): {len(missing)}")
        for m in result["missing_pages"]:
            click.echo(f"  {m['page']} ({m['references']} refs)")

        click.echo(f"\nUnderdeveloped pages (< 100 chars, {min_refs}+ refs): {len(underdeveloped)}")
        for u in result["underdeveloped_pages"]:
            click.echo(f"  {u['page']} ({u['references']} refs, {u['content_length']} chars)")

        if include_orphans:
            click.echo(f"\nOrphaned pages (zero incoming links): {len(orphans)}")
            for o in result["orphaned_pages"]:
                click.echo(f"  {o}")

@cli.command("analyze-journal-patterns", epilog="""\b
Example:
  logseq-cli --token TOKEN analyze-journal-patterns --timeframe "last 30 days" --mood --topics
""")
@click.option("--timeframe", default="last 30 days", help="Date range for analysis")
@click.option("--mood/--no-mood", default=True, help="Include mood detection")
@click.option("--topics/--no-topics", default=True, help="Include topic analysis")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def analyze_journal_patterns(ctx, timeframe, mood, topics, as_json):
    """Analyze patterns in journal entries."""
    api = ctx.obj["api"]
    start, end = parse_date_range(timeframe)
    pages = api.get_all_pages()

    # Word lists and the project tag are language- and graph-specific: the
    # built-ins are English, so a journal written in another language scores
    # zero moods and no project progress, silently. [analysis] in the config
    # replaces them; see docs/configuration.md.
    cfg = load_config()
    mood_positive = _word_pattern(
        get(cfg, "analysis", "mood_positive")
        or ["happy", "great", "excited", "good", "wonderful", "productive", "grateful"])
    mood_negative = _word_pattern(
        get(cfg, "analysis", "mood_negative")
        or ["sad", "tired", "stressed", "frustrated", "anxious", "overwhelmed", "bad"])
    mood_labels = get(cfg, "analysis", "mood_labels") or ["mood", "feeling"]
    mood_keyword = re.compile(
        r"(?:" + "|".join(re.escape(str(w)) for w in mood_labels) + r"):\s*(\w+)",
        re.IGNORECASE)
    # Lines worth showing as evidence under the mood counts. Kept to explicit
    # statements and emoji for the same reason the counting is: a bare "happy"
    # in a sentence is as likely to be "not happy", and listing it under a
    # count of zero reads as a contradiction. The labels come from config, so
    # a graph writing "stimmung:" is covered.
    mood_indicator_patterns = [f"{label}:" for label in mood_labels] + [
        "\U0001f60a", "\U0001f614", "\U0001f620", "\U0001f60c",
    ]
    # Two ways of writing a task. Logseq's own are the markers (TODO, DOING,
    # DONE...); the markdown checkbox is what people paste in from elsewhere.
    # Counting only the checkbox reported "0 complete, 0 incomplete" for a
    # graph with over a thousand tasks — a number that reads like a
    # measurement rather than a pattern that cannot match.
    # The markers are upper-case in Logseq and only there, so they are matched
    # case-sensitively: "Now that we finished" and "Later kam die Rückmeldung"
    # open a sentence, not a task. The checkbox alternative keeps (?i), where
    # "[X]" and "[x]" are both in the wild.
    # The bullet may repeat: get_page_content prefixes each block with "- ",
    # so a block already starting with one arrives as "- - TODO ...".
    incomplete_task = re.compile(
        r"(?i:- \[ \])|^(?:\s*-\s*)*(?:TODO|DOING|NOW|LATER|WAITING|IN-PROGRESS)\b",
        re.MULTILINE)
    complete_task = re.compile(
        r"(?i:- \[x\])|^(?:\s*-\s*)*(?:DONE|CANCELED|CANCELLED)\b",
        re.MULTILINE)
    link_pattern = re.compile(r"\[\[(.*?)\]\]")
    # Projects get named in more than one way. A namespace prefix covers both
    # the tag (#projects/alpha) and the link ([[projects/alpha]]), because a
    # graph that namespaces its project pages usually writes both; graphs that
    # tag flatly (#alpha) configure the tags themselves instead.
    project_tag = get(cfg, "analysis", "project_tag_prefix") or "#project/"
    project_pattern = _project_pattern(
        str(project_tag), get(cfg, "analysis", "project_tags"))
    # Habits stay checkbox-only on purpose: a habit is a repeated checkbox
    # list, and treating every TODO as a habit would drown the real ones.
    habit_checkbox = re.compile(r"- \[[ x]\]", re.IGNORECASE)

    entries = []
    topic_by_date = {}
    mood_entries = []
    total_incomplete = 0
    total_complete = 0

    # Extended analysis collectors
    mood_patterns = []  # {date, mood, context}
    habit_patterns = defaultdict(list)  # habit_name -> [{date, done}]
    project_progress = defaultdict(list)  # project -> [{date, status}]
    topics_by_month = defaultdict(set)  # YYYY-MM -> set of topics

    for page in pages:
        jd = page.get("journalDay") or page.get("journal-day")
        if not jd:
            continue
        try:
            d = journal_day_to_date(jd)
        except (ValueError, TypeError):
            continue

        dt = datetime.datetime.combine(d, datetime.time())
        if not (start <= dt <= end):
            continue

        page_name = page.get("originalName") or page.get("name", "")
        try:
            content = get_page_content(api, page_name)
        except Exception:
            content = ""

        date_str = format_journal_date(d)
        month_key = d.strftime("%Y-%m")
        entry = {"date": date_str, "page": page_name}

        # Topics
        if topics:
            links = link_pattern.findall(content)
            entry["topics"] = links
            topic_by_date[date_str] = links
            topics_by_month[month_key].update(links)

        # Mood, from explicit statements only.
        #
        # Counting every occurrence of a positive word measured how often such
        # words appear in technical prose, not how the day went: "nicht
        # zufrieden" and "läuft nicht gut" both scored as positive, and in the
        # journal this was checked against 16% of positive hits were negations
        # — concentrated in exactly the sentences that carry a judgement. A
        # number that says the opposite of its own evidence is worse than no
        # number, and negation is not something a word list can settle.
        #
        # So only a line that states a mood counts: "mood: good",
        # "stimmung: mies" — the labels are configurable, and the word lists
        # now classify that stated value rather than the whole journal.
        if mood:
            mood_data = {"positive": 0, "negative": 0, "keywords": []}
            kw_matches = mood_keyword.findall(content)
            mood_data["keywords"] = kw_matches
            for stated in kw_matches:
                if mood_positive.search(stated):
                    mood_data["positive"] += 1
                elif mood_negative.search(stated):
                    mood_data["negative"] += 1
            entry["mood"] = mood_data
            if mood_data["positive"] or mood_data["negative"] or kw_matches:
                mood_entries.append(entry)

        # Extended mood patterns - per block
        if mood and content:
            for line in content.split("\n"):
                line_stripped = line.strip().lstrip("- ")
                if not line_stripped:
                    continue
                line_lower = line_stripped.lower()
                for indicator in mood_indicator_patterns:
                    if indicator.lower() in line_lower:
                        mood_patterns.append({
                            "date": date_str,
                            "month": month_key,
                            "mood": indicator,
                            "context": line_stripped[:120],
                        })
                        break

        # Habits / tasks
        inc = len(incomplete_task.findall(content))
        comp = len(complete_task.findall(content))
        total_incomplete += inc
        total_complete += comp
        entry["tasks_incomplete"] = inc
        entry["tasks_complete"] = comp

        # Habit tracking - extract individual checkbox items
        for line in content.split("\n"):
            line_stripped = line.strip()
            if habit_checkbox.search(line_stripped):
                done = "[x]" in line_stripped.lower()
                habit_text = re.sub(r"- \[[ x]\]\s*", "", line_stripped, flags=re.IGNORECASE).strip()
                if habit_text:
                    habit_patterns[habit_text].append({"date": date_str, "done": done})

        # Project progress
        for line in content.split("\n"):
            line_stripped = line.strip().lstrip("- ")
            proj_match = project_pattern.search(line_stripped)
            if proj_match:
                # The pattern has one group per spelling (tag, link, and one
                # per configured flat tag), so only one of them is filled.
                name = next((g for g in proj_match.groups() if g), None)
                if name is None:
                    continue
                project_progress[name].append({
                    "date": date_str,
                    "status": line_stripped[:150],
                })

        entries.append(entry)

    # Aggregate topics
    all_topics = Counter()
    for date_topics in topic_by_date.values():
        all_topics.update(date_topics)

    # Compute habit stats
    habit_stats = {}
    for habit_name, occurrences in habit_patterns.items():
        total = len(occurrences)
        done_count = sum(1 for o in occurrences if o["done"])
        # Calculate streaks
        current_streak = 0
        longest_streak = 0
        streak = 0
        for o in occurrences:
            if o["done"]:
                streak += 1
                longest_streak = max(longest_streak, streak)
            else:
                streak = 0
        current_streak = streak
        habit_stats[habit_name] = {
            "total": total,
            "done": done_count,
            "completion_rate": round(done_count / total * 100, 1) if total > 0 else 0,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        }

    result = {
        "timeframe": timeframe,
        "entries_analyzed": len(entries),
        "tasks": {
            "total_complete": total_complete,
            "total_incomplete": total_incomplete,
            "completion_rate": (
                round(total_complete / (total_complete + total_incomplete) * 100, 1)
                if (total_complete + total_incomplete) > 0
                else 0
            ),
        },
    }
    if topics:
        result["top_topics"] = [{"topic": t, "count": c} for t, c in all_topics.most_common(15)]
    if mood:
        total_pos = sum(e.get("mood", {}).get("positive", 0) for e in entries)
        total_neg = sum(e.get("mood", {}).get("negative", 0) for e in entries)
        result["mood_summary"] = {
            "positive_signals": total_pos,
            "negative_signals": total_neg,
            "mood_keywords": [kw for e in entries for kw in e.get("mood", {}).get("keywords", [])],
        }
        result["mood_patterns"] = mood_patterns
    if habit_stats:
        result["habit_tracking"] = habit_stats
    if project_progress:
        result["project_progress"] = dict(project_progress)
    if topics:
        result["topic_evolution"] = {m: sorted(t) for m, t in sorted(topics_by_month.items(), reverse=True)}
    result["entries"] = entries

    if as_json:
        output(result, True)
    else:
        click.echo(f"=== Journal Patterns ({timeframe}) ===\n")
        click.echo(f"Entries analyzed: {len(entries)}")
        click.echo(f"\nTasks: {total_complete} complete, {total_incomplete} incomplete "
                    f"({result['tasks']['completion_rate']}% rate)")
        if topics and result.get("top_topics"):
            click.echo("\nTop Topics:")
            for t in result["top_topics"][:10]:
                click.echo(f"  {t['topic']}: {t['count']}")
        if mood and result.get("mood_summary"):
            ms = result["mood_summary"]
            click.echo(f"\nMood: +{ms['positive_signals']} positive, -{ms['negative_signals']} negative")
            if ms["mood_keywords"]:
                click.echo(f"  Keywords: {', '.join(ms['mood_keywords'])}")

        # Extended: Mood Patterns
        if mood and mood_patterns:
            click.echo("\nMood Patterns:")
            mood_by_month = defaultdict(list)
            for mp in mood_patterns:
                mood_by_month[mp["month"]].append(mp)
            for month in sorted(mood_by_month.keys(), reverse=True):
                click.echo(f"  {month}:")
                for mp in mood_by_month[month][:5]:
                    click.echo(f"    - {mp['mood']}: \"{mp['context']}\"")

        # Extended: Habit Tracking
        if habit_stats:
            click.echo("\nHabit Tracking:")
            for habit_name, stats in sorted(habit_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]:
                click.echo(f"  {habit_name}:")
                click.echo(f"    Completion: {stats['completion_rate']}% ({stats['done']}/{stats['total']})")
                click.echo(f"    Current streak: {stats['current_streak']} days")
                click.echo(f"    Longest streak: {stats['longest_streak']} days")

        # Extended: Project Progress
        if project_progress:
            click.echo("\nProject Progress:")
            for project, updates in sorted(project_progress.items()):
                click.echo(f"  {project}:")
                for u in updates[-5:]:
                    click.echo(f"    - {u['date']}: {u['status']}")

        # Extended: Topic Evolution
        if topics and topics_by_month:
            click.echo("\nTopic Evolution:")
            for month in sorted(topics_by_month.keys(), reverse=True):
                month_topics = sorted(topics_by_month[month])
                click.echo(f"  {month}: {', '.join(month_topics[:15])}")

@cli.command("suggest-connections", epilog="""\b
Example:
  logseq-cli --token TOKEN suggest-connections --min-confidence 0.7 --max-suggestions 10
""")
@click.option("--min-confidence", default=0.3, type=float, help="Minimum confidence score (0-1)")
@click.option("--min-shared", default=3, type=int, show_default=True,
              help="Minimum shared topics for a pair to count as connected")
@click.option("--max-suggestions", default=10, type=int, help="Maximum suggestions to return (1 or greater); the remainder is reported as withheld")
@click.option("--focus", default=None, help="Focus on specific page/topic")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def suggest_connections(ctx, min_confidence, min_shared, max_suggestions, focus, as_json):
    """Suggest connections between pages based on shared topics."""
    api = ctx.obj["api"]

    # 0 is refused rather than answered with an empty list: the empty result is
    # reported as "no connections found above confidence threshold", which
    # blames the graph for what the flag did.
    if max_suggestions < 1:
        fail("--max-suggestions must be 1 or greater.", as_json)

    pages = api.get_all_pages()

    # Build topic index: page -> set of topics
    page_topics = {}
    topic_pages = defaultdict(set)

    for page in pages:
        if page.get("journalDay") or page.get("journal-day") or page.get("journal?"):
            continue
        name = page.get("originalName") or page.get("name", "")
        try:
            content = get_page_content(api, name)
        except Exception:
            content = ""

        topics_found = set(extract_topics(content))
        # Also add the page name itself as a topic
        page_topics[name] = topics_found
        for t in topics_found:
            topic_pages[t.lower()].add(name)

    # Calculate similarity between page pairs
    suggestions = []
    page_list = list(page_topics.keys())
    if focus:
        # Only compare focus page against others
        page_list = [p for p in page_list if p.lower() == focus.lower()]

    for i, page_a in enumerate(page_list):
        topics_a = page_topics.get(page_a, set())
        if not topics_a:
            continue

        compare_to = list(page_topics.keys()) if focus else page_list[i+1:]
        for page_b in compare_to:
            if page_a == page_b:
                continue
            topics_b = page_topics.get(page_b, set())
            if not topics_b:
                continue

            # Jaccard similarity on lowercase topics
            a_lower = {t.lower() for t in topics_a}
            b_lower = {t.lower() for t in topics_b}
            intersection = a_lower & b_lower
            union = a_lower | b_lower

            if not union:
                continue

            # Jaccard alone rewards the thinnest evidence there is: two pages
            # that link one page each, the same one, score 1/1 = 1.0 and sort
            # above a pair sharing 35 topics out of 38. A single shared topic
            # is a coincidence, not a connection, so it does not qualify.
            if len(intersection) < min_shared:
                continue

            score = len(intersection) / len(union)
            if score >= min_confidence:
                suggestions.append({
                    "page_a": page_a,
                    "page_b": page_b,
                    "confidence": round(score, 3),
                    "shared_topics": sorted(intersection),
                })

    # Ties on confidence are common and meaningless on their own; the pair with
    # more shared topics is the better suggestion of the two.
    suggestions.sort(key=lambda s: (s["confidence"], len(s["shared_topics"])),
                     reverse=True)
    # Counted before the cap: "found" is a statement about the graph, and
    # counting the survivors would report three pairs as one whenever the cap
    # bites. What the cap left out is named rather than dropped in silence.
    total_found = len(suggestions)
    suggestions = suggestions[:max_suggestions]
    withheld = total_found - len(suggestions)

    result = {
        "suggestions": suggestions,
        "total_found": total_found,
        "min_confidence": min_confidence,
        "min_shared_topics": min_shared,
    }
    if withheld:
        result["withheld"] = withheld

    if as_json:
        output(result, True)
    else:
        if not suggestions:
            click.echo("No connections found above confidence threshold.")
        else:
            click.echo(f"=== Suggested Connections ({len(suggestions)}) ===\n")
            for s in suggestions:
                click.echo(f"  {s['page_a']}  <->  {s['page_b']}")
                click.echo(f"    Confidence: {s['confidence']:.1%}")
                click.echo(f"    Shared: {', '.join(s['shared_topics'][:5])}")
                click.echo()
            # Never truncate silently: the same promise the journal reads make.
            if withheld:
                click.echo(
                    f"Note: showing {len(suggestions)} of {total_found} "
                    f"suggestion(s); {withheld} omitted. Raise --max-suggestions "
                    "to see more.",
                    err=True,
                )
