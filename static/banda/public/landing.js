/* Landing page behaviour, from the design preview. No inline scripts: the CSP allows only files. */


    // Header shadow on scroll
    const header = document.getElementById('siteHeader');
    const onScroll = () => {
      const s = window.scrollY > 8;
      header.classList.toggle('shadow-[0_8px_24px_-18px_rgba(28,23,18,.35)]', s);
      header.classList.toggle('border-cream-line', s);
      header.classList.toggle('border-transparent', !s);
    };
    window.addEventListener('scroll', onScroll, { passive: true }); onScroll();

    // Mobile menu
    const menuBtn = document.getElementById('menuBtn');
    const mobileMenu = document.getElementById('mobileMenu');
    menuBtn.addEventListener('click', () => {
      const open = mobileMenu.classList.toggle('hidden') === false;
      menuBtn.setAttribute('aria-expanded', String(open));
      menuBtn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    });
    mobileMenu.querySelectorAll('a').forEach(a => a.addEventListener('click', () => { mobileMenu.classList.add('hidden'); menuBtn.setAttribute('aria-expanded', 'false'); }));

    // Scroll-spy: active nav item follows the section in view
    const links = document.querySelectorAll('[data-spy]');
    const setActive = id => links.forEach(l => {
      const on = l.dataset.spy === id;
      l.classList.toggle('text-brand-navy', on);
      l.classList.toggle('after:scale-x-100', on);
      on ? l.setAttribute('aria-current', 'true') : l.removeAttribute('aria-current');
    });
    setActive('top');
    const spy = new IntersectionObserver(es => es.forEach(e => e.isIntersecting && setActive(e.target.id)), { rootMargin: '-45% 0px -50% 0px' });
    ['top', 'services', 'how', 'pricing', 'about', 'faqs'].forEach(id => spy.observe(document.getElementById(id)));

    // Reveal on scroll + progress bars
    const reveal = new IntersectionObserver(es => es.forEach(e => {
      if (!e.isIntersecting) return;
      e.target.classList.add('in');
      e.target.querySelectorAll('.bar').forEach(b => b.style.width = b.dataset.w);
      reveal.unobserve(e.target);
    }), { threshold: 0.15 });
    document.querySelectorAll('.reveal').forEach((el, i) => {
      if (el.classList.contains('svc')) el.style.transitionDelay = (i % 5) * 0.06 + 's';
      reveal.observe(el);
    });

    // FAQ: opening one answer closes the others
    const faqs = document.querySelectorAll('#faqs details');
    faqs.forEach(d => d.addEventListener('toggle', () => { if (d.open) faqs.forEach(o => o !== d && (o.open = false)); }));



    // Dashboard showcase: plays once it scrolls into view; the pointer demo then loops.
(function () {
        const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const stage = document.getElementById('dsStage');
    const $ = id => document.getElementById(id);
    if (!reduce) document.documentElement.classList.add('ds-js');

    function countUp(el, to, ms, delay) {
      el.textContent = '0';
      setTimeout(() => { const t0 = performance.now(); const step = t => { const k = Math.min(1, (t - t0) / ms); el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); }; requestAnimationFrame(step); }, delay);
    }

    // The demo loop: the pointer approves the draft, the request moves to Delivered, then everything resets.
    const frame = stage.querySelector('.ds-frame'), cur = $('dsCursor'), btn = $('dsApprove');
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const moveTo = (el, dx = 0.6, dy = 0.55) => {
      const f = frame.getBoundingClientRect(), r = el.getBoundingClientRect();
      cur.style.transform = `translate(${r.left - f.left + r.width * dx}px, ${r.top - f.top + r.height * dy}px)`;
    };
    function setChip(el, cls, text) { el.className = 'ds-chip ds-' + cls + ' ds-swap'; el.textContent = text; }
    // The demo rests while the section is off screen or the tab is hidden.
    let onScreen = false;
    async function loop() {
      while (true) {
        while (!onScreen || document.hidden) await wait(400);
        cur.style.transition = 'none'; cur.style.transform = `translate(${frame.clientWidth * .55}px, ${frame.clientHeight + 20}px)`;
        await wait(60); cur.style.transition = ''; cur.style.opacity = '1';
        moveTo(btn); await wait(1300);
        btn.classList.add('ds-press');
        const rp = document.createElement('span'); rp.className = 'ds-ripple'; rp.style.left = '60%'; rp.style.top = '55%'; btn.appendChild(rp); setTimeout(() => rp.remove(), 700);
        await wait(160); btn.classList.remove('ds-press'); btn.classList.add('ds-done'); $('apT').textContent = 'Approved';
        await wait(500); $('dsToast').classList.add('ds-show');
        setChip($('chipP'), 'c-done', 'Delivered');
        $('sDone').textContent = '3'; $('sCheck').textContent = '0'; $('sDoneCard').classList.remove('ds-flash'); void $('sDoneCard').offsetWidth; $('sDoneCard').classList.add('ds-flash');
        moveTo($('chipP'), .5, 1.6); await wait(2600);
        $('dsToast').classList.remove('ds-show'); cur.style.opacity = '0'; await wait(700);
        // next thing waiting on you
        $('wWhat').textContent = 'Share supplier details'; $('wMeta').textContent = 'Due 18 Nov · Operations & Logistics';
        btn.classList.remove('ds-done'); $('apT').textContent = 'Send information';
        await wait(2600);
        // reset for the next round
        $('wWhat').textContent = 'Approve the business proposal'; $('wMeta').textContent = 'Due 13 Nov · Business & Admin'; $('apT').textContent = 'Review and approve';
        setChip($('chipP'), 'c-check', 'Being checked'); $('sDone').textContent = '2'; $('sCheck').textContent = '1';
        await wait(1500);
      }
    }

    function start() {
      stage.classList.add('ds-in');
      if (reduce) return;
      document.querySelectorAll('.ds-num[data-to]').forEach((n, i) => countUp(n, +n.dataset.to, 900, 700 + i * 80));
      setTimeout(loop, 2600);
    }
    if (reduce || !('IntersectionObserver' in window)) start();
    else {
      let started = false;
      new IntersectionObserver(e => {
        onScreen = e[0].isIntersecting;
        if (onScreen && !started) { started = true; start(); }
      }, { threshold: .25 }).observe(stage);
    }
  })();
  

    // Pricing switches. Prices, the yearly discount and the extra legal review fee come from the
    // server (the plans and billing settings set in the app), in the #pricingData block. The switch
    // only shows prices: the price a client pays follows the country they choose at sign-up.
    (function () {
      const data = document.getElementById('pricingData');
      if (!data) return;
      const D = JSON.parse(data.textContent);
      const money = (minor, currency) => {
        const n = minor / 100, whole = minor % 100 === 0;
        const f = n.toLocaleString(currency === 'NGN' ? 'en-NG' : 'en-US', { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 });
        return (currency === 'NGN' ? '₦' : '$') + f;
      };
      const P = {
        PLANS: Object.keys(D.plans),
        price: (k, region, billing) => {
          const p = (D.plans[k] || {})[region];
          if (!p) return null;
          if (billing !== 'yearly') return { perMonth: p.month, total: p.month, currency: p.currency };
          return { perMonth: Math.round(p.year / 12), total: p.year, currency: p.currency };
        },
      };
      const REGION = { africa: 'ng', intl: 'intl' };
      const state = { region: null, billing: 'monthly' };
      try { const saved = JSON.parse(localStorage.getItem('banda.pricing') || '{}'); Object.assign(state, saved); } catch (e) {}
      if (!REGION[state.region]) {
        let tz = '';
        try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) {}
        state.region = tz && !tz.startsWith('Africa/') ? 'intl' : 'africa';
      }
      if (state.billing !== 'yearly') state.billing = 'monthly';
      function render() {
        const region = REGION[state.region], yearly = state.billing === 'yearly';
        document.querySelectorAll('[data-save]').forEach(el => { el.textContent = (D.save || {})[region] || ''; });
        document.querySelectorAll('[data-region]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.region === state.region)));
        document.querySelectorAll('[data-billing]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.billing === state.billing)));
        P.PLANS.forEach(k => {
          const pr = P.price(k, region, state.billing);
          const priceEl = document.querySelector(`[data-price="${k}"]`), billedEl = document.querySelector(`[data-billed="${k}"]`);
          if (!pr || !priceEl) return;
          priceEl.textContent = money(Math.round(pr.perMonth / 100) * 100, pr.currency);
          if (billedEl) billedEl.textContent = yearly ? `${money(pr.total, pr.currency)} billed once a year` : '';
        });
        const L = D.legal[region], legal = L ? money(L.minor, L.currency) : '';
        document.getElementById('priceNote').textContent = region === 'ng'
          ? `Prices in Naira for businesses in Nigeria. Elsewhere in Africa, you pay the same plan in US dollars at today’s rate.${legal ? ` Extra legal reviews are ${legal} each.` : ''}`
          : `Prices in US dollars for businesses outside Africa. A US or UK reviewer is included where a document needs one.${legal ? ` Extra legal reviews are ${legal} each.` : ''}`;
      }
      document.addEventListener('click', e => {
        const b = e.target.closest('[data-region],[data-billing]'); if (!b) return;
        if (b.dataset.region) state.region = b.dataset.region; else state.billing = b.dataset.billing;
        try { localStorage.setItem('banda.pricing', JSON.stringify(state)); } catch (e) {}
        render();
      });
      render();
    })();
  