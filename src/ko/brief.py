"""Morning brief: deterministic pipeline, NOT an agent loop.

Gather → gather → ONE cheap-model synthesis. Each source is best-effort: a
misconfigured or unauthenticated source becomes a one-liner note rather than
crashing the whole brief. The _try() pattern mirrors agents/_toolsets.py.

    `ko brief`       → gather all sections → one LLM call → scannable markdown
    `ko brief --raw` → print gathered sections verbatim, skip LLM entirely

Sends calendar and email content to the configured LLM (see --raw to skip).
"""

from __future__ import annotations


def _try(label: str, fn, *args, **kwargs) -> tuple[str, str]:
    """Call fn(); on any exception, return a one-liner fallback note instead of raising.

    Returns (section_title, text) — on failure the text is a single note line so
    the caller always gets something printable, never an exception.
    """
    try:
        return (label, fn(*args, **kwargs))
    except Exception as e:
        return (label, f"({label}: {e})")


def _today_events() -> str:
    """Today's calendar events as a plain text list."""
    from datetime import datetime, timedelta
    from ko import gcal

    tz = gcal.tz()
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    events = gcal.list_events(time_min=start, time_max=end)
    if not events:
        return "(no events today)"
    lines = []
    for e in events:
        s = e.start
        if "T" in s:
            # timed event — show HH:MM
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                local = dt.astimezone(tz)
                time_str = local.strftime("%H:%M")
            except (ValueError, OSError):
                time_str = s[11:16]
            lines.append(f"  {time_str}  {e.summary}")
        else:
            lines.append(f"  (all day)  {e.summary}")
    return "\n".join(lines)


def _unread_email() -> str:
    """Recent unread inbox, ~10 messages, one line each."""
    from ko import gmail

    msgs = gmail.recent(n=10, unread=True)
    if not msgs:
        return "(no unread messages)"
    lines = []
    for m in msgs:
        # Truncate snippet: strip whitespace, cap at 80 chars
        snip = " ".join(m.snippet.split())
        if len(snip) > 80:
            snip = snip[:77] + "..."
        sender = m.from_.split("<")[0].strip().strip('"') or m.from_
        lines.append(f"  {m.date[:10]}  {sender[:25]:<25}  {m.subject[:50]}")
        if snip:
            lines.append(f"    {snip}")
    return "\n".join(lines)


def _hn_top() -> str:
    """Top 10 HN stories, one line each: points, title, url."""
    from ko import hn

    stories = hn.top(n=10)
    if not stories:
        return "(no stories)"
    return "\n".join(
        f"  {s.points:>4}pts  {s.title}  {s.hn_url}" for s in stories
    )


def _hf_top() -> str:
    """Top 5 daily AI papers, one line each: upvotes, title."""
    from ko import hf

    papers = hf.top(n=5)
    if not papers:
        return "(no papers)"
    # hf.top returns HF's "trending" order; re-sort by upvotes so the column
    # reads like the HN section above it
    papers = sorted(papers, key=lambda p: p.upvotes, reverse=True)
    return "\n".join(
        f"  {p.upvotes:>3}▲  {p.title}" for p in papers
    )


def gather() -> list[tuple[str, str]]:
    """Collect all brief sections. Each source is best-effort — failures become notes.

    Returns a list of (section_title, text) pairs in display order.
    """
    sections = [
        _try("Today", _today_events),
        _try("Unread email", _unread_email),
        _try("Hacker News", _hn_top),
        _try("AI papers (HF)", _hf_top),
    ]
    return sections


def render_raw(sections: list[tuple[str, str]]) -> str:
    """Format gathered sections as plain text for --raw output or LLM input."""
    parts = []
    for title, text in sections:
        parts.append(f"## {title}\n{text}")
    return "\n\n".join(parts)


BRIEF_SYSTEM = (
    "Write a concise morning brief from these sections. "
    "Scannable markdown: what's on today, emails that actually need attention "
    "(ignore marketing), the 2-3 HN/paper items worth a click and why, one line each. "
    "No filler, no preamble."
)
