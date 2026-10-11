// Preview-only helper: forms and video show a short message instead of submitting.
(function () {
  var toast = document.getElementById("pv-toast");
  var timer;
  function say(msg) {
    if (!toast) return;
    toast.textContent = msg; toast.hidden = false;
    clearTimeout(timer); timer = setTimeout(function () { toast.hidden = true; }, 3600);
  }
  document.addEventListener("submit", function (e) {
    e.preventDefault(); e.stopImmediatePropagation();
    say("This is a preview. In the installed app this button saves, submits or opens the Yoco checkout.");
  }, true);
  document.addEventListener("click", function (e) {
    if (e.target.closest(".yt__play")) {
      e.preventDefault(); e.stopImmediatePropagation();
      say("Your YouTube video plays here once you paste its link in Admin, Site settings.");
    }
    var a = e.target.closest('a[href="#csv"]');
    if (a) { e.preventDefault(); say("In the installed app this downloads the payments as a CSV file."); }
  }, true);
  var css = document.createElement("style");
  css.textContent = "#pv-toast{position:fixed;left:50%;bottom:calc(20px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);z-index:300;max-width:min(520px,calc(100vw - 32px));background:#1f2a5c;color:#fff;padding:14px 18px;border-radius:10px;font-weight:600;font-size:.95rem;box-shadow:0 16px 40px -12px rgba(0,0,0,.5);border-left:6px solid #d6f43c}";
  document.head.appendChild(css);
})();
