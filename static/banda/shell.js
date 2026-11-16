// banda app pages: mobile menu, user menu, sidebar fitting and the top bar shadow.
(function () {
  const $ = id => document.getElementById(id);
  const sidebar = $('sidebar'), scrim = $('scrim'), openBtn = $('openMenu'), closeBtn = $('closeMenu');
  if (sidebar && openBtn) {
    const isOpen = () => sidebar.classList.contains('is-open');
    const setMenu = open => {
      const wasOpen = isOpen();
      const focusInside = sidebar.contains(document.activeElement);
      sidebar.classList.toggle('is-open', open); scrim.hidden = !open; openBtn.setAttribute('aria-expanded', String(open));
      if (open) { const a = sidebar.querySelector('a'); if (a) a.focus(); }
      else if (wasOpen && focusInside) openBtn.focus(); // don't leave focus on a hidden link
    };
    openBtn.addEventListener('click', () => setMenu(true));
    if (closeBtn) closeBtn.addEventListener('click', () => setMenu(false));
    scrim.addEventListener('click', () => setMenu(false));
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && isOpen()) setMenu(false); });
  }
  const userBtn = $('userBtn'), userMenu = $('userMenu');
  // The account menu is a simple show/hide list of ordinary links and buttons (Tab moves through it).
  if (userBtn && userMenu) {
    const close = (restore) => {
      if (userMenu.hidden) return;
      const inside = userMenu.contains(document.activeElement);
      userMenu.hidden = true; userBtn.setAttribute('aria-expanded', 'false');
      if (restore || inside) userBtn.focus();
    };
    userBtn.addEventListener('click', e => {
      e.stopPropagation();
      const open = userMenu.hidden; userMenu.hidden = !open; userBtn.setAttribute('aria-expanded', String(open));
    });
    userMenu.addEventListener('click', e => e.stopPropagation());
    document.addEventListener('click', () => close(false));
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(true); });
    // Tabbing out of the list closes it.
    userMenu.addEventListener('focusout', e => { if (!userMenu.contains(e.relatedTarget) && e.relatedTarget !== userBtn) close(false); });
  }
  // If the window is too short for the whole menu, the help card steps aside.
  const help = document.querySelector('.side-help'), nav = document.querySelector('#sidebar nav');
  if (help && nav) {
    const over = () => nav.scrollHeight > nav.clientHeight + 1;
    const fit = () => { help.style.display = ''; help.classList.remove('is-compact'); if (over()) help.classList.add('is-compact'); if (over()) help.style.display = 'none'; };
    fit(); addEventListener('resize', fit); addEventListener('load', fit);
    if (document.fonts) document.fonts.ready.then(fit);
  }
  const bar = document.querySelector('.topbar');
  if (bar) { const sync = () => bar.classList.toggle('is-stuck', scrollY > 4); addEventListener('scroll', sync, { passive: true }); sync(); }
})();
