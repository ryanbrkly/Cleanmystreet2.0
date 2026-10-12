// Clean My Street — small progressive enhancements. Everything works without JS.
(function () {
  "use strict";

  // Mobile navigation
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "Close" : "Menu";
    });
  }

  // Lightweight YouTube embed: load the iframe only when the visitor presses play.
  document.querySelectorAll(".yt[data-yt]").forEach(function (box) {
    var btn = box.querySelector(".yt__play");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var id = box.getAttribute("data-yt");
      var iframe = document.createElement("iframe");
      iframe.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(id) + "?autoplay=1&rel=0&modestbranding=1";
      iframe.title = btn.getAttribute("aria-label") || "Video";
      iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture";
      iframe.allowFullscreen = true;
      box.innerHTML = "";
      box.appendChild(iframe);
    });
  });

  // Lightbox: opens any [data-lightbox] photo; arrows step through photos in the same group
  var lb = document.getElementById("lightbox");
  var lastFocus = null, group = [], index = 0;
  function show(i) {
    var t = group[i]; if (!t) return;
    index = i;
    var img = lb.querySelector("img"), caps = lb.querySelectorAll("p");
    img.src = t.getAttribute("data-lightbox");
    img.alt = t.getAttribute("data-caption") || "";
    caps[0].textContent = t.getAttribute("data-caption") || "";
    caps[1].textContent = group.length > 1 ? (i + 1) + " of " + group.length : "";
    lb.querySelectorAll(".lightbox__nav").forEach(function (b) { b.hidden = group.length < 2; });
  }
  function openLightbox(trigger) {
    if (!lb) return;
    lastFocus = document.activeElement;
    var scope = trigger.closest("[data-gallery], .chapter, .beforeafter, .feed, .gallery");
    group = scope ? Array.prototype.slice.call(scope.querySelectorAll("[data-lightbox]")) : [trigger];
    show(Math.max(0, group.indexOf(trigger)));
    lb.classList.add("open");
    lb.querySelector("[data-close]").focus();
  }
  function closeLightbox() {
    if (!lb) return;
    lb.classList.remove("open");
    lb.querySelector("img").src = "";
    if (lastFocus) lastFocus.focus();
  }
  function step(d) { if (group.length > 1) show((index + d + group.length) % group.length); }
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-lightbox]");
    if (t) { e.preventDefault(); openLightbox(t); return; }
    var nav = e.target.closest(".lightbox__nav");
    if (nav) { step(parseInt(nav.getAttribute("data-step"), 10)); return; }
    if (lb && (e.target === lb || e.target.closest("[data-close]"))) closeLightbox();
  });
  document.addEventListener("keydown", function (e) {
    if (!lb || !lb.classList.contains("open")) return;
    if (e.key === "Escape") closeLightbox();
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowLeft") step(-1);
  });

  // Photo story timeline: drag with the mouse, swipe on touch, arrow buttons; a drag never opens a photo
  document.querySelectorAll("[data-storyline]").forEach(function (box) {
    var track = box.querySelector(".storyline__track");
    var navs = box.querySelectorAll(".storyline__nav");
    if (!track) return;
    var down = false, startX = 0, startLeft = 0, moved = 0;
    track.addEventListener("pointerdown", function (e) {
      if (e.pointerType !== "mouse" || e.button !== 0) return; // touch scrolls natively
      down = true; moved = 0; startX = e.clientX; startLeft = track.scrollLeft;
    });
    window.addEventListener("pointermove", function (e) {
      if (!down) return;
      var dx = e.clientX - startX;
      if (Math.abs(dx) > 4 && !track.classList.contains("is-dragging")) track.classList.add("is-dragging");
      moved = Math.max(moved, Math.abs(dx));
      track.scrollLeft = startLeft - dx;
    });
    window.addEventListener("pointerup", function () {
      if (!down) return;
      down = false;
      track.classList.remove("is-dragging");
    });
    // swallow the click that ends a drag so the lightbox doesn't open
    track.addEventListener("click", function (e) {
      if (moved > 6) { e.preventDefault(); e.stopPropagation(); moved = 0; }
    }, true);
    function stepWidth() {
      var item = track.querySelector(".storyline__step");
      return item ? item.getBoundingClientRect().width : track.clientWidth * 0.8;
    }
    function update() {
      var max = track.scrollWidth - track.clientWidth - 2;
      navs.forEach(function (b) {
        b.disabled = parseInt(b.getAttribute("data-dir"), 10) < 0 ? track.scrollLeft <= 2 : track.scrollLeft >= max;
      });
    }
    navs.forEach(function (b) {
      b.addEventListener("click", function () {
        var smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        track.scrollBy({ left: parseInt(b.getAttribute("data-dir"), 10) * stepWidth(), behavior: smooth ? "smooth" : "auto" });
      });
    });
    track.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  });

  // Project feed filter (before / during / after)
  var filter = document.querySelector(".feed-filter");
  if (filter) {
    filter.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-phase]");
      if (!b) return;
      var phase = b.getAttribute("data-phase");
      filter.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      document.querySelectorAll(".feed-item").forEach(function (item) {
        item.hidden = phase !== "all" && item.getAttribute("data-phase") !== phase;
      });
    });
  }

  // Contribution amounts: typing a custom amount clears the preset, and vice versa
  var custom = document.getElementById("amount_custom");
  if (custom) {
    custom.addEventListener("input", function () {
      if (custom.value) document.querySelectorAll("input[name=amount]").forEach(function (r) { r.checked = false; });
    });
    document.querySelectorAll("input[name=amount]").forEach(function (r) {
      r.addEventListener("change", function () { custom.value = ""; });
    });
  }

  // Copy link buttons
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var text = btn.getAttribute("data-copy");
      var done = function () { var o = btn.innerHTML; btn.textContent = "Link copied"; setTimeout(function () { btn.innerHTML = o; }, 1800); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () { window.prompt("Copy this link:", text); });
      else window.prompt("Copy this link:", text);
    });
  });

  // Native share sheet on phones
  document.querySelectorAll("[data-share]").forEach(function (btn) {
    if (!navigator.share) { btn.hidden = true; return; }
    btn.addEventListener("click", function () {
      navigator.share({ title: btn.getAttribute("data-title"), url: btn.getAttribute("data-share") }).catch(function () {});
    });
  });

  // Admin email editor: insert placeholders and keep the preview live
  var lastField = null;
  document.querySelectorAll("[data-insert-target]").forEach(function (el) {
    el.addEventListener("focus", function () { lastField = el; });
  });
  document.querySelectorAll("[data-insert]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var el = lastField || document.getElementById("em-html");
      if (!el) return;
      var text = btn.getAttribute("data-insert");
      var start = el.selectionStart || 0, end = el.selectionEnd || 0;
      el.value = el.value.slice(0, start) + text + el.value.slice(end);
      el.focus();
      el.selectionStart = el.selectionEnd = start + text.length;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
  var liveForm = document.querySelector("form[data-live-preview]");
  if (liveForm) {
    var timer = null;
    var refresh = function () {
      var oldAction = liveForm.getAttribute("action"), oldTarget = liveForm.getAttribute("target");
      liveForm.setAttribute("action", liveForm.getAttribute("data-live-preview"));
      liveForm.setAttribute("target", "email-preview");
      liveForm.submit();
      if (oldAction === null) liveForm.removeAttribute("action"); else liveForm.setAttribute("action", oldAction);
      if (oldTarget === null) liveForm.removeAttribute("target"); else liveForm.setAttribute("target", oldTarget);
    };
    liveForm.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(refresh, 700); });
    refresh();
  }

  // Confirm destructive admin actions
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.getAttribute("data-confirm"))) e.preventDefault();
    });
  });
})();
