(() => {
  'use strict';
  const site=window.MoeSite;
  const grid=document.getElementById('minimal-product-grid');
  const statusText=document.getElementById('homeStoreStatus');
  if(!grid) return;
  const esc=site?.escapeHtml || (v=>String(v??''));
  const price=p=>{const vals=(p?.store?.options||[]).map(x=>Number(x.price)).filter(Number.isFinite);return vals.length?'From $'+Math.min(...vals).toFixed(2):'View options'};
  const state=p=>String(p?.status?.state||p?.status?.label||'Online').toLowerCase();
  const status=p=>{const raw=String(p?.status?.state||p?.status?.label||'Online');const low=raw.toLowerCase();if(/offline|down|outage/.test(low))return{label:raw,cls:'down'};if(/degraded|issue|limited|updat|maintenance/.test(low))return{label:raw,cls:'warn'};return{label:raw||'Online',cls:'live'}};
  async function load(){
    try{
      const items=site?.fetchJSON?await site.fetchJSON('/api/store-products',{headers:{Accept:'application/json'},cache:'no-cache'},9000):await fetch('/api/store-products').then(r=>r.json());
      const enabled=(Array.isArray(items)?items:[]).filter(x=>x?.store?.enabled);
      const list=[...enabled].sort((a,b)=>Number(Boolean(b.featured))-Number(Boolean(a.featured))||Number(a.displayOrder||9999)-Number(b.displayOrder||9999)).slice(0,3);
      if(statusText){
        const bad=enabled.filter(x=>/offline|down|outage/.test(state(x))).length;
        const warn=enabled.filter(x=>/degraded|issue|limited|updat|maintenance/.test(state(x))).length;
        statusText.classList.remove('warn','down');
        if(bad){statusText.classList.add('down');statusText.innerHTML=`<i></i> ${bad} product${bad===1?'':'s'} offline`;}
        else if(warn){statusText.classList.add('warn');statusText.innerHTML=`<i></i> ${warn} product${warn===1?'':'s'} updating`;}
        else statusText.innerHTML='<i></i> Store online';
      }
      grid.innerHTML=list.length?list.map(x=>{const s=status(x);const slug=encodeURIComponent(x.slug||x.id||'');return `<article class="minimal-product-card"><a class="minimal-product-image" href="/product/${slug}"><img src="${esc(x.image||'/static/logo.png')}" alt="${esc(x.name||'Product')}" loading="lazy" decoding="async"><span class="${s.cls}"><i></i> ${esc(s.label)}</span></a><div class="minimal-product-info"><div><small>${esc(x.category||'Digital product')}</small><h3><a href="/product/${slug}">${esc(x.name||'Product')}</a></h3></div><div class="minimal-product-bottom"><strong>${esc(price(x))}</strong><a href="/product/${slug}" aria-label="View ${esc(x.name||'product')}"><i class="fas fa-arrow-right"></i></a></div></div></article>`}).join(''):'<div class="minimal-empty">No featured products are available right now.</div>';
    }catch{
      if(statusText){statusText.classList.add('warn');statusText.innerHTML='<i></i> Status unavailable';}
      grid.innerHTML='<div class="minimal-empty"><strong>Products are temporarily unavailable.</strong><span>Open the store to try again.</span><a class="minimal-btn secondary" href="/products">Open store</a></div>';
    }
  }
  load();
})();
