"""Generate two design proposals from the real scan output.

Both read out/roles.json so what you are judging is your actual data, not
lorem ipsum with three cards in a row.
"""
import html as H, json, sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
d = json.load(open(ROOT / "out" / "roles.json"))
JOBS = (d["new"] + d["matching"])
NEW = {j["uid"] for j in d["new"]}
META = d["meta"]


def rows(n=None):
    return JOBS[:n] if n else JOBS


# ---------------------------------------------------------------- OPTION A
A_CSS = """
/* DESIGN PLAN — "Terminal"
   PALETTE  bg #0d0f14 (near-black, no bloom) / panel #12151c / line #1e2430
            text #d6dbe4 / muted #6b7686 / amber #e8a33d / green #46b17b
   TYPE     ui-monospace throughout. Data is the content; mono makes columns
            line up without a grid and signals "instrument, not article".
   LAYOUT   One table, 32px rows, ~18 visible. Sortable. Live filter.
            Salary and score are real columns you compare down, not pills.
   RISK     Mono at length is tiring and dark-first excludes people who read
            better on light. Mitigated: 1.45 line-height, only the table is
            mono-dense, and a light theme is a full token remap not an invert.
*/
@layer reset, tokens, base, components;
@layer reset { *,*::before,*::after{box-sizing:border-box;margin:0;padding:0} }
@layer tokens {
:root{
  --bg:#0d0f14; --panel:#12151c; --line:#1e2430; --text:#d6dbe4;
  --muted:#6b7686; --amber:#e8a33d; --green:#46b17b; --red:#d4776b;
  --s1:4px;--s2:8px;--s3:16px;--s4:24px;--s5:32px;--s6:48px;
  --dur:160ms; --ease:cubic-bezier(.25,.46,.45,.94);
  --mono:ui-monospace,"JetBrains Mono","SF Mono",Menlo,Consolas,monospace;
}
:root[data-theme="light"]{
  --bg:#f4f5f7; --panel:#fff; --line:#dde1e8; --text:#1a1f28;
  --muted:#68717f; --amber:#a8620a; --green:#1a7a4a; --red:#b4372a;
}
@media (prefers-color-scheme:light){ :root:not([data-theme="dark"]){
  --bg:#f4f5f7; --panel:#fff; --line:#dde1e8; --text:#1a1f28;
  --muted:#68717f; --amber:#a8620a; --green:#1a7a4a; --red:#b4372a; } }
@media (prefers-reduced-motion:reduce){:root{--dur:.01ms}}
}
@layer base {
body{background:var(--bg);color:var(--text);font-family:var(--mono);
  font-size:13px;line-height:1.45;-webkit-font-smoothing:antialiased;
  padding:var(--s4) var(--s4) var(--s6)}
.wrap{max-width:1400px;margin:0 auto}
a{color:inherit;text-decoration:none}
:focus-visible{outline:2px solid var(--amber);outline-offset:2px}
}
@layer components {
.bar{display:flex;flex-wrap:wrap;gap:var(--s4);align-items:baseline;
  border-bottom:1px solid var(--line);padding-bottom:var(--s3);margin-bottom:var(--s3)}
.brand{font-size:16px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--amber)}
.kv{color:var(--muted);font-size:12px}
.kv b{color:var(--text);font-weight:600;font-variant-numeric:tabular-nums}
.tools{margin-left:auto;display:flex;gap:var(--s2);align-items:center}
input[type=search]{background:var(--panel);border:1px solid var(--line);color:var(--text);
  font-family:var(--mono);font-size:12px;padding:6px 10px;border-radius:3px;width:220px}
input[type=search]::placeholder{color:var(--muted)}
button.t{background:var(--panel);border:1px solid var(--line);color:var(--muted);
  font-family:var(--mono);font-size:11px;padding:6px 10px;border-radius:3px;cursor:pointer;
  transition:color var(--dur) var(--ease),border-color var(--dur) var(--ease)}
button.t:hover{color:var(--text);border-color:var(--muted)}
button.t[aria-pressed=true]{color:var(--amber);border-color:var(--amber)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:4px;background:var(--panel)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
thead th{position:sticky;top:0;background:var(--panel);z-index:2;text-align:left;
  font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
  font-weight:600;padding:8px 10px;border-bottom:1px solid var(--line);
  cursor:pointer;white-space:nowrap;user-select:none}
thead th:hover{color:var(--text)}
thead th[data-dir]::after{content:" ↓";color:var(--amber)}
thead th[data-dir="asc"]::after{content:" ↑"}
th.num,td.num{text-align:right}
tbody tr{border-bottom:1px solid var(--line);height:32px}
tbody tr:hover{background:color-mix(in srgb,var(--amber) 7%,transparent)}
tbody tr.new td:first-child{box-shadow:inset 2px 0 0 var(--green)}
td{padding:5px 10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:340px}
td.sc{color:var(--amber);font-weight:700;width:46px}
td.title a:hover{color:var(--amber);text-decoration:underline}
td.co{color:var(--muted)}
td.pay.yes{color:var(--green)}
td.pay.no{color:var(--muted);font-style:italic}
.warn{color:var(--red);font-size:11px}
.tagn{color:var(--green);font-size:10px;letter-spacing:.08em}
footer{margin-top:var(--s4);color:var(--muted);font-size:11px;display:flex;gap:var(--s4);flex-wrap:wrap}

/* Narrow screens. A dense table cannot simply shrink, so the low-value
   columns go and the row becomes a two-line record: the decision fields
   (score, role, company, pay) stay, source and posted date drop. Sorting and
   filtering still work on the full set underneath. */
@media (max-width:760px){
  body{padding:var(--s3) var(--s2) var(--s5);font-size:14px}
  .bar{gap:var(--s2) var(--s3)}
  .tools{margin-left:0;width:100%}
  input[type=search]{flex:1;width:auto}
  .scroll{border:none;background:transparent}
  thead{display:none}
  tbody tr{display:grid;grid-template-columns:44px 1fr;gap:0 var(--s2);
    height:auto;padding:10px var(--s2);background:var(--panel);
    border:1px solid var(--line);border-radius:4px;margin-bottom:6px}
  tbody tr.new{border-left:2px solid var(--green)}
  tbody tr.new td:first-child{box-shadow:none}
  td{padding:0;max-width:none;white-space:normal;overflow:visible}
  td.sc{grid-row:1/span 2;font-size:18px;align-self:start;text-align:left}
  td.title{font-size:14px;line-height:1.3}
  td.co,td.pay{display:inline;font-size:12px}
  td.co::after{content:" · ";color:var(--line)}
  td:nth-child(4),td:nth-child(6),td:nth-child(7){display:none}
}
}
"""

A_JS = r"""
const tb=document.querySelector('#t tbody'), all=[...tb.rows];
let q='',onlyNew=false,onlyPay=false;
function apply(){for(const r of all){const t=r.innerText.toLowerCase();
  r.style.display=(!q||t.includes(q))&&(!onlyNew||r.classList.contains('new'))&&(!onlyPay||r.dataset.p==='1')?'':'none';}}
document.getElementById('q').addEventListener('input',e=>{q=e.target.value.toLowerCase();apply()});
for(const [id,fn] of [['fnew',()=>onlyNew=!onlyNew],['fpay',()=>onlyPay=!onlyPay]]){
  const b=document.getElementById(id);b.onclick=()=>{fn();b.setAttribute('aria-pressed',b.getAttribute('aria-pressed')==='false');apply()}}
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k=+th.dataset.k, dir=th.dataset.dir==='desc'?'asc':'desc';
  document.querySelectorAll('th[data-k]').forEach(o=>o.removeAttribute('data-dir'));
  th.dataset.dir=dir;
  const val=r=>k===0?+r.dataset.s:r.cells[k].innerText.trim().toLowerCase();
  all.sort((a,b)=>{const x=val(a),y=val(b);return (x<y?-1:x>y?1:0)*(dir==='asc'?1:-1)});
  all.forEach(r=>tb.appendChild(r));});
document.getElementById('theme').onclick=e=>{const r=document.documentElement;
  const l=r.dataset.theme!=='light';r.dataset.theme=l?'light':'dark';e.target.textContent=l?'dark':'light'};
"""


def opt_a():
    trs = []
    for j in rows():
        isnew = j["uid"] in NEW
        pay = j["salary_label"]
        paid = j["salary"]["confirmed"]
        flags = [f for f in j["flags"] if "not screened" in f or "unconfirmed" not in f]
        warn = " ⚠" if any("not screened" in f for f in j["flags"]) else ""
        trs.append(
          f'<tr class="{"new" if isnew else ""}" data-s="{j["score"]}" data-p="{1 if paid else 0}">'
          f'<td class="sc num">{j["score"]:.0f}</td>'
          f'<td class="title"><a href="{H.escape(j["url"])}" target="_blank" rel="noopener">{H.escape(j["title"])}</a>'
          f'{" <span class=tagn>NEW</span>" if isnew else ""}<span class="warn">{warn}</span></td>'
          f'<td class="co">{H.escape(j["company"])}</td>'
          f'<td>{H.escape(j["location"] or "—")}</td>'
          f'<td class="pay {"yes" if paid else "no"} num">{H.escape(pay)}</td>'
          f'<td class="num">{H.escape(j["posted_at"] or "—")}</td>'
          f'<td class="co">{H.escape(j["platform"])}</td></tr>')
    paid_n = sum(1 for j in JOBS if j["salary"]["confirmed"])
    return f"""<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>job-radar — terminal</title>
<style>{A_CSS}</style></head><body><div class="wrap">
<div class="bar">
  <span class="brand">job&#8203;-radar</span>
  <span class="kv">sources <b>{META['sources_ok']}/{META['sources_total']}</b></span>
  <span class="kv">postings <b>{META['postings']:,}</b></span>
  <span class="kv">matching <b>{len(JOBS)}</b></span>
  <span class="kv">new <b>{len(NEW)}</b></span>
  <span class="kv">pay stated <b>{paid_n}</b></span>
  <div class="tools">
    <input type="search" id="q" placeholder="filter…" aria-label="Filter roles">
    <button class="t" id="fnew" aria-pressed="false">new only</button>
    <button class="t" id="fpay" aria-pressed="false">pay stated</button>
    <button class="t" id="theme">light</button>
  </div>
</div>
<div class="scroll"><table id="t">
<thead><tr>
<th class="num" data-k="0" data-dir="desc">Score</th><th data-k="1">Role</th><th data-k="2">Company</th>
<th data-k="3">Location</th><th class="num" data-k="4">Salary</th><th class="num" data-k="5">Posted</th><th data-k="6">Source</th>
</tr></thead><tbody>{''.join(trs)}</tbody></table></div>
<footer><span>⚠ = not screened against your dealbreakers (source gives no description)</span>
<span>Roles with stated pay below your floor are hidden. Unstated pay is shown, marked.</span></footer>
</div>
"""  + A_JS + """
</script></body></html>"""

Path(ROOT/"design"/"option-a-terminal.html").write_text(opt_a(), encoding="utf-8")
print("wrote design/option-a-terminal.html")


# ---------------------------------------------------------------- OPTION B
B_CSS = """
/* DESIGN PLAN — "The Brief"
   PALETTE  paper #fbfaf8 / card #fff / ink #16161a / muted #6d6a63
            accent #1a5c3a (deep forest — considered, not corporate blue)
            flag #9a5b1f (warning, a separate hue from the accent on purpose)
   TYPE     Fraunces-style serif display via Georgia fallback for the ranked
            headline; DM Sans / system sans for everything else. Real contrast
            between the two, not two similar sans faces.
   LAYOUT   Deliberately NOT a table. A ranked shortlist where each entry
            argues for itself: why it scored, what the catch is. Below it,
            everything else collapses to one dense line each.
   RISK     Fewer roles visible per screen, and it only works if the scoring
            is trustworthy — a bad rank is more damaging here than in a table
            because the design asks you to believe it. Mitigated by printing
            the reasons rather than just the number.
*/
@layer reset, tokens, base, components;
@layer reset { *,*::before,*::after{box-sizing:border-box;margin:0;padding:0} }
@layer tokens {
:root{
  --paper:#fbfaf8; --card:#fff; --ink:#16161a; --muted:#6d6a63;
  --line:#e6e2db; --accent:#1a5c3a; --flag:#9a5b1f; --pay:#1a5c3a;
  --s1:4px;--s2:8px;--s3:16px;--s4:24px;--s5:32px;--s6:48px;--s7:64px;
  --dur:200ms;--ease:cubic-bezier(.25,.46,.45,.94);
  --serif:"Fraunces",Georgia,"Iowan Old Style",serif;
  --sans:"DM Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --shadow:0 1px 2px rgba(20,18,14,.05);
}
:root[data-theme="dark"]{
  --paper:#14151a; --card:#1b1d24; --ink:#eceae5; --muted:#9a978f;
  --line:#2a2d36; --accent:#5fbf8d; --flag:#d0964a; --pay:#5fbf8d;
  --shadow:0 1px 2px rgba(0,0,0,.4);
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --paper:#14151a; --card:#1b1d24; --ink:#eceae5; --muted:#9a978f;
  --line:#2a2d36; --accent:#5fbf8d; --flag:#d0964a; --pay:#5fbf8d;
  --shadow:0 1px 2px rgba(0,0,0,.4); } }
@media (prefers-reduced-motion:reduce){:root{--dur:.01ms}}
}
@layer base {
body{background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased;
  padding:var(--s6) var(--s4) var(--s7)}
.wrap{max-width:800px;margin:0 auto}
a{color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:2px}
}
@layer components {
header{margin-bottom:var(--s6)}
h1{font-family:var(--serif);font-size:2.4375rem;line-height:1.05;letter-spacing:-.02em;font-weight:700}
.sub{color:var(--muted);margin-top:var(--s2);font-size:.9375rem}
.sub b{color:var(--ink);font-variant-numeric:tabular-nums;font-weight:600}
h2{font-family:var(--serif);font-size:1.25rem;font-weight:700;margin:var(--s6) 0 var(--s3);
  padding-bottom:var(--s2);border-bottom:1px solid var(--line)}
.lede{color:var(--muted);font-size:.875rem;margin:calc(var(--s3) * -1) 0 var(--s4)}
.pick{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:var(--s4);margin-bottom:var(--s3);box-shadow:var(--shadow);
  display:grid;grid-template-columns:auto 1fr;gap:var(--s3) var(--s4);
  transition:border-color var(--dur) var(--ease)}
.pick:hover{border-color:var(--accent)}
.rank{font-family:var(--serif);font-size:1.9375rem;line-height:1;color:var(--muted);
  font-variant-numeric:tabular-nums;min-width:1.6em}
.pick.is-new .rank{color:var(--accent)}
.role{font-size:1.25rem;font-weight:600;line-height:1.25;letter-spacing:-.01em}
.role a{text-decoration:none}
.role a:hover{text-decoration:underline;text-decoration-color:var(--accent);text-underline-offset:3px}
.org{color:var(--muted);font-size:.875rem;margin-top:2px}
.facts{display:flex;flex-wrap:wrap;gap:var(--s1) var(--s4);margin-top:var(--s3);
  font-size:.875rem;font-variant-numeric:tabular-nums}
.facts .k{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;display:block}
.facts .v{font-weight:600}
.facts .v.pay{color:var(--pay)}
.facts .v.unk{color:var(--muted);font-weight:400;font-style:italic}
.why{margin-top:var(--s3);font-size:.875rem;color:var(--muted);line-height:1.5}
.why b{color:var(--ink);font-weight:600}
.catch{margin-top:var(--s2);font-size:.875rem;color:var(--flag);
  padding-left:var(--s3);border-left:2px solid var(--flag)}
.badge{display:inline-block;font-size:.6875rem;letter-spacing:.09em;text-transform:uppercase;
  color:var(--accent);border:1px solid var(--accent);border-radius:999px;padding:1px 8px;
  vertical-align:middle;margin-left:var(--s2)}
ul.rest{list-style:none;border-top:1px solid var(--line)}
ul.rest li{display:grid;grid-template-columns:3.2em 1fr auto;gap:var(--s3);align-items:baseline;
  padding:9px var(--s2);border-bottom:1px solid var(--line);font-size:.875rem}
ul.rest li:hover{background:var(--card)}
ul.rest .s{color:var(--muted);font-variant-numeric:tabular-nums;font-weight:600}
ul.rest .n a{text-decoration:none}
ul.rest .n a:hover{text-decoration:underline}
ul.rest .n small{color:var(--muted);margin-left:var(--s2)}
ul.rest .p{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
ul.rest .p.yes{color:var(--pay)}
footer{margin-top:var(--s6);padding-top:var(--s3);border-top:1px solid var(--line);
  color:var(--muted);font-size:.8125rem}
}
"""


def opt_b():
    top, rest = JOBS[:8], JOBS[8:]
    picks = []
    for i, j in enumerate(top, 1):
        paid = j["salary"]["confirmed"]
        reasons = [r for r in j["reasons"] if "salary" not in r.lower()]
        why = ", ".join(reasons[:3]) or "matched your filters"
        catches = [f for f in j["flags"] if "unconfirmed" not in f]
        isnew = j["uid"] in NEW
        picks.append(f"""<article class="pick{' is-new' if isnew else ''}">
  <div class="rank">{i}</div>
  <div>
    <div class="role"><a href="{H.escape(j['url'])}" target="_blank" rel="noopener">{H.escape(j['title'])}</a>
      {'<span class="badge">new</span>' if isnew else ''}</div>
    <div class="org">{H.escape(j['company'])}</div>
    <div class="facts">
      <div><span class="k">Pay</span><span class="v {'pay' if paid else 'unk'}">{H.escape(j['salary_label'])}</span></div>
      <div><span class="k">Where</span><span class="v">{H.escape((j['location'] or 'not stated')[:44])}</span></div>
      <div><span class="k">Posted</span><span class="v">{H.escape(j['posted_at'] or '—')}</span></div>
      <div><span class="k">Fit</span><span class="v">{j['score']:.0f}<span style="color:var(--muted);font-weight:400">/100</span></span></div>
    </div>
    <p class="why">Scored on <b>{H.escape(why)}</b>.</p>
    {''.join(f'<p class="catch">{H.escape(c)}</p>' for c in catches[:2])}
  </div>
</article>""")

    lis = []
    for j in rest:
        paid = j["salary"]["confirmed"]
        lis.append(f'<li><span class="s">{j["score"]:.0f}</span>'
                   f'<span class="n"><a href="{H.escape(j["url"])}" target="_blank" rel="noopener">{H.escape(j["title"])}</a>'
                   f'<small>{H.escape(j["company"])}</small></span>'
                   f'<span class="p {"yes" if paid else ""}">{H.escape(j["salary_label"])}</span></li>')

    paid_n = sum(1 for j in JOBS if j["salary"]["confirmed"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>job-radar — the brief</title>
<style>{B_CSS}</style></head><body><div class="wrap">
<header>
  <h1>Eight worth opening</h1>
  <p class="sub">From <b>{META['postings']:,}</b> postings across <b>{META['sources_ok']}</b> employer boards ·
     <b>{len(JOBS)}</b> cleared your filters · <b>{len(NEW)}</b> new since the last run ·
     <b>{paid_n}</b> published a salary</p>
</header>
<h2>The shortlist</h2>
<p class="lede">Ranked by fit. Every score shows its working, and anything the tool could not check is called out rather than hidden.</p>
{''.join(picks)}
<h2>Everything else that cleared your filters ({len(rest)})</h2>
<p class="lede">Same rules, lower rank. Scan or ignore.</p>
<ul class="rest">{''.join(lis)}</ul>
<footer>Roles with a stated salary below your floor are hidden. Roles with no stated salary are shown and marked, because most employers publish nothing.</footer>
</div></body></html>"""


Path(ROOT/"design"/"option-b-brief.html").write_text(opt_b(), encoding="utf-8")
print("wrote design/option-b-brief.html")
