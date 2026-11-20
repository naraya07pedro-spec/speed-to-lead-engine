"""Server-rendered dashboard. No JavaScript, no build step, escapes everything."""

from __future__ import annotations

from datetime import datetime
from html import escape

from .config import Settings
from .hours import local_now
from .metrics import Report
from .store import Lead

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--bad:#b42318;--warn:#b54708;--ok:#067647}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
main{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0}h2{font-size:15px;margin:32px 0 12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--muted);margin:4px 0 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.k{color:var(--muted);font-size:13px}.v{font-size:26px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}
.bad{color:var(--bad)}.warn{color:var(--warn)}.ok{color:var(--ok)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border-bottom:0}.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.safety{background:#fef3f2;border-color:#fecdca;color:var(--bad)}.emergency{background:#fffaeb;border-color:#fedf89;color:var(--warn)}
.empty{padding:28px;text-align:center;color:var(--muted)}code{background:#eee;padding:1px 5px;border-radius:4px}
"""


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 1:
        return "<1s"
    if value < 90:
        return f"{value:.0f}s"
    if value < 5400:
        return f"{value / 60:.1f}m"
    return f"{value / 3600:.1f}h"


def _age(now: datetime, then: datetime) -> str:
    return _fmt_seconds((now - then).total_seconds()) + " ago"


def _card(label: str, value: str, tone: str = "") -> str:
    return f'<div class="card"><div class="k">{escape(label)}</div><div class="v {tone}">{escape(value)}</div></div>'


def render_dashboard(settings: Settings, report: Report, leads: list[Lead], *, now: datetime) -> str:
    pct = report.human_within_5_min_pct
    cards = "".join(
        [
            _card("Leads", str(report.total_leads)),
            _card("Auto-reply median", _fmt_seconds(report.median_auto_reply_seconds), "ok"),
            _card("Callback median", _fmt_seconds(report.median_human_response_seconds)),
            _card("Called < 5 min", "-" if pct is None else f"{pct:.0f}%", "ok" if (pct or 0) >= 80 else "warn"),
            _card(
                f"Waiting > {settings.escalation_minutes} min",
                str(report.not_contacted_over_sla),
                "bad" if report.not_contacted_over_sla else "ok",
            ),
            _card("Duplicates caught", str(report.duplicates_absorbed)),
        ]
    )

    source_rows = (
        "".join(
            f"<tr><td>{escape(src)}</td><td>{st.leads}</td><td>{st.contacted}</td>"
            f"<td>{_fmt_seconds(st.median_human_seconds)}</td></tr>"
            for src, st in report.by_source.items()
        )
        or '<tr><td colspan="4" class="empty">No leads yet</td></tr>'
    )

    lead_rows = []
    for lead in leads:
        if lead.human_touched_at:
            status = f'<span class="ok">called back in {_fmt_seconds((lead.human_touched_at - lead.received_at).total_seconds())}</span>'
        elif lead.escalated_at:
            status = '<span class="bad">escalated, still waiting</span>'
        else:
            status = '<span class="warn">waiting</span>'
        lead_rows.append(
            f"<tr><td>#{lead.id}</td><td>{escape(_age(now, lead.received_at))}</td>"
            f"<td>{escape(lead.name or '-')}<br><span class='k'>{escape(lead.phone or lead.email or '')}</span></td>"
            f"<td>{escape(lead.source)}</td>"
            f"<td><span class='pill {escape(lead.urgency)}'>{escape(lead.urgency)}</span></td>"
            f"<td>{escape(lead.message[:90])}</td><td>{status}</td></tr>"
        )
    leads_html = "".join(lead_rows) or (
        '<tr><td colspan="7" class="empty">No leads yet. In demo mode, '
        "<code>POST /demo/simulate</code> fills this page.</td></tr>"
    )

    local = local_now(settings, now)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Speed-to-Lead · {escape(settings.business_name)}</title><style>{CSS}</style></head>
<body><main>
<h1>{escape(settings.business_name)}: lead response</h1>
<p class="sub">Last {report.window_days} days · {escape(local.strftime("%a %d %b, %H:%M"))} {escape(settings.business_timezone)}</p>
<div class="grid">{cards}</div>
<h2>By source</h2>
<table><tr><th>Source</th><th>Leads</th><th>Called back</th><th>Median callback</th></tr>{source_rows}</table>
<h2>Latest leads</h2>
<table><tr><th>ID</th><th>Received</th><th>Contact</th><th>Source</th><th>Urgency</th><th>Message</th><th>Status</th></tr>{leads_html}</table>
</main></body></html>"""
