"""Interactive dashboard, rendered from the database.

Shares the CSS and the layout with the static renderer so the two views of the
same data cannot drift apart visually. The difference is that every row here
carries actions, and those actions write back.
"""

from __future__ import annotations

import html as _h
import json
from urllib.parse import quote
from collections import Counter
from datetime import datetime

from .. import store
from .html import _CSS, _cap_location, _SECTORS, _MODES

_EXTRA_CSS = """
/* Actions. Kept quiet: the row is the content, these are what you do to it. */
.acts{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:var(--s2);margin-top:var(--s3)}
.acts button,.acts a.btn{border:1px solid var(--line);background:var(--surface);
  color:var(--muted);font:inherit;font-size:.8125rem;font-weight:500;
  letter-spacing:-.008em;padding:6px 12px;border-radius:var(--r-md);cursor:pointer;
  text-decoration:none;display:inline-flex;align-items:center;gap:6px;
  transition:color var(--dur) var(--ease),border-color var(--dur) var(--ease),
             background var(--dur) var(--ease)}
.acts button:hover,.acts a.btn:hover{color:var(--ink);border-color:var(--muted)}
.acts button.primary{color:var(--accent);border-color:var(--accent)}
.acts button.primary:hover{background:var(--accent);color:var(--surface)}
.acts button:disabled{opacity:.4;cursor:not-allowed}
.acts select{border:1px solid var(--line);background:var(--surface);color:var(--muted);
  font:inherit;font-size:.8125rem;padding:6px 10px;border-radius:var(--r-md);cursor:pointer}
.acts select:hover{color:var(--ink)}
.rownote{grid-column:1/-1;font-size:.8125rem;color:var(--muted);margin-top:var(--s2);
  font-style:italic}
.acts button:disabled:hover{color:var(--muted);border-color:var(--line)}
.acts button.busy{color:var(--accent);border-color:var(--accent)}
.acts button.busy::after{content:"";width:9px;height:9px;border-radius:50%;
  border:1.5px solid currentColor;border-top-color:transparent;
  animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.acts button.busy::after{animation:none}}
.row.settled{opacity:.45}
.row.settled .role{text-decoration:line-through;text-decoration-thickness:1px}
.docs{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:var(--s2);margin-top:var(--s2);
  font-size:.8125rem}
.docs a{color:var(--accent);text-decoration:none;border-bottom:1px solid transparent}
.docs a:hover{border-bottom-color:var(--accent)}
.docs .rating{color:var(--pay);font-weight:600;font-variant-numeric:tabular-nums}
.docs .gatefail{color:var(--flag)}
.err{grid-column:1/-1;font-size:.8125rem;color:var(--flag);margin-top:var(--s2)}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);
  background:var(--ink);color:var(--surface);padding:10px 18px;border-radius:var(--r-pill);
  font-size:.875rem;box-shadow:var(--shadow-up);opacity:0;pointer-events:none;
  transition:opacity var(--dur) var(--ease);z-index:50;max-width:90vw;text-align:center}
.toast.show{opacity:1}
"""

_JS = r"""
const $=s=>document.querySelector(s), toast=$('#toast');
let f='all', secs=new Set(), modes=new Set(), country='', city='';

function say(msg,ms=3200){toast.textContent=msg;toast.classList.add('show');
  clearTimeout(say._t);say._t=setTimeout(()=>toast.classList.remove('show'),ms);}

const SETTLED=new Set(['rejected','withdrawn','skipped','closed']);
function apply(){let n=0;
  for(const r of document.querySelectorAll('.row')){
    const st=r.dataset.status;
    const viewOk = f==='all' || (f==='open' && !SETTLED.has(st)) ||
                   (f==='pay' && r.dataset.pay==='1');
    const ok = viewOk
      && (secs.size===0  || secs.has(r.dataset.sector))
      && (modes.size===0 || modes.has(r.dataset.mode))
      && (!country || r.dataset.country===country)
      && (!city    || r.dataset.city===city);
    r.hidden=!ok; if(ok)n++;}
  $('#empty').hidden=n>0; $('#list').hidden=n===0;}

document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.seg button').forEach(o=>o.setAttribute('aria-selected','false'));
  b.setAttribute('aria-selected','true'); f=b.dataset.f; apply();});
document.querySelectorAll('.chips button').forEach(b=>b.onclick=()=>{
  const on=b.getAttribute('aria-pressed')==='true';
  b.setAttribute('aria-pressed', on?'false':'true');
  const set=b.dataset.sec?secs:modes, key=b.dataset.sec||b.dataset.mode;
  on?set.delete(key):set.add(key); apply();});
$('#fcountry').onchange=e=>{country=e.target.value;apply()};
$('#fcity').onchange=e=>{city=e.target.value;apply()};

async function post(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  return {ok:r.ok, data:await r.json().catch(()=>({}))};}

document.addEventListener('click', async e=>{
  // Document links reveal the file in Finder. Without this they navigate the
  // tab to a raw JSON body and you lose the dashboard.
  const doc=e.target.closest('.docs a');
  if(doc){ e.preventDefault();
    const r=await fetch(doc.getAttribute('href'));
    const d=await r.json().catch(()=>({}));
    say(d.ok?'Revealed in Finder':(d.error||'could not open it'));
    return;}

  const row=e.target.closest('.row'); if(!row) return;
  const uid=row.dataset.uid;

  // Scoped to the button. The row wrapper also carries data-status, for
  // filtering, so an unscoped closest() walked up from every button and hit
  // the row first: Screen, CV, Cover letter and Apply all silently posted
  // status "new" and returned before reaching their own branch. Skip worked
  // only because its own button carries the attribute, so closest stopped
  // there -- which is why testing Skip alone said the buttons were fine.
  const st=e.target.closest('button[data-status]');
  if(st){ const status=st.dataset.status;
    const {ok,data}=await post('/api/status',{uid,status});
    if(!ok){say(data.error||'could not save');return}
    row.dataset.status=status;
    row.classList.toggle('settled', SETTLED.has(status));
    say(status==='skipped'?'Skipped. It will not come back.':'Marked '+status);
    apply(); return;}

  if(e.target.closest('[data-apply]')){
    await post('/api/status',{uid,status:'applied'});
    row.dataset.status='applied'; say('Marked applied, opening the job board');
    return;}

  if(e.target.closest('[data-note]')){
    const cur=row.querySelector('.rownote');
    const note=prompt('Note for this role:', cur?cur.textContent:'');
    if(note===null) return;
    const {ok,data}=await post('/api/status',
      {uid,status:row.dataset.status,note:note});
    if(!ok){say(data.error||'could not save');return}
    say('Note saved'); location.reload(); return;}

  const gen=e.target.closest('[data-gen]');
  if(gen && !gen.disabled){
    const kind=gen.dataset.gen;
    const {ok,data}=await post('/api/generate',{uid,kind});
    const err=row.querySelector('.err');
    if(!ok){ err.hidden=false; err.textContent=data.error||'could not start';
             say(data.error||'could not start',5000); return;}
    err.hidden=true;
    gen.classList.add('busy'); gen.disabled=true;
    say(kind==='screen'?'Screening. Takes a few seconds.'
       :'Drafting. This takes a few minutes; the row updates itself.');
    poll();}
});

let polling=null;
async function poll(){
  if(polling) return;
  polling=setInterval(async ()=>{
    const r=await fetch('/api/jobs'); if(!r.ok) return;
    const d=await r.json();
    const busy=new Set(d.jobs.filter(j=>j.state==='pending'||j.state==='running')
                            .map(j=>j.uid+':'+j.kind));
    let anyBusy=false;
    for(const row of document.querySelectorAll('.row')){
      const uid=row.dataset.uid;
      row.querySelectorAll('[data-gen]').forEach(b=>{
        const on=busy.has(uid+':'+b.dataset.gen);
        if(on) anyBusy=true;
        // toggle() has already removed the class by the time we test it, so
      // asking whether it is still busy always said no and the button stayed
      // disabled for ever once a job finished.
      const was=b.classList.contains('busy');
      b.classList.toggle('busy',on);
      if(was && !on) b.disabled=false;});
    }
    const failed=d.jobs.filter(j=>j.state==='failed');
    for(const j of failed){
      const row=document.querySelector(`.row[data-uid="${j.uid}"]`);
      if(row){const e=row.querySelector('.err');e.hidden=false;
              e.textContent='Generation failed: '+(j.error||'unknown');}}
    const done=d.jobs.some(j=>j.state==='done');
    if(done){ clearInterval(polling); polling=null;
              say('Done. Reloading to show the documents.');
              setTimeout(()=>location.reload(),900); return;}
    if(!anyBusy && d.jobs.length===0){clearInterval(polling);polling=null;}
  },2500);}

if(document.querySelector('.acts button.busy')) poll();
"""


def _rows(con):
    """The last scan's results, plus every role you have acted on.

    Filtering on the last scan alone made applied and interviewing roles
    disappear the moment a posting closed, a source was rate-limited, or a
    `--limit` run happened -- taking their status and their generated
    documents with them, with no other view of them anywhere.
    """
    return con.execute("""
        SELECT r.*, COALESCE(s.status,'new') AS status, COALESCE(s.note,'') AS note
        FROM roles r LEFT JOIN role_state s ON s.uid = r.uid
        WHERE r.last_seen = (SELECT MAX(last_seen) FROM roles)
           OR COALESCE(s.status,'new') <> 'new'
           OR r.uid IN (SELECT DISTINCT uid FROM artifacts)
        ORDER BY r.score DESC, r.company COLLATE NOCASE
    """).fetchall()


def render(con) -> str:
    rows = _rows(con)
    arts = {}
    for a in con.execute("SELECT * FROM artifacts ORDER BY id"):
        arts.setdefault(a["uid"], {})[a["kind"]] = dict(a)
    live = {j["uid"]: dict(j) for j in con.execute(
        "SELECT * FROM jobs WHERE state IN ('pending','running')")}

    total = len(rows)
    paid = sum(1 for r in rows if r["salary_confirmed"])
    settled = sum(1 for r in rows if r["status"] in store.SETTLED)

    sec = Counter((r["sector"] or "other") for r in rows)
    chips = "".join(
        f'<button aria-pressed="false" data-sec="{_h.escape(s, quote=True)}">'
        f'{_h.escape(_SECTORS.get(s, s.title()))}<span class="n">{n}</span></button>'
        for s, n in sec.most_common())
    mc = Counter((r["work_mode"] or "unstated") for r in rows)
    modes = "".join(
        f'<button aria-pressed="false" data-mode="{m}">{_MODES[m]}'
        f'<span class="n">{mc[m]}</span></button>'
        for m in ("remote", "hybrid", "office", "unstated") if mc.get(m))
    cc = Counter((r["country"] or "unknown") for r in rows)
    countries = '<option value="">All countries</option>' + "".join(
        f'<option value="{_h.escape(c, quote=True)}">{_h.escape(c)} ({n})</option>'
        for c, n in cc.most_common())
    cty = Counter(r["city"] for r in rows if r["city"])
    cities = '<option value="">All cities</option>' + "".join(
        f'<option value="{_h.escape(c, quote=True)}">{_h.escape(c)} ({n})</option>'
        for c, n in sorted(cty.items(), key=lambda x: (-x[1], x[0])))

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job radar</title><style>{_CSS}{_EXTRA_CSS}</style></head><body><div class="wrap">
<header>
  <h1>{total} roles worth a look</h1>
  <p class="sub"><b>{paid}</b> with a salary &middot; <b>{settled}</b> settled &middot;
     live from the database, so anything you click sticks</p>
</header>
<div class="seg" role="tablist" aria-label="Filter roles">
  <button role="tab" aria-selected="true"  data-f="all">All</button>
  <button role="tab" aria-selected="false" data-f="open">Open</button>
  <button role="tab" aria-selected="false" data-f="pay">Salary shown</button>
</div>
<div class="chips" role="group" aria-label="Filter by sector">{chips}</div>
<div class="chips" role="group" aria-label="Filter by working pattern">{modes}</div>
<div class="selects">
  <label><span>Country</span><select id="fcountry" aria-label="Country">{countries}</select></label>
  <label><span>City</span><select id="fcity" aria-label="City">{cities}</select></label>
</div>
<div class="list" id="list">{"".join(_row(r, arts.get(r["uid"], {}), live.get(r["uid"])) for r in rows)}</div>
<p class="empty" id="empty" hidden>Nothing matches those filters.</p>
<footer>Roles with a stated salary below your floor are hidden. Nothing is generated
unless you click for it.</footer>
</div><div class="toast" id="toast"></div>
<script>{_JS}</script></body></html>"""


def _row(r, arts, job) -> str:
    settled = r["status"] in store.SETTLED
    paid = bool(r["salary_confirmed"])
    meta = " \u00b7 ".join(x for x in [r["company"], _cap_location(r["location"] or "")] if x)
    status_pill = (f'<span class="status {_h.escape(r["status"], quote=True)}">'
                   f'{_h.escape(r["status"])}</span>' if r["status"] != "new" else "")

    docs = []
    if "cv" in arts:
        a = arts["cv"]
        rating = (f'<span class="rating">{a["rating"]:.0f}/100</span>'
                  if a["rating"] else "")
        fails = [k for k, v in json.loads(a["gates"] or "{}").items() if v is False]
        gate = (f'<span class="gatefail">{len(fails)} gate(s) failed</span>'
                if fails else "")
        docs.append(f'<a href="/open?path={_h.escape(quote(str(a["path"])), quote=True)}">CV</a> {rating} {gate}')
    if "cover_letter" in arts:
        a = arts["cover_letter"]
        ov = json.loads(a["gates"] or "{}").get("no_overlap_with_cv")
        # None means the check never ran, which is not the same as passing.
        warn = ('' if ov is True else
                '<span class="gatefail">'
                + ('overlaps the CV' if ov is False else 'overlap not checked')
                + '</span>')
        docs.append(f'<a href="/open?path={_h.escape(quote(str(a["path"])), quote=True)}">Cover letter</a> {warn}')
    if "screen" in arts:
        v = arts["screen"]["summary"] or "screened"
        docs.append(f'<a href="/open?path={_h.escape(quote(str(arts["screen"]["path"])), quote=True)}">Screening</a> <span class="rating">{_h.escape(v)}</span>')

    # The static page warns when a source gives no description; the served one
    # did not, and that is the page with the money buttons on it.
    notes = [f for f in json.loads(r["flags"] or "[]")
             if "not screened" in f or "listing only" in f]
    busy = job["kind"] if job else ""
    has_cv = "cv" in arts

    def b(kind, label, cls=""):
        on = busy == kind
        return (f'<button class="{cls}{" busy" if on else ""}" data-gen="{kind}" '
                f'{"disabled" if on else ""}>{label}</button>')

    letter_btn = (b("cover_letter", "Cover letter") if has_cv else
                  '<button disabled title="Draft the CV first: the letter is '
                  'checked against it for repeated phrasing">Cover letter</button>')

    return (
        f'<div class="row{" settled" if settled else ""}" data-uid="{_h.escape(r["uid"], quote=True)}" '
        f'data-status="{_h.escape(r["status"], quote=True)}" '
        f'data-pay="{1 if paid else 0}" '
        f'data-sector="{_h.escape(r["sector"] or "other", quote=True)}" '
        f'data-mode="{_h.escape(r["work_mode"] or "unstated", quote=True)}" '
        f'data-country="{_h.escape(r["country"] or "unknown", quote=True)}" '
        f'data-city="{_h.escape(r["city"] or "", quote=True)}">'
        f'<div><div class="role">'
        f'<a href="{_h.escape(r["url"])}" target="_blank" rel="noopener">{_h.escape(r["title"])}</a>'
        f'{status_pill}</div>'
        f'<div class="meta">{_h.escape(meta)}</div></div>'
        f'<div class="right"><span class="pay{"" if paid else " unk"}">'
        f'{_h.escape(r["salary_label"] or "unconfirmed salary")}</span></div>'
        + (f'<div class="docs">{" &middot; ".join(docs)}</div>' if docs else "")
        + (f'<div class="note">{_h.escape(notes[0])}</div>' if notes else "")
        + (f'<div class="rownote">{_h.escape(r["note"])}</div>' if r["note"] else "")
        + '<div class="acts">'
        + b("screen", "Screen", "primary")
        + b("cv", "CV")
        + letter_btn
        + f'<a class="btn" href="{_h.escape(r["url"])}" target="_blank" rel="noopener" '
          f'data-apply="1">Apply</a>'
        + '<button data-status="skipped">Skip</button>'
        + ('<button data-status="interested">Unskip</button>' if settled else '')
        # The dashboard offered two of the ten statuses and no note, while the
        # CLI had all ten and a note, so the browser could not record an
        # interview date -- the thing a tracker is for.
        + '<select class="setstatus" aria-label="Set status">'
        + '<option value="">Status\u2026</option>'
        + "".join(
            f'<option value="{s}"{" selected" if s == r["status"] else ""}>{s}</option>'
            for s in store.STATUSES)
        + '</select>'
        + '<button data-note="1" title="Add or edit a note">Note</button>'
        + '</div>'
        + (f'<div class="err" hidden></div>')
        + '</div>')
