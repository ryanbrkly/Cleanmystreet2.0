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

  // Lightbox for gallery and progress photos
  var lb = document.getElementById("lightbox");
  var lastFocus = null;
  function openLightbox(src, caption) {
    if (!lb) return;
    lastFocus = document.activeElement;
    lb.querySelector("img").src = src;
    lb.querySelector("img").alt = caption || "";
    lb.querySelector("p").textContent = caption || "";
    lb.classList.add("open");
    lb.querySelector("[data-close]").focus();
  }
  function closeLightbox() {
    if (!lb) return;
    lb.classList.remove("open");
    lb.querySelector("img").src = "";
    if (lastFocus) lastFocus.focus();
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-lightbox]");
    if (t) { e.preventDefault(); openLightbox(t.getAttribute("data-lightbox"), t.getAttribute("data-caption")); return; }
    if (lb && (e.target === lb || e.target.closest("[data-close]"))) closeLightbox();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && lb && lb.classList.contains("open")) closeLightbox();
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

  // Confirm destructive admin actions
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.getAttribute("data-confirm"))) e.preventDefault();
    });
  });
})();
