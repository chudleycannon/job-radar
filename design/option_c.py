"""Option C — calm, roomy, Apple/Notion-clean. Built from the real scan."""
import html as H, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
d = json.load(open(ROOT / "out" / "roles.json"))
JOBS = d["new"] + d["matching"]
NEW = {j["uid"] for j in d["new"]}
META = d["meta"]

CSS = """
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

def build():
    rows = []
    for j in JOBS:
        paid = j["salary"]["confirmed"]
        isnew = j["uid"] in NEW
        unscreened = any("not screened" in f for f in j["flags"])
        loc = j["location"] or ""
        # Try separators strongest first, and stop at the one that actually
        # splits. A single-element list is truthy, so `a or b` silently kept
        # the unsplit string and every multi-location role ate four lines.
        # A pipe or slash means genuinely separate places. A comma usually
        # means one place described hierarchically -- "London, England,
        # United Kingdom" is not three locations -- so commas only count as a
        # list once there are more than a city/region/country's worth.
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
            loc = f"{parts[0]} +{len(parts) - 1} more"
        elif not loc:
            loc = "Location not stated"
        meta = " · ".join(x for x in [j["company"], loc] if x)
        rows.append(
          f'<div class="row" data-new="{1 if isnew else 0}" data-pay="{1 if paid else 0}" '
          f'data-sector="{H.escape(j.get("sector") or "other", quote=True)}" '
          f'data-mode="{H.escape(j.get("work_mode") or "unstated", quote=True)}" '
          f'data-country="{H.escape(j.get("country") or "unknown", quote=True)}" '
          f'data-city="{H.escape(j.get("city") or "", quote=True)}" '
          f'data-t="{H.escape((j["title"]+" "+j["company"]+" "+(j["location"] or "")).lower(), quote=True)}">'
          f'<div><div class="role">{"<span class=dot></span>" if isnew else ""}'
          f'<a href="{H.escape(j["url"])}" target="_blank" rel="noopener">{H.escape(j["title"])}</a></div>'
          f'<div class="meta">{H.escape(meta)}</div></div>'
          f'<div class="right"><span class="pay{"" if paid else " unk"}">{H.escape(j["salary_label"])}</span></div>'
          + (f'<div class="note">Not screened against your dealbreakers — this source gives no description</div>' if unscreened else '')
          + '</div>')

    paid_n = sum(1 for j in JOBS if j["salary"]["confirmed"])
    bits = [f"<b>{META['postings']:,}</b> postings across <b>{META['sources_ok']}</b> boards"]
    if NEW:
        bits.append(f"<b>{len(NEW)}</b> new")
    bits.append(f"<b>{paid_n}</b> with a salary")
    stats = " &middot; ".join(bits)

    LABELS = {"technology":"Tech","finance":"Finance","healthcare":"Health",
              "public-sector":"Public sector","charity":"Charity","education":"Education",
              "retail":"Retail","media":"Media","telecoms":"Telecoms","travel":"Travel",
              "industry":"Industry","professional-services":"Prof. services","other":"Other"}
    MODES = {"remote":"Remote","hybrid":"Hybrid","office":"Office","unstated":"Not stated"}
    mcounts = Counter(j.get("work_mode") or "unstated" for j in JOBS)
    modes = "".join(
        f'<button aria-pressed="false" data-mode="{m}">{MODES[m]}<span class="n">{mcounts[m]}</span></button>'
        for m in ("remote", "hybrid", "office", "unstated") if mcounts.get(m))

    ccounts = Counter(j.get("country") or "unknown" for j in JOBS)
    countries = '<option value="">All countries</option>' + "".join(
        f'<option value="{H.escape(c, quote=True)}">{H.escape(c)} ({n})</option>'
        for c, n in ccounts.most_common())

    citycounts = Counter(j["city"] for j in JOBS if j.get("city"))
    cities = '<option value="">All cities</option>' + "".join(
        f'<option value="{H.escape(c, quote=True)}">{H.escape(c)} ({n})</option>'
        for c, n in sorted(citycounts.items(), key=lambda x: (-x[1], x[0])))

    counts = Counter((j.get("sector") or "other") for j in JOBS)
    chips = "".join(
        f'<button aria-pressed="false" data-sec="{H.escape(s, quote=True)}">'
        f'{H.escape(LABELS.get(s, s.title()))}<span class="n">{n}</span></button>'
        for s, n in counts.most_common() if n)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job radar</title><style>{CSS}</style></head><body><div class="wrap">
<header>
  <h1>{len(JOBS)} roles worth a look</h1>
  <p class="sub">{stats}</p>
</header>
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
<div class="list" id="list">{''.join(rows)}</div>
<p class="empty" id="empty" hidden>Nothing here.</p>
<footer>Roles with a stated salary below your floor are hidden. Roles with no stated salary
are shown and marked, because most employers still publish nothing.</footer>
</div>
<script>{JS}</script></body></html>"""

JS = r"""
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

Path(ROOT/"design"/"option-c-calm.html").write_text(build(), encoding="utf-8")
print("wrote design/option-c-calm.html")
