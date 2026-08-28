(() => {
  'use strict';
  const form = document.getElementById('supportTicketForm');
  if (!form) return;
  const orderSelect = document.getElementById('orderItemSelect');
  const orderId = document.getElementById('orderIdInput');
  const itemIndex = document.getElementById('itemIndexInput');
  const files = document.getElementById('supportAttachments');
  const fileSummary = document.getElementById('supportFileSummary');
  const maxFiles = Math.max(1, Number(form.dataset.maxFiles) || 3);
  const maxBytes = Math.max(1, Number(form.dataset.maxFileMb) || 8) * 1024 * 1024;

  function syncOrder() {
    const [id = '', index = '0'] = String(orderSelect?.value || '::0').split('::');
    if (orderId) orderId.value = id;
    if (itemIndex) itemIndex.value = /^\d+$/.test(index) ? index : '0';
  }

  form.querySelectorAll('input[maxlength], textarea[maxlength]').forEach(field => {
    const counter = form.querySelector(`[data-count-for="${CSS.escape(field.name)}"]`);
    if (!counter) return;
    const update = () => { counter.textContent = `${field.value.length} / ${field.maxLength}`; };
    field.addEventListener('input', update);
    update();
  });

  files?.addEventListener('change', () => {
    const selected = Array.from(files.files || []);
    const tooMany = selected.length > maxFiles;
    const tooLarge = selected.find(file => file.size > maxBytes);
    if (tooMany || tooLarge) {
      files.value = '';
      fileSummary.textContent = tooMany ? `Choose up to ${maxFiles} files.` : `${tooLarge.name} is larger than ${form.dataset.maxFileMb}MB.`;
      fileSummary.classList.add('field-error');
      window.showSiteToast?.(fileSummary.textContent, 'warning');
      return;
    }
    fileSummary.classList.remove('field-error');
    if (!selected.length) fileSummary.textContent = 'No files selected';
    else fileSummary.textContent = `${selected.length} file${selected.length === 1 ? '' : 's'} · ${(selected.reduce((sum, file) => sum + file.size, 0) / 1024 / 1024).toFixed(1)}MB total`;
  });

  orderSelect?.addEventListener('change', syncOrder);
  form.addEventListener('submit', syncOrder);
  syncOrder();
})();
