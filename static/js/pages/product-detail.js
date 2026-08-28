(() => {
  'use strict';
  const site=window.MoeSite;
  const dataEl=document.getElementById('productData');
  if(!dataEl) return;
  let product={};
  try{product=JSON.parse(dataEl.textContent||'{}')}catch{return}
  const optionsWrap=document.getElementById('detail-options');
  const descriptionWrap=document.getElementById('detail-description');
  const addButton=document.getElementById('add-detail-cart');
  const wishlistButton=document.getElementById('detail-wishlist');
  const shareButton=document.getElementById('detail-share');
  const esc=site?.escapeHtml || (v=>String(v??''));
  const FALLBACK=site?.fallbackImage||'/static/logo.png';
  let cart=site?.cart?.read?.()||[];
  let selectedOption=null;
  const choiceKey=`moeProductOption:${String(product.slug||product.id||'')}`;

  function show(message,type='success'){if(window.showSiteToast)window.showSiteToast(message,type)}
  function normalizeOption(o){return{id:o?.id??o?.uniqid??'',uniqid:String(o?.uniqid??o?.id??''),name:String(o?.name||'Option'),price:Math.max(0,Number(o?.price||0))}}
  function getOptions(){return(product?.store?.options||[]).map(normalizeOption).filter(o=>o.uniqid)}
  function soldOut(){return /(out of stock|sold out|unavailable|disabled)/i.test(String(product?.store?.stockStatus||''))}
  function saveCart(){cart=site?.cart?.write?.(cart)||cart}
  function syncAddButton(){
    if(!addButton)return;
    const unavailable=soldOut()||!selectedOption;
    addButton.disabled=unavailable;
    if(soldOut()) addButton.innerHTML='<i class="fas fa-ban"></i> Unavailable';
    else if(!selectedOption) addButton.innerHTML='<i class="fas fa-circle-info"></i> No purchase option';
    else addButton.innerHTML=`<i class="fas fa-cart-plus"></i> Add to cart <span class="pd-button-price">$${selectedOption.price.toFixed(2)}</span>`;
  }
  function renderOptions(){
    const options=getOptions();
    const saved=site?.storage?.readText(choiceKey,'')||'';
    selectedOption=options.find(o=>o.uniqid===saved)||options[0]||null;
    if(!options.length){optionsWrap.innerHTML='<div class="empty-state">No purchase options are currently available.</div>';syncAddButton();return}
    optionsWrap.innerHTML=options.map(opt=>`<button type="button" class="pd-option ${opt.uniqid===selectedOption.uniqid?'selected':''}" data-option-uniqid="${esc(opt.uniqid)}" aria-pressed="${opt.uniqid===selectedOption.uniqid?'true':'false'}"><span class="pd-option-left"><span class="pd-option-check"></span><span class="pd-option-copy"><span>${esc(opt.name)}</span><small>Digital access</small></span></span><strong class="pd-option-price">$${opt.price.toFixed(2)}</strong></button>`).join('');
    optionsWrap.querySelectorAll('.pd-option').forEach(btn=>btn.addEventListener('click',()=>{
      optionsWrap.querySelectorAll('.pd-option').forEach(x=>{x.classList.remove('selected');x.setAttribute('aria-pressed','false')});
      btn.classList.add('selected');btn.setAttribute('aria-pressed','true');
      selectedOption=options.find(opt=>opt.uniqid===btn.dataset.optionUniqid)||options[0]||null;
      if(selectedOption)site?.storage?.writeText(choiceKey,selectedOption.uniqid);
      syncAddButton();
    }));
    syncAddButton();
  }
  function renderDescription(){
    const text=product.detailedDescription||product.description||'No additional product details have been added yet.';
    const lines=String(text).split(/\n+/).map(x=>x.trim()).filter(Boolean);
    descriptionWrap.innerHTML=lines.length?lines.map(line=>`<div class="pd-line">${esc(line)}</div>`).join(''):'<div class="empty-state">No additional product details have been added yet.</div>';
  }
  function addToCart(){
    if(!selectedOption||soldOut())return show('This product is not currently available.','error');
    const existing=cart.find(i=>String(i.uniqid)===selectedOption.uniqid);
    if(existing)existing.quantity=Math.min(10,(Number(existing.quantity)||1)+1);
    else cart.push({uniqid:selectedOption.uniqid,productId:product.id,slug:product.slug,productName:product.name,image:product.image||FALLBACK,optionId:selectedOption.id,optionName:selectedOption.name,price:selectedOption.price,quantity:1});
    saveCart();
    if(window.showCartToast)window.showCartToast({productName:product.name,optionName:selectedOption.name,image:product.image||FALLBACK,price:selectedOption.price});
    else show('Added to cart','success');
    fetch('/api/analytics/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'add_to_cart',product_slug:product.slug,value:selectedOption.price})}).catch(()=>{});
  }
  function syncWishlist(){
    const saved=Boolean(window.MoeWishlist?.has(product.slug||product.id));
    wishlistButton?.classList.toggle('active',saved);wishlistButton?.setAttribute('aria-pressed',String(saved));wishlistButton?.setAttribute('aria-label',saved?'Remove saved product':'Save product');
  }
  async function share(){
    const data={title:product.name||'Product',text:`View ${product.name||'this product'} on moealturej`,url:location.href};
    try{
      if(navigator.share){await navigator.share(data);return}
      await navigator.clipboard.writeText(location.href);show('Product link copied','success');
    }catch(err){if(err?.name!=='AbortError')show('Could not share this link','error')}
  }

  addButton?.addEventListener('click',addToCart);
  wishlistButton?.addEventListener('click',()=>{const added=window.MoeWishlist?.toggle(product);syncWishlist();show(added?'Saved to wishlist':'Removed from wishlist','success')});
  shareButton?.addEventListener('click',share);
  window.addEventListener('storage',e=>{if(e.key==='moeCart')cart=site?.cart?.read?.()||cart});
  window.MoeWishlist?.addRecent(product);syncWishlist();renderOptions();renderDescription();
  fetch('/api/analytics/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'product_view',product_slug:product.slug})}).catch(()=>{});
})();
