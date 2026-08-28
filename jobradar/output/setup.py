"""Browser setup page."""

from __future__ import annotations

import html as _h

from .favicon import link_tag as _favicon_tag, mark as _favicon_mark
from .html import _CSS

_FAVICON = _favicon_tag()
_MARK = _favicon_mark()

_SETUP_CSS = """
.setup{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);
  overflow:hidden;box-shadow:var(--shadow)}
.setup section{padding:var(--s5);border-bottom:1px solid var(--line)}
.setup section:last-of-type{border-bottom:0}
.setup h2{font-size:1rem;margin-bottom:var(--s3);letter-spacing:-.01em}
.setup p{color:var(--muted);font-size:.9375rem;margin-bottom:var(--s4)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--s4)}
.field{display:flex;flex-direction:column;gap:6px;margin-bottom:var(--s4)}
.field:last-child{margin-bottom:0}
.field label,.check span{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);font-weight:650}
.field input,.field textarea,.field select{width:100%;border:1px solid var(--line);
  background:var(--surface-2);color:var(--ink);border-radius:8px;font:inherit;
  font-size:.9375rem;padding:10px 12px}
.field textarea{min-height:78px;resize:vertical}
.check{display:flex;align-items:center;gap:10px;margin:0 0 var(--s4)}
.check input{width:18px;height:18px;accent-color:var(--accent)}
.hint{font-size:.8125rem;color:var(--muted);margin-top:-2px}
.savebar{display:flex;align-items:center;gap:var(--s3);padding:var(--s5);
  background:var(--surface-2);border-top:1px solid var(--line)}
.savebar button{font:inherit;font-size:.9375rem;font-weight:650;border:1px solid var(--accent);
  background:var(--accent);color:white;border-radius:8px;padding:10px 16px;cursor:pointer}
.savebar button.secondary{background:transparent;color:var(--accent)}
.savebar button:disabled{opacity:.55;cursor:default}
#msg{font-size:.875rem;color:var(--muted)}
#msg.err{color:var(--flag)}
@media(max-width:640px){.grid{grid-template-columns:1fr}.savebar{flex-direction:column;align-items:stretch}}
"""

_JS = r"""
const form=document.querySelector('#setup'), msg=document.querySelector('#msg');
const csv=v=>v.split(',').map(x=>x.trim()).filter(Boolean);
const lines=v=>v.split(/\n|,/).map(x=>x.trim()).filter(Boolean);
function say(text,bad=false){msg.textContent=text;msg.className=bad?'err':'';}
form.addEventListener('submit',async e=>{
  e.preventDefault();
  const b=form.querySelector('[type=submit]');
  b.disabled=true; say('Saving...');
  const fd=new FormData(form);
  const body={
    cv_path:(fd.get('cv_path')||'').trim(),
    titles_include:lines(fd.get('titles_include')||''),
    titles_exclude:lines(fd.get('titles_exclude')||''),
    countries:csv(fd.get('countries')||''),
    remote_ok:fd.get('remote_ok')==='on',
    remote_only:fd.get('remote_only')==='on',
    relocate_to:csv(fd.get('relocate_to')||''),
    need_sponsorship:csv(fd.get('need_sponsorship')||''),
    exclude_locations:csv(fd.get('exclude_locations')||''),
    salary_floor:(fd.get('salary_floor')||'').trim(),
    salary_currency:(fd.get('salary_currency')||'').trim(),
    dealbreakers:lines(fd.get('dealbreakers')||''),
    sectors:csv(fd.get('sectors')||''),
    source_countries:csv(fd.get('source_countries')||''),
    concurrency:(fd.get('concurrency')||'').trim()
  };
  const r=await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){say(d.error||'Could not save the config.',true); b.disabled=false; return;}
  say('Saved. Opening the dashboard...');
  setTimeout(()=>location.href='/',500);
});
document.querySelector('#scan').addEventListener('click',async ()=>{
  const r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},
    body:'{}'});
  const d=await r.json().catch(()=>({}));
  say(r.ok ? (d.message||'Scan started.') : (d.error||'Could not start scan.'), !r.ok);
});
document.querySelector('#settings').addEventListener('click',()=>{location.href='/settings';});
"""


def render(config_path: str = "config.yaml", error: str = "") -> str:
    err = (f'<p class="err">{_h.escape(error)}</p>' if error else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Set up job radar</title>{_FAVICON}<style>{_CSS}{_SETUP_CSS}</style></head>
<body><div class="wrap"><header>
  <div class="brand">{_MARK}<span>job radar</span></div>
  <h1>Set up your search</h1>
  <p class="sub">Write the config from the browser, then run scans and work the board here.</p>
</header>{err}
<form class="setup" id="setup">
  <section>
    <h2>Your CV</h2>
    <p>Use a path this process can read. In Docker, put the file under the mounted data folder and use a path like <code>/data/my-cv.pdf</code>.</p>
    <div class="field"><label for="cv_path">CV path</label><input id="cv_path" name="cv_path" required placeholder="/data/my-cv.pdf"></div>
  </section>
  <section>
    <h2>Roles</h2>
    <div class="field"><label for="titles_include">Titles to find</label><textarea id="titles_include" name="titles_include" required placeholder="engineering manager&#10;head of engineering"></textarea><div class="hint">One per line, or comma separated.</div></div>
    <div class="field"><label for="titles_exclude">Titles to exclude</label><textarea id="titles_exclude" name="titles_exclude" placeholder="product manager&#10;sales"></textarea></div>
  </section>
  <section>
    <h2>Location</h2>
    <div class="grid">
      <div class="field"><label for="countries">Countries you can work in</label><input id="countries" name="countries" value="UK"></div>
      <div class="field"><label for="relocate_to">Countries you would relocate to</label><input id="relocate_to" name="relocate_to"></div>
    </div>
    <label class="check"><input type="checkbox" name="remote_ok" checked><span>Include fully remote roles</span></label>
    <label class="check"><input type="checkbox" name="remote_only"><span>Remote only</span></label>
    <div class="grid">
      <div class="field"><label for="need_sponsorship">Need sponsorship in</label><input id="need_sponsorship" name="need_sponsorship"></div>
      <div class="field"><label for="exclude_locations">Always exclude places</label><input id="exclude_locations" name="exclude_locations"></div>
    </div>
  </section>
  <section>
    <h2>Salary and filters</h2>
    <div class="grid">
      <div class="field"><label for="salary_floor">Minimum stated salary</label><input id="salary_floor" name="salary_floor" placeholder="70000"></div>
      <div class="field"><label for="salary_currency">Currency</label><input id="salary_currency" name="salary_currency" value="GBP"></div>
    </div>
    <div class="field"><label for="dealbreakers">Dealbreakers</label><textarea id="dealbreakers" name="dealbreakers" placeholder="take-home test&#10;on-call"></textarea><div class="hint">Plain words or phrases. A match in the job description hides the role.</div></div>
  </section>
  <section>
    <h2>Sources</h2>
    <div class="field"><label for="sectors">Sectors</label><input id="sectors" name="sectors" placeholder="technology, finance"></div>
    <div class="field"><label for="source_countries">Only read boards tagged for</label><input id="source_countries" name="source_countries" placeholder="UK, IE"></div>
    <div class="field"><label for="concurrency">Concurrent boards</label><input id="concurrency" name="concurrency" value="16" inputmode="numeric"></div>
    <div class="hint">Config will be written to <code>{_h.escape(config_path)}</code>.</div>
  </section>
  <div class="savebar">
    <button type="submit">Save setup</button>
    <button class="secondary" id="scan" type="button">Start scan</button>
    <button class="secondary" id="settings" type="button">Settings</button>
    <span id="msg"></span>
  </div>
</form></div><script>{_JS}</script></body></html>"""
