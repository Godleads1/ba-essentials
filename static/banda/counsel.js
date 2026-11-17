/* "Need Counselling?": a button fixed to the bottom-right of every client workspace page.
   It opens a small panel with Counselling (email, and later a phone number) and the
   Business Prayer Hour (a free conference call link, and later its day and time).

   Privacy: nothing here records who opens the panel or what they click.
   The details come from the page (a JSON data block the server writes from the
   super admin's Counselling & prayer settings). An empty value hides that line. */
(function () {
  if (window.__bandaCounsel) return; window.__bandaCounsel = true;

  const C = Object.assign({
    enabled: true,
    label: 'Need Counselling?',
    email: 'counselingunit@bandaessentials.com',
    phone: '',            // added later, e.g. '+234 800 000 0000'
    prayerLink: '',       // the free conference call link
    prayerWhen: '',       // e.g. 'Every Monday, 7:00–8:00am (WAT)'
    emergency: '112',
  }, readConfig());
  if (!C.enabled) return;

  function readConfig() {
    const el = document.getElementById('bandaCounselConfig');
    if (!el) return { enabled: false };
    try { return JSON.parse(el.textContent); } catch (_) { return { enabled: false }; }
  }

  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = u => /^https:\/\//i.test(u || '') ? u : '';
  const telHref = p => 'tel:' + String(p).replace(/[^\d+]/g, '');
  const icon = (paths, cls) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="${cls || ''}">${paths}</svg>`;
  const HEART = '<path d="M11 14h2a2 2 0 1 0 0-4h-3c-.6 0-1.1.2-1.4.6L3 16"/><path d="m7 20 1.6-1.4c.3-.4.8-.6 1.4-.6h4c1.1 0 2.1-.4 2.8-1.2l4.6-4.4a2 2 0 0 0-2.75-2.91l-4.2 3.9"/><path d="m2 15 6 6"/><path d="M19.5 8.5c.7-.7 1.5-1.6 1.5-2.7A2.73 2.73 0 0 0 16 4a2.78 2.78 0 0 0-5 1.8c0 1.2.8 2 1.5 2.8L16 12Z"/>';
  const CHAT = '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>';
  const PRAY = '<path d="M10 9h4"/><path d="M12 7v5"/><path d="M14 22v-4a2 2 0 0 0-4 0v4"/><path d="M18 22V5.618a1 1 0 0 0-.553-.894l-4.553-2.277a2 2 0 0 0-1.788 0L6.553 4.724A1 1 0 0 0 6 5.618V22"/><path d="m18 7 3.447 1.724a1 1 0 0 1 .553.894V20a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-10.382a1 1 0 0 1 .553-.894L6 7"/>';
  const X = '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>';


  function render() {
    const root = document.createElement('div'); root.className = 'bc-help';
    const phone = C.phone ? `
        <div class="bc-addr"><span>${esc(C.phone)}</span><a class="bc-mini" href="${esc(telHref(C.phone))}">Call</a></div>` : '';
    const link = safeUrl(C.prayerLink);
    root.innerHTML = `
      <div class="bc-help-panel" id="bcHelpPanel" role="region" aria-labelledby="bcHelpTitle" hidden>
        <div class="bc-help-head">
          <h2 class="bc-help-title" id="bcHelpTitle" tabindex="-1">You don’t have to carry it alone</h2>
          <button type="button" class="bc-help-close" data-bc-close aria-label="Close">${icon(X)}</button>
        </div>
        <p class="bc-help-sub">Running a business is hard. Talk to someone, or pray with other business owners.</p>

        <section class="bc-card" aria-labelledby="bcCounselT">
          <div class="bc-card-h"><span class="bc-card-ic bc-card-ic--navy">${icon(CHAT)}</span><h3 id="bcCounselT">Counselling</h3></div>
          <p>Talk privately with our counselling team. Write to us and we’ll get back to you.</p>
          <div class="bc-addr"><span id="bcEmail">${esc(C.email).replace('@', '@<wbr>')}</span><button type="button" class="bc-mini" data-bc-copy aria-describedby="bcEmail">Copy</button></div>
          ${phone}
          <div class="bc-row"><a class="bc-go bc-go--navy" href="mailto:${esc(C.email)}?subject=${encodeURIComponent('Counselling request')}">Send an email</a></div>
        </section>

        <section class="bc-card" aria-labelledby="bcPrayT">
          <div class="bc-card-h"><span class="bc-card-ic bc-card-ic--orange">${icon(PRAY)}</span><h3 id="bcPrayT">Business Prayer Hour</h3></div>
          <p>Join other business owners to pray together on a free conference call.</p>
          ${C.prayerWhen ? `<p class="bc-when">${esc(C.prayerWhen)}</p>` : ''}
          ${link
            ? `<div class="bc-row"><a class="bc-go bc-go--cta" href="${esc(link)}" target="_blank" rel="noopener noreferrer">Join the call<span class="bc-vh"> (opens in a new tab)</span></a></div>`
            : `<span class="bc-soon">${C.prayerWhen ? 'Call link coming soon' : 'Day, time and call link coming soon'}</span>`}
        </section>

        <p class="bc-foot">In an emergency, call <b>${esc(C.emergency)}</b>.</p>
      </div>
      <button type="button" class="bc-help-btn" id="bcHelpBtn" aria-expanded="false" aria-controls="bcHelpPanel" title="${esc(C.label)}">${icon(HEART, 'bc-ic')}<span class="bc-help-label">${esc(C.label)}</span></button>`;
    document.body.appendChild(root);
    // Leave room at the end of the page so the button never covers the last buttons or cards.
    document.body.classList.add('bc-has-help');

    const btn = root.querySelector('#bcHelpBtn'), panel = root.querySelector('#bcHelpPanel');
    const open = () => { panel.hidden = false; btn.setAttribute('aria-expanded', 'true'); root.querySelector('#bcHelpTitle').focus(); };
    const close = (restore) => {
      if (panel.hidden) return;
      const inside = panel.contains(document.activeElement);
      panel.hidden = true; btn.setAttribute('aria-expanded', 'false');
      if (restore || inside) btn.focus();
    };
    btn.addEventListener('click', e => { e.stopPropagation(); panel.hidden ? open() : close(true); });
    root.querySelector('[data-bc-close]').addEventListener('click', () => close(true));
    panel.addEventListener('click', e => e.stopPropagation());
    document.addEventListener('click', () => close(false));
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(true); });

    const copyBtn = root.querySelector('[data-bc-copy]');
    copyBtn.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(C.email); copyBtn.textContent = 'Copied'; }
      catch (_) { // fall back to selecting the address so it can be copied by hand
        const r = document.createRange(); r.selectNodeContents(root.querySelector('#bcEmail'));
        const s = getSelection(); s.removeAllRanges(); s.addRange(r); copyBtn.textContent = 'Selected';
      }
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render); else render();
})();
