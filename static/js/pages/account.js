(() => {
  'use strict';
  const site = window.MoeSite;
  const avatarInput = document.getElementById('profileAvatarInput');
  const avatarStatus = document.getElementById('profileAvatarStatus');
  const avatarEditor = avatarInput?.closest('.profile-avatar-editor');
  const csrfToken = document.querySelector('#profileForm input[name="csrf_token"]')?.value || '';
  let savedAvatarUrl = document.getElementById('profileAvatarPreview')?.src || '';

  const setAvatarStatus = (message, state = '') => {
    if (!avatarStatus) return;
    avatarStatus.textContent = message;
    avatarStatus.className = `profile-avatar-status ${state}`.trim();
  };
  const setAllAvatars = url => document.querySelectorAll('[data-profile-avatar]').forEach(image => { image.src = url; });

  avatarInput?.addEventListener('change', async event => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 4 * 1024 * 1024) {
      input.value = '';
      setAvatarStatus('That image is over 4 MB.', 'error');
      return;
    }
    const allowedTypes = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);
    const allowedExtension = /\.(png|jpe?g|gif|webp)$/i.test(file.name || '');
    if (!allowedTypes.has(file.type) && !allowedExtension) {
      input.value = '';
      setAvatarStatus('Choose a PNG, JPG, GIF, or WEBP image.', 'error');
      return;
    }
    const previewUrl = URL.createObjectURL(file);
    setAllAvatars(previewUrl);
    avatarEditor?.classList.add('is-uploading');
    setAvatarStatus('Uploading and saving your image…', 'uploading');
    const body = new FormData();
    body.append('csrf_token', csrfToken);
    body.append('avatar', file, file.name);
    try {
      const payload = site ? await site.fetchJSON('/account/profile/avatar', {
        method: 'POST', body, credentials: 'same-origin', headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
      }, 15000) : null;
      if (!payload?.ok || !payload.avatar_url) throw new Error(payload?.error || 'The image could not be saved.');
      savedAvatarUrl = `${payload.avatar_url}${payload.avatar_url.includes('?') ? '&' : '?'}v=${Date.now()}`;
      setAllAvatars(savedAvatarUrl);
      input.value = '';
      setAvatarStatus('Profile image saved.', 'success');
    } catch (error) {
      setAllAvatars(savedAvatarUrl);
      setAvatarStatus(error?.name === 'AbortError' ? 'Upload timed out. Check your connection and try again.' : (error.message || 'The image could not be saved.'), 'error');
    } finally {
      URL.revokeObjectURL(previewUrl);
      avatarEditor?.classList.remove('is-uploading');
    }
  });

  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const value = button.dataset.copy || '';
    try {
      await navigator.clipboard.writeText(value);
      button.innerHTML = '<i class="fas fa-check" aria-hidden="true"></i>';
      button.setAttribute('aria-label', 'Copied');
      setTimeout(() => { button.innerHTML = '<i class="fas fa-copy" aria-hidden="true"></i>'; button.setAttribute('aria-label', 'Copy key'); }, 1200);
    } catch {
      window.showSiteToast?.('Could not copy automatically. Select the key and copy it manually.', 'warning');
    }
  }));

  const bio = document.getElementById('profileBio');
  const bioCount = document.getElementById('profileBioCount');
  const updateBioCount = () => { if (bio && bioCount) bioCount.textContent = `${bio.value.length} / ${bio.maxLength}`; };
  bio?.addEventListener('input', updateBioCount);
  updateBioCount();

  const links = [...document.querySelectorAll('.account-nav a')];
  const sections = [...document.querySelectorAll('.account-main > section[id]')];
  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    links.forEach(link => link.classList.toggle('active', link.getAttribute('href') === `#${visible.target.id}`));
  }, { rootMargin: '-30% 0px -58%', threshold: [0, .2, .5] });
  sections.forEach(section => observer.observe(section));

  document.querySelector('#notifications form[action="/account/notifications/read"]')?.addEventListener('submit', async function(event) {
    event.preventDefault();
    const button = this.querySelector('button');
    if (button) button.disabled = true;
    try {
      const response = await fetch(this.action, {
        method: 'POST', body: new FormData(this),
        headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin'
      });
      if (!response.ok) throw new Error('Could not mark notifications read.');
      document.querySelectorAll('.notification-item.unread').forEach(item => item.classList.remove('unread'));
      window.dispatchEvent(new Event('focus'));
    } catch {
      this.submit();
    } finally {
      if (button) button.disabled = false;
    }
  });
})();
