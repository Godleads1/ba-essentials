// banda sign-in pages. Everything here is a convenience: the server checks and
// normalises the phone number or email, and the forms work without this script.
(function () {
  const $ = id => document.getElementById(id);
  // Numbers copied from WhatsApp or contacts often carry invisible marks, full-width digits or long dashes.
  const clean = v => (v || '').normalize('NFKC').replace(/\p{Cf}/gu, '').trim();
  const isPhone = v => {
    v = clean(v);
    if (/[\p{L}@]/u.test(v)) return false;
    let d = v.replace(/\D/g, '');
    if (/^\+/.test(v) || v.startsWith('00')) return d.length >= 8 && d.length <= 15; // international
    if (d.startsWith('234')) d = d.slice(3);
    if (d.startsWith('0')) d = d.slice(1);
    return /^[2789]\d{9}$/.test(d); // Nigerian mobile
  };
  const DOMAINS = { gmail: 'gmail.com', gmial: 'gmail.com', gmai: 'gmail.com', gamil: 'gmail.com', yahoo: 'yahoo.com', yaho: 'yahoo.com', ymail: 'ymail.com', hotmail: 'hotmail.com', outlook: 'outlook.com', icloud: 'icloud.com', live: 'live.com', aol: 'aol.com' };
  const TYPOS = new Set(['gmial', 'gmai', 'gamil', 'yaho']);
  const kind = v => {
    const t = clean(v);
    if (/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(t)) {
      const dom = t.split('@')[1].toLowerCase();
      return TYPOS.has(dom.split('.')[0]) || /\.con$/.test(dom) ? '' : 'email';
    }
    return isPhone(t) ? 'phone' : '';
  };
  function emailHint(t) {
    const m = t.match(/^([^\s@]+)@([^\s@]*)$/);
    if (!m) return null;
    const [, user, dom] = m, low = dom.toLowerCase().replace(/\.+$/, '');
    if (!low) return { msg: 'Add the rest of your email address after the @, like name@gmail.com.' };
    const base = low.split('.')[0];
    if (TYPOS.has(base)) return { msg: 'Check the spelling of your email address.', fix: `${user}@${DOMAINS[base]}` };
    if (DOMAINS[base] && !low.includes('.')) return { msg: 'Your email address is missing its ending.', fix: `${user}@${DOMAINS[base]}` };
    if (/\.con$/.test(low)) return { msg: 'Check the ending of your email address.', fix: `${user}@${dom.replace(/\.con$/i, '.com')}` };
    if (!low.includes('.')) return { msg: `Add the ending of your email address, like @${low}.com.` };
    return { msg: 'Check your email address. It should look like name@gmail.com.' };
  }

  // ---------- sign in: one box for phone number or email ----------
  const ident = $('ident');
  if (ident && ident.form) {
    const chanSet = $('chanSet'), err = $('identError'), msg = $('identMsg'), fix = $('identFix');
    const sync = () => { if (chanSet) chanSet.hidden = kind(ident.value) !== 'phone'; };
    const hide = () => { if (!err) return; err.hidden = true; ident.removeAttribute('aria-invalid'); };
    const show = t => {
      const h = /@/.test(t) ? emailHint(t) : null;
      const digits = t.replace(/\D/g, '');
      msg.textContent = h ? h.msg
        : digits.length >= 7 && !/\p{L}/u.test(t) ? 'That phone number doesn’t look complete. Nigerian mobile numbers have 11 digits, like 0801 234 5678. For other countries, start with + and the country code.'
        : (chanSet ? 'Enter your phone number, like 0801 234 5678, or your email address.' : 'Enter your email address.');
      if (h && h.fix) { fix.textContent = `Use ${h.fix}`; fix.dataset.value = h.fix; fix.hidden = false; } else fix.hidden = true;
      err.hidden = false; ident.setAttribute('aria-invalid', 'true');
    };
    ident.addEventListener('input', () => { sync(); hide(); });
    if (fix) fix.addEventListener('click', () => { ident.value = fix.dataset.value; hide(); sync(); ident.focus(); });
    ident.form.addEventListener('submit', e => {
      ident.value = clean(ident.value);
      const k = kind(ident.value);
      if (!k || (k === 'phone' && !chanSet)) { e.preventDefault(); show(ident.value); ident.focus(); }
    });
    sync();
  }

  // ---------- code page: wait before asking for another code ----------
  const resend = $('resend');
  if (resend && resend.dataset.wait) {
    let left = parseInt(resend.dataset.wait, 10) || 0;
    const label = resend.textContent;
    const tick = () => {
      if (left <= 0) { resend.disabled = false; resend.textContent = label; return; }
      resend.disabled = true; resend.textContent = `Send a new code in ${left}s`; left -= 1; setTimeout(tick, 1000);
    };
    tick();
  }
  const code = $('code');
  if (code) code.addEventListener('input', () => { code.value = clean(code.value).replace(/\D/g, '').slice(0, 6); });
})();
