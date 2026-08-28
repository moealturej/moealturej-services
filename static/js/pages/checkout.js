(() => {
  'use strict';
  if (!window.MoeSite) return;

  const { cart: cartStore, storage, escapeHtml, fetchJSON } = window.MoeSite;
  let config = {};
  try { config = JSON.parse(document.getElementById('checkoutConfig')?.textContent || '{}'); } catch {}
  const csrf = String(config.csrf || '');
  let cart = cartStore.read();
  let discountCode = storage.readText('moeDiscountCode', '').toUpperCase().replace(/[^A-Z0-9_-]/g, '').slice(0, 32);
  let previewRequest = 0;

  const message = document.getElementById('pay-message');
  const list = document.getElementById('checkout-list');
  const discountInput = document.getElementById('discount-code');
  const applyButton = document.getElementById('apply-discount');
  const stripeButton = document.getElementById('stripe-btn');
  const paypalButton = document.getElementById('paypal-direct-btn');

  function setMessage(text, busy = false, type = '') {
    message.classList.remove('error', 'success');
    if (type) message.classList.add(type);
    message.replaceChildren();
    if (busy) {
      const spinner = document.createElement('span');
      spinner.className = 'loading-spinner';
      spinner.setAttribute('aria-hidden', 'true');
      message.appendChild(spinner);
    }
    message.append(document.createTextNode(String(text || '')));
  }

  const money = value => `$${Math.max(0, Number(value) || 0).toFixed(2)}`;
  const payload = () => ({
    items: cartStore.read().map(item => ({
      slug: item.slug,
      option_id: item.optionId || item.uniqid,
      quantity: Number(item.quantity || 1)
    })),
    discount_code: discountCode
  });

  function setPaymentButtonsDisabled(disabled) {
    document.querySelectorAll('.payment-method').forEach(button => { button.disabled = disabled; });
  }

  async function postJson(url, data) {
    const json = await fetchJSON(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(data)
    }, 15000);
    if (!json || json.ok === false) throw new Error(json?.error || 'The request could not be completed.');
    return json;
  }

  function renderEmpty() {
    list.innerHTML = '<div class="checkout-message">Your cart is empty. <a href="/store">Return to the store</a> to add a product.</div>';
    ['subtotal', 'discount', 'fee', 'total'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = id === 'discount' ? '-$0.00' : '$0.00';
    });
    document.getElementById('discount-lines').replaceChildren();
    setMessage('Add an item before continuing to payment.');
    setPaymentButtonsDisabled(true);
  }

  async function loadPreview() {
    cart = cartStore.read();
    if (!cart.length) { renderEmpty(); return; }
    const request = ++previewRequest;
    setPaymentButtonsDisabled(true);
    setMessage('Loading your order…', true);
    try {
      const json = await postJson('/api/checkout/preview', payload());
      if (request !== previewRequest) return;
      const checkout = json.cart || {};
      document.getElementById('subtotal').textContent = money(checkout.subtotal);
      document.getElementById('discount').textContent = `-${money(checkout.discount || 0)}`;
      document.getElementById('fee').textContent = money(checkout.fee);
      document.getElementById('total').textContent = money(checkout.total);
      document.getElementById('discount-lines').innerHTML = (checkout.discounts || []).map(discount => `
        <div class="discount-pill"><span>${escapeHtml(discount.label || 'Discount')}</span><strong>-${money(discount.amount || 0)}</strong></div>`).join('');
      list.innerHTML = (checkout.items || []).map(item => `
        <article class="checkout-item">
          <img src="${escapeHtml(item.image || '/static/logo.png')}" alt="${escapeHtml(item.product_name || 'Product')}">
          <div class="checkout-item-copy"><h3>${escapeHtml(item.product_name || 'Product')}</h3><p>${escapeHtml(item.option_name || 'Standard option')} &nbsp;×&nbsp; ${Math.max(1, Number(item.quantity) || 1)}</p></div>
          <strong>${money(item.line_amount)}</strong>
        </article>`).join('');
      setPaymentButtonsDisabled(false);
      setMessage('Your order is ready. Select a payment method to continue.', false, 'success');
    } catch (error) {
      if (request !== previewRequest) return;
      setPaymentButtonsDisabled(true);
      const text = error?.name === 'AbortError' ? 'Checkout took too long to respond. Check your connection and try again.' : (error.message || 'Could not load checkout.');
      setMessage(text, false, 'error');
    }
  }

  async function beginPayment(button, url, pendingKey = '') {
    cart = cartStore.read();
    if (!cart.length) { renderEmpty(); return; }
    setPaymentButtonsDisabled(true);
    setMessage(`Opening secure ${button === stripeButton ? 'Stripe' : 'PayPal'} checkout…`, true);
    try {
      const json = await postJson(url, payload());
      const target = json.url || json.approve_url;
      if (!target) throw new Error('The payment provider did not return a checkout link.');
      if (pendingKey) storage.writeText(pendingKey, json.order_id || '');
      window.location.assign(target);
    } catch (error) {
      setPaymentButtonsDisabled(false);
      setMessage(error?.name === 'AbortError' ? 'The payment provider took too long to respond. Please try again.' : (error.message || 'Payment could not be started.'), false, 'error');
    }
  }

  stripeButton?.addEventListener('click', () => beginPayment(stripeButton, '/checkout/stripe'));
  paypalButton?.addEventListener('click', () => beginPayment(paypalButton, '/checkout/paypal/create', 'moePendingPayPalOrder'));

  discountInput.value = discountCode;
  discountInput.addEventListener('input', () => {
    discountInput.value = (discountInput.value || '').toUpperCase().replace(/[^A-Z0-9_-]/g, '').slice(0, 32);
  });
  discountInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); applyButton.click(); }
  });
  applyButton.addEventListener('click', async () => {
    discountCode = (discountInput.value || '').toUpperCase().replace(/[^A-Z0-9_-]/g, '').slice(0, 32);
    discountInput.value = discountCode;
    if (discountCode) storage.writeText('moeDiscountCode', discountCode);
    else storage.remove('moeDiscountCode');
    await loadPreview();
  });

  window.addEventListener('storage', event => { if (event.key === cartStore.key) loadPreview(); });
  window.addEventListener('moeNetworkRestored', loadPreview);
  window.addEventListener('pageshow', event => { if (event.persisted) loadPreview(); });
  loadPreview();
})();
