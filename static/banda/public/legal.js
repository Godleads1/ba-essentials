/* Legal pages: highlight the section being read in the contents list. */

    // Highlight the section you're reading in the contents list.
    const links = [...document.querySelectorAll('aside .toc a')];
    if ('IntersectionObserver' in window && links.length) {
      const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) links.forEach(a => a.classList.toggle('is-here', a.hash === '#' + e.target.id)); }), { rootMargin: '-90px 0px -70% 0px' });
      document.querySelectorAll('.doc h2[id]').forEach(h => io.observe(h));
    }
  