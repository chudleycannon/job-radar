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
from .favicon import link_tag as _favicon_tag, mark as _favicon_mark
from .markdown import to_html as _md

_FAVICON = _favicon_tag()
_MARK = _favicon_mark()

from .html import _CSS, _cap_location, _SECTORS, _MODES, safe_url

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
/* A facet whose count has fallen to nothing under the current tab. Left
   clickable, because one of them may be the filter you are already in. */
.chips button.none{opacity:.45}
"""

_JS = r"""
const $=s=>document.querySelector(s), toast=$('#toast');
// Opens on Open, not All. All includes skipped and rejected, and they sort by
// score like everything else, so a skipped role you already dismissed sat at
// the top of the board every time you refreshed.
let f='open', secs=new Set(), modes=new Set(), country='', city='';

function say(msg,ms=3200){toast.textContent=msg;toast.classList.add('show');
  clearTimeout(say._t);say._t=setTimeout(()=>toast.classList.remove('show'),ms);}

const SETTLED=new Set(['rejected','withdrawn','skipped','closed']);
// Applications with something still owed on them, either way.
const IN_FLIGHT=new Set(['applied','submitted','interviewing','offer']);
// Applications that ended. Not "skipped": you never applied to those.
const CLOSED_OUT=new Set(['rejected','withdrawn','closed']);
// The number on a facet has to be the number that facet will show you.
//
// These were counted once, in Python, over every row in the database, while
// the page opens on Open and Open hides everything settled. So a board with
// one skipped public-sector role rendered a chip reading "Public sector 1",
// and clicking it emptied the list and said "Nothing matches those filters".
// Same for the country and city menus. Counting here, against the rows the
// current tab admits, means the chip cannot promise a role the tab hides.
//
// Each dimension is counted with its own filter left off, because that is
// what clicking would do: sector chips are an OR within sectors, so with Tech
// selected the number on Finance is what adding Finance would bring in.
function paintCounts(sec,mode,ctry,city){
  for(const b of document.querySelectorAll('.chips button')){
    const k=b.dataset.sec||b.dataset.mode;
    const n=(b.dataset.sec?sec:mode)[k]||0;
    const el=b.querySelector('.n'); if(el) el.textContent=n;
    b.classList.toggle('none',n===0);}
  for(const [q,m] of [['#fcountry',ctry],['#fcity',city]])
    for(const o of document.querySelectorAll(q+' option')){
      if(!o.value) continue;
      o.textContent=(o.dataset.label||o.value)+' ('+(m[o.value]||0)+')';}}

function apply(){let n=0;
  const cSec={},cMode={},cCountry={},cCity={};
  const bump=(m,k)=>{m[k]=(m[k]||0)+1};
  for(const r of document.querySelectorAll('.row')){
    const st=r.dataset.status;
    const viewOk = f==='all' || (f==='open' && !SETTLED.has(st)) ||
                   (f==='pay' && r.dataset.pay==='1') ||
                   (f==='new' && r.dataset.new==='1') ||
                   (f==='live' && IN_FLIGHT.has(st)) ||
                   (f==='closed' && CLOSED_OUT.has(st)) ||
                   (f==='fit' && (+r.dataset.fit)>=70);
    const okSec  = secs.size===0  || secs.has(r.dataset.sector);
    const okMode = modes.size===0 || modes.has(r.dataset.mode);
    const okCtry = !country || r.dataset.country===country;
    const okCity = !city    || r.dataset.city===city;
    if(viewOk&&okMode&&okCtry&&okCity) bump(cSec,r.dataset.sector);
    if(viewOk&&okSec &&okCtry&&okCity) bump(cMode,r.dataset.mode);
    if(viewOk&&okSec &&okMode&&okCity) bump(cCountry,r.dataset.country);
    if(viewOk&&okSec &&okMode&&okCtry) bump(cCity,r.dataset.city);
    const ok = viewOk && okSec && okMode && okCtry && okCity;
    r.hidden=!ok; if(ok)n++;}
  paintCounts(cSec,cMode,cCountry,cCity);
  $('#empty').hidden=n>0; $('#list').hidden=n===0;}

document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.seg button').forEach(o=>o.setAttribute('aria-selected','false'));
  b.setAttribute('aria-selected','true'); f=b.dataset.f;
  // Choosing the Best fit tab implies you want fit order, but it no longer
  // owns it: the sort select below stays in charge everywhere, so you can
  // read New in fit order, which is the pairing you actually want.
  if(f==='fit'){const sel=$('#fsort'); if(sel) sel.value='fit';}
  resort(); apply();});
// Rank order, used everywhere except Best fit. Applications you are in come
// first because they are the ones with a deadline on them; things you settled
// go last because you have already decided. Score breaks the tie in between.
function tier(r){
  const st=r.dataset.status;
  if(IN_FLIGHT.has(st)) return 0;
  if(SETTLED.has(st)) return 2;
  return 1;}
const byRank=(a,b)=>tier(a)-tier(b)||(+b.dataset.score)-(+a.dataset.score);
// An unranked role stores fit -1. Sorting on that raw value buries every role
// you have not paid to rank yet below a role scored 0, which reads as "these
// are bad" rather than "these are unknown". They sort after the scored ones
// but ahead of a genuine zero, and keep their filter-score order among
// themselves so the list is still useful before you rank anything.
//
// This mapped -1 to -0.5, which is still below zero, so the fix did nothing:
// an unranked Head of Engineering with the second-highest filter score on the
// board sorted underneath a role that had been read and scored 0 with the
// reason "no people leadership at all". Fit scores are whole numbers, so any
// value strictly between 0 and 1 puts unknown exactly where the paragraph
// above says it belongs.
const fitOf=(r)=>{const v=+r.dataset.fit; return v<0 ? 0.5 : v;};
const byFit=(a,b)=>tier(a)-tier(b)
  ||fitOf(b)-fitOf(a)||(+b.dataset.score)-(+a.dataset.score);
// Salary, in the order the numbers can actually be trusted.
//
// Two things were wrong with sorting on one number. data-payfloor used to
// fall back to the top of the range when a posting stated no bottom, so
// "up to 175,000" claimed a floor of 175,000 and led the list ahead of a
// role guaranteeing 150,000-180,000 -- the vaguer advert winning on a figure
// nobody had promised. And the comparison ran across currencies, so 200,000
// of one thing outranked 175,000 of another purely on the digits, which is
// the exact guess `salary.clears_floor` refuses to make ("cross-currency
// comparison is refused rather than guessed at"). Those rows are already
// flagged "not compared" against the floor; ranking them by it anyway
// contradicted the flag on the same row.
//
// So: roles are grouped by how much their figure is worth relying on, and
// only compared inside a group. On a board that is all one currency, which
// is the normal case, this changes nothing.
//   3  a stated bottom of range, in the currency most of the board uses
//   2  a stated bottom of range, in some other currency
//   1  a stated top but no bottom, so no floor at all
//   0  no stated pay
function payGroup(r){
  const floor=+r.dataset.payfloor||0;
  if(floor) return (!HOME_CUR || (r.dataset.paycur||'')===HOME_CUR) ? 3 : 2;
  return (+r.dataset.paytop||0) ? 1 : 0;}
const bySalary=(a,b)=>tier(a)-tier(b)
  ||payGroup(b)-payGroup(a)
  ||(+b.dataset.payfloor||0)-(+a.dataset.payfloor||0)
  ||(+b.dataset.paytop||0)-(+a.dataset.paytop||0)
  ||(+b.dataset.score)-(+a.dataset.score);
const byNew=(a,b)=>tier(a)-tier(b)
  ||(b.dataset.seen||'').localeCompare(a.dataset.seen||'')
  ||(+b.dataset.score)-(+a.dataset.score);
const SORTS={rank:byRank, fit:byFit, salary:bySalary, new:byNew};
function resort(){
  const sel=$('#fsort'), list=$('#list');
  const cmp=SORTS[sel && sel.value] || byRank;
  [...list.querySelectorAll('.row')].sort(cmp).forEach(r=>list.appendChild(r));}

// Sort and filter once at load. The filter previously only ran on a click,
// which was invisible while the default view was All and everything showed
// anyway; the moment the default became Open, every settled role was still on
// screen until you touched a tab.
(function(){
  const sel=$('#fsort');
  if(sel) sel.onchange=()=>{resort(); apply();};
  resort(); apply();})();

document.querySelectorAll('.chips button').forEach(b=>b.onclick=()=>{
  const on=b.getAttribute('aria-pressed')==='true';
  b.setAttribute('aria-pressed', on?'false':'true');
  const set=b.dataset.sec?secs:modes, key=b.dataset.sec||b.dataset.mode;
  on?set.delete(key):set.add(key); apply();});
// Ranking spends tokens, so the click shows the cost and waits for a yes.
// Everything else that spends in this tool works the same way.
const rankBtn=$('#rank'), rankInfo=$('#rankinfo');
async function rankState(){
  const r=await fetch('/api/rank'); if(!r.ok) return null;
  return r.json();}
const stopBtn=$('#rankstop');
function mmss(t){const m=Math.floor(t/60),s=t%60;
  return m?`${m}m ${String(s).padStart(2,'0')}s`:`${s}s`;}

function paintRank(d, extra=0){
  if(d.state!=='running') return;
  const batch=Math.floor(d.done/d.batch_size)+1;
  const of=Math.ceil(d.total/d.batch_size);
  rankInfo.textContent = d.stopping
    ? `stopping after this batch (${d.done}/${d.total} done)`
    : `${d.done}/${d.total} scored · batch ${Math.min(batch,of)} of ${of} `+
      `in flight · ${mmss(d.elapsed+extra)}`;}

async function refreshRankInfo(){
  const d=await rankState(); if(!d) return d;
  _rank=d; _tick=0;
  if(d.state==='running'){
    rankBtn.classList.add('busy'); rankBtn.disabled=true;
    stopBtn.hidden=false;
    // The counter only moves once per batch, roughly two minutes apart. With
    // nothing else changing, a run in progress looked identical to one that
    // had hung, so say which batch is in flight and how long it has been.
    const batch=Math.floor(d.done/d.batch_size)+1;
    const of=Math.ceil(d.total/d.batch_size);
    rankInfo.textContent = d.stopping
      ? `stopping after this batch (${d.done}/${d.total} done)`
      : `${d.done}/${d.total} scored · batch ${Math.min(batch,of)} of ${of} `+
        `in flight · ${mmss(d.elapsed)}`;
  }else{
    rankBtn.classList.remove('busy'); rankBtn.disabled=false;
    stopBtn.hidden=true;
    // Say what is actually true. "0 unranked" was rendered as "everything is
    // ranked", while a quarter of the board carried no score at all because
    // those postings have no description to judge fit against.
    const bits=[];
    if(d.pending) bits.push(`${d.pending} to rank`);
    if(d.scored) bits.push(`${d.scored} ranked`);
    if(d.unrankable) bits.push(`${d.unrankable} listing-only, nothing to rank`);
    rankInfo.textContent = bits.join(' · ');
    // A run that stopped because the account ran out looks exactly like one
    // that finished, unless it says so.
    if(d.error) say(d.error, 12000);}
  return d;}
refreshRankInfo();

stopBtn.onclick=async ()=>{
  stopBtn.disabled=true;
  const {ok,data}=await post('/api/rank/stop',{});
  say(ok ? (data.message||'stopping') : (data.error||'could not stop'),6000);
  stopBtn.disabled=false; refreshRankInfo();};

// Poll for the real numbers, and tick the clock locally in between, so the
// line is visibly moving every second rather than freezing between polls.
let _rank=null, _tick=0;
setInterval(async ()=>{
  const d=await rankState(); _rank=d; _tick=0;
  if(d) paintRank(d);
}, 3000);
setInterval(()=>{ if(_rank && _rank.state==='running'){ _tick++; paintRank(_rank, _tick);} }, 1000);

rankBtn.onclick=async ()=>{
  const d=await rankState(); if(!d) return;
  if(!d.pending){ say('Everything with a description is already ranked'); return; }
  const ok=confirm(
    `Rank ${d.pending} roles against your CV?\n\n`+
    `About ${d.tokens.toLocaleString()} input tokens, in ${d.batches} call(s).\n`+
    `Screening them one at a time would be about `+
    `${d.screen_tokens.toLocaleString()}.`);
  if(!ok) return;
  const {ok:started,data}=await post('/api/rank',{});
  if(!started){ say(data.error||'could not start'); return; }
  say('Ranking started. This takes a couple of minutes.');
  const t=setInterval(async ()=>{
    const s=await refreshRankInfo();
    if(s && s.state!=='running'){ clearInterval(t);
      say('Ranked. Reloading.'); setTimeout(()=>location.reload(),700);}
  },3000);
  refreshRankInfo();};

// Only rendered when the list is behind, so it is a fix offered at the moment
// the problem is visible rather than a control sitting there for ever.
const pullBtn=$('#pull');
if(pullBtn) pullBtn.onclick=async ()=>{
  pullBtn.disabled=true; pullBtn.textContent='Pulling...';
  const {ok,data}=await post('/api/pull',{});
  if(!ok){ pullBtn.disabled=false; pullBtn.textContent='Pull';
           say(data.error||'could not pull',7000); return; }
  say(data.message||'pulled');
  if(data.changed){ pullBtn.textContent='Pulled';
    // The scan reads the source list at startup, so the new boards only
    // arrive on the next scan. Say that rather than implying it is done.
    setTimeout(()=>say('Run a scan to read the boards that just arrived',6000),1200);
  } else { pullBtn.disabled=false; pullBtn.textContent='Pull'; }
};

$('#fcountry').onchange=e=>{country=e.target.value;apply()};
$('#fcity').onchange=e=>{city=e.target.value;apply()};

async function post(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  return {ok:r.ok, data:await r.json().catch(()=>({}))};}

// One place that moves a row to a status, so the page and the database
// cannot end up saying different things.
//
// Every handler used to do its own subset. Skip set row.dataset.status and
// nothing else: no pill was created, and the status select still read "new",
// so a row you had just skipped looked untouched while the database had it
// settled. The pill is only in the markup for a role that is not "new", so
// it has to be made on the way up and taken away on the way back down.
function mark(row,status){
  row.dataset.status=status;
  row.classList.toggle('settled', SETTLED.has(status));
  let pill=row.querySelector('.status');
  if(status==='new'){ if(pill) pill.remove(); }
  else{
    if(!pill){ pill=document.createElement('span');
      const head=row.querySelector('.role'); if(head) head.appendChild(pill); }
    if(pill){ pill.textContent=status; pill.className='status '+status; }}
  const sel=row.querySelector('select.setstatus'); if(sel) sel.value=status;}

// The status select was rendered with all ten statuses and never wired to
// anything, so picking "rejected" looked like it worked, changed nothing, and
// reverted on the next refresh. It is a change event, not a click, which is
// why the click handler below never saw it.
document.addEventListener('change', async e=>{
  const sel=e.target.closest('select.setstatus'); if(!sel) return;
  const row=sel.closest('.row'); if(!row) return;
  const status=sel.value; if(!status) return;
  const prev=row.dataset.status;
  const {ok,data}=await post('/api/status',{uid:row.dataset.uid,status});
  if(!ok){ sel.value=prev||''; say(data.error||'could not save'); return; }
  mark(row,status);
  say('Marked '+status+(SETTLED.has(status)?'. It will not come back.':''));
  apply();
});

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
    mark(row,status);
    say(status==='skipped'?'Skipped. It will not come back.':'Marked '+status);
    apply(); return;}

  if(e.target.closest('[data-apply]')){
    // Apply is also just the link to the advert, and it posted "applied"
    // unconditionally. So re-opening the board for a role you were already
    // interviewing for traded the interview for an application, in the
    // database, with the pill on screen still reading "interviewing" so
    // there was nothing to notice. That is the same trade store.PROGRESS
    // exists to stop a merge making, made by the button pressed most often.
    // It only ever moves a role forward now; the link opens either way.
    const cur=row.dataset.status||'new';
    if((PROGRESS[cur]||0) >= PROGRESS.applied){
      say('Opening the job board. Still marked '+cur+'.'); return;}
    const {ok,data}=await post('/api/status',{uid,status:'applied'});
    if(!ok){ say(data.error||'could not save'); return; }
    mark(row,'applied'); say('Marked applied, opening the job board');
    apply(); return;}

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
    // Show the newest job per role, not every failure ever returned. A retry
    // that is already running was being covered by the error from the attempt
    // it replaced, so the row said "generation failed" while the spinner span.
    // Nothing cleared the message either, so once shown it stayed until the
    // page was reloaded.
    const newest=new Map();
    for(const j of d.jobs){
      const prev=newest.get(j.uid);
      if(!prev || j.id>prev.id) newest.set(j.uid,j);}
    for(const row of document.querySelectorAll('.row')){
      const e=row.querySelector('.err'); if(!e) continue;
      const j=newest.get(row.dataset.uid);
      if(j && j.state==='failed'){
        e.hidden=false; e.textContent='Generation failed: '+(j.error||'unknown');
      }else if(j){
        e.hidden=true; e.textContent='';}}
    const done=d.jobs.some(j=>j.state==='done');
    if(done){ clearInterval(polling); polling=null;
              say('Done. Reloading to show the documents.');
              setTimeout(()=>location.reload(),900); return;}
    if(!anyBusy && d.jobs.length===0){clearInterval(polling);polling=null;}
  },2500);}

if(document.querySelector('.acts button.busy')) poll();
"""


def _rows(con):
    """Roles seen recently, plus every role you have acted on.

    Filtering on the last scan alone made applied and interviewing roles
    disappear the moment a posting closed, a source was rate-limited, or a
    `--limit` run happened -- taking their status and their generated
    documents with them, with no other view of them anywhere. Filtering on
    the newest date alone had the same effect for everything else: one
    limited run emptied the board.
    """
    return con.execute("""
        SELECT r.*, COALESCE(s.status,'new') AS status, COALESCE(s.note,'') AS note
        FROM roles r LEFT JOIN role_state s ON s.uid = r.uid
        WHERE """ + store.LIVE_SQL + " AND " + store.ACTIONABLE_SQL + """
           OR COALESCE(s.status,'new') <> 'new'
           OR r.uid IN (SELECT DISTINCT uid FROM artifacts)
        ORDER BY r.score DESC, r.company COLLATE NOCASE
    """).fetchall()


def render(con, home_currency: str = "") -> str:
    rows = _rows(con)
    # Keyed on the scan date rather than the run number, so a second scan the
    # same day does not empty the New tab.
    run = store.new_today(con)
    arts = {}
    for a in con.execute("SELECT * FROM artifacts ORDER BY id"):
        arts.setdefault(a["uid"], {})[a["kind"]] = dict(a)
    live = {j["uid"]: dict(j) for j in con.execute(
        "SELECT * FROM jobs WHERE state IN ('pending','running')")}

    total = len(rows)
    paid = sum(1 for r in rows if r["salary_confirmed"])
    settled = sum(1 for r in rows if r["status"] in store.SETTLED)

    # "What changed since yesterday" is the whole point of running this daily,
    # and the count was previously only ever a line of stdout that scrolled
    # away. first_run is in the database already; this surfaces it.
    fresh = sum(1 for r in rows if r["uid"] in run)
    _new_count = f'<span class="n">{fresh}</span>' if fresh else ""

    # Applications you are actually in. The board is mostly a list of things
    # you have not done anything about; the handful you have is the part with
    # a deadline attached, and it was scattered among three hundred rows.
    inflight = sum(1 for r in rows if r["status"] in store.IN_FLIGHT)
    _live_count = f'<span class="n">{inflight}</span>' if inflight else ""

    # Rejections and withdrawals, which every other view hides. Worth being
    # able to look at on purpose: it is the record of what you actually went
    # for, and it is the only place to notice a pattern in what comes back.
    # Skipped roles are not here -- you never applied to those.
    shut = sum(1 for r in rows if r["status"] in store.CLOSED_OUT)
    _closed_count = f'<span class="n">{shut}</span>' if shut else ""

    # Roles judged a real fit against the CV, not merely eligible against the
    # filters. -1 means unranked, which is not the same as bad.
    good = sum(1 for r in rows if (r["fit"] or -1) >= 70)
    _fit_count = f'<span class="n">{good}</span>' if good else ""

    # When the bundled source list was last checked against reality. Shown
    # because nothing else tells you: the weekly validation and growth jobs
    # run upstream, so a clone's list freezes on the day it was cloned and a
    # fork only ever prunes its own. A checkout months behind quietly loses
    # boards as they migrate and looks exactly as healthy as a fresh one.
    from .. import sources as _src
    age = _src.age_days()
    if age is None:
        _sync = ('<span class="sync warn" title="sources.json carries no date">'
                 'sources: never synced</span>'
                 '<button id="pull" type="button">Pull</button>')
    else:
        when = "today" if age == 0 else ("yesterday" if age == 1
                                         else f"{age} days ago")
        # Upstream validates weekly, so eight days is one missed cycle.
        cls = "sync warn" if age > 8 else "sync"
        tip = ("Run `git pull` to get boards that have moved and employers "
               "added since." if age > 8 else "Up to date with the weekly "
               "upstream check.")
        _sync = (f'<span class="{cls}" title="{_h.escape(tip, quote=True)}">'
                 f'sources synced {when}</span>'
                 + ('<button id="pull" type="button" title="git pull --ff-only '
                    'in this checkout">Pull</button>' if age > 8 else ''))

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
    # data-label so the count can be rewritten in the browser against the rows
    # the current tab actually shows, without losing the name.
    cc = Counter((r["country"] or "unknown") for r in rows)
    countries = '<option value="">All countries</option>' + "".join(
        f'<option value="{_h.escape(c, quote=True)}" '
        f'data-label="{_h.escape(c, quote=True)}">{_h.escape(c)} ({n})</option>'
        for c, n in cc.most_common())
    cty = Counter(r["city"] for r in rows if r["city"])
    cities = '<option value="">All cities</option>' + "".join(
        f'<option value="{_h.escape(c, quote=True)}" '
        f'data-label="{_h.escape(c, quote=True)}">{_h.escape(c)} ({n})</option>'
        for c, n in sorted(cty.items(), key=lambda x: (-x[1], x[0])))

    # Your currency, the one `salary.floor` is written in. `bySalary` only
    # compares figures inside one currency, because `salary.clears_floor`
    # refuses to compare across them and the sort has no business being
    # braver than the filter.
    #
    # This used to be the currency most of the BOARD's stated salaries were
    # in, which is not the same thing and inverted the sort for anyone whose
    # results are mostly foreign. On a GBP floor over a board holding 10 USD,
    # 8 GBP and 7 EUR figures, USD won the vote, so every row already stamped
    # "salary in USD, floor in GBP, not compared" sorted ABOVE the sterling
    # rows that HAD been compared -- the sort contradicting the caveat printed
    # on the same row, which is the exact fault the grouping was added to fix.
    #
    # The board's own modal currency stays as the fallback, for a config with
    # no currency set at all.
    home_cur = (home_currency or "").upper()
    if not home_cur:
        _cur = Counter(r["salary_currency"] for r in rows
                       if r["salary_confirmed"] and r["salary_currency"])
        home_cur = _cur.most_common(1)[0][0] if _cur else ""
    prelude = (f"const HOME_CUR={json.dumps(home_cur)},"
               f"PROGRESS={json.dumps(store.PROGRESS)};")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job radar</title>{_FAVICON}<style>{_CSS}{_EXTRA_CSS}</style></head><body><div class="wrap">
<header>
  <div class="brand">{_MARK}<span>job radar</span></div>
  <h1>{total} roles worth a look</h1>
  <p class="sub"><b>{paid}</b> with a salary &middot; <b>{settled}</b> settled &middot;
     live from the database, so anything you click sticks</p>
</header>
<div class="seg" role="tablist" aria-label="Filter roles">
  <button role="tab" aria-selected="false" data-f="all">All</button>
  <button role="tab" aria-selected="false" data-f="new">New{_new_count}</button>
  <button role="tab" aria-selected="false" data-f="fit">Best fit{_fit_count}</button>
  <button role="tab" aria-selected="false" data-f="live">In flight{_live_count}</button>
  <button role="tab" aria-selected="false" data-f="closed">Closed{_closed_count}</button>
  <button role="tab" aria-selected="true"  data-f="open">Open</button>
  <button role="tab" aria-selected="false" data-f="pay">Salary shown</button>
</div>
<div class="actions"><button id="rank" type="button">Rank against my CV</button>
  <button id="rankstop" type="button" hidden>Stop</button>
  <span id="rankinfo"></span>{_sync}</div>
<div class="chips" role="group" aria-label="Filter by sector">{chips}</div>
<div class="chips" role="group" aria-label="Filter by working pattern">{modes}</div>
<div class="selects">
  <label><span>Sort</span><select id="fsort" aria-label="Sort order">
    <option value="rank">Priority</option>
    <option value="fit">Fit against my CV</option>
    <option value="salary">Salary</option>
    <option value="new">Newest</option>
  </select></label>
  <label><span>Country</span><select id="fcountry" aria-label="Country">{countries}</select></label>
  <label><span>City</span><select id="fcity" aria-label="City">{cities}</select></label>
</div>
<div class="list" id="list">{"".join(_row(r, arts.get(r["uid"], {}), live.get(r["uid"]), run) for r in rows)}</div>
<p class="empty" id="empty" hidden>Nothing matches those filters.</p>
<footer>Roles with a stated salary below your floor are hidden. Nothing is generated
unless you click for it.</footer>
</div><div class="toast" id="toast"></div>
<script>{prelude}{_JS}</script></body></html>"""


def _row(r, arts, job, run=0) -> str:
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
    # The screening is the thing you asked for, so it goes in the row rather
    # than behind a link to a file. <details> gives the minimise for free and
    # keeps working with JavaScript off. Open by default: you clicked Screen
    # to read it, and a collapsed answer is one more click for no reason.
    # The fit score, where it can be read next to the role rather than only
    # in a sort order.
    fitline = ""
    fv = r["fit"] if r["fit"] is not None else -1
    if fv < 0 and len((r["description"] or "").strip()) < 200:
        # An absent score read as "not ranked yet" on a role that can never be
        # ranked. Say which it is, once, quietly.
        fitline = ('<div class="fit none"><b>&mdash;</b>'
                   '<span class="why">no description from this source, so '
                   'there is nothing to score against your CV</span></div>')
    elif fv >= 0:
        band = "good" if fv >= 70 else ("mid" if fv >= 50 else "low")
        fitline = (f'<div class="fit {band}"><b>{fv}</b>'
                   f'<span class="lbl">fit</span>'
                   + (f'<span class="why">{_h.escape(r["fit_why"] or "")}</span>'
                      if r["fit_why"] else "") + '</div>')

    screening = ""
    if "screen" in arts:
        v = arts["screen"]["summary"] or "screened"
        body = (arts["screen"].get("body") or "").strip()
        if body:
            verdict_class = ("skip" if v.upper().startswith("SKIP")
                             else "apply" if v.upper().startswith("APPLY") else "")
            screening = (
                f'<details class="screening" open><summary>'
                f'<span class="v {verdict_class}">{_h.escape(v.replace("_", " "))}</span>'
                f'<span class="lbl">screening</span></summary>'
                f'<div class="md">{_md(body)}</div></details>')
        else:
            docs.append(
                f'<a href="/open?path={_h.escape(quote(str(arts["screen"]["path"])), quote=True)}">'
                f'Screening</a> <span class="rating">{_h.escape(v)}</span>')

    # The static page warns when a source gives no description; the served one
    # did not, and that is the page with the money buttons on it.
    # Everything worth a caveat, not just the missing-description one. The
    # note that a salary was never compared to the floor existed on 12 roles
    # in one run and reached no view a person ever opens, so a EUR floor
    # looked like it had passed a set of sterling figures below it. Same for
    # a posting that rules out sponsorship.
    notes = [f for f in json.loads(r["flags"] or "[]")
             if ("not screened" in f or "listing only" in f
                 or "not compared" in f or "sponsor" in f)]
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
        f'data-new="{1 if r["uid"] in run else 0}" '
        # The bottom of a stated range, and only that. This fell back to the
        # top when a posting stated no bottom, so "up to 175,000" reported a
        # floor of 175,000 and led the salary sort ahead of a role actually
        # guaranteeing 150,000. The ceiling is kept beside it under its own
        # name, and the currency travels with them because comparing figures
        # across currencies is a guess this tool refuses to make elsewhere.
        f'data-payfloor="{int(r["salary_min"] or 0)}" '
        f'data-paytop="{int(r["salary_max"] or r["salary_min"] or 0)}" '
        f'data-paycur="{_h.escape(r["salary_currency"] or "", quote=True) if paid else ""}" '
        f'data-seen="{_h.escape(str(r["first_seen"] or ""), quote=True)}" '
        f'data-fit="{r["fit"] if r["fit"] is not None else -1}" '
        f'data-score="{r["score"] or 0}" '
        f'data-sector="{_h.escape(r["sector"] or "other", quote=True)}" '
        f'data-mode="{_h.escape(r["work_mode"] or "unstated", quote=True)}" '
        f'data-country="{_h.escape(r["country"] or "unknown", quote=True)}" '
        f'data-city="{_h.escape(r["city"] or "", quote=True)}">'
        f'<div><div class="role">'
        f'<a href="{_h.escape(safe_url(r["url"]))}" target="_blank" rel="noopener">{_h.escape(r["title"])}</a>'
        f'{status_pill}</div>'
        f'<div class="meta">{_h.escape(meta)}</div></div>'
        f'<div class="right"><span class="pay{"" if paid else " unk"}">'
        f'{_h.escape(r["salary_label"] or "unconfirmed salary")}</span></div>'
        + (f'<div class="docs">{" &middot; ".join(docs)}</div>' if docs else "")
        # All of them, not notes[0]. A role could be both unscreenable and
        # carrying a salary that was never compared to the floor, and only
        # the first ever appeared.
        + "".join(f'<div class="note">{_h.escape(n)}</div>' for n in notes)
        + fitline
        + (f'<div class="rownote">{_h.escape(r["note"])}</div>' if r["note"] else "")
        + screening
        + '<div class="acts">'
        + b("screen", "Screen", "primary")
        + b("cv", "CV")
        + letter_btn
        + f'<a class="btn" href="{_h.escape(safe_url(r["url"]))}" target="_blank" rel="noopener" '
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
