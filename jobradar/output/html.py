"""HTML dashboard.

DESIGN PLAN
  PALETTE
    bg         #f7f5f2  warm off-white, editorial rather than clinical
    surface    #ffffff
    text       #1c1917
    muted      #6b6560
    accent     #c4620a  burnt amber: warm authority, and deliberately not the
                        indigo/purple every generated dashboard reaches for
    border     #e2ddd8
    good       #1a7a4a  new-since-last-run
    warn       #9a6b1f  unconfirmed salary / soft flags
  TYPE
    display    Georgia serif, for headings and the count
    body       system sans
    Deliberately no webfont: this file is opened from disk, often offline, and
    a CDN font that fails to load is worse than a good system stack.
  LAYOUT
    One dominant number at the top left, then new roles, then everything else.
    Roles are rows, not a three-card grid.
  AESTHETIC RISK
    Warm neutrals on a data view can read muddy. Mitigated by keeping every
    row surface pure white so the warmth only shows in the gaps.
  DARK MODE
    Token remapping, both prefers-color-scheme and [data-theme].
"""

from __future__ import annotations

import html as _h
import json
from datetime import datetime
from pathlib import Path

from ..models import Job

_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f7f5f2; --surface:#fff; --text:#1c1917; --muted:#6b6560;
  --accent:#c4620a; --border:#e2ddd8; --good:#1a7a4a; --warn:#9a6b1f;
  --shadow-sm:0 1px 2px rgba(0,0,0,.06);
  --s1:4px; --s2:8px; --s3:16px; --s4:24px; --s5:32px; --s6:48px;
  --dur:200ms; --ease:cubic-bezier(.25,.46,.45,.94);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#16130f; --surface:#1f1c18; --text:#f0ece6; --muted:#9b948c;
    --accent:#e07830; --border:#2e2922; --good:#4ba97a; --warn:#c9973f;
    --shadow-sm:0 1px 2px rgba(0,0,0,.4);
  }
}
:root[data-theme="dark"]{
  --bg:#16130f; --surface:#1f1c18; --text:#f0ece6; --muted:#9b948c;
  --accent:#e07830; --border:#2e2922; --good:#4ba97a; --warn:#c9973f;
  --shadow-sm:0 1px 2px rgba(0,0,0,.4);
}
@media (prefers-reduced-motion:reduce){:root{--dur:.01ms}}
body{background:var(--bg);color:var(--text);line-height:1.55;
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:16px;-webkit-font-smoothing:antialiased;padding:var(--s5) var(--s4) var(--s6)}
.wrap{max-width:1080px;margin:0 auto}
h1,h2,.big{font-family:Georgia,"Iowan Old Style",serif;font-weight:700;letter-spacing:-.01em}
h1{font-size:2.4375rem;line-height:1.1}
h2{font-size:1.5625rem;margin:var(--s6) 0 var(--s3)}
.sub{color:var(--muted);margin-top:var(--s2)}
header{display:flex;flex-wrap:wrap;gap:var(--s4);align-items:flex-end;
  justify-content:space-between;padding-bottom:var(--s4);border-bottom:2px solid var(--text)}
.big{font-size:3.0625rem;line-height:1;font-variant-numeric:tabular-nums;color:var(--accent)}
.stats{display:flex;gap:var(--s5);flex-wrap:wrap}
.stat .k{font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.stat .v{font-size:1.25rem;font-variant-numeric:tabular-nums;font-weight:600}
.row{display:grid;grid-template-columns:auto 1fr auto;gap:var(--s3);align-items:start;
  background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:var(--s3) var(--s4);margin-bottom:var(--s2);box-shadow:var(--shadow-sm);
  transition:border-color var(--dur) var(--ease),transform var(--dur) var(--ease)}
.row:hover{border-color:var(--accent);transform:translateY(-1px)}
.row.new{border-left:4px solid var(--good)}
.sc{font-variant-numeric:tabular-nums;font-weight:700;font-size:1.25rem;
  color:var(--accent);min-width:3ch;text-align:right}
.t{font-weight:600;font-size:1.0625rem}
.t a{color:inherit;text-decoration:none}
.t a:hover{text-decoration:underline;text-decoration-color:var(--accent)}
.meta{color:var(--muted);font-size:.875rem;margin-top:2px}
.tags{margin-top:var(--s2);display:flex;flex-wrap:wrap;gap:var(--s1)}
.tag{font-size:.75rem;padding:2px 8px;border-radius:999px;border:1px solid var(--border);
  color:var(--muted);background:var(--bg)}
.tag.pay{color:var(--good);border-color:var(--good)}
.tag.unc{color:var(--warn);border-color:var(--warn)}
.tag.flag{color:var(--warn)}
.right{text-align:right;font-size:.8125rem;color:var(--muted);white-space:nowrap}
.pill{display:inline-block;font-size:.6875rem;text-transform:uppercase;letter-spacing:.08em;
  color:var(--good);border:1px solid var(--good);border-radius:999px;padding:1px 7px}
.note{background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--warn);
  border-radius:8px;padding:var(--s3) var(--s4);margin:var(--s4) 0;font-size:.9375rem}
.drops{display:flex;flex-wrap:wrap;gap:var(--s2);margin-top:var(--s3)}
.drops span{font-size:.8125rem;color:var(--muted);border:1px dashed var(--border);
  border-radius:6px;padding:2px 10px;font-variant-numeric:tabular-nums}
footer{margin-top:var(--s6);padding-top:var(--s3);border-top:1px solid var(--border);
  color:var(--muted);font-size:.8125rem}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
@media (max-width:640px){
  body{padding:var(--s4) var(--s3)}
  .row{grid-template-columns:auto 1fr}
  .right{grid-column:2;text-align:left}
}
"""


def _row(j: Job, is_new: bool) -> str:
    tags = []
    if j.salary.confirmed:
        tags.append(f'<span class="tag pay">{_h.escape(j.salary.raw or "pay stated")}</span>')
    else:
        tags.append('<span class="tag unc">unconfirmed salary</span>')
    for r in j.reasons[:3]:
        if "salary" in r.lower():
            continue
        tags.append(f'<span class="tag">{_h.escape(r)}</span>')
    for f in j.flags[:2]:
        tags.append(f'<span class="tag flag">{_h.escape(f)}</span>')

    meta = " · ".join(x for x in [j.company, j.location or "location not stated",
                                  j.department or ""] if x)
    return f"""<article class="row{' new' if is_new else ''}">
  <div class="sc">{j.score:.0f}</div>
  <div>
    <div class="t"><a href="{_h.escape(j.url)}" target="_blank" rel="noopener">{_h.escape(j.title)}</a>
      {'<span class="pill">new</span>' if is_new else ''}</div>
    <div class="meta">{_h.escape(meta)}</div>
    <div class="tags">{''.join(tags)}</div>
  </div>
  <div class="right">{_h.escape(j.posted_at or '')}<br>{_h.escape(j.platform)}</div>
</article>"""


def render(
    new: list[Job],
    seen: list[Job],
    *,
    dropped: dict[str, int],
    sources_ok: int,
    sources_total: int,
    throttled: list[str],
    title: str = "Job radar",
) -> str:
    total = len(new) + len(seen)
    stamp = datetime.now().strftime("%d %b %Y, %H:%M")
    unconfirmed = sum(1 for j in new + seen if not j.salary.confirmed)

    warn = ""
    if throttled:
        names = ", ".join(throttled[:8]) + (" and others" if len(throttled) > 8 else "")
        warn = (f'<div class="note"><strong>{len(throttled)} sources returned nothing '
                f'but have returned jobs before.</strong> That usually means rate limiting '
                f'rather than an empty board, so treat them as unknown rather than empty: '
                f'{_h.escape(names)}.</div>')

    drops = "".join(
        f"<span>{_h.escape(k)} &middot; {v}</span>"
        for k, v in sorted(dropped.items(), key=lambda x: -x[1])[:12]
    )

    body_new = "".join(_row(j, True) for j in new) or \
        '<p class="sub">Nothing new since the last run.</p>'
    body_seen = "".join(_row(j, False) for j in seen)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_h.escape(title)}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<header>
  <div>
    <h1>{_h.escape(title)}</h1>
    <p class="sub">{stamp} &middot; {sources_ok} of {sources_total} sources responded</p>
  </div>
  <div class="stats">
    <div class="stat"><div class="k">matching</div><div class="big">{total}</div></div>
    <div class="stat"><div class="k">new</div><div class="v">{len(new)}</div></div>
    <div class="stat"><div class="k">no stated pay</div><div class="v">{unconfirmed}</div></div>
  </div>
</header>
{warn}
<h2>New since last run</h2>
{body_new}
<h2>Everything else matching ({len(seen)})</h2>
{body_seen}
<footer>
  <p>Roles with a stated salary below your floor are not shown. Roles with no stated
  salary are shown and marked, because most employers still do not publish one.</p>
  <div class="drops">{drops}</div>
  <p style="margin-top:var(--s3)">Built by <a href="https://github.com/maccydee/job-radar">job-radar</a>.</p>
</footer>
</div></body></html>"""


def write(path: Path, **kwargs) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(**kwargs), encoding="utf-8")
    return path
