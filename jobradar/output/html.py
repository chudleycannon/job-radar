"""HTML dashboard.

The design is Option C, chosen after two rejected directions. The full plan
and the named aesthetic risk live in the DESIGN_PLAN comment inside CSS below.

Anything changed here must be looked at before it ships: render the file,
screenshot it at mobile and desktop width, scroll past the header. Every
defect this design went through -- a stray score reading as part of a salary,
a filter that set `hidden` on rows that a class selector kept visible, a
location eating four lines -- passed an automated audit and was obvious the
moment someone looked.
"""

from __future__ import annotations

import html as _h
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..models import Job

_CSS = """
/* DESIGN PLAN — "Calm"
   PALETTE  bg #f5f5f7 (Apple system grey) / surface #fff / ink #1d1d1f
            muted #6e6e73 / line #e8e8ed / accent #2563a8 (steel blue)
            pay #1a7a4a (semantic, deliberately NOT the accent hue)
   TYPE     system-ui throughout, which is SF on the devices this is read on.
            Distinction comes from weight and tracking, not a second face:
            heavy negative tracking on large text is the whole Apple move, and
            bolting a serif onto it would fight the reference.
   LAYOUT   Roomy list, not a table. 64px rows, one role per line, generous
            padding. Segmented control to switch view. Mobile-first: this is
            read on a phone, so the phone layout is the real one.
   RISK     Comfortable density means ~6 roles per screen against 318 matches,
            so scanning the whole set takes real scrolling. Accepted on
            purpose: the filters and the ranking do the reducing, not the
            density. If that turns out to be wrong the row height is one token.
   DARK     Token remap to #000-adjacent #1c1c1e surfaces, not an inversion.
*/
@layer reset, tokens, base, components;
@layer reset{*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}}
@layer tokens{
:root{
  --bg:#f5f5f7; --surface:#fff; --surface-2:#fbfbfd; --ink:#1d1d1f;
  --muted:#6e6e73; --line:#e8e8ed; --accent:#2563a8; --pay:#1a7a4a;
  --flag:#9a6114;
  --r-lg:16px; --r-md:10px; --r-pill:999px;
  --s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px;--s6:32px;--s7:48px;--s8:64px;
  --shadow:0 1px 3px rgba(0,0,0,.04),0 1px 2px rgba(0,0,0,.03);
  --shadow-up:0 4px 16px rgba(0,0,0,.07);
  --dur:220ms;--ease:cubic-bezier(.25,.46,.45,.94);
  --font:system-ui,-apple-system,"SF Pro Text","Segoe UI",Roboto,sans-serif;
}
:root[data-theme="dark"]{
  --bg:#0b0b0d; --surface:#18181b; --surface-2:#232327; --ink:#f5f5f7;
  --muted:#9b9ba3; --line:#33333a; --accent:#6ba3e8; --pay:#5fbf8d; --flag:#d8a55a;
  --shadow:0 1px 3px rgba(0,0,0,.5); --shadow-up:0 4px 16px rgba(0,0,0,.6);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0b0b0d; --surface:#18181b; --surface-2:#232327; --ink:#f5f5f7;
  --muted:#9b9ba3; --line:#33333a; --accent:#6ba3e8; --pay:#5fbf8d; --flag:#d8a55a;
  --shadow:0 1px 3px rgba(0,0,0,.5); --shadow-up:0 4px 16px rgba(0,0,0,.6);}}
@media (prefers-reduced-motion:reduce){:root{--dur:.01ms}}
}
@layer base{
body{background:var(--bg);color:var(--ink);font-family:var(--font);
  font-size:17px;line-height:1.47;-webkit-font-smoothing:antialiased;
  padding:var(--s7) var(--s4) var(--s8)}
.wrap{max-width:720px;margin:0 auto}
a{color:inherit;text-decoration:none}
:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:6px}
}
@layer components{
header{margin-bottom:var(--s5)}
h1{font-size:2.4375rem;line-height:1.08;font-weight:700;letter-spacing:-.028em}
.sub{color:var(--muted);margin-top:var(--s2);font-size:.9375rem;letter-spacing:-.01em}
.sub b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}

.seg{display:flex;gap:2px;background:var(--surface-2);border:1px solid var(--line);
  padding:3px;border-radius:var(--r-md);margin:var(--s5) 0 var(--s4);width:fit-content;max-width:100%}
.seg button{flex:1;white-space:nowrap;border:0;background:transparent;color:var(--muted);
  font:inherit;font-size:.875rem;font-weight:500;letter-spacing:-.01em;
  padding:7px 14px;border-radius:7px;cursor:pointer;
  transition:background var(--dur) var(--ease),color var(--dur) var(--ease)}
.seg button[aria-selected=true]{background:var(--surface);color:var(--ink);
  box-shadow:var(--shadow);font-weight:600}

.chips{display:flex;gap:var(--s2);overflow-x:auto;padding:0 0 var(--s2);
  margin-bottom:var(--s4);scrollbar-width:none;-webkit-overflow-scrolling:touch}
.chips::-webkit-scrollbar{display:none}
.chips button{flex:0 0 auto;border:1px solid var(--line);background:var(--surface);
  color:var(--muted);font:inherit;font-size:.8125rem;font-weight:500;letter-spacing:-.008em;
  padding:6px 13px;border-radius:var(--r-pill);cursor:pointer;white-space:nowrap;
  transition:background var(--dur) var(--ease),color var(--dur) var(--ease),
             border-color var(--dur) var(--ease)}
.chips button:hover{color:var(--ink)}
.chips button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
:root[data-theme="dark"] .chips button[aria-pressed=true]{color:#0b0b0d}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .chips button[aria-pressed=true]{color:#0b0b0d}}
.chips .n{opacity:.6;font-variant-numeric:tabular-nums;margin-left:5px}

.selects{display:flex;gap:var(--s2);margin-bottom:var(--s4)}
.selects label{flex:1;min-width:0;position:relative}
.selects span{position:absolute;top:7px;left:13px;font-size:.6875rem;color:var(--muted);
  letter-spacing:.04em;text-transform:uppercase;pointer-events:none}
.selects select{width:100%;appearance:none;-webkit-appearance:none;
  background:var(--surface);border:1px solid var(--line);border-radius:var(--r-md);
  color:var(--ink);font:inherit;font-size:.9375rem;font-weight:500;letter-spacing:-.01em;
  padding:22px 30px 8px 12px;cursor:pointer;
  transition:border-color var(--dur) var(--ease)}
.selects select:hover{border-color:var(--muted)}
.selects label::after{content:"";position:absolute;right:14px;top:58%;width:7px;height:7px;
  border-right:1.5px solid var(--muted);border-bottom:1.5px solid var(--muted);
  transform:translateY(-50%) rotate(45deg);pointer-events:none}

.list{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);
  overflow:hidden;box-shadow:var(--shadow)}
.row{display:grid;grid-template-columns:1fr auto;gap:var(--s2) var(--s4);align-items:center;
  padding:var(--s4) var(--s5);border-bottom:1px solid var(--line);
  transition:background var(--dur) var(--ease)}
/* A class selector beats the UA stylesheet's [hidden]{display:none}, so
   .row{display:grid} silently kept filtered rows on screen. Any element given
   a display value needs its own hidden rule. */
.row[hidden]{display:none}
.row:last-child{border-bottom:0}
.row:hover{background:var(--surface-2)}
.role{font-size:1.0625rem;font-weight:600;letter-spacing:-.016em;line-height:1.3;
  /* The title wins. Salary is supporting information, not the headline. */
  color:var(--ink)}
.role a:hover{color:var(--accent)}
.meta{color:var(--muted);font-size:.875rem;letter-spacing:-.008em;margin-top:3px}
.right{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:3px}
.pay{font-size:.875rem;font-weight:500;font-variant-numeric:tabular-nums;
  letter-spacing:-.01em;color:var(--pay);white-space:nowrap}
.pay.unk{color:var(--muted);font-weight:400;font-size:.875rem}
.score{font-size:.75rem;color:var(--muted);font-variant-numeric:tabular-nums}
.dot{display:inline-block;width:6px;height:6px;border-radius:var(--r-pill);
  background:var(--accent);margin-right:6px;vertical-align:middle}
.note{grid-column:1/-1;font-size:.8125rem;color:var(--flag);margin-top:2px}
.status{display:inline-block;font-size:.6875rem;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;padding:2px 8px;border-radius:var(--r-pill);margin-left:var(--s2);
  vertical-align:2px;border:1px solid currentColor}
.status.applied,.status.submitted{color:var(--accent)}
.status.interviewing{color:var(--pay)}
.status.interested{color:var(--muted)}
.empty{padding:var(--s7) var(--s5);text-align:center;color:var(--muted);font-size:.9375rem}
footer{margin-top:var(--s5);color:var(--muted);font-size:.8125rem;line-height:1.5;
  padding:0 var(--s2)}

@media (max-width:640px){
  body{padding:var(--s5) var(--s3) var(--s7);font-size:16px}
  h1{font-size:1.9375rem}
  .row{grid-template-columns:1fr;padding:var(--s4)}
  .right{flex-direction:row;align-items:baseline;gap:var(--s3);
    justify-content:flex-start;text-align:left}
  .seg{width:100%}
}
}
"""

_JS = r"""
const rows=[...document.querySelectorAll('.row')],empty=document.getElementById('empty');
let f='all', secs=new Set(), modes=new Set(), country='', city='';
function apply(){let n=0;
  for(const r of rows){
    const viewOk=f==='all'||(f==='new'&&r.dataset.new==='1')||(f==='pay'&&r.dataset.pay==='1');
    const ok = viewOk
      && (secs.size===0  || secs.has(r.dataset.sector))
      && (modes.size===0 || modes.has(r.dataset.mode))
      && (!country || r.dataset.country===country)
      && (!city    || r.dataset.city===city);
    r.hidden=!ok; if(ok)n++;}
  empty.hidden=n>0; document.getElementById('list').hidden=n===0;}
document.querySelectorAll('.chips button').forEach(b=>b.onclick=()=>{
  const on=b.getAttribute('aria-pressed')==='true';
  b.setAttribute('aria-pressed', on?'false':'true');
  const set = b.dataset.sec ? secs : modes, key = b.dataset.sec || b.dataset.mode;
  on?set.delete(key):set.add(key); apply();});
document.getElementById('fcountry').onchange=e=>{country=e.target.value;apply()};
document.getElementById('fcity').onchange=e=>{city=e.target.value;apply()};
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.seg button').forEach(o=>o.setAttribute('aria-selected','false'));
  b.setAttribute('aria-selected','true'); f=b.dataset.f; apply();});
"""

_SECTORS = {"technology": "Tech", "finance": "Finance", "healthcare": "Health",
            "public-sector": "Public sector", "charity": "Charity",
            "education": "Education", "retail": "Retail", "media": "Media",
            "telecoms": "Telecoms", "travel": "Travel", "industry": "Industry",
            "professional-services": "Prof. services", "other": "Other"}
_MODES = {"remote": "Remote", "hybrid": "Hybrid", "office": "Office",
          "unstated": "Not stated"}


def _cap_location(loc: str) -> str:
    """Shorten a location without lying about it.

    A pipe or slash separates genuinely different places. A comma usually
    describes one place hierarchically, so "London, England, United Kingdom"
    is one location and must not be rendered as "London +2 more".
    """
    loc = (loc or "").strip()
    parts, cap_at = [loc], 2
    for sep in ("|", "/"):
        bits = [x.strip() for x in loc.split(sep) if x.strip()]
        if len(bits) > 1:
            parts, cap_at = bits, 2
            break
    else:
        bits = [x.strip() for x in loc.split(",") if x.strip()]
        if len(bits) > 3:
            parts, cap_at = bits, 1
    if len(parts) > cap_at:
        return f"{parts[0]} +{len(parts) - 1} more"
    return loc or "Location not stated"


def _row(j: Job, is_new: bool) -> str:
    paid = j.salary.confirmed
    unscreened = any("not screened" in f for f in j.flags)
    # The flags that change what you should do about a role. "Salary in GBP,
    # floor in EUR, not compared" reached roles.json and nowhere a person
    # looks, so an unconverted figure below the floor read as one that passed.
    caveats = [f for f in j.flags
               if "not compared" in f or "sponsor" in f or "soft flag" in f]
    meta = " \u00b7 ".join(x for x in [j.company, _cap_location(j.location)] if x)
    return (
        f'<div class="row" data-new="{1 if is_new else 0}" data-pay="{1 if paid else 0}" '
        f'data-sector="{_h.escape(j.sector or "other", quote=True)}" '
        f'data-mode="{_h.escape(j.work_mode or "unstated", quote=True)}" '
        f'data-country="{_h.escape(j.country or "unknown", quote=True)}" '
        f'data-city="{_h.escape(j.city or "", quote=True)}">'
        f'<div><div class="role">{"<span class=dot></span>" if is_new else ""}'
        f'<a href="{_h.escape(j.url)}" target="_blank" rel="noopener">{_h.escape(j.title)}</a>'
        + (f'<span class="status {_h.escape(j.app_status, quote=True)}">'
           f'{_h.escape(j.app_status)}</span>' if j.app_status else '')
        + '</div>'
        + f'<div class="meta">{_h.escape(meta)}</div></div>'
        f'<div class="right"><span class="pay{"" if paid else " unk"}">'
        f'{_h.escape(j.salary.label())}</span></div>'
        + ('<div class="note">Not screened against your dealbreakers &mdash; '
           'this source gives no description</div>' if unscreened else '')
        + "".join(f'<div class="note">{_h.escape(c)}</div>' for c in caveats)
        + '</div>')


def render(new: list[Job], seen: list[Job], *, dropped, sources_ok, sources_total,
           throttled, postings: int = 0, title: str = "Job radar") -> str:
    jobs = new + seen
    new_ids = {j.uid for j in new}
    paid_n = sum(1 for j in jobs if j.salary.confirmed)

    bits = [f"<b>{postings or (sum(dropped.values()) + len(jobs)):,}</b> postings "
            f"across <b>{sources_ok}</b> boards"]
    if new:
        bits.append(f"<b>{len(new)}</b> new")
    bits.append(f"<b>{paid_n}</b> with a salary")
    stats = " &middot; ".join(bits)

    sec = Counter((j.sector or "other") for j in jobs)
    chips = "".join(
        f'<button aria-pressed="false" data-sec="{_h.escape(s, quote=True)}">'
        f'{_h.escape(_SECTORS.get(s, s.title()))}<span class="n">{n}</span></button>'
        for s, n in sec.most_common())

    mc = Counter((j.work_mode or "unstated") for j in jobs)
    modes = "".join(
        f'<button aria-pressed="false" data-mode="{m}">{_MODES[m]}'
        f'<span class="n">{mc[m]}</span></button>'
        for m in ("remote", "hybrid", "office", "unstated") if mc.get(m))

    cc = Counter((j.country or "unknown") for j in jobs)
    countries = '<option value="">All countries</option>' + "".join(
        f'<option value="{_h.escape(c, quote=True)}">{_h.escape(c)} ({n})</option>'
        for c, n in cc.most_common())

    cty = Counter(j.city for j in jobs if j.city)
    cities = '<option value="">All cities</option>' + "".join(
        f'<option value="{_h.escape(c, quote=True)}">{_h.escape(c)} ({n})</option>'
        for c, n in sorted(cty.items(), key=lambda x: (-x[1], x[0])))

    warn = ""
    if throttled:
        names = ", ".join(throttled[:6]) + (" and others" if len(throttled) > 6 else "")
        warn = (f'<p class="warnbar"><b>{len(throttled)} sources returned nothing but '
                f'have before.</b> That usually means rate limiting rather than an empty '
                f'board, so treat them as unknown: {_h.escape(names)}.</p>')

    rows = "".join(_row(j, j.uid in new_ids) for j in jobs)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_h.escape(title)}</title><style>{_CSS}</style></head><body><div class="wrap">
<header>
  <h1>{len(jobs)} roles worth a look</h1>
  <p class="sub">{stats}</p>
</header>
{warn}
<div class="seg" role="tablist" aria-label="Filter roles">
  <button role="tab" aria-selected="true"  data-f="all">All</button>
  <button role="tab" aria-selected="false" data-f="new">New</button>
  <button role="tab" aria-selected="false" data-f="pay">Salary shown</button>
</div>
<div class="chips" role="group" aria-label="Filter by sector">{chips}</div>
<div class="chips" role="group" aria-label="Filter by working pattern">{modes}</div>
<div class="selects">
  <label><span>Country</span><select id="fcountry" aria-label="Country">{countries}</select></label>
  <label><span>City</span><select id="fcity" aria-label="City">{cities}</select></label>
</div>
<div class="list" id="list">{rows}</div>
<p class="empty" id="empty" hidden>Nothing matches those filters.</p>
<footer>Roles with a stated salary below your floor are hidden. Roles with no stated
salary are shown and marked, because most employers still publish nothing.
Working pattern is only known where the employer said so.</footer>
</div>
<script>{_JS}</script></body></html>"""


def write(path, **kwargs):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(**kwargs), encoding="utf-8")
    return path
