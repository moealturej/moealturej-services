(() => {
  'use strict';
  const site = window.MoeSite;
  const FALLBACK_IMAGE = site?.fallbackImage || '/static/logo.png';
  const CART_STORAGE_KEY = 'moeCart';
  const productsContainer = document.getElementById('products-container');
  const filterContainer = document.getElementById('filter-container');
  const searchInput = document.getElementById('search-input');
  const sortSelect = document.getElementById('sort-select');
  const notification = document.getElementById('notification');
  const resultCount = document.getElementById('result-count');
  const statProducts = document.getElementById('stat-products');
  const statOnline = document.getElementById('stat-online');
  const resetButton = document.getElementById('store-reset');
  if (!productsContainer || !filterContainer || !searchInput || !sortSelect) return;

  let products = [];
  let cart = site?.cart?.read?.() || [];
  let loading = false;
  const initialNode = document.getElementById('initialStoreProducts');
  let initialProducts = null;
  if (initialNode) {
    try {
      const parsed = JSON.parse(initialNode.textContent || '[]');
      if (Array.isArray(parsed)) initialProducts = parsed;
    } catch {}
  }
  const url = new URL(location.href);
  let currentFilter = (url.searchParams.get('category') || 'all').toLowerCase();
  let currentSearch = (url.searchParams.get('q') || '').trim().toLowerCase();
  const validSorts = new Set(['featured', 'price-low', 'price-high', 'name', 'saved']);
  let initialSort = url.searchParams.get('sort') || site?.storage?.readText('moeStoreSort', 'featured') || 'featured';
  if (location.hash === '#wishlist') initialSort = 'saved';
  sortSelect.value = validSorts.has(initialSort) ? initialSort : 'featured';
  searchInput.value = url.searchParams.get('q') || '';

  const esc = site?.escapeHtml || (v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])));
  const debounce = site?.debounce || ((fn, wait=150) => { let t; return (...args) => { clearTimeout(t); t=setTimeout(() => fn(...args), wait); }; });

  function showNotification(message, type='') {
    if (window.showSiteToast) return window.showSiteToast(message, type);
    if (!notification) return;
    notification.textContent = message;
    notification.className = `notice-toast ${type}`.trim();
    notification.classList.add('show');
    setTimeout(() => notification.classList.remove('show'), 2400);
  }
  function saveCart() {
    cart = site?.cart?.write?.(cart) || cart;
    if (!site?.cart) {
      try { localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(cart)); } catch {}
      window.dispatchEvent(new Event('moeCartUpdated'));
    }
  }
  function options(product) {
    return (product?.store?.options || []).map(o => ({
      id: o?.id ?? o?.uniqid ?? '',
      uniqid: String(o?.uniqid ?? o?.id ?? ''),
      name: o?.name || 'Option',
      price: Math.max(0, Number(o?.price || 0))
    })).filter(o => o.uniqid);
  }
  function minPrice(product) {
    const prices = options(product).map(o => o.price).filter(Number.isFinite);
    return prices.length ? Math.min(...prices) : 0;
  }
  function priceText(product) {
    const p = minPrice(product);
    return p > 0 ? `$${p.toFixed(2)}+` : 'View options';
  }
  function statusInfo(product) {
    const service = String(product?.status?.state || product?.status?.label || 'Online').trim();
    const stock = String(product?.store?.stockStatus || 'In Stock').trim();
    const hay = `${service} ${stock}`.toLowerCase();
    const soldOut = /(out of stock|sold out|unavailable|disabled)/.test(stock.toLowerCase());
    if (soldOut || /(offline|down|outage)/.test(hay)) return { cls:'is-red', icon:'fa-circle', label:soldOut ? stock : service, available:!soldOut };
    if (/(degraded|limited|issue|updat|maintenance)/.test(hay)) return { cls:'is-yellow', icon:'fa-triangle-exclamation', label:service, available:true };
    return { cls:'is-green', icon:'fa-circle', label:service || 'Online', available:true };
  }
  function statusChip(product) {
    const info = statusInfo(product);
    return `<span class="chip ${info.cls}"><i class="fas ${info.icon}"></i> ${esc(info.label)}</span>`;
  }
  function updateUrl({replace=true}={}) {
    const next = new URL(location.href);
    const q = searchInput.value.trim();
    if (q) next.searchParams.set('q', q); else next.searchParams.delete('q');
    if (currentFilter !== 'all') next.searchParams.set('category', currentFilter); else next.searchParams.delete('category');
    if (sortSelect.value !== 'featured') next.searchParams.set('sort', sortSelect.value); else next.searchParams.delete('sort');
    if (next.hash === '#wishlist' && sortSelect.value !== 'saved') next.hash = '';
    history[replace ? 'replaceState' : 'pushState']({}, '', next.pathname + next.search + next.hash);
  }
  function addToCart(product, opt) {
    if (!opt?.uniqid) return showNotification('This option is unavailable.', 'error');
    const existing = cart.find(i => String(i.uniqid) === String(opt.uniqid));
    if (existing) existing.quantity = Math.min(10, (Number(existing.quantity) || 1) + 1);
    else cart.push({
      uniqid: opt.uniqid,
      productId: product.id,
      slug: product.slug,
      productName: product.name,
      image: product.image || FALLBACK_IMAGE,
      optionId: opt.id,
      optionName: opt.name,
      price: Number(opt.price || 0),
      quantity: 1
    });
    saveCart();
    if (window.showCartToast) window.showCartToast({productName:product.name, optionName:opt.name, image:product.image || FALLBACK_IMAGE, price:Number(opt.price || 0)});
    else showNotification('Added to cart', 'success');
  }

  function buildFilters() {
    const enabled = products.filter(p => p?.store?.enabled);
    const counts = new Map();
    for (const p of enabled) {
      const key = String(p.category || 'Other').trim();
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    const categories = [...counts.keys()].sort((a,b) => a.localeCompare(b));
    if (currentFilter !== 'all' && !categories.some(c => c.toLowerCase() === currentFilter)) currentFilter = 'all';
    filterContainer.innerHTML = `<button class="filter-btn ${currentFilter==='all'?'active':''}" data-category="all"><span>All products</span><small>${enabled.length}</small></button>${categories.map(c => `<button class="filter-btn ${c.toLowerCase()===currentFilter?'active':''}" data-category="${esc(c.toLowerCase())}"><span>${esc(c)}</span><small>${counts.get(c)}</small></button>`).join('')}`;
    filterContainer.querySelectorAll('.filter-btn').forEach(btn => btn.addEventListener('click', () => {
      currentFilter = btn.dataset.category || 'all';
      filterContainer.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b === btn));
      updateUrl();
      renderProducts();
    }));
  }

  function getFiltered() {
    let list = products.filter(p => p?.store?.enabled);
    if (sortSelect.value === 'saved') {
      const saved = new Set((window.MoeWishlist?.get() || []).map(x => String(x.slug)));
      list = list.filter(p => saved.has(String(p.slug || p.id || '')));
    }
    if (currentFilter !== 'all') list = list.filter(p => String(p.category || '').toLowerCase() === currentFilter);
    if (currentSearch) {
      list = list.filter(p => [p.name,p.category,p.description,p.detailedDescription,...(p.features||[])].join(' ').toLowerCase().includes(currentSearch));
    }
    const sort = sortSelect.value;
    if (sort === 'price-low') list.sort((a,b) => minPrice(a)-minPrice(b));
    else if (sort === 'price-high') list.sort((a,b) => minPrice(b)-minPrice(a));
    else if (sort === 'name') list.sort((a,b) => String(a.name||'').localeCompare(String(b.name||'')));
    else if (sort === 'featured') list.sort((a,b) => Number(Boolean(b.featured))-Number(Boolean(a.featured)) || Number(a.displayOrder||9999)-Number(b.displayOrder||9999));
    return list;
  }

  function renderProducts() {
    const list = getFiltered();
    const hasFilters = Boolean(currentSearch || currentFilter !== 'all' || sortSelect.value !== 'featured');
    if (resetButton) resetButton.hidden = !hasFilters;
    resultCount.textContent = `${list.length} product${list.length===1?'':'s'}${hasFilters?' matched':''}`;
    if (!list.length) {
      const savedMode = sortSelect.value === 'saved';
      productsContainer.innerHTML = `<div class="empty-state store-empty"><i class="fas ${savedMode?'fa-heart':'fa-magnifying-glass'}"></i><h3>${savedMode?'No saved products yet':'No products found'}</h3><p>${savedMode?'Tap the heart on a product to save it here.':'Try clearing your search or selecting another category.'}</p>${hasFilters?'<button type="button" class="secondary-cta" id="empty-reset">Clear filters</button>':''}</div>`;
      document.getElementById('empty-reset')?.addEventListener('click', resetFilters);
      return;
    }
    const frag = document.createDocumentFragment();
    for (const product of list) {
      const opts = options(product);
      const info = statusInfo(product);
      const card = document.createElement('article');
      card.className = 'product-card';
      const slug = encodeURIComponent(product.slug || product.id || '');
      card.innerHTML = `<div class="product-media"><a class="product-image-link" href="/product/${slug}" aria-label="View ${esc(product.name || 'product')}"><img src="${esc(product.image || FALLBACK_IMAGE)}" alt="${esc(product.name || 'Product')}" loading="lazy" decoding="async"></a><button class="wishlist-toggle" type="button" aria-label="Save ${esc(product.name || 'product')}"><i class="fas fa-heart"></i></button><div class="product-badges">${(product.badges||[]).slice(0,2).map(b=>`<span class="product-badge">${esc(b)}</span>`).join('')}</div></div><div class="product-body"><div class="product-title-row"><a href="/product/${slug}" class="product-name">${esc(product.name || 'Untitled Product')}</a><div class="product-price">${esc(priceText(product))}</div></div><div class="product-meta">${statusChip(product)}<span class="chip"><i class="fas fa-layer-group"></i> ${esc(product.category || 'Product')}</span></div><div class="option-list">${opts.length ? opts.map((o,i)=>`<button type="button" class="option ${i===0?'selected':''}" data-id="${esc(o.uniqid)}" aria-pressed="${i===0?'true':'false'}"><span>${esc(o.name)}</span><span>$${o.price.toFixed(2)}</span></button>`).join('') : '<span class="chip">No purchase options</span>'}</div><div class="product-actions"><button type="button" class="product-btn buy" ${!opts.length || !info.available?'disabled':''}><i class="fas fa-cart-plus"></i> ${info.available?'Add to cart':'Unavailable'}</button><a href="/product/${slug}" class="product-btn">Details</a></div></div>`;
      let selected = opts[0] || null;
      card.querySelectorAll('.option').forEach(btn => btn.addEventListener('click', () => {
        card.querySelectorAll('.option').forEach(b => { b.classList.remove('selected'); b.setAttribute('aria-pressed','false'); });
        btn.classList.add('selected'); btn.setAttribute('aria-pressed','true');
        selected = opts.find(o => o.uniqid === btn.dataset.id) || opts[0] || null;
      }));
      const wishBtn = card.querySelector('.wishlist-toggle');
      const syncWish = () => { const active=window.MoeWishlist?.has(product.slug||product.id); wishBtn?.classList.toggle('active',!!active); wishBtn?.setAttribute('aria-pressed',String(!!active)); };
      syncWish();
      wishBtn?.addEventListener('click', () => {
        const added = window.MoeWishlist.toggle(product); syncWish();
        showNotification(added ? 'Saved to wishlist' : 'Removed from wishlist', 'success');
        if (sortSelect.value === 'saved') renderProducts();
      });
      card.querySelector('.buy')?.addEventListener('click', () => selected ? addToCart(product, selected) : showNotification('No purchase option available', 'error'));
      frag.appendChild(card);
    }
    productsContainer.replaceChildren(frag);
  }

  function updateStats() {
    const enabled = products.filter(p => p?.store?.enabled);
    statProducts.textContent = String(enabled.length);
    statOnline.textContent = String(enabled.filter(p => !['is-red'].includes(statusInfo(p).cls)).length);
  }

  function resetFilters() {
    currentFilter='all'; currentSearch=''; searchInput.value=''; sortSelect.value='featured';
    site?.storage?.writeText('moeStoreSort','featured');
    buildFilters(); updateUrl(); renderProducts();
    searchInput.focus();
  }

  async function loadProducts(force=false) {
    if (loading) return;
    loading = true;
    const cacheKey='moe_store_products_v3';

    // The Flask page already contains a fresh catalog snapshot. Rendering it
    // directly removes the extra /api/store-products request on every visit.
    if (!force && Array.isArray(initialProducts)) {
      products = initialProducts;
      initialProducts = null;
      try { sessionStorage.setItem(cacheKey, JSON.stringify({savedAt:Date.now(),items:products})); } catch {}
      buildFilters(); updateStats(); renderProducts();
      loading = false;
      return;
    }

    if (!force) {
      try {
        const cached = JSON.parse(sessionStorage.getItem(cacheKey) || 'null');
        if (cached?.items && Date.now()-Number(cached.savedAt||0) < 5*60*1000) {
          products = cached.items; buildFilters(); updateStats(); renderProducts();
          loading = false;
          return;
        }
      } catch { try { sessionStorage.removeItem(cacheKey); } catch {} }
    }
    try {
      const items = site?.fetchJSON ? await site.fetchJSON('/api/store-products', {headers:{Accept:'application/json'}, cache:'default'}, 10000) : await fetch('/api/store-products').then(r => { if(!r.ok) throw new Error(); return r.json(); });
      products = Array.isArray(items) ? items : [];
      try { sessionStorage.setItem(cacheKey, JSON.stringify({savedAt:Date.now(),items:products})); } catch {}
      buildFilters(); updateStats(); renderProducts();
    } catch (error) {
      if (!products.length) {
        productsContainer.innerHTML = `<div class="empty-state store-empty"><i class="fas fa-cloud-arrow-down"></i><h3>Store temporarily unavailable</h3><p>We couldn’t load the product catalog. Check your connection and try again.</p><button type="button" class="secondary-cta" id="store-retry">Retry</button></div>`;
        resultCount.textContent='Unable to load';
        document.getElementById('store-retry')?.addEventListener('click',()=>loadProducts(true));
      }
    } finally { loading=false; }
  }

  let lastTrackedSearch = '';
  const searchChanged = debounce(() => {
    currentSearch = searchInput.value.trim().toLowerCase();
    updateUrl(); renderProducts();
    if (currentSearch.length >= 2 && currentSearch !== lastTrackedSearch) {
      lastTrackedSearch = currentSearch;
      fetch('/api/analytics/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'search',query:currentSearch}),keepalive:true}).catch(()=>{});
    }
  }, 800);
  searchInput.addEventListener('input', searchChanged);
  sortSelect.addEventListener('change', () => {
    if (!validSorts.has(sortSelect.value)) sortSelect.value='featured';
    site?.storage?.writeText('moeStoreSort',sortSelect.value);
    updateUrl(); renderProducts();
  });
  resetButton?.addEventListener('click', resetFilters);
  window.addEventListener('moeWishlistUpdated', () => { if (sortSelect.value==='saved') renderProducts(); });
  window.addEventListener('moeNetworkRestored', () => { if (!products.length) loadProducts(true); });
  window.addEventListener('storage', e => { if (e.key===CART_STORAGE_KEY) cart=site?.cart?.read?.() || cart; });
  document.addEventListener('keydown', e => {
    if (e.key==='/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName||'')) { e.preventDefault(); searchInput.focus(); }
    if (e.key==='Escape' && document.activeElement===searchInput) { searchInput.value=''; currentSearch=''; updateUrl(); searchInput.blur(); renderProducts(); }
  });
  window.addEventListener('popstate', () => {
    const u=new URL(location.href); currentSearch=(u.searchParams.get('q')||'').trim().toLowerCase(); currentFilter=(u.searchParams.get('category')||'all').toLowerCase(); sortSelect.value=validSorts.has(u.searchParams.get('sort'))?u.searchParams.get('sort'):'featured'; searchInput.value=u.searchParams.get('q')||''; buildFilters(); renderProducts();
  });

  loadProducts();
})();
