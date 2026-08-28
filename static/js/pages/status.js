(() => {
  'use strict';
  if (!window.MoeSite) return;
  const { storage, escapeHtml, fetchJSON, fallbackImage } = window.MoeSite;
  const grid = document.getElementById('status-grid');
  const totalEl = document.getElementById('overview-total');
  const opEl = document.getElementById('overview-operational');
  const degEl = document.getElementById('overview-degraded');
  const offEl = document.getElementById('overview-offline');
  const overallLabel = document.getElementById('overall-label');
  const overallCopy = document.getElementById('overall-copy');
  const overallBeacon = document.getElementById('overall-beacon');
  let loading = false;
  let lastUpdated = 0;
  let timer = null;

  function stateInfo(item) {
    const raw = String(item?.status?.state || item?.status?.label || item?.store?.stockStatus || 'operational').toLowerCase();
    if (raw.includes('offline') || raw.includes('out')) return { type: 'offline', cls: 'down', label: item?.status?.label || 'Offline' };
    if (raw.includes('degraded') || raw.includes('limited') || raw.includes('update')) return { type: 'degraded', cls: 'warn', label: item?.status?.label || 'Limited' };
    return { type: 'operational', cls: '', label: item?.status?.label || 'Operational' };
  }

  function formatDate(value) {
    if (!value) return 'Updated recently';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : `Updated ${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`;
  }

  function render(items) {
    items = Array.isArray(items) ? items : [];
    const states = items.map(stateInfo);
    const operational = states.filter(item => item.type === 'operational').length;
    const degraded = states.filter(item => item.type === 'degraded').length;
    const offline = states.filter(item => item.type === 'offline').length;
    totalEl.textContent = String(items.length);
    opEl.textContent = String(operational);
    degEl.textContent = String(degraded);
    offEl.textContent = String(offline);
    overallBeacon.classList.toggle('issue', degraded + offline > 0);
    if (offline) {
      overallLabel.textContent = 'Some systems are offline';
      overallCopy.textContent = `${offline} service${offline === 1 ? ' is' : 's are'} currently unavailable.`;
    } else if (degraded) {
      overallLabel.textContent = 'Some systems are limited';
      overallCopy.textContent = `${degraded} service${degraded === 1 ? ' is' : 's are'} experiencing reduced availability.`;
    } else {
      overallLabel.textContent = 'All systems operational';
      overallCopy.textContent = 'No active service disruptions are currently detected.';
    }
    if (!items.length) {
      grid.innerHTML = '<div class="empty-state"><strong>No services found</strong><br>No products are currently included in the status feed.</div>';
      return;
    }
    grid.innerHTML = items.map(item => {
      const info = stateInfo(item);
      return `<article class="st-row">
        <img class="st-icon" src="${escapeHtml(item.image || fallbackImage)}" alt="${escapeHtml(item.name || 'Service')}">
        <div><div class="st-name">${escapeHtml(item.name || 'Untitled service')}</div><div class="st-meta"><span><i class="fas fa-code-branch" aria-hidden="true"></i> ${escapeHtml(item?.downloads?.version || 'Latest')}</span><span>•</span><span>${escapeHtml(formatDate(item?.status?.lastUpdated))}</span></div></div>
        <span class="st-state ${info.cls}"><span class="st-dot"></span>${escapeHtml(info.label)}</span>
      </article>`;
    }).join('');
  }

  async function loadStatus({ force = false } = {}) {
    if (loading || (document.hidden && !force)) return;
    loading = true;
    const cached = storage.readJSON('moe_status_cache', null);
    if (!lastUpdated && cached?.items && Date.now() - Number(cached.savedAt || 0) < 5 * 60 * 1000) render(cached.items);
    try {
      const items = await fetchJSON('/api/status', { headers: { Accept: 'application/json' }, cache: force ? 'no-cache' : 'default' }, 10000);
      render(items);
      lastUpdated = Date.now();
      storage.writeJSON('moe_status_cache', { items, savedAt: lastUpdated });
    } catch (error) {
      if (!cached?.items) {
        grid.innerHTML = '<div class="empty-state"><strong>Status temporarily unavailable</strong><br>The live feed could not be loaded. It will retry automatically.</div>';
        overallLabel.textContent = 'Unable to verify systems';
        overallCopy.textContent = 'Live service health could not be reached.';
        overallBeacon.classList.add('issue');
      }
    } finally {
      loading = false;
    }
  }

  function schedule() {
    clearInterval(timer);
    timer = setInterval(() => loadStatus(), 60000);
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - lastUpdated > 45000) loadStatus({ force: true });
  });
  window.addEventListener('moeNetworkRestored', () => loadStatus({ force: true }));
  loadStatus();
  schedule();
})();
