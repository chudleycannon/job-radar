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
from ..state import atomic_write_text

from .favicon import link_tag as _favicon_tag, mark as _favicon_mark

_FAVICON = _favicon_tag()
_MARK = _favicon_mark()


_CSS = """
/* DESIGN PLAN, "Calm"
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
/* The wordmark sits above the count rather than replacing it. The headline is
   still the number of roles, because that is what someone came to find out;
   the brand is a label, so it is small, muted and out of the way. */
/* A masthead, not a footnote. It sits on its own line above the count, at a
   size you would actually put on a site, with a rule under it so the page
   reads as a product rather than a report someone exported. */
.brand{display:flex;align-items:center;gap:12px;padding-bottom:var(--s4);
  margin-bottom:var(--s4);border-bottom:1px solid var(--line)}
.brand .mark{display:block;border-radius:9px;flex:0 0 auto}
.brand span{font-size:1.375rem;font-weight:650;letter-spacing:-.022em;
  color:var(--ink)}

.actions{display:flex;align-items:center;gap:12px;margin-bottom:var(--s4)}
.actions button{font:inherit;font-size:.8125rem;font-weight:550;cursor:pointer;
  padding:7px 14px;border-radius:8px;border:1px solid var(--accent);
  background:transparent;color:var(--accent);
  transition:background var(--dur) var(--ease),color var(--dur) var(--ease)}
.actions button:hover:not(:disabled){background:var(--accent);color:#fff}
.actions button:disabled{opacity:.5;cursor:default}
#rankstop{border-color:#d98080;color:#d98080}
#rankstop:hover:not(:disabled){background:#d98080;color:#fff}
.actions #rankinfo{font-size:.8125rem;color:var(--muted);
  font-variant-numeric:tabular-nums}
/* Upstream revalidates weekly, so past eight days you have missed a cycle and
   your board is quietly losing employers that have moved. Red rather than
   amber: this one is not a preference, it is results you are not being shown. */
.sync{margin-left:auto;font-size:.75rem;color:var(--muted);cursor:help;
  border-bottom:1px dotted var(--line)}
.sync.warn{color:#d98080;border-bottom-color:#d98080}
#pull{margin-left:8px;font-size:.75rem;padding:4px 10px;border-radius:7px;
  border:1px solid #d98080;background:transparent;color:#d98080;cursor:pointer}
#pull:hover:not(:disabled){background:#d98080;color:#fff}
#pull:disabled{opacity:.55;cursor:default}

/* Fit against the CV, from `job-radar rank`. Sits under the title because it
   is a judgement about the role, not a property of it like salary. */
/* The fit number is the thing you scan the board for, so it is sized like a
   number rather than like a label. It was .75rem, smaller than the body text
   around it, which made the one figure you are looking for the quietest thing
   in the row. */
.fit{grid-column:1/-1;margin-top:8px;font-size:.8125rem;color:var(--muted);
  display:flex;align-items:center;gap:10px}
.fit b{font-variant-numeric:tabular-nums;font-weight:700;font-size:1.0625rem;
  letter-spacing:-.02em;line-height:1;padding:5px 10px;border-radius:8px;
  background:var(--line);color:var(--ink);min-width:2.4em;text-align:center}
.fit .lbl{font-size:.6875rem;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);margin-left:-4px}
.fit.good b{background:rgba(95,191,141,.20);color:var(--pay);
  box-shadow:inset 0 0 0 1px rgba(95,191,141,.35)}
.fit.mid b{background:rgba(216,165,90,.16);color:var(--flag)}
.fit.low b{background:var(--line);color:var(--muted)}
.fit.none b{background:transparent;color:var(--muted);padding-left:0;
  font-size:.875rem;min-width:0}
.fit span.why{color:var(--muted);line-height:1.45}

/* The screening, inline under the role it belongs to. */
.screening{grid-column:1/-1;margin-top:var(--s3);border:1px solid var(--line);
  border-radius:10px;background:var(--surface-2);overflow:hidden}
.screening>summary{cursor:pointer;list-style:none;padding:10px 14px;
  display:flex;align-items:center;gap:10px;font-size:.8125rem}
.screening>summary::-webkit-details-marker{display:none}
.screening>summary::after{content:"Hide";margin-left:auto;color:var(--muted);
  font-size:.75rem}
.screening:not([open])>summary::after{content:"Show"}
.screening>summary:hover{background:var(--surface)}
.screening .v{font-weight:650;letter-spacing:.02em;font-size:.6875rem;
  text-transform:uppercase;padding:3px 8px;border-radius:999px;
  background:var(--line);color:var(--ink)}
.screening .v.skip{background:rgba(200,80,80,.16);color:#d98080}
.screening .v.apply{background:rgba(95,191,141,.16);color:var(--pay)}
.screening .lbl{color:var(--muted)}
.md{padding:2px 16px 14px;font-size:.875rem;line-height:1.62;color:var(--ink)}
.md h4,.md h5,.md h6{font-size:.8125rem;text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);margin:var(--s4) 0 var(--s2);
  font-weight:650}
.md h4:first-child{margin-top:var(--s2)}
.md p{margin:0 0 var(--s3)}
.md ul{margin:0 0 var(--s3);padding-left:1.15rem}
.md li{margin-bottom:6px}
.md code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8125em;
  background:var(--line);padding:1px 5px;border-radius:4px}
.md hr{border:0;border-top:1px solid var(--line);margin:var(--s4) 0}
.md strong{font-weight:650}
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
.chips .n,.seg .n{opacity:.6;font-variant-numeric:tabular-nums;margin-left:5px}
/* The scrollbar is hidden, so on a desktop width the thirteenth sector chip
   simply vanished off the right edge with nothing to say it was there. Wrap
   where there is room; keep the swipe strip on a phone, where it is the
   expected gesture. */
@media (min-width:640px){.chips{flex-wrap:wrap;overflow-x:visible}}

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
.row{display:grid;grid-template-columns:auto 1fr auto;gap:var(--s2) var(--s4);align-items:center;
  padding:var(--s4) var(--s5);border-bottom:1px solid var(--line);
  transition:background var(--dur) var(--ease);
  /* Skip layout and paint for rows nobody is looking at.
     A board is thousands of rows and the browser laid out and painted every
     one of them before it would show the first: 4,191 rows took seconds on a
     page the server had already answered in under one. Every row stays in the
     DOM, which matters because the filters, the counts and the status writes
     all walk it, so this changes what the browser does with a row and not
     what the page contains.
     `contain-intrinsic-size` is the guess used for a row that has not been
     laid out, and without it the scrollbar jumps as rows come into view. 92px
     is a one-line role at the current padding; a taller row corrects itself
     the moment it is rendered. `auto` keeps the last real measurement, so a
     row only guesses once. */
  content-visibility:auto;contain-intrinsic-size:auto 92px}
/* A class selector beats the UA stylesheet's [hidden]{display:none}, so
   .row{display:grid} silently kept filtered rows on screen. Any element given
   a display value needs its own hidden rule. */
.row[hidden]{display:none}
.row:last-child{border-bottom:0}
.row:hover{background:var(--surface-2)}
.role{font-size:1.0625rem;font-weight:600;letter-spacing:-.016em;line-height:1.3;
  /* The title wins. Salary is supporting information, not the headline. */
  color:var(--ink)}
/* Break inside a word when there is no other way to fit it.
   Nothing here wrapped a string with no spaces in it, and `.list` clips
   rather than scrolls, so the overflow was not merely ugly, it was
   unreachable: a 180 character location rendered 1,384px wide inside a 620px
   row, the page itself did not scroll sideways, and everything past the edge
   was cut off with nothing to say it was there. A 395 character job title is
   fine, because titles have spaces in them; a company name, a German compound
   word, or a bare URL in a location field is not.
   `anywhere` rather than `break-word` so the break is also allowed to shrink
   the grid column, which is what stops the row overflowing in the first
   place. */
.role,.meta,.co{overflow-wrap:anywhere}
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
.empty b{color:var(--ink)}
.empty p{margin:0 auto;max-width:38rem}
.empty p+p{margin-top:var(--s4)}
/* The drop reasons. Left-aligned inside the centred panel, because a column
   of counts read down the page is the thing being compared and centring it
   puts every number in a different place. */
.empty ul{list-style:none;margin:var(--s4) auto;padding:0;max-width:34rem;text-align:left}
.empty li{display:flex;justify-content:space-between;gap:var(--s4);
  padding:6px 0;border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums}
.empty li:last-child{border-bottom:0}
.empty li b{flex:0 0 auto}
.empty code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8125em;
  background:var(--line);color:var(--ink);padding:1px 5px;border-radius:4px}
footer{margin-top:var(--s5);color:var(--muted);font-size:.8125rem;line-height:1.5;
  padding:0 var(--s2)}

@media (max-width:640px){
  body{padding:var(--s5) var(--s3) var(--s7);font-size:16px}
  h1{font-size:1.9375rem}
  .row{grid-template-columns:auto 1fr;padding:var(--s4)}
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


# A job board supplies the apply URL, and six adapters read it straight out of
# third-party JSON -- on several of them it is employer-supplied. Escaping it
# stops the attribute breaking out; it does nothing about the scheme, so a
# javascript: href rendered as a live link in an origin that owns /api/generate
# and /api/status. Same-origin, so the CSRF check waves it through.
_SAFE_SCHEMES = ("http://", "https://", "mailto:")


def safe_url(url: str) -> str:
    """The URL if a browser may follow it, "" if it must not."""
    u = (url or "").strip()
    # Control characters and whitespace are stripped by parsers before the
    # scheme is read, so "java\tscript:" is javascript: to a browser.
    bare = "".join(c for c in u if c.isprintable() and not c.isspace()).lower()
    return u if bare.startswith(_SAFE_SCHEMES) else ""


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


# Flags that say nothing a reader can act on, and are therefore the only ones
# hidden. Everything else is shown.
_QUIET = (
    # Already rendered as the salary itself.
    "unconfirmed salary",
)


def _row(j: Job, is_new: bool) -> str:
    paid = j.salary.confirmed
    unscreened = any("not screened" in f for f in j.flags)
    # The flags that change what you should do about a role. "Salary in GBP,
    # floor in EUR, not compared" reached roles.json and nowhere a person
    # looks, so an unconverted figure below the floor read as one that passed.
    # An allow-list of three substrings meant every flag written since was
    # computed, stored, and shown to nobody. Counted on one board: 237 roles
    # carried "posted N days ago; check it is still open" and 0 of them
    # displayed it, including a posting from 2022 rendered exactly like a
    # fresh one. Also invisible: "N days a week in the office" on 103 roles,
    # "posted in N locations" on 51, "export control or clearance" on 43, and
    # "barely screened" on 5. `export control` missed only because the string
    # does not contain the word "sponsor".
    #
    # Inverted, so a new flag is visible by default and the list names what to
    # HIDE. A flag exists because somebody decided the reader needs it; the
    # burden belongs on the thing that suppresses one.
    caveats = [f for f in j.flags if not any(q in f for q in _QUIET)]
    meta = " \u00b7 ".join(x for x in [j.company, _cap_location(j.location)] if x)
    return (
        f'<div class="row" data-new="{1 if is_new else 0}" data-pay="{1 if paid else 0}" '
        f'data-sector="{_h.escape(j.sector or "other", quote=True)}" '
        f'data-mode="{_h.escape(j.work_mode or "unstated", quote=True)}" '
        f'data-country="{_h.escape(j.country or "unknown", quote=True)}" '
        f'data-city="{_h.escape(j.city or "", quote=True)}">'
        f'<div><div class="role">{"<span class=dot></span>" if is_new else ""}'
        f'<a href="{_h.escape(safe_url(j.url))}" target="_blank" rel="noopener">{_h.escape(j.title)}</a>'
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


# What to try when a scan matched nothing, worded the same way the CLI words
# it, because they are the same advice about the same run and a reader who
# saw one and then the other should not have to work out whether they agree.
_NEXT_STEPS = ('Most often this is the titles. Check <code>titles.include</code> '
               'matches how postings are actually worded, and add employers '
               'yourself with <code>job-radar discover &lt;company&gt; --add</code>.')


def _empty_state(jobs, dropped, sources_ok, sources_total, postings) -> str:
    """The panel the page shows when the list has nothing in it.

    It said "Nothing matches those filters." in both of the two situations
    that can produce it, and they want opposite things from the reader.

    On a first run that matched nothing the page loads with every filter on
    All and the sentence is simply false: no filter on this page is hiding
    anything, and the reader is sent to look for a switch that is not there.
    The scan that produced the page knew exactly what happened -- how many
    postings arrived, how many sources answered out of how many were asked,
    and the reason each posting was dropped -- and printed all of it to a
    terminal the reader has probably already closed. None of it reached the
    one surface they are actually looking at. All of it is in the arguments
    this function already receives.

    With rows on the page the filters really are the only way to empty it, so
    the old sentence is right there and only wants the way back out.
    """
    if jobs:
        return ('<div class="empty" id="empty" hidden><p>Nothing matches those '
                'filters. Choose <b>All</b> above, and clear any sector, '
                'working pattern, country or city you picked.</p></div>')

    read = (f"<b>{sources_ok:,}</b> of {sources_total:,} sources answered"
            if sources_total else "no sources were read")
    # Post-dedupe, which is what the reasons below add up to. `postings` is
    # pre-dedupe and is the bigger number.
    accounted = sum(dropped.values())
    total = postings or accounted
    if not total:
        # Nothing arrived at all, which is a broken scan rather than an empty
        # market, and the two deserve opposite reactions from the reader.
        return ('<div class="empty" id="empty"><p><b>This scan read nothing.</b> '
                f'{read}, and not one posting came back, so this page is empty '
                'because the scan failed and not because nothing matched. '
                'Check the network, then run <code>job-radar scan</code> '
                'again.</p></div>')

    items = sorted(dropped.items(), key=lambda x: -x[1])
    li = "".join(f"<li>{_h.escape(reason)}<b>{n:,}</b></li>"
                 for reason, n in items[:5])
    if len(items) > 5:
        li += (f"<li>in {len(items) - 5} smaller reasons"
               f"<b>{sum(n for _, n in items[5:]):,}</b></li>")
    # The same gap the CLI explains: the reasons are counted after duplicates
    # are merged, and `postings` is counted before, so a heading promising to
    # account for every posting leaves an unexplained hole unless the merge is
    # named. It was 891 on a 300-board run and nothing said why.
    merged = total - accounted
    if merged > 0:
        li += (f"<li>the same role posted more than once, merged"
               f"<b>{merged:,}</b></li>")

    return ('<div class="empty" id="empty"><p><b>Nothing got through.</b> '
            f'{read}, {total:,} postings came back, and every one of them was '
            f'dropped. Where they went:</p><ul>{li}</ul>'
            f'<p>{_NEXT_STEPS}</p></div>')


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

    # The footer told every reader that a salary floor was hiding roles from
    # them, whether or not they had one. `floor: null` is what the setup
    # wizard writes when somebody does not know what to put, so the sentence
    # was false on the default new-user page: nothing was hidden, and the
    # first thing the dashboard said about the filtering was wrong.
    #
    # `dropped` is the one piece of evidence this function has about what the
    # floor actually did, and it is exact rather than inferred. `screen.run`
    # counts one "stated pay below floor" per posting it dropped for that
    # reason, and counts none at all when `cfg.salary_floor` is falsy, because
    # `salary.clears_floor` returns `(True, "")` before it looks at anything.
    # So the count answers "were roles hidden by a floor" without needing the
    # config: say it when it happened, with the number, and say nothing when
    # it did not.
    below = dropped.get("stated pay below floor", 0)
    floor_line = (f"{below:,} role{'' if below == 1 else 's'} with a stated "
                  f"salary below your floor {'is' if below == 1 else 'are'} "
                  f"hidden. " if below else "")

    rows = "".join(_row(j, j.uid in new_ids) for j in jobs)
    empty = _empty_state(jobs, dropped, sources_ok, sources_total, postings)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_h.escape(title)}</title>{_FAVICON}<style>{_CSS}</style></head><body><div class="wrap">
<header>
  <div class="brand">{_MARK}<span>job radar</span></div>
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
{empty}
<footer>{floor_line}Roles with no stated
salary are shown and marked, because most employers still publish nothing.
Working pattern is only known where the employer said so.</footer>
</div>
<script>{_JS}</script></body></html>"""


def write(path, **kwargs):
    # Atomic. This one is regenerable, but a truncated page is the worst kind
    # of broken: a browser renders what it got and shows no error, so half a
    # dashboard looks like a dashboard with half the roles in it. Replacing at
    # the rename means an interrupted run leaves yesterday's complete page.
    return atomic_write_text(path, render(**kwargs))
