"""HTML for the local settings page."""

from __future__ import annotations

import html as _h

from .favicon import link_tag as _favicon_tag
from .interactive import _CSS
from .setup import _SETUP_CSS


_JS = r"""
const form=document.querySelector('#settings'), msg=document.querySelector('#msg');
const key=document.querySelector('#anthropic_api_key');
const clear=document.querySelector('#clear_key');
function say(text,bad=false){msg.textContent=text;msg.className=bad?'err':'';}
async function load(){
  const r=await fetch('/api/settings');
  const d=await r.json().catch(()=>({}));
  if(!r.ok){say(d.error||'Could not load settings.',true);return;}
  form.provider.value=d.provider||'claude_cli';
  form.model.value=d.model||'claude-sonnet-5';
  form.base_url.value=d.base_url||'';
  form.max_tokens.value=d.max_tokens||4096;
  key.placeholder=d.anthropic_key_set?'Saved Anthropic key':'sk-ant-api03-...';
  document.querySelector('#key_state').textContent=d.anthropic_key_set
    ? 'Anthropic key saved. Leave blank to keep it.'
    : 'No Anthropic key saved.';
}
form.addEventListener('submit',async e=>{
  e.preventDefault();
  const b=form.querySelector('[type=submit]');
  b.disabled=true; say('Saving...');
  const fd=new FormData(form);
  const body={
    provider:fd.get('provider'),
    model:(fd.get('model')||'').trim(),
    base_url:(fd.get('base_url')||'').trim(),
    max_tokens:(fd.get('max_tokens')||'').trim(),
    anthropic_api_key:(fd.get('anthropic_api_key')||'').trim(),
    clear_anthropic_key:clear.checked
  };
  const r=await fetch('/api/settings',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  b.disabled=false;
  if(!r.ok){say(d.error||'Could not save settings.',true);return;}
  key.value=''; clear.checked=false; say('Saved.');
  load();
});
load();
"""


def render(config_path: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settings · job radar</title>{_favicon_tag()}<style>{_CSS}{_SETUP_CSS}
.setup nav{{padding:var(--s5);border-bottom:1px solid var(--line)}}
.setup nav a{{color:var(--accent);font-size:.9375rem;font-weight:650}}
</style></head>
<body><div class="wrap"><header>
  <div class="brand"><svg width="34" height="34" class="mark" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#18181b"/><g fill="none" stroke="#6ba3e8" stroke-linecap="round"><path d="M11.5 25.5a13 13 0 0 1 0-19" stroke-width="3.6" opacity=".45"/><path d="M17.5 21.5a6.5 6.5 0 0 1 0-11" stroke-width="3.6"/></g><circle cx="24.5" cy="16" r="4.6" fill="#5fbf8d"/></svg><span>job radar</span></div>
  <h1>Settings</h1>
  <p class="sub">AI calls can use an Anthropic-compatible API key, including DeepSeek, or fall back to the authenticated Claude CLI.</p>
</header>
<form class="setup" id="settings">
  <nav><a href="/">Back to dashboard</a></nav>
  <section>
    <h2>AI provider</h2>
    <div class="field"><label for="provider">Provider</label>
      <select id="provider" name="provider">
        <option value="anthropic">Anthropic-compatible API</option>
        <option value="claude_cli">Claude CLI fallback</option>
      </select>
    </div>
    <div class="field"><label for="model">Model</label>
      <input id="model" name="model" value="claude-sonnet-5">
      <div class="hint">Used by ranking, screening, CV drafts and cover letters.</div>
    </div>
    <div class="field"><label for="base_url">Base URL</label>
      <input id="base_url" name="base_url" placeholder="https://api.deepseek.com/anthropic">
      <div class="hint">Leave blank for Anthropic. Use DeepSeek's Anthropic-compatible base URL for DeepSeek.</div>
    </div>
    <div class="field"><label for="max_tokens">Max output tokens</label>
      <input id="max_tokens" name="max_tokens" value="4096" inputmode="numeric">
    </div>
  </section>
  <section>
    <h2>Anthropic credentials</h2>
    <div class="field"><label for="anthropic_api_key">API key</label>
      <input id="anthropic_api_key" name="anthropic_api_key" type="password" autocomplete="off">
      <div class="hint" id="key_state">Loading...</div>
    </div>
    <label class="check"><input type="checkbox" id="clear_key" name="clear_key"><span>Remove saved Anthropic key</span></label>
    <div class="hint">Secrets are stored only in { _h.escape(config_path) } and are redacted before being sent to AI prompts.</div>
  </section>
  <div class="savebar">
    <button type="submit">Save settings</button>
    <span id="msg"></span>
  </div>
</form></div><script>{_JS}</script></body></html>"""
