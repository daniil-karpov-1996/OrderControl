(function () {
  window.wsClientId = "dc-" + Math.random().toString(36).slice(2) + "-" + Date.now().toString(36);
})();

(function () {
  try {
    const el = document.getElementById("footerYear");
    if (el) el.textContent = String(new Date().getFullYear());
  } catch (_) { }
})();

(function () {
  window.toggleTheme = function () {
    const cur = document.documentElement.getAttribute("data-theme") || "dark";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (_) { }
    syncThemeMenuChoices();
  };

  function syncThemeMenuChoices() {
    const cur = document.documentElement.getAttribute("data-theme") || "dark";
    document.querySelectorAll("[data-theme-pick]").forEach(function (btn) {
      const v = btn.getAttribute("data-theme-pick");
      const on = v === cur;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  document.addEventListener("click", function (e) {
    const pick = e.target.closest("[data-theme-pick]");
    if (!pick) return;
    e.preventDefault();
    const th = pick.getAttribute("data-theme-pick");
    if (th !== "light" && th !== "dark") return;
    document.documentElement.setAttribute("data-theme", th);
    try {
      localStorage.setItem("theme", th);
    } catch (_) { }
    syncThemeMenuChoices();
  });

  function bootThemeUi() {
    syncThemeMenuChoices();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootThemeUi);
  } else {
    bootThemeUi();
  }

  const path = location.pathname.replace(/\/+$/, "") || "/";
  document.querySelectorAll(".app-nav a.app-link, .app-nav a.app-nav-dd__item").forEach(a => {
    const href = (a.getAttribute("href") || "").replace(/\/+$/, "") || "/";
    if (href === path) a.classList.add("active");
  });
})();

(function () {
  var toggle = document.getElementById("appNavToggle");
  var overlay = document.getElementById("appNavOverlay");
  var nav = document.getElementById("appNav");
  if (!toggle || !overlay || !nav) return;

  function isOpen() {
    return document.body.classList.contains("nav-open");
  }
  function closeNav() {
    document.body.classList.remove("nav-open");
    toggle.setAttribute("aria-expanded", "false");
    overlay.setAttribute("aria-hidden", "true");
  }
  function openNav() {
    var um = document.getElementById("appUserMenu");
    if (um) um.open = false;
    var gt = document.getElementById("appGuestThemeMenu");
    if (gt) gt.open = false;
    document.body.classList.add("nav-open");
    toggle.setAttribute("aria-expanded", "true");
    overlay.setAttribute("aria-hidden", "false");
  }

  toggle.addEventListener("click", function () {
    if (isOpen()) closeNav();
    else openNav();
  });
  overlay.addEventListener("click", closeNav);
  nav.querySelectorAll("a.app-link, a.app-nav-dd__item").forEach(function (a) {
    a.addEventListener("click", function () {
      if (window.matchMedia && window.matchMedia("(max-width: 900px)").matches) closeNav();
    });
  });
})();

(function () {
  var nav = document.getElementById("appNav");
  if (!nav) return;
  var dds = nav.querySelectorAll(".app-nav-dd");
  if (!dds.length) return;

  dds.forEach(function (dd) {
    dd.addEventListener("toggle", function () {
      if (!dd.open) return;
      dds.forEach(function (other) {
        if (other !== dd) other.open = false;
      });
      var um = document.getElementById("appUserMenu");
      if (um) um.open = false;
      var gt = document.getElementById("appGuestThemeMenu");
      if (gt) gt.open = false;
    });
  });

  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest(".app-nav-dd")) return;
    dds.forEach(function (dd) {
      dd.open = false;
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    dds.forEach(function (dd) {
      dd.open = false;
    });
  });

  nav.querySelectorAll("a.app-nav-dd__item").forEach(function (a) {
    a.addEventListener("click", function () {
      var dd = a.closest(".app-nav-dd");
      if (dd) dd.open = false;
    });
  });
})();

(function () {
  var menus = [document.getElementById("appUserMenu"), document.getElementById("appGuestThemeMenu")].filter(
    Boolean
  );
  if (!menus.length) return;

  function closeNavDropdowns() {
    var nav = document.getElementById("appNav");
    if (!nav) return;
    nav.querySelectorAll(".app-nav-dd").forEach(function (dd) {
      dd.open = false;
    });
  }

  menus.forEach(function (menu) {
    menu.addEventListener("toggle", function () {
      if (menu.open) closeNavDropdowns();
    });
  });

  document.addEventListener("click", function (e) {
    menus.forEach(function (menu) {
      if (!menu.open) return;
      if (menu.contains(e.target)) return;
      menu.open = false;
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    menus.forEach(function (menu) {
      if (menu.open) menu.open = false;
    });
  });
})();


(function () {
  let iframeDocWithHotkeys = null;
  let iframeHotkeysHandler = null;

  function isModalOpen() {
    const backdrop = document.getElementById("modalBackdrop");
    return !!(backdrop && !backdrop.classList.contains("hidden"));
  }

  function bindIframeHotkeys(doc) {
    if (!doc) return;
    if (iframeDocWithHotkeys && iframeHotkeysHandler) {
      try { iframeDocWithHotkeys.removeEventListener("keydown", iframeHotkeysHandler, true); } catch (_) { }
    }
    iframeHotkeysHandler = function (e) {
      if (!isModalOpen()) return;
      if (e.key === "Escape") {
        e.preventDefault();
        closeModal();
        return;
      }
    };
    doc.addEventListener("keydown", iframeHotkeysHandler, true);
    iframeDocWithHotkeys = doc;
  }

  function openModal(url, title) {
    const backdrop = document.getElementById("modalBackdrop");
    const frame = document.getElementById("modalFrame");
    const modal = document.getElementById("modalWindow");
    const titleEl = document.getElementById("modalTitle");
    if (!backdrop || !frame) {
      window.location.href = url;
      return;
    }
    if (modal) modal.style.height = "";
    if (titleEl && title) titleEl.textContent = title;
    var sbw = Math.max(0, window.innerWidth - document.documentElement.clientWidth);
    document.body.style.setProperty("--sbw", sbw + "px");
    document.body.classList.add("modal-open");

    if (modal) {
      modal.classList.add("is-loading");
    }
    frame.style.opacity = "0";
    backdrop.classList.add("is-preloading");
    backdrop.classList.remove("hidden");
    backdrop.classList.add("is-visible");
    frame.src = url;

    frame.onload = function () {
      var skipReveal = false;
      try {
        const doc = frame.contentDocument || frame.contentWindow.document;
        if (!doc) return;
        const loadedPath = (frame.contentWindow && frame.contentWindow.location && frame.contentWindow.location.pathname) || "";
        var isModalClose = loadedPath === "/modal/close" || (loadedPath.length && loadedPath.endsWith("/modal/close"));
        if (isModalClose) {
          if (modal) modal.style.height = "";
          skipReveal = true;
          return;
        }
        bindIframeHotkeys(doc);
        var iw = frame.contentWindow;
        if (iw && typeof iw.icInitComboboxes === "function") {
          try {
            iw.icInitComboboxes(doc);
          } catch (_) { }
        }
        const container = doc.querySelector(".container") || doc.body;
        const contentHeight = container ? container.offsetHeight : 0;
        if (modal && contentHeight) {
          const header = modal.querySelector(".modal__header");
          const headerH = header ? header.offsetHeight : 0;
          const desired = Math.min(contentHeight + headerH + 24, window.innerHeight - 20, 860);
          modal.style.height = desired + "px";
        }
      } catch (_) {
      } finally {
        if (modal) modal.classList.remove("is-loading");
        backdrop.classList.remove("is-preloading");
        if (!skipReveal) frame.style.opacity = "1";
      }
    };
  }

  function closeModal(opts) {
    opts = opts || {};
    var instant = !!opts.instant;
    const backdrop = document.getElementById("modalBackdrop");
    const frame = document.getElementById("modalFrame");
    const modal = document.getElementById("modalWindow");
    if (!backdrop || !frame) return;
    backdrop.classList.remove("is-visible");

    function doCleanup() {
      if (iframeDocWithHotkeys && iframeHotkeysHandler) {
        try { iframeDocWithHotkeys.removeEventListener("keydown", iframeHotkeysHandler, true); } catch (_) { }
      }
      iframeDocWithHotkeys = null;
      iframeHotkeysHandler = null;
      frame.src = "about:blank";
      frame.style.opacity = "0";
      if (modal) {
        modal.classList.remove("is-loading");
        modal.style.height = "";
      }
      backdrop.classList.remove("is-preloading");
      backdrop.classList.add("hidden");
      document.body.classList.remove("modal-open");
      document.body.style.removeProperty("--sbw");
      window.location.reload();
    }

    if (instant) {
      doCleanup();
      return;
    }
    setTimeout(doCleanup, 180);
  }

  window.openEditModal = openModal;
  window.closeEditModal = closeModal;

  document.addEventListener("keydown", function (e) {
    if (!isModalOpen()) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeModal();
    }
  }, true);

  function clickTargetElement(t) {
    if (!t) return null;
    if (t.nodeType === 1) return t;
    return t.parentElement || null;
  }

  document.addEventListener("click", function (e) {
    const el = clickTargetElement(e.target);
    const btn = el && el.closest && el.closest("[data-modal-close]");
    if (btn) {
      e.preventDefault();
      closeModal();
      return;
    }
    const link = el && el.closest && el.closest("a[data-open-modal]");
    if (!link) return;
    const url = link.href || (link.getAttribute("href") || "").replace(/&amp;/g, "&");
    const title = link.getAttribute("data-modal-title") || link.getAttribute("title") || "";
    if (!url) return;
    e.preventDefault();
    openModal(url, title);
  });
})();

(function () {
  if (window.self !== window.top) {
    document.documentElement.setAttribute("data-in-modal", "1");
  }
})();

(function () {
  window.toggleDescription = function (button) {
    const descriptionDiv = button.previousElementSibling;
    const isExpanded = descriptionDiv.classList.contains("expanded");

    if (isExpanded) {
      descriptionDiv.classList.remove("expanded");
      button.innerText = "Показать больше";
    } else {
      const allDescriptions = document.querySelectorAll(".description.expanded");
      allDescriptions.forEach((desc) => {
        desc.classList.remove("expanded");
        const btn = desc.nextElementSibling;
        btn.innerText = "Показать больше";
      });

      descriptionDiv.classList.add("expanded");
      button.innerText = "Скрыть";
    }
  };

})();

(function () {
  function updateDescriptionButtons() {
    document.querySelectorAll(".description-cell").forEach((cell) => {
      const desc = cell.querySelector(".description");
      const btn = cell.querySelector(".toggle-description-btn");

      if (!desc || !btn) return;

      const isOverflowing = desc.scrollWidth > desc.clientWidth;

      if (isOverflowing) {
        btn.classList.remove("hidden");
      } else {
        btn.classList.add("hidden");
      }
    });
  }

  document.addEventListener("DOMContentLoaded", updateDescriptionButtons);
  window.addEventListener("resize", updateDescriptionButtons);
})();

(function () {
  function cacheAllocPopRefs(details) {
    if (!details._allocPanel) {
      details._allocPanel = details.querySelector(".alloc-pop__panel");
    }
    if (!details._allocSummary) {
      details._allocSummary = details.querySelector(".alloc-pop__summary");
    }
  }

  function repositionPanel(details) {
    const panel = details._allocPanel;
    const summary = details._allocSummary;
    if (!panel || !summary || !details.open || !panel.classList.contains("alloc-pop__panel--layer")) return;
    const r = summary.getBoundingClientRect();
    const pw = Math.max(panel.offsetWidth || 0, 260);
    const ph = panel.offsetHeight || 0;
    let left = r.left;
    if (left + pw > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - pw - 8);
    }
    if (left < 8) left = 8;
    let top = r.bottom + 6;
    if (top + ph > window.innerHeight - 8 && ph > 0) {
      top = Math.max(8, r.top - ph - 6);
    }
    panel.style.left = left + "px";
    panel.style.top = top + "px";
  }

  function attachFloating(details) {
    cacheAllocPopRefs(details);
    const panel = details._allocPanel;
    const summary = details._allocSummary;
    if (!panel || !summary) return;
    document.body.appendChild(panel);
    panel.classList.add("alloc-pop__panel--layer");
    repositionPanel(details);
    requestAnimationFrame(function () {
      repositionPanel(details);
    });
  }

  function detachFloating(details) {
    cacheAllocPopRefs(details);
    const panel = details._allocPanel;
    if (!panel) return;
    panel.classList.remove("alloc-pop__panel--layer");
    panel.style.left = "";
    panel.style.top = "";
    if (panel.parentNode === document.body) {
      details.appendChild(panel);
    }
  }

  function onScrollOrResize() {
    document.querySelectorAll("details.alloc-pop[open]").forEach(function (d) {
      repositionPanel(d);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("details.alloc-pop").forEach(cacheAllocPopRefs);
    document.querySelectorAll(".table-wrap").forEach(function (el) {
      el.addEventListener("scroll", onScrollOrResize, { passive: true });
    });
  });

  document.addEventListener("toggle", function (e) {
    const d = e.target;
    if (!d || !d.classList || !d.classList.contains("alloc-pop")) return;
    cacheAllocPopRefs(d);
    if (d.open) {
      document.querySelectorAll("details.alloc-pop[open]").forEach(function (other) {
        if (other !== d) other.removeAttribute("open");
      });
      attachFloating(d);
    } else {
      detachFloating(d);
    }
  }, true);

  document.addEventListener("click", function (e) {
    const t = e.target;
    if (t && t.closest && t.closest("details.alloc-pop")) return;
    if (t && t.closest && t.closest(".alloc-pop__panel--layer")) return;
    document.querySelectorAll("details.alloc-pop[open]").forEach(function (d) {
      d.removeAttribute("open");
    });
  });

  window.addEventListener("scroll", onScrollOrResize, true);
  window.addEventListener("resize", onScrollOrResize);

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    document.querySelectorAll("details.alloc-pop[open]").forEach(function (d) {
      d.removeAttribute("open");
    });
  });
})();

(function () {
  document.addEventListener("click", function (e) {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || a.getAttribute("href").startsWith("#")) return;
    const href = a.getAttribute("href");
    if (href.indexOf("page=") === -1) return;
    try {
      const targetPath = new URL(a.href, location.origin).pathname.replace(/\/+$/, "") || "/";
      const currentPath = location.pathname.replace(/\/+$/, "") || "/";
      if (targetPath !== currentPath) return;
      sessionStorage.setItem("paginationScrollY", String(window.scrollY));
    } catch (_) { }
  });

  function restoreScroll() {
    try {
      const saved = sessionStorage.getItem("paginationScrollY");
      if (saved == null) return;
      sessionStorage.removeItem("paginationScrollY");
      const y = parseInt(saved, 10);
      if (!isFinite(y) || y < 0) return;
      requestAnimationFrame(function () {
        window.scrollTo(0, y);
      });
    } catch (_) { }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", restoreScroll);
  } else {
    restoreScroll();
  }
})();

(function () {
  if (window.self !== window.top) return;
  const isMobile = window.matchMedia && window.matchMedia("(max-width: 640px)").matches;

  const STORAGE_KEY = "dc_toasts_v1";
  const MAX_TOASTS = 5;
  const timers = new Map();

  function now() { return Date.now(); }
  function safeText(s) { return String(s ?? ""); }
  function uid() { return "t-" + Math.random().toString(36).slice(2) + "-" + Date.now().toString(36); }

  function loadToasts() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (_) { return []; }
  }
  function saveToasts(arr) {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(arr)); } catch (_) { }
  }

  function cleanupExpired(arr) {
    const t = now();
    return arr.filter(x => {
      const timeoutMs = Number(x.timeoutMs || 0) || 0;
      if (timeoutMs <= 0) return true;
      return (Number(x.createdAt || 0) + timeoutMs) > t;
    });
  }

  function removeToast(id) {
    const tid = timers.get(id);
    if (tid) {
      clearTimeout(tid);
      timers.delete(id);
    }
    const arr = loadToasts().filter(t => t.id !== id);
    saveToasts(arr);
    render();
  }

  function createToastEl(t) {
    const el = document.createElement("div");
    el.className = "toast";
    el.dataset.variant = t.variant || "success";
    el.dataset.id = t.id;

    const top = document.createElement("div");
    top.className = "toast__top";

    const content = document.createElement("div");
    const title = document.createElement("div");
    title.className = "toast__title";
    title.textContent = safeText(t.title);

    const body = document.createElement("div");
    body.className = "toast__body";
    body.textContent = safeText(t.body);

    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast__close";
    close.textContent = "×";
    close.addEventListener("click", () => removeToast(t.id));

    content.appendChild(title);
    if (t.body) content.appendChild(body);
    top.appendChild(content);
    top.appendChild(close);

    const prog = document.createElement("div");
    prog.className = "toast__progress";
    const bar = document.createElement("span");
    prog.appendChild(bar);

    el.appendChild(top);
    el.appendChild(prog);

    const timeoutMs = Number(t.timeoutMs || 0) || 0;
    if (timeoutMs > 0) {
      const startAt = Number(t.createdAt || now());
      const left = Math.max(0, (startAt + timeoutMs) - now());
      const frac = Math.max(0, Math.min(1, left / timeoutMs));
      bar.style.transition = "none";
      bar.style.transform = `scaleX(${frac})`;
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          bar.style.transition = `transform ${left}ms linear`;
          bar.style.transform = "scaleX(0)";
        });
      });

      if (!timers.has(t.id)) {
        timers.set(t.id, setTimeout(() => removeToast(t.id), left + 50));
      }
    } else {
      bar.style.display = "none";
    }

    return el;
  }

  function render() {
    const host = document.getElementById("toastHost");
    if (!host) return;
    let arr = cleanupExpired(loadToasts());
    arr = arr.slice(-MAX_TOASTS).reverse();
    saveToasts(arr.slice().reverse());

    host.innerHTML = "";
    for (const t of arr) host.appendChild(createToastEl(t));
  }

  window.showToast = function (opts) {
    opts = opts || {};
    const t = {
      id: uid(),
      title: safeText(opts.title || ""),
      body: safeText(opts.body || ""),
      variant: safeText(opts.variant || "success"),
      timeoutMs: Number(opts.timeoutMs || 10000) || 10000,
      createdAt: now(),
    };
    let arr = cleanupExpired(loadToasts());
    arr.push(t);
    if (arr.length > MAX_TOASTS) arr = arr.slice(arr.length - MAX_TOASTS);
    saveToasts(arr);
    render();
  };

  function consumeUrlToastsNoop() {
    try {
      const url = new URL(window.location.href);
      const keys = ["toast_title", "toast_body", "toast_variant", "toast_timeout"];
      let changed = false;
      for (const k of keys) {
        if (url.searchParams.has(k)) {
          url.searchParams.delete(k);
          changed = true;
        }
      }
      if (changed) window.history.replaceState({}, "", url.toString());
    } catch (_) { }
  }

  if (isMobile) {
    window.showToast = function () { };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => {
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) { }
        consumeUrlToastsNoop();
      });
    } else {
      try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) { }
      consumeUrlToastsNoop();
    }
    return;
  }

  function consumeUrlToasts() {
    try {
      const url = new URL(window.location.href);

      if (url.searchParams.get("clear_toasts") === "1") {
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) { }
        url.searchParams.delete("clear_toasts");
        window.history.replaceState({}, "", url.toString());
        return;
      }

      const title = url.searchParams.get("toast_title");
      if (!title) return;
      const body = url.searchParams.get("toast_body") || "";
      const variant = url.searchParams.get("toast_variant") || "success";
      const timeoutMs = Number(url.searchParams.get("toast_timeout") || 10000) || 10000;

      window.showToast({ title, body, variant, timeoutMs });

      url.searchParams.delete("toast_title");
      url.searchParams.delete("toast_body");
      url.searchParams.delete("toast_variant");
      url.searchParams.delete("toast_timeout");
      window.history.replaceState({}, "", url.toString());
    } catch (_) { }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      consumeUrlToasts();
      render();
    });
  } else {
    consumeUrlToasts();
    render();
  }
})();

(function () {
  function getWsContext() {
    var path = (location.pathname || "").replace(/\/+$/, "") || "/";
    if (path === "/" || path === "/admin") return "dashboard";
    if (path === "/clients") return "clients";
    var m = /^\/clients\/(\d+)$/.exec(path);
    if (m) return "client/" + m[1];
    if (path === "/categories") return "categories";
    m = /^\/categories\/(\d+)$/.exec(path);
    if (m) return "category/" + m[1];
    if (path === "/subcategories") return "subcategories";
    m = /^\/subcategories\/(\d+)$/.exec(path);
    if (m) return "subcategory/" + m[1];
    if (path === "/products") return "products";
    m = /^\/products\/(\d+)$/.exec(path);
    if (m) return "product/" + m[1];
    if (path === "/stock/in") return "stock_in";
    if (path === "/stock/out") return "stock_out";
    if (path === "/stock/inventory") return "inventory";
    if (path === "/stock/moves") return "stock_moves";
    if (path === "/admins" || /^\/admins\/\d+$/.test(path)) return "admins";
    if (path === "/logs") return "logs";
    return null;
  }

  var lastRtId = -1;
  var pollActiveMs = 2500;
  var pollHiddenMs = 12000;
  var pollErrorBaseMs = 4000;
  var rtPollTimer = null;
  var rtBackoffMs = pollErrorBaseMs;
  var rtMaxBackoffMs = 60000;
  var rtUserEnabled = false;
  var rtMenuToggleBtn = null;

  function getRtPrefKey() {
    return String(window.__icRealtimePrefKey || "oc_rt_enabled_u0");
  }

  function loadRtEnabled() {
    try {
      var raw = localStorage.getItem(getRtPrefKey());
      if (raw == null) return !!window.__icRealtimeDefaultOn;
      return raw === "1";
    } catch (_) {
      return !!window.__icRealtimeDefaultOn;
    }
  }

  function saveRtEnabled(v) {
    try {
      localStorage.setItem(getRtPrefKey(), v ? "1" : "0");
    } catch (_) { }
  }

  function realtimeEnabled() {
    return !!rtUserEnabled;
  }

  function getBasePollMs() {
    return document.hidden ? pollHiddenMs : pollActiveMs;
  }

  if (!window.__icComboboxClickBound) {
    window.__icComboboxClickBound = true;
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      document.querySelectorAll(".ic-combobox").forEach(function (w) {
        if (!w.contains(t) && typeof w.__icClose === "function") w.__icClose();
      });
    });
  }

  function icInitComboboxes(root) {
    root = root || document;
    if (!root.querySelectorAll) return;
    root.querySelectorAll("select.ic-combobox").forEach(function (sel) {
      if (sel.getAttribute("data-ic-combobox-init") === "1" || sel.disabled) return;
      sel.setAttribute("data-ic-combobox-init", "1");

      function optLabel(opt) {
        return (opt.textContent || "").replace(/\s+/g, " ").trim();
      }

      var placeholderText = (sel.getAttribute("data-ic-placeholder") || "").trim();
      if (!placeholderText) {
        for (var pxi = 0; pxi < sel.options.length; pxi++) {
          if (sel.options[pxi].value === "") {
            placeholderText = optLabel(sel.options[pxi]);
            break;
          }
        }
      }
      if (!placeholderText) placeholderText = "Не выбрано";

      var skipPlaceholderInList = !!sel.required;
      var skipOptIdx = -1;
      if (skipPlaceholderInList) {
        for (var ski = 0; ski < sel.options.length; ski++) {
          if (sel.options[ski].value === "") {
            skipOptIdx = ski;
            break;
          }
        }
      }

      var wrap = document.createElement("div");
      wrap.className = "ic-combobox";
      sel.parentNode.insertBefore(wrap, sel);
      wrap.appendChild(sel);
      sel.classList.add("ic-combobox-native");
      sel.setAttribute("tabindex", "-1");

      var box = document.createElement("div");
      box.className = "ic-combobox__box";
      var input = document.createElement("input");
      input.type = "text";
      input.className = "ic-combobox__input";
      input.setAttribute("autocomplete", "off");
      input.setAttribute("spellcheck", "false");
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-expanded", "false");
      var listId = "ic-lst-" + Math.random().toString(36).slice(2, 11);
      input.setAttribute("aria-controls", listId);

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ic-combobox__btn";
      btn.setAttribute("tabindex", "-1");
      btn.innerHTML =
        '<span class="material-icons-outlined" aria-hidden="true">expand_more</span>';

      var list = document.createElement("ul");
      list.className = "ic-combobox__list";
      list.id = listId;
      list.hidden = true;
      list.setAttribute("role", "listbox");

      box.appendChild(input);
      box.appendChild(btn);
      wrap.insertBefore(box, sel);
      wrap.insertBefore(list, sel);

      var items = [];
      for (var oi = 0; oi < sel.options.length; oi++) {
        (function (idx) {
          if (idx === skipOptIdx) return;
          var opt = sel.options[idx];
          var li = document.createElement("li");
          li.className = "ic-combobox__option";
          li.setAttribute("role", "option");
          li.setAttribute("data-idx", String(idx));
          li.textContent = optLabel(opt);
          if (opt.disabled) li.classList.add("ic-combobox__option--disabled");
          list.appendChild(li);
          items.push(li);
        })(oi);
      }

      function optionIdxToItemIdx(oi) {
        for (var ji = 0; ji < items.length; ji++) {
          if (parseInt(items[ji].getAttribute("data-idx"), 10) === oi)
            return ji;
        }
        return -1;
      }

      var open = false;
      var hi = -1;
      var inModal =
        document.documentElement.getAttribute("data-in-modal") === "1";

      function mountFloatingListOnBody() {
        if (!inModal) return;
        if (list.parentNode !== document.body) document.body.appendChild(list);
      }

      function restoreFloatingListInWrap() {
        if (!inModal) return;
        if (list.parentNode === document.body) wrap.insertBefore(list, sel);
      }

      function positionFloatingList() {
        if (!inModal) return;
        mountFloatingListOnBody();
        var br = wrap.getBoundingClientRect();
        var margin = 6;
        var pad = 8;
        var vh = window.innerHeight;
        var vw = window.innerWidth;
        var visCount = 0;
        for (var vi = 0; vi < items.length; vi++) {
          if (!items[vi].hidden) visCount++;
        }
        var estH = Math.min(280, Math.max(visCount * 36 + 8, 52));
        var spaceBelow = vh - br.bottom - margin;
        var spaceAbove = br.top - margin;
        var openUp =
          spaceBelow < Math.min(estH, 96) && spaceAbove > spaceBelow;
        var maxH = openUp
          ? Math.min(280, spaceAbove - pad)
          : Math.min(280, spaceBelow - pad);
        maxH = Math.max(72, maxH);
        list.classList.add("ic-combobox__list--floating");
        list.style.position = "fixed";
        list.style.width = br.width + "px";
        list.style.left =
          Math.max(pad, Math.min(br.left, vw - br.width - pad)) + "px";
        list.style.right = "auto";
        list.style.maxHeight = maxH + "px";
        list.style.zIndex = "10000";
        if (openUp) {
          list.style.top = "auto";
          list.style.bottom = vh - br.top + margin + "px";
        } else {
          list.style.bottom = "auto";
          list.style.top = br.bottom + margin + "px";
        }
      }

      function clearFloatingListStyles() {
        if (!inModal) return;
        list.classList.remove("ic-combobox__list--floating");
        list.style.position = "";
        list.style.top = "";
        list.style.bottom = "";
        list.style.left = "";
        list.style.width = "";
        list.style.right = "";
        list.style.maxHeight = "";
        list.style.zIndex = "";
        restoreFloatingListInWrap();
      }

      var repositionFloating = function () {
        if (open && inModal) positionFloatingList();
      };

      function syncIn() {
        var opt =
          sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
        if (!opt || opt.value === "") {
          input.value = "";
          input.placeholder = placeholderText;
        } else {
          input.value = optLabel(opt);
          input.placeholder = "";
        }
      }

      function paintHi() {
        for (var i = 0; i < items.length; i++) {
          items[i].classList.toggle(
            "ic-combobox__option--hi",
            i === hi && !items[i].hidden
          );
        }
        if (hi >= 0 && items[hi] && !items[hi].hidden) {
          try {
            items[hi].scrollIntoView({ block: "nearest" });
          } catch (_) { }
        }
      }

      function applyFilter(forcedQ) {
        var q =
          forcedQ !== undefined && forcedQ !== null
            ? String(forcedQ).toLowerCase()
            : (input.value || "").toLowerCase();
        var firstVis = -1;
        for (var i = 0; i < items.length; i++) {
          var li = items[i];
          var tx = (li.textContent || "").toLowerCase();
          var vis = !q || tx.indexOf(q) !== -1;
          li.hidden = !vis;
          if (vis && firstVis < 0) firstVis = i;
        }
        var si = sel.selectedIndex;
        var itemForSel = optionIdxToItemIdx(si);
        if (itemForSel >= 0 && !items[itemForSel].hidden) hi = itemForSel;
        else hi = firstVis;
        paintHi();
        if (open && inModal) positionFloatingList();
      }

      function setOpen(v, showAll) {
        if (v) {
          open = true;
          list.hidden = false;
          wrap.classList.add("ic-combobox--open");
          input.setAttribute("aria-expanded", "true");
          if (showAll === true) applyFilter("");
          else applyFilter();
          if (inModal) {
            positionFloatingList();
            requestAnimationFrame(function () {
              requestAnimationFrame(positionFloatingList);
            });
            window.addEventListener("resize", repositionFloating);
            document.addEventListener("scroll", repositionFloating, true);
          }
          return;
        }
        if (!open) return;
        open = false;
        if (inModal) {
          window.removeEventListener("resize", repositionFloating);
          document.removeEventListener("scroll", repositionFloating, true);
          clearFloatingListStyles();
        }
        list.hidden = true;
        wrap.classList.remove("ic-combobox--open");
        input.setAttribute("aria-expanded", "false");
        syncIn();
        hi = -1;
        for (var j = 0; j < items.length; j++) {
          items[j].hidden = false;
          items[j].classList.remove("ic-combobox__option--hi");
        }
      }

      wrap.__icClose = function () {
        if (open) setOpen(false);
      };

      function choose(idx) {
        if (idx < 0 || idx >= sel.options.length) return;
        var opt = sel.options[idx];
        if (opt.disabled) return;
        sel.selectedIndex = idx;
        syncIn();
        setOpen(false);
        sel.dispatchEvent(new Event("input", { bubbles: true }));
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }

      function selectInputTextIfAny() {
        try {
          if (input.value) input.select();
        } catch (_) { }
      }

      input.addEventListener("focus", function () {
        setOpen(true, true);
        selectInputTextIfAny();
      });

      input.addEventListener("input", function () {
        if (!open) setOpen(true, false);
        else applyFilter();
      });

      input.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          if (open) {
            setOpen(false);
            e.preventDefault();
          }
          return;
        }
        if (!open) {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            setOpen(true, true);
            selectInputTextIfAny();
            e.preventDefault();
          }
          return;
        }
        if (e.key === "ArrowDown") {
          e.preventDefault();
          if (!items.length) return;
          var s = hi < 0 ? -1 : hi;
          for (var step = 0; step < items.length; step++) {
            s = s < items.length - 1 ? s + 1 : 0;
            if (!items[s].hidden) {
              hi = s;
              break;
            }
          }
          paintHi();
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          if (!items.length) return;
          var s2 = hi < 0 ? items.length : hi;
          for (var step2 = 0; step2 < items.length; step2++) {
            s2 = s2 > 0 ? s2 - 1 : items.length - 1;
            if (!items[s2].hidden) {
              hi = s2;
              break;
            }
          }
          paintHi();
          return;
        }
        if (e.key === "Enter") {
          if (hi >= 0 && !items[hi].hidden) {
            var ix = parseInt(items[hi].getAttribute("data-idx"), 10);
            if (!isNaN(ix)) {
              choose(ix);
              e.preventDefault();
            }
          }
        }
      });

      btn.addEventListener("click", function (e) {
        e.preventDefault();
        if (open) {
          setOpen(false);
          return;
        }
        input.focus();
        setOpen(true, true);
        selectInputTextIfAny();
      });

      list.addEventListener("mousedown", function (e) {
        var li = e.target.closest(".ic-combobox__option");
        if (!li || li.classList.contains("ic-combobox__option--disabled") || li.hidden)
          return;
        e.preventDefault();
        var ix = parseInt(li.getAttribute("data-idx"), 10);
        if (!isNaN(ix)) choose(ix);
      });

      syncIn();
    });
  }

  window.icInitComboboxes = icInitComboboxes;

  function scheduleRealtimePoll() {
    if (!realtimeEnabled()) return;
    if (rtPollTimer) clearTimeout(rtPollTimer);
    var ms = Math.max(1000, rtBackoffMs || getBasePollMs());
    rtPollTimer = setTimeout(pollRealtime, ms);
  }

  function pollRealtime() {
    rtPollTimer = null;
    if (!realtimeEnabled()) return;
    var context = getWsContext();
    if (!context) return;
    var url = "/realtime/poll?since=" + encodeURIComponent(String(lastRtId)) + "&context=" + encodeURIComponent(context) + "&client=" + encodeURIComponent(window.wsClientId || "");
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("poll " + r.status);
        return r.json();
      })
      .then(function (data) {
        rtBackoffMs = getBasePollMs();
        if (data && typeof data.last_id === "number") lastRtId = data.last_id;
        if (data && data.invalidate) {
          if (context === "dashboard") {
            window.location.reload();
            return;
          }
          refreshFragment();
        }
        scheduleRealtimePoll();
      })
      .catch(function () {
        rtBackoffMs = Math.min(
          rtMaxBackoffMs,
          Math.max(pollErrorBaseMs, rtBackoffMs * 2)
        );
        scheduleRealtimePoll();
      });
  }

  function refreshFragment() {
    var main = document.getElementById("appMain");
    if (!main) return;
    var url = location.pathname + (location.search ? location.search + "&" : "?") + "fragment=1";
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        main.innerHTML = html;
        try {
          if (window.__icInitPhoneFields) window.__icInitPhoneFields(main);
        } catch (_) { }
        icInitComboboxes(main);
      })
      .catch(function () { });
  }

  function ensureFormClientId() {
    var cid = window.wsClientId || "";
    document.querySelectorAll("form").forEach(function (form) {
      if (form.getAttribute("data-ic-no-ws") === "1") return;
      if (form.querySelector('input[name="ws_client_id"]')) return;
      var inp = document.createElement("input");
      inp.type = "hidden";
      inp.name = "ws_client_id";
      inp.value = cid;
      form.appendChild(inp);
    });
  }

  function bootShared() {
    ensureFormClientId();
    icInitComboboxes(document);
  }

  function onRtUserToggle() {
    rtUserEnabled = !rtUserEnabled;
    saveRtEnabled(rtUserEnabled);
    updateRealtimeToggleUi();
    if (rtUserEnabled) {
      rtBackoffMs = getBasePollMs();
      if (rtPollTimer) {
        clearTimeout(rtPollTimer);
        rtPollTimer = null;
      }
      pollRealtime();
    } else if (rtPollTimer) {
      clearTimeout(rtPollTimer);
      rtPollTimer = null;
    }
  }

  function bootRealtimePoll() {
    if (window.self !== window.top) return;
    rtMenuToggleBtn = document.getElementById("rtMenuToggleBtn");
    rtUserEnabled = loadRtEnabled();
    updateRealtimeToggleUi();
    if (rtMenuToggleBtn) {
      rtMenuToggleBtn.addEventListener("click", function () {
        onRtUserToggle();
      });
    }
    if (rtUserEnabled) {
      rtBackoffMs = getBasePollMs();
      pollRealtime();
    }
    window.addEventListener("online", function () {
      if (!realtimeEnabled()) return;
      rtBackoffMs = getBasePollMs();
      if (rtPollTimer) clearTimeout(rtPollTimer);
      pollRealtime();
    });
    document.addEventListener("visibilitychange", function () {
      if (!realtimeEnabled()) return;
      rtBackoffMs = getBasePollMs();
      if (rtPollTimer) {
        clearTimeout(rtPollTimer);
        rtPollTimer = null;
      }
      scheduleRealtimePoll();
    });
  }

  function updateRealtimeToggleUi() {
    var on = realtimeEnabled();
    var label = document.getElementById("rtMenuToggleLabel");
    var icon = document.querySelector("#rtMenuToggleBtn .app-user-menu__toggle-row__icon");
    if (rtMenuToggleBtn && label) {
      rtMenuToggleBtn.setAttribute("aria-pressed", on ? "true" : "false");
      rtMenuToggleBtn.title = on ? "Автообновление включено" : "Автообновление выключено";
      label.textContent = on ? "Включено" : "Выключено";
      rtMenuToggleBtn.classList.toggle("is-on", on);
    }
    if (icon) {
      icon.textContent = on ? "sync" : "sync_disabled";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      bootShared();
      bootRealtimePoll();
    });
  } else {
    bootShared();
    bootRealtimePoll();
  }
})();

(function () {
  var disallowed = /[^0-9+\s().-]/g;

  var CC_RULES = [
    { code: "998", groups: [2, 3, 2, 2] },
    { code: "996", groups: [3, 3, 3] },
    { code: "995", groups: [2, 3, 4] },
    { code: "994", groups: [2, 3, 2, 2] },
    { code: "993", groups: [2, 2, 2, 2] },
    { code: "992", groups: [3, 2, 4] },
    { code: "380", groups: [2, 3, 2, 2] },
    { code: "375", groups: [2, 3, 2, 2] },
    { code: "373", groups: [3, 2, 2, 2] },
    { code: "372", groups: [3, 4] },
    { code: "374", groups: [2, 3, 3] },
    { code: "90", groups: [3, 3, 4] },
    { code: "49", groups: [3, 2, 2, 2, 2] },
    { code: "44", groups: [4, 3, 4] },
    { code: "39", groups: [3, 3, 4] },
    { code: "34", groups: [3, 3, 3] },
    { code: "33", groups: [1, 2, 2, 2, 2] },
    { code: "32", groups: [3, 2, 2, 2] },
    { code: "31", groups: [1, 2, 2, 2, 2] },
    { code: "48", groups: [3, 3, 3] },
    { code: "86", groups: [3, 4, 4] },
    { code: "1", groups: [3, 3, 4] },
    { code: "7", groups: [3, 3, 2, 2] },
  ];
  CC_RULES.sort(function (a, b) {
    return b.code.length - a.code.length;
  });

  function digitsOnly(s) {
    return String(s || "").replace(/\D/g, "");
  }

  function applyGroups(national, groups) {
    var parts = [];
    var i = 0;
    for (var g = 0; g < groups.length && i < national.length; g++) {
      var take = Math.min(groups[g], national.length - i);
      parts.push(national.slice(i, i + take));
      i += take;
    }
    if (i < national.length) parts.push(national.slice(i));
    return parts.join(" ");
  }

  function chunkEvery(s, n) {
    var parts = [];
    for (var j = 0; j < s.length; j += n) parts.push(s.slice(j, j + n));
    return parts.join(" ");
  }

  function maxDigitsForRule(rule) {
    return rule.code.length + rule.groups.reduce(function (a, b) {
      return a + b;
    }, 0);
  }

  function findRule(digitStr) {
    for (var r = 0; r < CC_RULES.length; r++) {
      var c = CC_RULES[r].code;
      if (digitStr.length >= c.length && digitStr.slice(0, c.length) === c) {
        return CC_RULES[r];
      }
    }
    return null;
  }

  function formatInternational(raw) {
    raw = String(raw || "").trim();
    if (!raw) return "";

    var d = digitsOnly(raw);
    if (!d) {
      if (raw.indexOf("+") !== -1) return "+";
      return "";
    }

    var maxLen = 15;
    var ruleGuess = findRule(d);
    if (ruleGuess) maxLen = maxDigitsForRule(ruleGuess);
    if (d.length > maxLen) d = d.slice(0, maxLen);

    var rule = findRule(d);
    if (!rule) return "+" + chunkEvery(d, 3);
    var nat = d.slice(rule.code.length);
    var fmt = applyGroups(nat, rule.groups);
    return "+" + rule.code + (fmt ? " " + fmt : "");
  }

  function stripInvalid(el) {
    var v = String(el.value || "");
    var s = v.replace(disallowed, "");
    if (s !== v) el.value = s;
  }

  function applyFormat(el, moveCaretEnd) {
    var before = el.value;
    var after = formatInternational(before);
    if (after !== before) {
      el.value = after;
      if (moveCaretEnd) {
        try {
          var len = after.length;
          el.setSelectionRange(len, len);
        } catch (_) { }
      }
    }
  }

  function initPhoneFields(root) {
    var scope = root || document;
    scope.querySelectorAll('input[name="phone"]').forEach(function (el) {
      if (String(el.value || "").trim()) applyFormat(el, false);
    });
  }

  document.addEventListener(
    "input",
    function (e) {
      var t = e.target;
      if (!t || t.nodeName !== "INPUT" || t.name !== "phone") return;
      stripInvalid(t);
      applyFormat(t, true);
    },
    true
  );

  document.addEventListener(
    "blur",
    function (e) {
      var t = e.target;
      if (!t || t.nodeName !== "INPUT" || t.name !== "phone") return;
      stripInvalid(t);
      applyFormat(t, false);
      if (String(t.value || "").trim() === "+") t.value = "";
    },
    true
  );

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initPhoneFields(document);
    });
  } else {
    initPhoneFields(document);
  }

  window.__icInitPhoneFields = initPhoneFields;
})();