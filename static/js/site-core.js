(() => {
  'use strict';

  const FALLBACK_IMAGE = '/static/logo.png';
  const isObject = value => value && typeof value === 'object' && !Array.isArray(value);
  const clamp = (value, min, max) => Math.min(max, Math.max(min, Number(value) || 0));
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[ch]));

  const storage = {
    readJSON(key, fallback = null) {
      try {
        const raw = localStorage.getItem(key);
        if (raw === null) return fallback;
        const parsed = JSON.parse(raw);
        return parsed === null ? fallback : parsed;
      } catch {
        try { localStorage.removeItem(key); } catch {}
        return fallback;
      }
    },
    writeJSON(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
    },
    readText(key, fallback = '') {
      try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
    },
    writeText(key, value) {
      try { localStorage.setItem(key, String(value ?? '')); return true; } catch { return false; }
    },
    remove(key) { try { localStorage.removeItem(key); } catch {} }
  };

  function sanitizeCart(items) {
    if (!Array.isArray(items)) return [];
    return items.slice(0, 80).map(item => {
      if (!isObject(item)) return null;
      const slug = String(item.slug || '').slice(0, 160);
      const uniqid = String(item.uniqid || item.optionId || '').slice(0, 160);
      if (!slug || !uniqid) return null;
      return {
        ...item,
        slug,
        uniqid,
        optionId: item.optionId ?? item.uniqid ?? '',
        productName: String(item.productName || 'Product').slice(0, 180),
        optionName: String(item.optionName || 'Option').slice(0, 180),
        image: String(item.image || FALLBACK_IMAGE).slice(0, 1200),
        price: Math.max(0, Number(item.price) || 0),
        quantity: Math.round(clamp(item.quantity || 1, 1, 10))
      };
    }).filter(Boolean);
  }

  const cart = {
    key: 'moeCart',
    read() {
      const raw = storage.readJSON(this.key, []);
      const clean = sanitizeCart(raw);
      // Repair malformed/old cart data only when the normalized value changed.
      try {
        if (JSON.stringify(raw) !== JSON.stringify(clean)) storage.writeJSON(this.key, clean);
      } catch {}
      return clean;
    },
    write(items) {
      const clean = sanitizeCart(items);
      storage.writeJSON(this.key, clean);
      window.dispatchEvent(new Event('moeCartUpdated'));
      return clean;
    },
    count(items = this.read()) { return items.reduce((n, item) => n + clamp(item.quantity || 1, 1, 10), 0); },
    total(items = this.read()) { return items.reduce((n, item) => n + (Number(item.price) || 0) * clamp(item.quantity || 1, 1, 10), 0); }
  };

  async function fetchJSON(url, options = {}, timeoutMs = 12000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Math.max(1000, timeoutMs));
    const externalSignal = options.signal;
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener('abort', () => controller.abort(), { once: true });
    }
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const error = new Error((data && (data.error || data.message)) || `Request failed (${response.status})`);
        error.status = response.status;
        error.data = data;
        throw error;
      }
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  function debounce(fn, wait = 180) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  function setButtonBusy(button, busy, label = '') {
    if (!button) return;
    if (busy) {
      if (!button.dataset.originalHtml) button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.innerHTML = `<i class="fas fa-circle-notch fa-spin" aria-hidden="true"></i><span>${escapeHtml(label || 'Working…')}</span>`;
    } else {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      if (button.dataset.originalHtml) {
        button.innerHTML = button.dataset.originalHtml;
        delete button.dataset.originalHtml;
      }
    }
  }

  window.MoeSite = Object.freeze({
    storage, cart, clamp, escapeHtml, fetchJSON, debounce, setButtonBusy, fallbackImage: FALLBACK_IMAGE
  });

  function setupNetworkStatus() {
    let banner = document.getElementById('networkStatusBanner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'networkStatusBanner';
      banner.className = 'network-status-banner';
      banner.setAttribute('role', 'status');
      banner.setAttribute('aria-live', 'polite');
      document.body.appendChild(banner);
    }
    const update = () => {
      const offline = navigator.onLine === false;
      document.documentElement.classList.toggle('is-offline', offline);
      banner.classList.toggle('show', offline);
      banner.innerHTML = offline ? '<i class="fas fa-wifi"></i><span>You’re offline. Changes that need the server will resume when your connection returns.</span>' : '';
      if (!offline) window.dispatchEvent(new CustomEvent('moeNetworkRestored'));
    };
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    update();
  }

  function setupPasswordReveal() {
    document.querySelectorAll('.auth-card input[type="password"]').forEach(input => {
      if (input.dataset.revealReady === '1') return;
      input.dataset.revealReady = '1';
      const label = input.closest('label');
      if (!label) return;
      label.classList.add('has-password-reveal');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'password-reveal';
      button.setAttribute('aria-label', 'Show password');
      button.setAttribute('aria-pressed', 'false');
      button.innerHTML = '<i class="fas fa-eye" aria-hidden="true"></i>';
      button.addEventListener('click', () => {
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
        button.setAttribute('aria-pressed', String(show));
        button.innerHTML = `<i class="fas ${show ? 'fa-eye-slash' : 'fa-eye'}" aria-hidden="true"></i>`;
        input.focus({ preventScroll: true });
      });
      label.appendChild(button);
    });
  }

  function setupImageFallbacks() {
    document.addEventListener('error', event => {
      const img = event.target;
      if (!(img instanceof HTMLImageElement) || img.dataset.fallbackApplied === '1') return;
      img.dataset.fallbackApplied = '1';
      img.src = FALLBACK_IMAGE;
    }, true);
  }

  function setupNativeSubmitLock() {
    document.addEventListener('submit', event => {
      if (event.defaultPrevented) return;
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || form.dataset.allowDuplicateSubmit === 'true') return;
      if (form.dataset.nativeSubmitting === 'true') {
        event.preventDefault();
        return;
      }
      form.dataset.nativeSubmitting = 'true';
      const submitter = event.submitter instanceof HTMLElement ? event.submitter : form.querySelector('button[type="submit"],input[type="submit"]');
      if (submitter && !submitter.disabled) {
        submitter.setAttribute('aria-busy', 'true');
        setTimeout(() => { try { submitter.disabled = true; } catch {} }, 0);
      }
    });
  }

  function setupNav() {
    const topbar = document.querySelector('.topbar');
    const mobileButton = document.getElementById('mobileMenuBtn');
    const more = document.querySelector('.nav-more');
    const moreButton = more?.querySelector(':scope > button');

    if (moreButton) {
      moreButton.setAttribute('aria-expanded', 'false');
      moreButton.addEventListener('click', event => {
        event.stopPropagation();
        const open = !more.classList.contains('open');
        more.classList.toggle('open', open);
        moreButton.setAttribute('aria-expanded', String(open));
      });
    }

    document.addEventListener('click', event => {
      if (more && !more.contains(event.target)) {
        more.classList.remove('open');
        moreButton?.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      more?.classList.remove('open');
      moreButton?.setAttribute('aria-expanded', 'false');
      if (topbar?.classList.contains('menu-open')) {
        topbar.classList.remove('menu-open');
        mobileButton?.setAttribute('aria-expanded', 'false');
        mobileButton?.setAttribute('aria-label', 'Open navigation');
        if (mobileButton) mobileButton.innerHTML = '<i class="fas fa-bars"></i>';
      }
    });
  }

  function setupCommandPalette() {
    const palette = document.getElementById('globalCommandPalette');
    const input = document.getElementById('globalCommandInput');
    const results = document.getElementById('globalCommandResults');
    if (!palette || !input || !results) return;

    const pages = [
      ['Store', '/products', 'fa-store'],
      ['Cart', '/cart', 'fa-bag-shopping'],
      ['My account', '/account', 'fa-user'],
      ['Support', '/support', 'fa-headset'],
      ['Downloads', '/downloads', 'fa-download'],
      ['Free downloads', '/free-downloads', 'fa-gift'],
      ['Guides', '/guides', 'fa-book-open'],
      ['System status', '/status', 'fa-signal'],
      ['Casino', '/casino', 'fa-dice'],
      ['Legal center', '/legal', 'fa-scale-balanced']
    ];
    let requestId = 0;
    let activeIndex = -1;

    const rows = () => [...results.querySelectorAll('a.command-item')];
    const setActive = index => {
      const items = rows();
      if (!items.length) { activeIndex = -1; return; }
      activeIndex = (index + items.length) % items.length;
      items.forEach((item, i) => item.classList.toggle('active', i === activeIndex));
      items[activeIndex]?.scrollIntoView({ block: 'nearest' });
    };
    const pageMarkup = list => list.map(([name, url, icon]) => `<a class="command-item" href="${url}"><i class="fas ${icon}"></i><span>${escapeHtml(name)}</span><small>Page</small><i class="fas fa-arrow-right"></i></a>`).join('');
    const productMarkup = items => (items || []).map(item => `<a class="command-item" href="/product/${encodeURIComponent(item.slug || '')}"><img src="${escapeHtml(item.image || FALLBACK_IMAGE)}" alt=""><span>${escapeHtml(item.name || 'Product')}</span><small>${escapeHtml(item.category || 'Product')}</small><i class="fas fa-arrow-right"></i></a>`).join('');

    async function draw(query = '') {
      const q = query.trim();
      const filteredPages = pages.filter(([name]) => !q || name.toLowerCase().includes(q.toLowerCase())).slice(0, q ? 5 : 8);
      let html = pageMarkup(filteredPages);
      const id = ++requestId;
      if (q.length >= 2) {
        results.innerHTML = html || '<div class="command-empty"><i class="fas fa-circle-notch fa-spin"></i> Searching…</div>';
        try {
          const data = await fetchJSON(`/api/store/search-suggestions?q=${encodeURIComponent(q)}`, { headers: { Accept: 'application/json' } }, 6000);
          if (id !== requestId) return;
          const products = productMarkup(data?.items || []);
          html = html + products;
        } catch {}
      }
      if (id !== requestId) return;
      results.innerHTML = html || '<div class="command-empty">No matching page or product.</div>';
      activeIndex = -1;
    }

    const open = () => {
      palette.classList.add('show');
      palette.setAttribute('aria-hidden', 'false');
      document.body.classList.add('command-open');
      input.focus();
      input.select();
      draw(input.value);
    };
    const close = () => {
      palette.classList.remove('show');
      palette.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('command-open');
      activeIndex = -1;
    };

    document.addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        palette.classList.contains('show') ? close() : open();
        return;
      }
      if (!palette.classList.contains('show')) return;
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      else if (event.key === 'ArrowDown') { event.preventDefault(); setActive(activeIndex + 1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(activeIndex - 1); }
      else if (event.key === 'Enter' && activeIndex >= 0) { event.preventDefault(); rows()[activeIndex]?.click(); }
    });
    palette.addEventListener('click', event => { if (event.target === palette) close(); });
    input.addEventListener('input', debounce(() => draw(input.value), 120));
  }

  function setup() {
    setupNetworkStatus();
    setupPasswordReveal();
    setupImageFallbacks();
    setupNativeSubmitLock();
    setupNav();
    setupCommandPalette();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup, { once: true });
  else setup();
})();
