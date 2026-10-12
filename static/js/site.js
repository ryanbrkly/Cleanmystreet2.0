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

  // Costing builder: live totals that mirror cms/costing.py
  document.querySelectorAll("form[data-costing]").forEach(function (form) {
    var VAT = parseFloat(form.getAttribute("data-vat")) || 15;
    var rowsBox = form.querySelector("[data-cost-rows]");
    var tpl = document.getElementById("cost-row-template");
    var labels = {};
    var firstSelect = form.querySelector("select[name=line_cat]");
    if (firstSelect) Array.prototype.forEach.call(firstSelect.options, function (o) { labels[o.value] = o.textContent; });
    function num(v) {
      var n = parseFloat(String(v || "").replace(/[R\s ]/g, "").replace(",", "."));
      return isFinite(n) && n > 0 ? n : 0;
    }
    function val(name, dflt) { var el = form.elements[name]; if (!el) return dflt; var n = parseFloat(String(el.value).replace(",", ".")); return isFinite(n) ? Math.max(0, n) : dflt; }
    function rand(cents) {
      var neg = cents < 0, r = Math.abs(cents) / 100;
      var s = (Math.round(r) === r ? Math.round(r).toString() : r.toFixed(2)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
      return (neg ? "-R" : "R") + s;
    }
    function roundUp(cents, step) { step = Math.round(step * 100); return step > 0 ? Math.ceil(cents / step - 1e-9) * step : Math.round(cents); }
    function price(cost, o, override) {
      cost = Math.round(cost);
      var v = o.vat ? VAT / 100 : 0;
      var charge = override ? Math.round(override) : roundUp(cost * (1 + o.markup / 100) * (1 + v), o.round);
      var excl = Math.round(charge / (1 + v)), fees = Math.round(charge * o.fee / 100), profit = excl - cost - fees;
      return { cost: cost, charge: charge, excl: excl, vat: charge - excl, fees: fees, profit: profit,
               margin: excl ? Math.round(profit * 1000 / excl) / 10 : 0, markup: cost ? Math.round((excl - cost) * 1000 / cost) / 10 : 0 };
    }
    function out(name, text) { var el = form.querySelector('[data-out="' + name + '"]'); if (el) el.textContent = text; }
    function calc() {
      var byCat = {}, order = [], direct = 0;
      form.querySelectorAll(".cost-row").forEach(function (row) {
        var q = num(row.querySelector("[name=line_qty]").value), r = Math.round(num(row.querySelector("[name=line_rate]").value) * 100);
        var total = Math.round(q * r), cat = row.querySelector("[name=line_cat]").value;
        row.querySelector("[data-line-total]").textContent = total ? rand(total) : "—";
        if (total) { if (!(cat in byCat)) { byCat[cat] = 0; order.push(cat); } byCat[cat] += total; direct += total; }
      });
      var o = { markup: val("markup_pct", 30), vat: form.elements.vat && form.elements.vat.checked, fee: val("fee_pct", 0), round: val("round_to", 10) };
      var cont = direct * val("contingency_pct", 0) / 100, over = (direct + cont) * val("overhead_pct", 0) / 100;
      var costF = direct + cont + over;
      var p = price(costF, o, Math.round(num(form.elements.price_override && form.elements.price_override.value) * 100));
      var cats = form.querySelector("[data-sum-cats]");
      if (cats) {
        cats.innerHTML = "";
        Object.keys(labels).forEach(function (k) {
          if (!byCat[k]) return;
          var d = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
          dt.textContent = labels[k]; dd.textContent = rand(byCat[k]); d.appendChild(dt); d.appendChild(dd); cats.appendChild(d);
        });
      }
      out("direct", rand(direct)); out("contingency", rand(Math.round(cont))); out("overhead", rand(Math.round(over)));
      out("cost", rand(p.cost)); out("excl", rand(p.excl)); out("vat", rand(p.vat)); out("charge", rand(p.charge));
      out("fees", rand(p.fees)); out("profit", rand(p.profit)); out("margin", p.margin + "% margin · " + p.markup + "% markup");
      var vr = form.querySelector("[data-vat-row]"); if (vr) vr.hidden = !o.vat;
      var pr = form.querySelector("[data-profit-row]");
      if (pr) { pr.classList.toggle("is-loss", p.profit < 0); pr.classList.toggle("is-thin", p.profit >= 0 && p.margin < 10); }
      var bar = form.querySelector("[data-split]");
      if (bar) { var sp = bar.children; sp[0].style.flexGrow = Math.max(p.cost, 0); sp[1].style.flexGrow = p.fees; sp[2].style.flexGrow = Math.max(p.profit, 0); }
      var mOn = form.elements.monthly_on && form.elements.monthly_on.checked;
      var mf = form.querySelector("[data-monthly-fields]"); if (mf) mf.hidden = !mOn;
      var ms = form.querySelector("[data-monthly-sum]");
      var visits = val("monthly_visits", 1);
      if (ms) ms.hidden = !(mOn && visits);
      if (mOn && visits) {
        var m = price(costF * val("monthly_factor_pct", 60) / 100 * visits, o, 0);
        out("m_cost", rand(m.cost)); out("m_charge", rand(m.charge)); out("m_profit", rand(m.profit)); out("m_margin", m.margin + "% margin");
        var mr = form.querySelector("[data-mprofit-row]"); if (mr) mr.classList.toggle("is-loss", m.profit < 0);
      }
      var man = form.querySelector("[data-public-manual]");
      if (man && form.elements.auto_public) man.hidden = form.elements.auto_public.checked;
    }
    function addRow(data) {
      if (!tpl || !rowsBox) return null;
      var row = tpl.content.firstElementChild.cloneNode(true);
      if (data) {
        row.querySelector("[name=line_cat]").value = data.cat;
        row.querySelector("[name=line_desc]").value = data.desc;
        row.querySelector("[name=line_unit]").value = data.unit;
        row.querySelector("[name=line_rate]").value = data.rate;
        row.querySelector("[name=line_qty]").value = "1";
      }
      rowsBox.appendChild(row);
      return row;
    }
    form.addEventListener("input", calc);
    form.addEventListener("change", calc);
    form.addEventListener("click", function (e) {
      if (e.target.closest("[data-cost-add]")) { var r = addRow(); if (r) r.querySelector("[name=line_desc]").focus(); calc(); }
      var rm = e.target.closest("[data-cost-remove]");
      if (rm) { var tr = rm.closest("tr"); if (form.querySelectorAll(".cost-row").length > 1) tr.remove(); else tr.querySelectorAll("input").forEach(function (i) { i.value = ""; }); calc(); }
    });
    var preset = form.querySelector("[data-cost-preset]");
    if (preset) preset.addEventListener("change", function () {
      var opt = preset.selectedOptions[0];
      if (!opt || !opt.value) return;
      // fill the first empty row, or add one
      var empty = Array.prototype.find.call(form.querySelectorAll(".cost-row"), function (r) { return !r.querySelector("[name=line_desc]").value && !r.querySelector("[name=line_rate]").value; });
      var data = { cat: opt.getAttribute("data-cat"), desc: opt.getAttribute("data-desc"), unit: opt.getAttribute("data-unit"), rate: opt.getAttribute("data-rate") };
      var row = empty;
      if (row) { row.querySelector("[name=line_cat]").value = data.cat; row.querySelector("[name=line_desc]").value = data.desc; row.querySelector("[name=line_unit]").value = data.unit; row.querySelector("[name=line_rate]").value = data.rate; row.querySelector("[name=line_qty]").value = "1"; }
      else row = addRow(data);
      preset.value = "";
      calc();
      var q = row && row.querySelector("[name=line_qty]"); if (q) { q.focus(); q.select(); }
    });
    calc();
  });

  // Confirm destructive admin actions
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.getAttribute("data-confirm"))) e.preventDefault();
    });
  });
})();
