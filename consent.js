/* Cookie choices, as described in the Cookie Notice (cookies.html).
   - First visit: a banner with equal "Accept" and "Reject" buttons. Nothing optional runs before a choice.
   - Any element with [data-cookie-settings] (the "Cookie settings" link in every footer) reopens the choices.
   - The choice itself is kept in a strictly necessary cookie, "cookie_choices", for 6 months.
   Today the site uses no optional cookies, so the choice is recorded for when one is added.
   Other code can read the choice with window.bandaConsent.get() and listen for "banda:consent". */
(function () {
  const NAME = 'cookie_choices', VERSION = '1', MAX_AGE = 60 * 60 * 24 * 182;

  function read() {
    const m = document.cookie.match(/(?:^|;\s*)cookie_choices=([^;]*)/);
    if (!m) return null;
    try {
      const v = JSON.parse(decodeURIComponent(m[1]));
      return v && v.v === VERSION ? v : null;
    } catch { return null; }
  }
  function save(prefs) {
    const v = { v: VERSION, preferences: !!prefs.preferences, analytics: false, at: new Date().toISOString() };
    document.cookie = `${NAME}=${encodeURIComponent(JSON.stringify(v))}; Max-Age=${MAX_AGE}; Path=/; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
    document.dispatchEvent(new CustomEvent('banda:consent', { detail: v }));
    return v;
  }

  const css = `
    .bc-banner{position:fixed;left:16px;right:16px;bottom:16px;z-index:80;max-width:460px;border-radius:18px;background:#1C1712;color:#fff;padding:18px 18px 16px;box-shadow:0 24px 50px -20px rgba(0,0,0,.55);font:14px/1.55 Inter,system-ui,sans-serif}
    .bc-banner h2{margin:0 0 4px;font:600 16px/1.3 Lexend,system-ui,sans-serif}
    .bc-banner p{margin:0;color:rgba(255,255,255,.8)}
    .bc-banner a{color:#F0A77C;font-weight:600;text-decoration:underline;text-underline-offset:2px}
    .bc-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
    .bc-btn{flex:1 1 auto;min-width:110px;border-radius:999px;padding:10px 16px;font:600 14px Inter,system-ui,sans-serif;cursor:pointer;border:1.5px solid #fff;background:#fff;color:#1C1712}
    .bc-btn:hover{background:#F5F0E8}
    .bc-btn:focus-visible,.bc-link:focus-visible,.bc-dlg input:focus-visible{outline:2px solid #F0A77C;outline-offset:2px}
    .bc-link{background:none;border:0;color:#fff;font:600 13.5px Inter,system-ui,sans-serif;text-decoration:underline;text-underline-offset:2px;cursor:pointer;padding:6px 2px}
    .bc-dlg{width:min(92vw,480px);border:0;border-radius:18px;padding:0;color:#241A15;font:14px/1.55 Inter,system-ui,sans-serif}
    .bc-dlg::backdrop{background:rgba(28,23,18,.5)}
    .bc-dlg form{padding:22px}
    .bc-dlg h2{margin:0;font:600 19px/1.3 Lexend,system-ui,sans-serif}
    .bc-dlg .bc-sub{margin:4px 0 0;color:#736C66;font-size:13.5px}
    .bc-opt{display:flex;gap:12px;align-items:flex-start;margin-top:12px;border:1px solid #ECE5DA;border-radius:14px;padding:12px 14px}
    .bc-opt b{display:block;font-weight:600}
    .bc-opt span{display:block;color:#736C66;font-size:13px}
    .bc-opt input{margin-top:3px;width:18px;height:18px;accent-color:#33397F;flex-shrink:0}
    .bc-tag{margin-left:auto;flex-shrink:0;border-radius:6px;background:#F5F0E8;padding:2px 8px;font-size:12px;font-weight:600;color:#5C534D;white-space:nowrap}
    .bc-actions{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;margin-top:18px}
    .bc-actions .bc-btn{flex:0 1 auto;border-color:#33397F;background:#fff;color:#33397F}
    .bc-actions .bc-btn.bc-main{background:#33397F;color:#fff}
    .bc-dlg a{color:#33397F;font-weight:600}
    @media (prefers-reduced-motion:no-preference){.bc-banner{animation:bcIn .35s cubic-bezier(.22,1,.36,1)}@keyframes bcIn{from{opacity:0;transform:translateY(12px)}}}
    @media print{.bc-banner{display:none}}`;
  const style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);

  let banner = null;
  function closeBanner() { if (banner) { banner.remove(); banner = null; } }
  function showBanner() {
    if (banner) return;
    banner = document.createElement('section');
    banner.className = 'bc-banner';
    banner.setAttribute('aria-label', 'Cookie choices');
    banner.innerHTML = `
      <h2>Cookies</h2>
      <p>We use only the cookies the site needs to keep you signed in and secure. We don’t use advertising or analytics cookies. If we ever add an optional cookie, it stays off unless you accept. <a href="cookies.html">Cookie Notice</a></p>
      <div class="bc-row">
        <button type="button" class="bc-btn" data-bc="accept">Accept</button>
        <button type="button" class="bc-btn" data-bc="reject">Reject</button>
      </div>
      <button type="button" class="bc-link" data-bc="settings">Choose for myself</button>`;
    banner.addEventListener('click', e => {
      const b = e.target.closest('[data-bc]'); if (!b) return;
      if (b.dataset.bc === 'accept') { save({ preferences: true }); closeBanner(); }
      if (b.dataset.bc === 'reject') { save({ preferences: false }); closeBanner(); }
      if (b.dataset.bc === 'settings') openSettings(b);
    });
    document.body.appendChild(banner);
  }

  let dlg = null;
  function openSettings(opener) {
    if (!dlg) {
      dlg = document.createElement('dialog');
      dlg.className = 'bc-dlg';
      dlg.setAttribute('aria-labelledby', 'bcTitle');
      dlg.innerHTML = `
        <form method="dialog">
          <h2 id="bcTitle">Cookie settings</h2>
          <p class="bc-sub">Choose which optional cookies we may use. You can change this at any time from “Cookie settings” at the bottom of every page. <a href="cookies.html">Read the Cookie Notice</a></p>
          <div class="bc-opt"><input type="checkbox" checked disabled aria-describedby="bcNec" /><div><b>Strictly necessary</b><span id="bcNec">Keep you signed in, protect forms and the site, and remember these choices.</span></div><span class="bc-tag">Always on</span></div>
          <label class="bc-opt"><input type="checkbox" id="bcPref" /><div><b>Preferences</b><span>Remember simple choices, such as light or dark mode.</span></div></label>
          <div class="bc-opt"><input type="checkbox" disabled aria-describedby="bcAn" /><div><b>Analytics</b><span id="bcAn">Not used. We’ll ask you before we ever add them.</span></div><span class="bc-tag">Not in use</span></div>
          <div class="bc-actions">
            <button value="reject" class="bc-btn">Reject all</button>
            <button value="save" class="bc-btn bc-main">Save choices</button>
          </div>
        </form>`;
      document.body.appendChild(dlg);
      dlg.addEventListener('close', () => {
        if (dlg.returnValue === 'save') { save({ preferences: dlg.querySelector('#bcPref').checked }); closeBanner(); }
        if (dlg.returnValue === 'reject') { save({ preferences: false }); closeBanner(); }
        const back = dlg._opener && document.contains(dlg._opener) ? dlg._opener : null;
        if (back) back.focus();
      });
    }
    const cur = read();
    dlg.querySelector('#bcPref').checked = !!(cur && cur.preferences);
    dlg.returnValue = '';
    dlg._opener = opener || document.activeElement;
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    dlg.querySelector('#bcPref').focus();
  }

  document.addEventListener('click', e => {
    const t = e.target.closest('[data-cookie-settings]');
    if (t) { e.preventDefault(); openSettings(t); }
  });

  window.bandaConsent = { get: read, open: openSettings };
  const start = () => { if (!read()) showBanner(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
