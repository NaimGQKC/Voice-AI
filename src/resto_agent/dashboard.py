"""Read-only web view of what the agent did on every call.

Design notes, because "build a dashboard" can mean very different things:

* **Read-only. Always.** It renders the call log and nothing else. There is no
  form, no mutation, no way to change a booking from here — Libro remains the
  system of record. That removes an entire class of production risk from a page
  the owner will open on a phone.
* **Server-rendered HTML, no build step.** It's a call list, a few counters and
  a table for one restaurant. A React toolchain would add a build, a deploy
  target and a second thing to keep alive, for a page that renders a table.
* **Runs inside the agent process**, so there is no second service to deploy,
  scale or monitor. The agent already needs a health-check port.
* **Phone numbers are masked by default** — the owner will open this in public.
  `?full=1` reveals them, which is a deliberate, visible act.

Auth is a single shared token compared in constant time. That is thin, and it is
on purpose: this exposes no mutations and no payment data, and the alternative
(user accounts, sessions, password resets) is a large amount of machinery to
maintain for one restaurant. It is served over HTTPS by the host. If this ever
grows write access, that trade has to be revisited.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone

import html

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from .store import CallStore

#: Set AGENT_DASHBOARD_TOKEN to require ?token=… . Unset = open (local use only).
_TOKEN_ENV = "AGENT_DASHBOARD_TOKEN"


def _authorized(token: str) -> bool:
    expected = os.environ.get(_TOKEN_ENV, "")
    if not expected:
        return True  # no token configured: local/dev use
    return hmac.compare_digest(token or "", expected)


def _mask(phone: str, full: bool) -> str:
    if full or len(phone) < 4:
        return phone or "—"
    return f"•••• {phone[-4:]}"


def _ago(iso: str) -> str:
    """'3h ago' reads better than a UTC timestamp on a phone screen."""
    try:
        then = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso or "—"
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 90:
        return "just now"
    for limit, div, unit in ((5400, 60, "min"), (172800, 3600, "h")):
        if secs < limit:
            return f"{int(secs // div)}{unit} ago"
    return f"{int(secs // 86400)}d ago"


CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a19;--dim:#6b6b68;--line:#e6e5e1;--card:#fff;
--ok:#1a7f4b;--warn:#b45309;--bad:#b42318;--accent:#1f3864}
@media(prefers-color-scheme:dark){:root{--bg:#131312;--fg:#ececeb;--dim:#9a9a96;
--line:#2b2b29;--card:#1c1c1a;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--accent:#93b4f5}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:20px;margin:0 0 2px}
.sub{color:var(--dim);font-size:13px;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:28px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .n{font-size:26px;font-weight:600;line-height:1.1}
.card .l{color:var(--dim);font-size:12px;margin-top:4px}
.bad .n{color:var(--bad)} .warn .n{color:var(--warn)} .ok .n{color:var(--ok)}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);
margin:32px 0 10px;font-weight:600}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}
th{text-align:left;font-weight:600;color:var(--dim);font-size:12px;
text-transform:uppercase;letter-spacing:.04em;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;
background:var(--line);color:var(--fg)}
.pill.ok{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.pill.bad{background:color-mix(in srgb,var(--bad) 18%,transparent);color:var(--bad)}
.empty{color:var(--dim);padding:18px 0;font-style:italic}
.note{color:var(--dim);font-size:12px;margin-top:6px}
footer{margin-top:44px;color:var(--dim);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
a{color:var(--accent)}
.bar{display:flex;align-items:center;gap:10px;margin:7px 0}
.bar .lab{width:150px;font-size:13px;flex:none}
.bar .track{flex:1;height:9px;background:var(--line);border-radius:99px;overflow:hidden}
.bar .fill{height:100%;background:var(--accent);border-radius:99px}
.bar .val{width:78px;text-align:right;font-size:13px;color:var(--dim);flex:none}
.why{color:var(--dim);font-size:12px;margin:2px 0 0 160px}
pre.tx{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:14px;white-space:pre-wrap;word-break:break-word;font-size:13px;line-height:1.6;
max-height:60vh;overflow:auto}
.seq{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--dim)}
.back{display:inline-block;margin-bottom:14px}
"""


def _stat(n, label: str, tone: str = "") -> str:
    return f'<div class="card {tone}"><div class="n">{n}</div><div class="l">{label}</div></div>'


def build_app(store: CallStore | None = None) -> FastAPI:
    app = FastAPI(title="restaurant voice agent", docs_url=None, redoc_url=None)
    _store = store

    def get_store() -> CallStore:
        nonlocal _store
        if _store is None:
            _store = CallStore()
        return _store

    @app.get("/health", response_class=HTMLResponse)
    async def health() -> str:
        """Liveness for the host. Deliberately unauthenticated and trivial."""
        return "ok"

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        token: str = Query(""),
        days: int = Query(7, ge=1, le=365),
        full: int = Query(0),
    ) -> str:
        if not _authorized(token):
            raise HTTPException(status_code=401, detail="Bad or missing token")

        st = get_store()
        s = st.stats(since_days=days)
        g = st.greeting_stats(since_days=days)
        pending = st.pending_messages()
        failed = st.failed_bookings(since_days=days)
        show = lambda p: _mask(p, bool(full))  # noqa: E731

        zero_rate = g.get("zero_user_turn_rate")
        zero_pct = "—" if zero_rate is None else f"{zero_rate * 100:.0f}%"

        cards = "".join([
            _stat(s["calls"], f"calls, last {days}d"),
            _stat(s["bookings_ok"], "bookings made", "ok"),
            _stat(s["bookings_failed"], "bookings FAILED",
                  "bad" if s["bookings_failed"] else ""),
            _stat(s["undelivered"], "not passed on",
                  "warn" if s["undelivered"] else ""),
            _stat(zero_pct, "hung up without speaking",
                  "warn" if (zero_rate or 0) > 0.10 else ""),
        ])

        # --- what calls actually turned into ---------------------------------
        breakdown = st.category_breakdown(since_days=days)
        if breakdown:
            from .store import CATEGORY_REASONS
            bars = "".join(
                f"<div class='bar'><div class='lab'>{cat}</div>"
                f"<div class='track'><div class='fill' style='width:{share*100:.0f}%'></div></div>"
                f"<div class='val'>{n} · {share*100:.0f}%</div></div>"
                f"<div class='why'>{CATEGORY_REASONS.get(cat, '')}</div>"
                for cat, n, share in breakdown
            )
        else:
            bars = "<p class='empty'>No finished calls yet.</p>"

        # --- things needing a human -----------------------------------------
        if pending:
            rows = "".join(
                f"<tr><td>{_ago(m.created_at)}</td>"
                f"<td><span class='pill'>{m.kind}</span></td>"
                f"<td>{m.name or '—'}</td><td>{show(m.phone)}</td>"
                f"<td>{m.body or ''}"
                + (f"<div class='note'>party of {m.party_size} · {m.wanted_date} {m.wanted_time}</div>"
                   if m.party_size or m.wanted_date else "")
                + "</td></tr>"
                for m in pending
            )
            todo = ("<div class='scroll'><table><tr><th>When</th><th>Type</th>"
                    "<th>Name</th><th>Phone</th><th>What they wanted</th></tr>"
                    f"{rows}</table></div>")
        else:
            todo = "<p class='empty'>Nothing outstanding — every message has been passed on.</p>"

        # --- bookings that failed -------------------------------------------
        if failed:
            rows = "".join(
                f"<tr><td>{_ago(r['created_at'])}</td><td>{r['name'] or '—'}</td>"
                f"<td>{show(r['phone'])}</td><td>{r['party_size'] or '—'}</td>"
                f"<td>{r['wanted_time'] or '—'}</td>"
                f"<td><span class='pill bad'>{(r['error'] or '')[:70]}</span></td></tr>"
                for r in failed
            )
            fails = ("<div class='scroll'><table><tr><th>When</th><th>Name</th>"
                     "<th>Phone</th><th>Party</th><th>Wanted</th><th>Why it failed</th>"
                     f"</tr>{rows}</table></div>")
        else:
            fails = f"<p class='empty'>No failed bookings in the last {days} days.</p>"

        # --- recent calls -----------------------------------------------------
        calls = st.recent_calls(limit=40)
        if calls:
            def outcome_pill(o: str) -> str:
                cls = "bad" if o == "no_user_turn" else ("ok" if o == "completed" else "")
                return f"<span class='pill {cls}'>{o or 'in progress'}</span>"
            rows = "".join(
                f"<tr><td>{_ago(r['started_at'])}</td>"
                f"<td>{outcome_pill(r['outcome'])}</td>"
                f"<td>{(r['detected_language'] if 'detected_language' in r.keys() else '') or '—'}</td>"
                f"<td><span class='pill'>{(r['category'] if 'category' in r.keys() else '') or '—'}</span></td>"
                f"<td><a href='/call/{r['call_id']}?token={token}'>"
                f"{r['call_id'][:12]}</a></td></tr>"
                for r in calls
            )
            calls_html = ("<div class='scroll'><table><tr><th>When</th><th>Outcome</th>"
                          f"<th>Lang</th><th>Category</th><th>Call</th></tr>{rows}</table></div>")
        else:
            calls_html = "<p class='empty'>No calls recorded yet.</p>"

        masked_note = (
            "" if full else
            " · <span class='note'>numbers masked — add <code>&amp;full=1</code> to reveal</span>"
        )
        return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Restaurant voice agent</title><style>{CSS}</style></head><body><div class="wrap">
<h1>Restaurant voice agent</h1>
<div class="sub">Last {days} days{masked_note}</div>
<div class="grid">{cards}</div>
<h2>What calls turned into</h2>{bars}
<h2>Needs a person</h2>{todo}
<h2>Bookings the agent could not make</h2>{fails}
<h2>Recent calls</h2>{calls_html}
<footer>Read-only. Libro remains the system of record for reservations —
nothing here can change a booking.</footer>
</div></body></html>"""

    @app.get("/call/{call_id}", response_class=HTMLResponse)
    async def call_detail(call_id: str, token: str = Query(""),
                          full: int = Query(0)) -> str:
        if not _authorized(token):
            raise HTTPException(status_code=401, detail="Bad or missing token")
        st = get_store()
        row = st.call_detail(call_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No such call")

        keys = row.keys()
        transcript = (row["transcript"] if "transcript" in keys else "") or ""
        if transcript:
            # html.escape: a caller can say anything, and it lands in this page.
            body = f"<pre class='tx'>{html.escape(transcript)}</pre>"
        else:
            body = ("<p class='empty'>No transcript stored for this call. "
                    "Transcripts are off by default — set "
                    "<code>AGENT_STORE_TRANSCRIPTS=1</code> to record them "
                    "(they are far more sensitive than a name and number; see "
                    "docs/DATA_RETENTION.md).</p>")

        seq = (row["tool_sequence"] if "tool_sequence" in keys else "") or "none"
        cat = (row["category"] if "category" in keys else "") or "—"
        lang = (row["detected_language"] if "detected_language" in keys else "") or "—"
        return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Call {html.escape(call_id[:12])}</title><style>{CSS}</style></head>
<body><div class="wrap">
<a class="back" href="/?token={token}">&larr; all calls</a>
<h1>Call {html.escape(call_id[:12])}</h1>
<div class="sub">{_ago(row['started_at'])} · {html.escape(cat)} ·
language {html.escape(lang)} · outcome {html.escape(row['outcome'] or '—')}</div>
<h2>Tools the agent used</h2>
<p class="seq">{html.escape(seq)}</p>
<h2>Transcript</h2>{body}
<footer>Read-only.</footer></div></body></html>"""

    return app
