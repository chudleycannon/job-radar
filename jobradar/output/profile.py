"""HTML for the editable candidate profile."""

import html as _h

from .favicon import link_tag as _favicon_tag, mark as _favicon_mark
from .interactive import _CSS
from .setup import _SETUP_CSS
from .. import store


_JS = r"""
const msg=document.querySelector('#msg');
function say(t,bad=false){msg.textContent=t;msg.className=bad?'err':'';}
function cardSay(card,t,bad=false){
  const m=card&&card.querySelector('.ev-msg');
  if(m){m.textContent=t;m.className=bad?'ev-msg err':'ev-msg';}
  else say(t,bad);
}
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(d.error||'Could not save.');
  return d;
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('button[data-status]');
  if(b){
    const card=b.closest('.evidence'), status=b.dataset.status;
    b.disabled=true; cardSay(card,'Saving...');
    try{await post('/api/profile/evidence',{id:+card.dataset.id,status});
      card.classList.remove('proposed','approved','rejected','archived');
      card.classList.add(status);
      card.querySelector('.ev-status').textContent=status;
      cardSay(card,status.charAt(0).toUpperCase()+status.slice(1)+'.');}
    catch(err){cardSay(card,err.message,true);}
    finally{b.disabled=false;}
    return;
  }
  const s=e.target.closest('button[data-save]');
  if(s){
    const card=s.closest('.evidence');
    const tags=card.querySelector('[name=tags]').value.split(',')
      .map(x=>x.trim()).filter(Boolean);
    s.disabled=true; cardSay(card,'Saving...');
    try{await post('/api/profile/evidence',{
      id:+card.dataset.id,
      title:card.querySelector('[name=title]').value,
      category:card.querySelector('[name=category]').value,
      employer:card.querySelector('[name=employer]').value,
      role_title:card.querySelector('[name=role_title]').value,
      date_range:card.querySelector('[name=date_range]').value,
      tags,
      body:card.querySelector('[name=body]').value
    }); card.querySelector('.card-summary strong').textContent=card.querySelector('[name=title]').value;
      cardSay(card,'Edits saved.');}
    catch(err){cardSay(card,err.message,true);}
    finally{s.disabled=false;}
  }
  const del=e.target.closest('button[data-delete-evidence]');
  if(del){
    const card=del.closest('.evidence');
    if(!confirm('Delete this rejected evidence card?')) return;
    del.disabled=true; cardSay(card,'Deleting...');
    try{await post('/api/profile/evidence/delete',{id:+card.dataset.id});
      card.remove(); say('Rejected evidence deleted.');}
    catch(err){cardSay(card,err.message,true);}
    finally{del.disabled=false;}
    return;
  }
  const ks=e.target.closest('button[data-save-keywords]');
  if(ks){
    const card=ks.closest('.keyword-group');
    ks.disabled=true; cardSay(card,'Saving...');
    try{await post('/api/profile/keywords',{
      id:+card.dataset.id,
      title:card.querySelector('[name=keyword_title]').value,
      keywords:card.querySelector('[name=keywords]').value,
      status:card.dataset.status||'approved'
    }); card.querySelector('.card-summary strong').textContent=card.querySelector('[name=keyword_title]').value;
      cardSay(card,'Keywords saved.');}
    catch(err){cardSay(card,err.message,true);}
    finally{ks.disabled=false;}
    return;
  }
  const kb=e.target.closest('button[data-keyword-status]');
  if(kb){
    const card=kb.closest('.keyword-group'), status=kb.dataset.keywordStatus;
    kb.disabled=true; cardSay(card,'Saving...');
    try{await post('/api/profile/keywords',{id:+card.dataset.id,status});
      card.dataset.status=status;
      card.classList.remove('proposed','approved','rejected','archived');
      card.classList.add(status);
      card.querySelector('.ev-status').textContent=status;
      cardSay(card,status.charAt(0).toUpperCase()+status.slice(1)+'.');}
    catch(err){cardSay(card,err.message,true);}
    finally{kb.disabled=false;}
    return;
  }
});
document.querySelector('#new-evidence').addEventListener('submit',async e=>{
  e.preventDefault();
  const fd=new FormData(e.target);
  try{await post('/api/profile/evidence',{
    title:fd.get('title'), category:fd.get('category'),
    tags:(fd.get('tags')||'').split(',').map(x=>x.trim()).filter(Boolean),
    body:fd.get('body'), status:'approved'
  }); say('Evidence added.'); location.reload();}
  catch(err){say(err.message,true);}
});
document.querySelector('#personal-info').addEventListener('submit',async e=>{
  e.preventDefault();
  const fd=new FormData(e.target);
  try{await post('/api/profile/personal',{
    name:fd.get('name'), email:fd.get('email'), linkedin:fd.get('linkedin'),
    github:fd.get('github'), links:fd.get('links')
  }); say('Personal info saved.');}
  catch(err){say(err.message,true);}
});
document.querySelector('#new-category').addEventListener('submit',async e=>{
  e.preventDefault();
  const fd=new FormData(e.target);
  try{await post('/api/profile/category',{name:fd.get('name')});
    say('Category added.'); location.reload();}
  catch(err){say(err.message,true);}
});
document.querySelector('#new-keywords').addEventListener('submit',async e=>{
  e.preventDefault();
  const fd=new FormData(e.target);
  try{await post('/api/profile/keywords',{
    title:fd.get('title'), keywords:fd.get('keywords'), status:'approved'
  }); say('Keyword group added.'); location.reload();}
  catch(err){say(err.message,true);}
});
document.querySelector('#rebuild-profile').addEventListener('click',async e=>{
  if(!confirm('Clear candidate profile evidence and rebuild it from the configured CV?')) return;
  const b=e.currentTarget;
  b.disabled=true; say('Rebuilding profile from CV...');
  try{const d=await post('/api/profile/rebuild',{});
    say(d.message||'Profile rebuilt.'); setTimeout(()=>location.reload(),650);}
  catch(err){say(err.message,true);}
  finally{b.disabled=false;}
});
"""


def render(rows: list[dict], import_note: str = "",
           custom_categories: list[str] | None = None,
           keyword_groups: list[dict] | None = None,
           personal_info: dict | None = None) -> str:
    custom_categories = custom_categories or []
    keyword_groups = keyword_groups or []
    personal_info = personal_info or {}
    cards = _grouped_cards(rows, custom_categories) or (
        '<p class="empty">No candidate evidence yet. Add a note below, or run setup '
        'with a CV so job radar can create proposed evidence for review.</p>')
    keywords = _keyword_groups(keyword_groups) or (
        '<p class="empty">No core expertise keywords yet. Add a group below, '
        'or rebuild the profile from a CV that contains keyword bullets.</p>')
    note = f'<p class="sub">{_h.escape(import_note)}</p>' if import_note else (
        '<p class="sub">Approved evidence is the reusable candidate profile. '
        'Proposed evidence is waiting for your review before future generation uses it.</p>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Candidate Profile · job radar</title>{_favicon_tag()}<style>{_CSS}{_SETUP_CSS}
.profile-nav{{display:flex;gap:14px;padding:var(--s5);border-bottom:1px solid var(--line)}}
.profile-nav a{{color:var(--accent);font-size:.9375rem;font-weight:650}}
.evidence,.keyword-group{{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);
  margin-bottom:var(--s3);overflow:hidden}}
.evidence.proposed,.keyword-group.proposed{{border-left:4px solid var(--accent)}}
.evidence.approved,.keyword-group.approved{{border-left:4px solid var(--pay)}}
.evidence.rejected,.evidence.archived,.keyword-group.rejected,.keyword-group.archived{{opacity:.68}}
.card-summary{{cursor:pointer;list-style:none;padding:var(--s4);display:flex;gap:10px;
  align-items:center;justify-content:space-between}}
.card-summary::-webkit-details-marker{{display:none}}
.card-body{{padding:0 var(--s4) var(--s4);display:grid;gap:var(--s3)}}
.ev-head{{display:flex;gap:10px;align-items:center;justify-content:space-between}}
.ev-status{{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  font-weight:700}}
.mini-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:var(--s3)}}
.evidence textarea{{min-height:120px}}
.keyword-group textarea{{min-height:86px}}
.ev-actions{{display:flex;flex-wrap:wrap;gap:8px}}
.ev-actions button{{font:inherit;font-size:.875rem;font-weight:650;border:1px solid var(--line);
  background:var(--surface-2);color:var(--ink);border-radius:8px;padding:8px 11px;cursor:pointer}}
.ev-actions button.primary{{background:var(--accent);border-color:var(--accent);color:white}}
.ev-actions button:disabled{{opacity:.55;cursor:default}}
.ev-msg{{align-self:center;color:var(--muted);font-size:.875rem}}
.ev-msg.err{{color:var(--flag)}}
.danger{{border-color:var(--flag)!important;color:var(--flag)!important;background:transparent!important}}
.group{{margin-bottom:var(--s3);border:1px solid var(--line);border-radius:var(--r-lg);
  background:var(--surface);overflow:hidden}}
.group>summary{{cursor:pointer;list-style:none;padding:var(--s4);display:flex;
  justify-content:space-between;gap:12px;align-items:center}}
.group>summary::-webkit-details-marker{{display:none}}
.group h3{{font-size:.95rem;margin:0;color:var(--muted);text-transform:uppercase;
  letter-spacing:.06em}}
.group .group-body{{padding:0 var(--s4) var(--s4)}}
.group .count{{color:var(--muted);font-size:.875rem}}
.group-empty{{color:var(--muted);font-size:.875rem;margin:0 0 var(--s3)}}
.empty{{color:var(--muted);padding:var(--s5);background:var(--surface);border:1px solid var(--line);
  border-radius:var(--r-lg)}}
@media(max-width:760px){{.mini-grid{{grid-template-columns:1fr}}.ev-head{{align-items:flex-start;
  flex-direction:column}}}}
</style></head>
<body><div class="wrap"><header>
  <div class="brand">{_favicon_mark()}<span>job radar</span></div>
  <h1>Candidate Profile</h1>{note}
</header>
<main class="setup">
  <nav class="profile-nav"><a href="/">Dashboard</a><a href="/settings">Settings</a></nav>
  <section>
    <h2>Personal info</h2>
    <form id="personal-info">
      <div class="grid">
        <div class="field"><label>Name</label><input name="name" value="{_esc_attr(personal_info.get('name', ''))}"></div>
        <div class="field"><label>Email</label><input name="email" type="email" value="{_esc_attr(personal_info.get('email', ''))}"></div>
      </div>
      <div class="grid">
        <div class="field"><label>LinkedIn</label><input name="linkedin" value="{_esc_attr(personal_info.get('linkedin', ''))}"></div>
        <div class="field"><label>GitHub</label><input name="github" value="{_esc_attr(personal_info.get('github', ''))}"></div>
      </div>
      <div class="field"><label>Other links</label><textarea name="links" placeholder="Portfolio, blog, publications, other profiles">{_h.escape(personal_info.get('links', ''))}</textarea></div>
      <div class="savebar"><button type="submit">Save personal info</button><span id="msg"></span></div>
    </form>
  </section>
  <section>
    <h2>Rebuild profile</h2>
    <p>Clear the candidate evidence stored here and reanalyse the configured CV. Roles, scans, generated documents and settings are left alone.</p>
    <div class="savebar"><button class="secondary danger" id="rebuild-profile" type="button">Rebuild from CV</button></div>
  </section>
  <section>
    <h2>Core expertise</h2>
    <p>Keyword groups are reusable skills and expertise terms. They are separate from evidence, so they can help matching and CV keyword selection without being treated as achievements.</p>
    {keywords}
    <form id="new-keywords">
      <div class="grid">
        <div class="field"><label>Group title</label><input name="title" required placeholder="Observability and operations"></div>
        <div class="field"><label>Keywords</label><textarea name="keywords" required placeholder="monitoring, alerting, SLOs, incident management"></textarea></div>
      </div>
      <div class="savebar"><button type="submit">Add keyword group</button></div>
    </form>
  </section>
  <section><h2>Evidence</h2>{cards}</section>
  <section>
    <h2>Evidence categories</h2>
    <p>Custom categories are added to the Evidence group.</p>
    <form id="new-category">
      <div class="grid">
        <div class="field"><label>New Evidence Category</label><input name="name" required placeholder="operational resilience"></div>
      </div>
      <div class="savebar"><button type="submit">Add category</button></div>
    </form>
  </section>
  <section>
    <h2>Add evidence</h2>
    <form id="new-evidence">
      <div class="grid">
        <div class="field"><label>Title</label><input name="title" required></div>
        <div class="field"><label>Category</label>{_category_select("category", "general", custom_categories)}</div>
      </div>
      <div class="field"><label>Tags</label><input name="tags" placeholder="incident management, audit"></div>
      <div class="field"><label>Evidence</label><textarea name="body" required></textarea></div>
      <div class="savebar"><button type="submit">Add approved evidence</button></div>
    </form>
  </section>
</main></div><script>{_JS}</script></body></html>"""


def _grouped_cards(rows: list[dict], custom_categories: list[str]) -> str:
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r.get("category") or "general", []).append(r)
    parts = []
    for label, cats in _groups(custom_categories):
        items = [r for c in cats for r in by_cat.pop(c, [])]
        if not items:
            continue
        cards = "\n".join(_card(r, custom_categories) for r in items)
        parts.append(
            f'<details class="group" open><summary><h3>{_h.escape(label)}</h3>'
            f'<span class="count">{len(items)}</span></summary>'
            f'<div class="group-body">{cards}</div></details>')
    leftovers = [r for group in by_cat.values() for r in group]
    if leftovers:
        cards = "\n".join(_card(r, custom_categories) for r in leftovers)
        parts.append(
            f'<details class="group" open><summary><h3>General Info</h3>'
            f'<span class="count">{len(leftovers)}</span></summary>'
            f'<div class="group-body">{cards}</div></details>')
    return "\n".join(parts)


def _keyword_groups(rows: list[dict]) -> str:
    return "\n".join(_keyword_card(r) for r in rows)


def _keyword_card(r: dict) -> str:
    keywords = ", ".join(r.get("keywords") or [])
    status = r.get("status") or "approved"
    opened = "" if status == "approved" else " open"
    return f"""<details class="keyword-group {_h.escape(status)}" data-id="{int(r['id'])}" data-status="{_h.escape(status, quote=True)}"{opened}>
  <summary class="card-summary"><strong>{_h.escape(r['title'])}</strong>
    <span class="ev-status">{_h.escape(status)}</span></summary>
  <div class="card-body">
  <div class="grid">
    <div class="field"><label>Group title</label><input name="keyword_title" value="{_h.escape(r['title'], quote=True)}"></div>
    <div class="field"><label>Keywords</label><textarea name="keywords">{_h.escape(keywords)}</textarea></div>
  </div>
  <div class="hint">Source: {_h.escape(r.get('source') or 'Manual')} · Updated {_h.escape(r.get('updated_at') or '')}</div>
  <div class="ev-actions">
    <button class="primary" data-save-keywords="1" type="button">Save keywords</button>
    <button data-keyword-status="approved" type="button">Approve</button>
    <button data-keyword-status="rejected" type="button">Reject</button>
    <button data-keyword-status="archived" type="button">Archive</button>
    <span class="ev-msg" aria-live="polite"></span>
  </div>
  </div>
</details>"""


def _groups(custom_categories: list[str]) -> list[tuple[str, list[str]]]:
    out = []
    for label, cats in store.EVIDENCE_GROUPS:
        group = list(cats)
        if label == "Evidence":
            group.extend(custom_categories)
        out.append((label, group))
    return out


def _card(r: dict, custom_categories: list[str] | None = None) -> str:
    custom_categories = custom_categories or []
    tags = ", ".join(r.get("tags") or [])
    opened = "" if r["status"] == "approved" else " open"
    delete = ('<button class="danger" data-delete-evidence="1" type="button">Delete</button>'
              if r["status"] == "rejected" else "")
    return f"""<details class="evidence {_h.escape(r['status'])}" data-id="{int(r['id'])}"{opened}>
  <summary class="card-summary"><strong>{_h.escape(r['title'])}</strong>
    <span class="ev-status">{_h.escape(r['status'])}</span></summary>
  <div class="card-body">
  <div class="grid">
    <div class="field"><label>Title</label><input name="title" value="{_h.escape(r['title'], quote=True)}"></div>
    <div class="field"><label>Category</label>{_category_select("category", r['category'], custom_categories)}</div>
  </div>
  <div class="mini-grid">
    <div class="field"><label>Employer</label><input name="employer" value="{_h.escape(r['employer'], quote=True)}"></div>
    <div class="field"><label>Role title</label><input name="role_title" value="{_h.escape(r['role_title'], quote=True)}"></div>
    <div class="field"><label>Date range</label><input name="date_range" value="{_h.escape(r['date_range'], quote=True)}"></div>
  </div>
  <div class="field"><label>Tags</label><input name="tags" value="{_h.escape(tags, quote=True)}"></div>
  <div class="field"><label>Evidence</label><textarea name="body">{_h.escape(r['body'])}</textarea></div>
  <div class="hint">Source: {_h.escape(r['source'] or 'Manual')} · Updated {_h.escape(r['updated_at'])}</div>
  <div class="ev-actions">
    <button class="primary" data-save="1" type="button">Save edits</button>
    <button data-status="approved" type="button">Approve</button>
    <button data-status="rejected" type="button">Reject</button>
    <button data-status="archived" type="button">Archive</button>
    {delete}
    <span class="ev-msg" aria-live="polite"></span>
  </div>
  </div>
</details>"""


def _category_select(name: str, selected: str,
                     custom_categories: list[str] | None = None) -> str:
    custom_categories = custom_categories or []
    opts = ""
    for label, cats in _groups(custom_categories):
        inner = "".join(
            f'<option value="{_h.escape(c, quote=True)}"'
            f'{" selected" if c == selected else ""}>{_label(c)}</option>'
            for c in cats)
        opts += f'<optgroup label="{_h.escape(label)}">{inner}</optgroup>'
    return f'<select name="{_h.escape(name, quote=True)}">{opts}</select>'


def _label(category: str) -> str:
    return _h.escape(str(category).replace("_", " "))


def _esc_attr(value: object) -> str:
    return _h.escape(str(value or ""), quote=True)
