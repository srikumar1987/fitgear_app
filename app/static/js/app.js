(function () {
  "use strict";

  // Keep the scroll position after a normal form post that redirects back to the same page.
  var SCROLL_KEY = "tur-scroll";
  try {
    var saved = JSON.parse(sessionStorage.getItem(SCROLL_KEY) || "null");
    sessionStorage.removeItem(SCROLL_KEY);
    if (saved && saved.path === location.pathname && Date.now() - saved.t < 10000 && !location.hash) {
      window.scrollTo(0, saved.y);
    }
  } catch (e) { /* ignore */ }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      e.preventDefault();
      return;
    }
    try {
      sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ path: location.pathname, y: window.scrollY, t: Date.now() }));
    } catch (err) { /* ignore */ }
  }, true);

  // "Saved" indicator for inline (HTMX) edits.
  var indicator = document.getElementById("save-indicator");
  var hideTimer;
  function flash(message, ok) {
    if (!indicator) return;
    indicator.textContent = message;
    indicator.className = "save-indicator show " + (ok ? "ok" : "err");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () { indicator.classList.remove("show"); }, ok ? 1200 : 4000);
  }
  document.body.addEventListener("htmx:afterRequest", function (e) {
    var verb = (e.detail.requestConfig && e.detail.requestConfig.verb) || "";
    if (verb.toLowerCase() !== "post") return;
    if (e.detail.successful) flash("Saved", true);
    else flash((e.detail.xhr && e.detail.xhr.responseText) || "Could not save", false);
  });
  document.body.addEventListener("htmx:sendError", function () { flash("App is not reachable. Is it still running?", false); });

  // Server can push normalised values back into inputs (e.g. "3xl" -> "XXXL").
  document.body.addEventListener("setValues", function (e) {
    (e.detail.items || []).forEach(function (pair) {
      var el = document.getElementById(pair[0]);
      if (!el) return;
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) el.value = pair[1];
      else el.textContent = pair[1];
    });
  });

  // "+ Another option" on the allocate form: copy the last option row with empty values.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-add-line]");
    if (!btn) return;
    var rows = btn.closest("form").querySelectorAll(".line-input");
    var last = rows[rows.length - 1];
    var copy = last.cloneNode(true);
    Array.prototype.forEach.call(copy.querySelectorAll("input"), function (i) { i.value = ""; });
    last.parentNode.insertBefore(copy, last.nextSibling);
    copy.querySelector("input").focus();
  });

  // Keyboard shortcuts: / search, n new order, r receive payment, v pay vendor.
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
    var body = document.body.dataset;
    if (e.key === "/") {
      var search = document.getElementById("global-search");
      if (search) { e.preventDefault(); search.focus(); }
    } else if (e.key === "n" && body.newOrder) {
      location.href = body.newOrder;
    } else if (e.key === "r" && body.receive) {
      location.href = body.receive;
    } else if (e.key === "v" && body.pay) {
      location.href = body.pay;
    }
  });

  // Payment split: fill the oldest balances first, show what is left to allocate.
  function paise(value) {
    return Math.round((parseFloat(String(value || "").replace(/[,₹\s]/g, "")) || 0) * 100);
  }
  function rupees(p) {
    return "₹" + (Math.abs(p) / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  }
  function setupSplit(form) {
    var amount = form.querySelector("[name=amount]");
    var parts = Array.prototype.slice.call(form.querySelectorAll("input[data-balance]"));
    var status = form.querySelector("[data-unallocated]");
    var submit = form.querySelector("[type=submit]");
    if (!amount || !parts.length) return;

    function refresh() {
      var diff = paise(amount.value) - parts.reduce(function (s, p) { return s + paise(p.value); }, 0);
      if (status) {
        status.textContent = diff === 0 ? "Fully allocated" : (diff > 0 ? "Left to allocate: " : "Over by: ") + rupees(diff);
        status.className = "small fw-semibold " + (diff === 0 ? "text-paid" : "text-overdue");
      }
      if (submit) submit.disabled = diff !== 0 || paise(amount.value) <= 0;
    }
    function autoSplit() {
      var remaining = paise(amount.value);
      parts.forEach(function (p) {
        var take = Math.min(Math.max(paise(p.dataset.balance), 0), remaining);
        p.value = take > 0 ? (take / 100).toFixed(2) : "";
        remaining -= take;
      });
      if (remaining > 0) {
        var open = parts.filter(function (p) { return paise(p.dataset.balance) > 0; });
        var target = open.length ? open[open.length - 1] : parts[0];
        target.value = ((paise(target.value) + remaining) / 100).toFixed(2);
      }
      refresh();
    }
    amount.addEventListener("input", autoSplit);
    parts.forEach(function (p) { p.addEventListener("input", refresh); });
    var autoBtn = form.querySelector("[data-auto-split]");
    if (autoBtn) autoBtn.addEventListener("click", autoSplit);
    refresh();
  }
  Array.prototype.forEach.call(document.querySelectorAll("form[data-split]"), setupSplit);

  // Design uploads: paste (Ctrl+V) or drag images into the box; they are added to the file input.
  function filesFrom(transfer) {
    var out = [];
    var stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
    var items = (transfer && transfer.items) ? Array.prototype.slice.call(transfer.items) : [];
    var raw = items.length
      ? items.filter(function (i) { return i.kind === "file"; }).map(function (i) { return i.getAsFile(); })
      : Array.prototype.slice.call((transfer && transfer.files) || []);
    raw.forEach(function (f, n) {
      if (!f || !/^image\/|^application\/pdf$/.test(f.type)) return;
      var generic = !f.name || /^image\.\w+$/i.test(f.name);
      var ext = (f.type.split("/")[1] || "png").replace("jpeg", "jpg");
      out.push(generic ? new File([f], "pasted-" + stamp + "-" + (n + 1) + "." + ext, { type: f.type }) : f);
    });
    return out;
  }
  function setupPasteUpload(form) {
    var input = form.querySelector("input[type=file]");
    var zone = form.querySelector("[data-paste-zone]");
    var preview = form.querySelector("[data-paste-preview]");
    if (!input || !zone || typeof DataTransfer === "undefined") return;

    function render() {
      preview.innerHTML = "";
      Array.prototype.forEach.call(input.files, function (f) {
        var el;
        if (f.type.indexOf("image/") === 0) {
          el = document.createElement("img");
          el.src = URL.createObjectURL(f);
        } else {
          el = document.createElement("span");
          el.className = "file-chip";
          el.textContent = f.name;
        }
        el.title = f.name;
        preview.appendChild(el);
      });
      zone.classList.toggle("has-files", input.files.length > 0);
    }
    function add(files) {
      if (!files.length) return false;
      var dt = new DataTransfer();
      Array.prototype.forEach.call(input.files, function (f) { dt.items.add(f); });
      files.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
      render();
      flash(files.length + (files.length === 1 ? " picture" : " pictures") + " added. Click Upload.", true);
      return true;
    }
    form._addFiles = add;
    form.addEventListener("paste", function (e) {
      if (add(filesFrom(e.clipboardData))) e.preventDefault();
    });
    zone.addEventListener("click", function () { zone.focus(); });
    zone.addEventListener("dragover", function (e) { e.preventDefault(); zone.classList.add("drag"); });
    zone.addEventListener("dragleave", function () { zone.classList.remove("drag"); });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.classList.remove("drag");
      add(filesFrom(e.dataTransfer));
    });
    input.addEventListener("change", render);
  }
  Array.prototype.forEach.call(document.querySelectorAll("form[data-paste-upload]"), setupPasteUpload);

  // Ctrl+V anywhere else on the Designs tab goes to the first (Design) box.
  document.addEventListener("paste", function (e) {
    if (e.target.closest && e.target.closest("form[data-paste-upload]")) return;
    if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName) || e.target.isContentEditable) return;
    var target = document.querySelector("form[data-paste-default]");
    if (target && target._addFiles && target._addFiles(filesFrom(e.clipboardData))) {
      e.preventDefault();
      target.scrollIntoView({ block: "nearest" });
    }
  });

  // Copy-to-clipboard buttons (statements for WhatsApp).
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    var source = document.getElementById(btn.dataset.copy);
    if (!source) return;
    source.select();
    var done = function () { flash("Copied. Paste it into WhatsApp.", true); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(source.value).then(done, function () { document.execCommand("copy"); done(); });
    } else {
      document.execCommand("copy");
      done();
    }
  });
})();
