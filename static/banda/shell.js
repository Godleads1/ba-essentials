// banda app pages: mobile menu, user menu, sidebar fitting and the top bar shadow.
(function () {
  const $ = id => document.getElementById(id);
  const sidebar = $('sidebar'), scrim = $('scrim'), openBtn = $('openMenu'), closeBtn = $('closeMenu');
  if (sidebar && openBtn) {
    const setMenu = open => {
      sidebar.classList.toggle('is-open', open); scrim.hidden = !open; openBtn.setAttribute('aria-expanded', String(open));
      if (open) { const a = sidebar.querySelector('a'); if (a) a.focus(); }
    };
    openBtn.addEventListener('click', () => setMenu(true));
    if (closeBtn) closeBtn.addEventListener('click', () => setMenu(false));
    scrim.addEventListener('click', () => setMenu(false));
    document.addEventListener('keydown', e => { if (e.key === 'Escape') setMenu(false); });
  }
  const userBtn = $('userBtn'), userMenu = $('userMenu');
  if (userBtn && userMenu) {
    const close = () => { userMenu.hidden = true; userBtn.setAttribute('aria-expanded', 'false'); };
    userBtn.addEventListener('click', e => { e.stopPropagation(); const open = userMenu.hidden; userMenu.hidden = !open; userBtn.setAttribute('aria-expanded', String(open)); });
    userMenu.addEventListener('click', e => e.stopPropagation());
    document.addEventListener('click', close);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
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
