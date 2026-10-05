/* Intuition guide: ticks, the built-so-far tracker, the soft lock, copy buttons, checks,
   replays and the theme toggle. No network, no libraries. Progress lives in localStorage under
   "intuition-guide:v1:<step-id>"; every access is wrapped so the page still works when storage is
   blocked (then ticks last until the page closes). */
(function () {
  "use strict";

  var G = {};
  try { G = JSON.parse(document.getElementById("guide-data").textContent) || {}; } catch (e) { G = {}; }
  var PREFIX = G.prefix || "intuition-guide:v1:";
  var memory = {};

  var store = (function () {
    try {
      var s = window.localStorage;
      var probe = PREFIX + "__probe";
      s.setItem(probe, "1");
      s.removeItem(probe);
      return s;
    } catch (e) {
      return null;
    }
  })();

  function get(key) {
    if (store) {
      try { return store.getItem(PREFIX + key); } catch (e) { /* fall through */ }
    }
    return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null;
  }
  function put(key, value) {
    if (value === null) { delete memory[key]; } else { memory[key] = value; }
    if (!store) return false;
    try {
      if (value === null) store.removeItem(PREFIX + key); else store.setItem(PREFIX + key, value);
      return true;
    } catch (e) {
      return false;
    }
  }
  function isTicked(id) { return get(id) === "1"; }
  function anchor(id) { return "step-" + String(id).replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase(); }
  function each(sel, fn, root) { Array.prototype.forEach.call((root || document).querySelectorAll(sel), fn); }

  if (!store) each("[data-storage-note]", function (el) { el.hidden = false; });

  /* ------------------------------------------------------------ theme */
  var root = document.documentElement;
  var themeBtn = document.querySelector("[data-theme-toggle]");
  var theme = get("pref:theme");
  function applyTheme(t) {
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t); else root.removeAttribute("data-theme");
    if (themeBtn) {
      var mode = t || "auto";
      var text = mode === "auto" ? "Theme follows your system" : (mode === "light" ? "Light theme" : "Dark theme");
      themeBtn.setAttribute("data-mode", mode);
      themeBtn.setAttribute("aria-label", text + ". Click to change.");
      themeBtn.title = text;
    }
  }
  applyTheme(theme);
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      theme = theme === null ? "light" : (theme === "light" ? "dark" : null);
      put("pref:theme", theme);
      applyTheme(theme);
    });
  }

  /* ------------------------------------------------------------ menu: close on outside click */
  var menu = document.querySelector("[data-menu]");
  if (menu) {
    document.addEventListener("click", function (ev) { if (menu.open && !menu.contains(ev.target)) menu.open = false; });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });
  }

  /* ------------------------------------------------------------ copy buttons */
  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  function selectText(el) {
    try {
      var range = document.createRange();
      range.selectNodeContents(el);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (e) { /* nothing more to do */ }
  }
  each("[data-copy]", function (btn) {
    btn.addEventListener("click", function () {
      var pre = btn.parentNode.querySelector("pre");
      var text = pre ? pre.textContent : "";
      function done(ok) {
        btn.textContent = ok ? "Copied" : "Select and copy";
        btn.classList.toggle("copied", ok);
        if (!ok && pre) selectText(pre);
        clearTimeout(btn._t);
        btn._t = setTimeout(function () { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1800);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(fallbackCopy(text)); });
      } else {
        done(fallbackCopy(text));
      }
    });
  });

  /* ------------------------------------------------------------ ticks, progress, tracker */
  var steps = G.steps || [];
  function refresh() {
    var done = 0;
    steps.forEach(function (id) {
      var on = isTicked(id);
      if (on) done += 1;
      var card = document.getElementById(anchor(id));
      if (card) card.classList.toggle("is-done", on);
      each('[data-toc="' + id + '"]', function (li) { li.classList.toggle("is-done", on); });
    });
    var txt = document.querySelector("[data-progress-text]");
    if (txt) txt.textContent = done + " of " + steps.length + " step" + (steps.length === 1 ? "" : "s") + " done";
    var fill = document.querySelector("[data-progress-fill]");
    if (fill) fill.style.width = (steps.length ? Math.round(100 * done / steps.length) : 0) + "%";

    var built = 0, total = 0;
    each("[data-built-by]", function (li) {
      var on = isTicked(li.getAttribute("data-built-by"));
      total += 1;
      if (on) built += 1;
      li.classList.toggle("is-built", on);
      var pill = li.querySelector("[data-pill]");
      if (pill) pill.textContent = on ? "Built" : "Not yet";
    });
    var summary = document.querySelector("[data-tracker-summary]");
    if (summary && total) {
      summary.textContent = built === 0
        ? "Every file the workshop produces. Tick a step and the files it builds are marked built."
        : built + " of " + total + " built so far. Ticks from every page count here.";
    }
  }
  each("[data-tick]", function (box) {
    var id = box.getAttribute("data-tick");
    box.checked = isTicked(id);
    box.addEventListener("change", function () {
      put(id, box.checked ? "1" : null);
      refresh();
    });
  });
  refresh();
  window.addEventListener("storage", function (ev) {
    if (ev.key && ev.key.indexOf(PREFIX) === 0) {
      each("[data-tick]", function (box) { box.checked = isTicked(box.getAttribute("data-tick")); });
      refresh();
    }
  });

  /* ------------------------------------------------------------ soft lock on later blocks */
  (function softLock() {
    var idx = G.courseIndex;
    var main = document.querySelector("[data-lesson-main]");
    var note = document.querySelector("[data-lock]");
    if (!main || !note || typeof idx !== "number" || idx < 1 || !store) return; // no storage: never lock
    if (get("show:" + G.page) === "1") return;
    var miss = null;
    for (var i = 0; i < idx && !miss; i++) {
      var p = G.course[i];
      for (var j = 0; j < p.steps.length; j++) {
        if (!isTicked(p.steps[j].id)) { miss = { page: p, step: p.steps[j] }; break; }
      }
    }
    if (!miss) return;
    var href = miss.page.slug + ".html#" + anchor(miss.step.id);
    main.classList.add("is-locked");
    note.hidden = false;
    each("[data-lock-go], [data-lock-step]", function (a) { a.href = href; });
    each("[data-lock-step]", function (a) { a.textContent = "step " + miss.step.id + " (" + miss.step.title + ")"; });
    each("[data-lock-page]", function (a) { a.href = miss.page.slug + ".html"; a.textContent = miss.page.label; });
    var show = note.querySelector("[data-show-anyway]");
    if (show) {
      show.addEventListener("click", function () {
        put("show:" + G.page, "1");
        main.classList.remove("is-locked");
        note.hidden = true;
      });
    }
  })();

  /* ------------------------------------------------------------ checks */
  each("[data-quiz]", function (fs) {
    var answer = parseInt(fs.getAttribute("data-answer"), 10);
    var why = fs.querySelector("[data-why]");
    var verdict = fs.querySelector("[data-verdict]");
    each('input[type="radio"]', function (input) {
      input.addEventListener("change", function () {
        var pick = parseInt(input.value, 10);
        each(".opt", function (opt) { opt.classList.remove("is-right", "is-wrong"); }, fs);
        var label = input.closest(".opt");
        var right = pick === answer;
        if (label) label.classList.add(right ? "is-right" : "is-wrong");
        if (!right) {
          var correct = fs.querySelectorAll(".opt")[answer];
          if (correct) correct.classList.add("is-right");
        }
        if (why) {
          why.hidden = false;
          why.classList.toggle("is-right", right);
          why.classList.toggle("is-wrong", !right);
        }
        if (verdict) verdict.textContent = right ? "Right." : "Not quite.";
      });
    }, fs);
  });

  /* ------------------------------------------------------------ replays */
  each("[data-replay]", function (box) {
    var list = box.querySelector("[data-timeline]");
    var play = box.querySelector("[data-play]");
    var all = box.querySelector("[data-show-all]");
    if (!list || !play) return;
    var items = list.children;
    var timer = null;
    var i = 0;
    function stop(label) {
      clearInterval(timer);
      timer = null;
      play.textContent = label || "Play it back";
    }
    function showAll() {
      stop();
      Array.prototype.forEach.call(items, function (li) { li.classList.remove("tl-hidden"); });
      i = items.length;
    }
    play.addEventListener("click", function () {
      if (timer) { stop("Resume"); return; }
      if (i >= items.length || i === 0) {
        Array.prototype.forEach.call(items, function (li) { li.classList.add("tl-hidden"); li.classList.remove("tl-in"); });
        list.scrollTop = 0;
        i = 0;
      }
      play.textContent = "Pause";
      timer = setInterval(function () {
        if (i >= items.length) { stop("Play again"); return; }
        var li = items[i];
        li.classList.remove("tl-hidden");
        li.classList.add("tl-in");
        var target = li.offsetTop + li.offsetHeight - list.clientHeight + 16;
        if (target > list.scrollTop) list.scrollTop = target;
        i += 1;
      }, 420);
    });
    if (all) all.addEventListener("click", showAll);
    box.addEventListener("toggle", function () { if (!box.open) showAll(); });
  });
})();
