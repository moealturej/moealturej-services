(() => {
  'use strict';

  const page = document.getElementById('cartPage');
  if (!page || !window.MoeSite) return;

  const { cart: cartStore, storage, escapeHtml, clamp } = window.MoeSite;
  const feePercent = Math.max(0, Number(page.dataset.feePercent) || 0);
  const isLoggedIn = page.dataset.authenticated === 'true';
  const list = document.getElementById('cart-list');
  const subtotalEl = document.getElementById('subtotal');
  const feeEl = document.getElementById('fee');
  const totalEl = document.getElementById('total');
  const checkoutBtn = document.getElementById('checkout-btn');
  const clearBtn = document.getElementById('clear-cart');
  const codeInput = document.getElementById('cart-code');
  const codeLabel = document.getElementById('cart-code-label');
  let cart = cartStore.read();
  let undoCart = null;

  const money = cents => `$${(Math.max(0, Number(cents) || 0) / 100).toFixed(2)}`;

  function save(next = cart) {
    cart = cartStore.write(next);
  }

  function summary() {
    const subtotal = cart.reduce((sum, item) => {
      const cents = Math.round(Math.max(0, Number(item.price) || 0) * 100);
      return sum + cents * clamp(item.quantity || 1, 1, 10);
    }, 0);
    const fee = Math.round(subtotal * (feePercent / 100));
    return { subtotal, fee, total: subtotal + fee };
  }

  function emptyMarkup() {
    return `
      <div class="empty-cart" role="status">
        <div>
          <div class="empty-cart-icon"><i class="fas fa-shopping-bag" aria-hidden="true"></i></div>
          <strong>Your cart is empty</strong>
          <span>${undoCart?.length ? 'Your previous cart can still be restored.' : 'Add a product from the store to get started.'}</span>
          <div class="empty-cart-actions">
            ${undoCart?.length ? '<button type="button" class="cart-undo" data-cart-action="undo"><i class="fas fa-rotate-left" aria-hidden="true"></i> Undo clear</button>' : ''}
            <a href="/store"><i class="fas fa-store" aria-hidden="true"></i> Browse products</a>
          </div>
        </div>
      </div>`;
  }

  function itemMarkup(item, index) {
    const quantity = clamp(item.quantity || 1, 1, 10);
    const linePrice = Math.max(0, Number(item.price) || 0) * quantity;
    return `
      <article class="cart-item" data-cart-index="${index}">
        <img class="cart-item-image" src="${escapeHtml(item.image || '/static/logo.png')}" alt="${escapeHtml(item.productName || 'Product')}">
        <div class="cart-item-info">
          <h3><a href="/product/${encodeURIComponent(item.slug)}">${escapeHtml(item.productName || 'Product')}</a></h3>
          <p class="cart-item-option">${escapeHtml(item.optionName || 'Standard option')}</p>
          <div class="cart-item-actions">
            <div class="quantity-control" aria-label="Quantity for ${escapeHtml(item.productName || 'product')}">
              <button type="button" data-cart-action="decrease" data-index="${index}" aria-label="Decrease quantity" ${quantity <= 1 ? 'disabled' : ''}>−</button>
              <strong aria-live="polite">${quantity}</strong>
              <button type="button" data-cart-action="increase" data-index="${index}" aria-label="Increase quantity" ${quantity >= 10 ? 'disabled' : ''}>+</button>
            </div>
            <button type="button" class="remove-item" data-cart-action="remove" data-index="${index}">Remove</button>
          </div>
        </div>
        <div class="cart-item-price">${money(Math.round(linePrice * 100))}</div>
      </article>`;
  }

  function render() {
    cart = cartStore.read();
    const totals = summary();
    subtotalEl.textContent = money(totals.subtotal);
    feeEl.textContent = money(totals.fee);
    totalEl.textContent = money(totals.total);
    checkoutBtn.disabled = cart.length === 0;
    clearBtn.disabled = cart.length === 0;
    list.innerHTML = cart.length ? cart.map(itemMarkup).join('') : emptyMarkup();
  }

  function updateQuantity(index, delta) {
    const item = cart[index];
    if (!item) return;
    item.quantity = Math.round(clamp((Number(item.quantity) || 1) + delta, 1, 10));
    save(cart);
    render();
  }

  list.addEventListener('click', event => {
    const button = event.target.closest('[data-cart-action]');
    if (!button) return;
    const action = button.dataset.cartAction;
    if (action === 'undo') {
      if (!undoCart?.length) return;
      save(undoCart);
      undoCart = null;
      render();
      window.showSiteToast?.('Cart restored.', 'success');
      return;
    }
    const index = Number(button.dataset.index);
    if (!Number.isInteger(index) || !cart[index]) return;
    if (action === 'increase') updateQuantity(index, 1);
    else if (action === 'decrease') updateQuantity(index, -1);
    else if (action === 'remove') {
      cart.splice(index, 1);
      save(cart);
      render();
    }
  });

  const savedCode = storage.readText('moeDiscountCode', '').toUpperCase().replace(/[^A-Z0-9_-]/g, '').slice(0, 32);
  codeInput.value = savedCode;
  codeLabel.textContent = savedCode || 'None';
  codeInput.addEventListener('input', () => {
    const code = (codeInput.value || '').toUpperCase().replace(/[^A-Z0-9_-]/g, '').slice(0, 32);
    codeInput.value = code;
    codeLabel.textContent = code || 'None';
    if (code) storage.writeText('moeDiscountCode', code);
    else storage.remove('moeDiscountCode');
  });

  clearBtn.addEventListener('click', () => {
    if (!cart.length) return;
    undoCart = cart.map(item => ({ ...item }));
    save([]);
    render();
    window.showSiteToast?.('Cart cleared. You can undo it here.', 'info');
  });

  checkoutBtn.addEventListener('click', () => {
    cart = cartStore.read();
    if (!cart.length) return;
    window.location.assign(isLoggedIn ? '/checkout' : '/login?next=/checkout');
  });

  window.addEventListener('storage', event => {
    if (event.key === cartStore.key) render();
  });
  window.addEventListener('moeCartUpdated', render);
  render();
})();
