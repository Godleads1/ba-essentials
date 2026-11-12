/* Public site behaviour: header shadow, mobile menu, scroll-spy, reveals and
   the testimonial carousel. Served from our own origin (CSP: script-src 'self'). */
(function () {
  "use strict";

  var root = document.documentElement;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Header gains a border and shadow once the page is scrolled.
  var header = document.getElementById("siteHeader");
  if (header) {
    var onScroll = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // Mobile menu.
  var menuBtn = document.getElementById("menuBtn");
  var mobileMenu = document.getElementById("mobileMenu");
  if (menuBtn && mobileMenu) {
    var setMenu = function (open) {
      mobileMenu.hidden = !open;
      menuBtn.setAttribute("aria-expanded", String(open));
      menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      menuBtn.classList.toggle("is-open", open);
    };
    menuBtn.addEventListener("click", function () {
      setMenu(mobileMenu.hidden);
    });
    mobileMenu.addEventListener("click", function (event) {
      if (event.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !mobileMenu.hidden) {
        setMenu(false);
        menuBtn.focus();
      }
    });
  }

  if (!("IntersectionObserver" in window)) return;

  // Scroll-spy: the active nav item follows the section in view (landing only).
  var spyLinks = document.querySelectorAll("[data-spy]");
  var spyTargets = [];
  spyLinks.forEach(function (link) {
    var target = document.getElementById(link.dataset.spy);
    if (target && spyTargets.indexOf(target) === -1) spyTargets.push(target);
  });
  if (spyTargets.length) {
    var setActive = function (id) {
      spyLinks.forEach(function (link) {
        var on = link.dataset.spy === id;
        link.classList.toggle("is-active", on);
        if (on) link.setAttribute("aria-current", "true");
        else link.removeAttribute("aria-current");
      });
    };
    setActive(spyTargets[0].id);
    var spy = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) setActive(entry.target.id);
        });
      },
      { rootMargin: "-45% 0px -50% 0px" }
    );
    spyTargets.forEach(function (target) { spy.observe(target); });
  }

  // Reveal on scroll. Content is only hidden after this class is set, so it
  // stays visible if the script fails to load.
  var revealEls = document.querySelectorAll(".reveal");
  if (revealEls.length && !reduceMotion) {
    root.classList.add("js-reveal");
    var reveal = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("in");
          reveal.unobserve(entry.target);
        });
      },
      { threshold: 0.15 }
    );
    revealEls.forEach(function (el) { reveal.observe(el); });
  }

  // Testimonial carousel: arrows step one card and loop at the ends.
  var track = document.getElementById("track");
  var prev = document.getElementById("prevBtn");
  var next = document.getElementById("nextBtn");
  if (track && prev && next) {
    var behavior = reduceMotion ? "auto" : "smooth";
    var step = function () {
      var card = track.firstElementChild;
      return (card ? card.offsetWidth : 300) + 16;
    };
    var atStart = function () { return track.scrollLeft <= 2; };
    var atEnd = function () {
      return track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
    };
    prev.addEventListener("click", function () {
      track.scrollBy({ left: atStart() ? track.scrollWidth : -step(), behavior: behavior });
    });
    next.addEventListener("click", function () {
      track.scrollBy({ left: atEnd() ? -track.scrollWidth : step(), behavior: behavior });
    });
  }
})();
