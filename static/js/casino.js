(() => {
  'use strict';

  const app = document.getElementById('casinoApp');
  if (!app) return;

  const csrf = app.dataset.csrf || '';
  const minWager = Number(app.dataset.minWager || 10);
  const maxWager = Number(app.dataset.maxWager || 25000);
  const balanceEl = document.getElementById('casinoBalance');
  const toastEl = document.getElementById('casinoToast');
  let toastTimer = 0;
  let balance = Number(String(balanceEl?.textContent || '0').replace(/,/g, '')) || 0;
  let mutationVersion = 0;

  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const storageGet = key => { try { return window.localStorage.getItem(key); } catch { return null; } };
  const storageSet = (key, value) => { try { window.localStorage.setItem(key, String(value)); } catch {} };
  const formatCredits = value => Math.max(0, Number(value || 0)).toLocaleString();
  const formatTime = value => {
    const date = window.parseSiteTimestamp ? window.parseSiteTimestamp(value) : new Date(value);
    if (!date || Number.isNaN(date.getTime())) return '';
    try {
      return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(date);
    } catch {
      return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }
  };

  function setBalance(value) {
    balance = Math.max(0, Number(value || 0));
    if (balanceEl) {
      balanceEl.textContent = formatCredits(balance);
      balanceEl.animate?.(
        [{ transform: 'scale(1)', color: '#fff' }, { transform: 'scale(1.12)', color: '#f4c96b' }, { transform: 'scale(1)', color: '#fff' }],
        { duration: 450, easing: 'ease-out' }
      );
    }
    syncWagerAffordability();
  }

  function showToast(message, type = '') {
    if (!toastEl) return;
    window.clearTimeout(toastTimer);
    toastEl.textContent = String(message || '');
    toastEl.className = `casino-toast show ${type}`;
    toastTimer = window.setTimeout(() => {
      toastEl.className = 'casino-toast';
    }, 3200);
  }

  function handleGameError(error) {
    showToast(error?.message || 'Something went wrong. Refreshing your game state…', 'error');
    // A POST can complete on the server even if the browser loses the response.
    // Always reconcile balance/active rounds after an error instead of leaving
    // dead buttons or inviting a duplicate wager.
    loadState(false);
  }

  function wagerNumber(value) {
    const raw = String(value ?? '').replace(/[^0-9]/g, '');
    return raw ? Number(raw) : 0;
  }

  function writeWager(input, value) {
    if (!input) return;
    const safe = Math.max(minWager, Math.min(maxWager, Math.floor(Number(value) || minWager)));
    input.value = String(safe);
    storageSet(`casino:wager:${input.id}`, safe);
    const control = input.closest('[data-wager-control]');
    control?.querySelectorAll('[data-wager-value]').forEach(button => {
      const selected = Number(button.dataset.wagerValue) === safe;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
  }

  function syncWagerAffordability() {
    document.querySelectorAll('[data-wager-control]').forEach(control => {
      const id = control.dataset.wagerControl;
      const input = document.getElementById(id);
      const locked = control.classList.contains('disabled');
      const buttons = [...control.querySelectorAll('[data-wager-value]')];
      buttons.forEach(button => {
        const tooExpensive = Number(button.dataset.wagerValue || 0) > balance;
        button.disabled = locked || tooExpensive;
        button.classList.toggle('unaffordable', !locked && tooExpensive);
      });
      if (!locked && input && Number(input.value || 0) > balance) {
        const affordable = buttons.map(button => Number(button.dataset.wagerValue || 0)).filter(value => value <= balance).sort((a, b) => b - a);
        if (affordable.length) writeWager(input, affordable[0]);
      }
    });
  }

  function readWager(id) {
    const input = document.getElementById(id);
    const wager = Math.floor(wagerNumber(input?.value));
    if (!Number.isFinite(wager) || wager < minWager) throw new Error(`Minimum wager is ${formatCredits(minWager)} credits.`);
    if (wager > maxWager) throw new Error(`Maximum wager is ${formatCredits(maxWager)} credits.`);
    if (wager > balance) throw new Error('You do not have enough credits for that wager.');
    if (input) input.value = String(wager);
    return wager;
  }

  function setWagerControlDisabled(id, disabled) {
    const control = document.querySelector(`[data-wager-control="${id}"]`);
    control?.classList.toggle('disabled', Boolean(disabled));
    const input = document.getElementById(id);
    if (input) input.disabled = Boolean(disabled);
    syncWagerAffordability();
  }

  async function api(url, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    if (method !== 'GET') mutationVersion += 1;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    const requestOptions = {
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(method !== 'GET' ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } : {}),
        ...(options.headers || {})
      },
      ...options,
      method,
      signal: controller.signal
    };
    if (requestOptions.body && typeof requestOptions.body !== 'string') {
      requestOptions.body = JSON.stringify(requestOptions.body);
    }
    let response;
    try {
      response = await fetch(url, requestOptions);
    } catch (error) {
      const wrapped = new Error(error?.name === 'AbortError' ? 'The request timed out. Refreshing your game state…' : 'Connection problem. Refreshing your game state…');
      wrapped.recoverState = true;
      throw wrapped;
    } finally {
      window.clearTimeout(timeout);
    }
    let data = {};
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok || data.ok === false) {
      if (data.age_gate_required) window.location.href = '/casino/age-check';
      const error = new Error(data.error || `Request failed (${response.status})`);
      error.recoverState = response.status >= 500 || /expired|no longer active|already settled|changed|refresh/i.test(error.message);
      throw error;
    }
    return data;
  }

  function setBusy(button, busy, busyText = 'Working…') {
    if (!button) return;
    if (busy) {
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<i class="fas fa-circle-notch fa-spin"></i> ${busyText}`;
    } else {
      button.disabled = false;
      if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
    }
  }

  document.querySelectorAll('[data-wager-control]').forEach(control => {
    const id = control.dataset.wagerControl;
    const input = document.getElementById(id);
    if (!input) return;

    control.querySelectorAll('[data-wager-value]').forEach(button => button.addEventListener('click', () => {
      writeWager(input, Number(button.dataset.wagerValue || minWager));
    }));
    const remembered = wagerNumber(storageGet(`casino:wager:${id}`));
    const allowed = [...control.querySelectorAll('[data-wager-value]')].map(button => Number(button.dataset.wagerValue));
    writeWager(input, allowed.includes(remembered) ? remembered : (wagerNumber(input.value) || 100));
  });
  syncWagerAffordability();

  // Tabs
  const tabs = [...document.querySelectorAll('[data-game-tab]')];
  const panels = [...document.querySelectorAll('[data-game-panel]')];

  function activateGame(name, { remember = true } = {}) {
    if (!tabs.some(tab => tab.dataset.gameTab === name)) name = 'slots';
    tabs.forEach(tab => {
      const active = tab.dataset.gameTab === name;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
    });
    panels.forEach(panel => panel.classList.toggle('active', panel.dataset.gamePanel === name));
    if (remember) storageSet('casino:last-game', name);
    if (name === 'plinko') window.requestAnimationFrame(resizePlinko);
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activateGame(tab.dataset.gameTab));
    tab.addEventListener('keydown', event => {
      if (!['ArrowRight', 'ArrowLeft'].includes(event.key)) return;
      event.preventDefault();
      const offset = event.key === 'ArrowRight' ? 1 : -1;
      const next = tabs[(index + offset + tabs.length) % tabs.length];
      next?.focus();
      next?.click();
    });
  });
  activateGame(storageGet('casino:last-game') || 'slots', { remember: false });

  // Fairness modal
  const fairnessModal = document.getElementById('fairnessModal');
  document.getElementById('openFairnessButton')?.addEventListener('click', () => {
    fairnessModal?.classList.add('open');
    fairnessModal?.setAttribute('aria-hidden', 'false');
  });
  fairnessModal?.querySelectorAll('[data-close-modal]').forEach(button => button.addEventListener('click', () => {
    fairnessModal.classList.remove('open');
    fairnessModal.setAttribute('aria-hidden', 'true');
  }));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { fairnessModal?.classList.remove('open'); fairnessModal?.setAttribute('aria-hidden', 'true'); }
  });

  // Daily bonus
  const dailyButton = document.getElementById('dailyClaimButton');
  dailyButton?.addEventListener('click', async () => {
    setBusy(dailyButton, true, 'Claiming');
    try {
      const data = await api('/api/casino/daily', { method: 'POST', body: {} });
      setBalance(data.balance);
      showToast(`Daily bonus claimed: +${formatCredits(data.bonus)} credits`, 'success');
      dailyButton.disabled = true;
      dailyButton.innerHTML = '<i class="fas fa-check"></i><span>Claimed today</span>';
    } catch (error) {
      handleGameError(error);
      setBusy(dailyButton, false);
    }
  });

  // Slots
  const slotMachine = document.getElementById('slotMachine');
  const slotButton = document.getElementById('slotSpinButton');
  const slotLever = document.getElementById('slotLever');
  const slotGrid = document.getElementById('slotGrid');
  const slotStatus = document.getElementById('slotStatus');
  const slotWinDisplay = document.getElementById('slotWinDisplay');
  const slotMachineButtons = [...document.querySelectorAll('[data-slot-machine]')];
  let slotCatalog = {};
  try {
    slotCatalog = JSON.parse(document.getElementById('slotMachineCatalog')?.textContent || '{}');
  } catch {
    slotCatalog = {};
  }
  let selectedSlotMachine = slotCatalog[storageGet('casino:slot-machine')] ? storageGet('casino:slot-machine') : (slotCatalog.classic ? 'classic' : Object.keys(slotCatalog)[0]);
  let slotsBusy = false;
  let slotTicker = 0;

  function slotSymbolNode(symbol, row, column) {
    const cell = document.createElement('div');
    const key = symbol?.key || '';
    cell.className = `slot-symbol symbol-${key}`;
    cell.dataset.row = String(row);
    cell.dataset.column = String(column);
    cell.dataset.key = key;
    const face = document.createElement('span');
    face.textContent = symbol?.symbol || '•';
    face.title = symbol?.name || key;
    cell.appendChild(face);
    return cell;
  }

  function starterSlotGrid(machine) {
    const symbols = machine?.symbols || [];
    const rows = Number(machine?.rows || 3);
    const columns = Number(machine?.columns || 3);
    return Array.from({ length: rows }, (_, row) => Array.from({ length: columns }, (_, column) => {
      return symbols[(row * columns + column) % Math.max(1, symbols.length)] || { key: '', symbol: '•', name: '' };
    }));
  }

  function renderSlotGrid(grid, winningCells = []) {
    if (!slotGrid) return;
    const machine = slotCatalog[selectedSlotMachine] || {};
    const rows = Array.isArray(grid) && grid.length ? grid : starterSlotGrid(machine);
    const columns = Number(machine.columns || rows[0]?.length || 3);
    const winners = new Set((winningCells || []).map(cell => `${cell[0]}:${cell[1]}`));
    slotGrid.textContent = '';
    slotGrid.className = `slot-reel-grid columns-${columns}`;
    rows.forEach((row, rowIndex) => row.forEach((symbol, columnIndex) => {
      const cell = slotSymbolNode(symbol, rowIndex, columnIndex);
      if (winners.has(`${rowIndex}:${columnIndex}`)) cell.classList.add('winning');
      slotGrid.appendChild(cell);
    }));
  }

  function renderSlotPaytable(machine) {
    const rows = document.getElementById('slotPaytableRows');
    if (!rows || !machine) return;
    rows.textContent = '';
    (machine.symbols || []).forEach(symbol => {
      const payouts = Object.entries(symbol.payouts || {});
      if (!payouts.length && symbol.key !== 'scatter') return;
      const item = document.createElement('div');
      item.className = 'slot-paytable-row';
      const icon = document.createElement('span');
      icon.className = `slot-paytable-symbol symbol-${symbol.key}`;
      icon.textContent = symbol.symbol;
      const copy = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = symbol.name;
      const values = document.createElement('small');
      if (symbol.key === 'scatter') {
        values.textContent = Object.entries(machine.scatter_payouts || {}).map(([count, value]) => `${count}+ pays ${value}× total bet`).join(' • ');
      } else {
        values.textContent = payouts.map(([count, value]) => `${count} = ${value}× line bet`).join(' • ');
      }
      copy.append(name, values);
      item.append(icon, copy);
      rows.appendChild(item);
    });
    if (machine.fruit_mix) {
      const item = document.createElement('div');
      item.className = 'slot-paytable-row';
      item.innerHTML = `<span class="slot-paytable-symbol">🍒🍋</span><div><strong>Mixed fruit</strong><small>Any 3 fruit pay ${machine.fruit_mix}× line bet</small></div>`;
      rows.appendChild(item);
    }
  }

  function selectSlotMachine(machineKey, grid = null, winningCells = []) {
    const machine = slotCatalog[machineKey];
    if (!machine || slotsBusy) return;
    selectedSlotMachine = machineKey;
    storageSet('casino:slot-machine', machineKey);
    slotMachineButtons.forEach(button => button.classList.toggle('active', button.dataset.slotMachine === machineKey));
    if (slotMachine) slotMachine.className = `slot-machine theme-${machine.theme || machineKey}`;
    const title = document.getElementById('slotPanelTitle');
    const description = document.getElementById('slotPanelDescription');
    const badge = document.getElementById('slotRtpBadge');
    const marquee = document.getElementById('slotMarqueeTitle');
    const lineCount = document.getElementById('slotLineCount');
    const paytableTitle = document.getElementById('slotPaytableTitle');
    const paytableLines = document.getElementById('slotPaytableLines');
    const ruleNote = document.getElementById('slotRuleNote');
    if (title) title.textContent = machine.label;
    if (description) description.textContent = machineKey === 'classic'
      ? 'Five paylines, wild substitutions, mixed-fruit wins, and two-cherry payouts.'
      : `${machine.paylines} paylines pay 3, 4, or 5 matching symbols from the left. Wilds substitute and scatters pay anywhere.`;
    if (badge) badge.textContent = `Approx. ${Number(machine.rtp).toFixed(1)}% RTP`;
    if (marquee) marquee.textContent = machine.label.toUpperCase();
    if (lineCount) lineCount.textContent = `${machine.paylines} PAYLINES`;
    if (paytableTitle) paytableTitle.textContent = `${machine.label} paytable`;
    if (paytableLines) paytableLines.textContent = `${machine.paylines} lines`;
    if (ruleNote) ruleNote.textContent = machineKey === 'classic'
      ? 'Two cherries and mixed fruit can win; all three symbols do not always need to match.'
      : 'Only the first 3 reels need to match for a line win; reels 4 and 5 increase the payout.';
    renderSlotPaytable(machine);
    renderSlotGrid(grid || starterSlotGrid(machine), winningCells);
    if (slotStatus) slotStatus.textContent = `${machine.subtitle} • choose a preset bet`;
    if (slotWinDisplay) slotWinDisplay.textContent = 'READY';
  }

  function randomizeSlotGrid() {
    const machine = slotCatalog[selectedSlotMachine];
    if (!machine) return;
    const symbols = machine.symbols || [];
    const grid = Array.from({ length: Number(machine.rows || 3) }, () => Array.from({ length: Number(machine.columns || 3) }, () => {
      return symbols[Math.floor(Math.random() * Math.max(1, symbols.length))] || { symbol: '•' };
    }));
    renderSlotGrid(grid);
  }

  async function spinSlots() {
    if (slotsBusy || !selectedSlotMachine) return;
    try {
      const wager = readWager('slotsWager');
      slotsBusy = true;
      slotMachine?.classList.add('spinning');
      slotLever?.classList.add('pulled');
      if (slotButton) slotButton.disabled = true;
      slotMachineButtons.forEach(button => { button.disabled = true; });
      setWagerControlDisabled('slotsWager', true);
      if (slotStatus) slotStatus.textContent = 'Reels spinning…';
      if (slotWinDisplay) slotWinDisplay.textContent = 'GOOD LUCK';
      const started = performance.now();
      slotTicker = window.setInterval(randomizeSlotGrid, 85);
      const data = await api('/api/casino/play/slots', { method: 'POST', body: { wager, machine: selectedSlotMachine } });
      await sleep(Math.max(0, 1250 - (performance.now() - started)));
      window.clearInterval(slotTicker);
      slotTicker = 0;
      renderSlotGrid(data.grid, data.winning_cells || []);
      [...slotGrid?.children || []].forEach(cell => {
        const delay = Number(cell.dataset.column || 0) * 110;
        cell.animate?.(
          [{ transform: 'translateY(-45px)', opacity: .15, filter: 'blur(5px)' }, { transform: 'translateY(0)', opacity: 1, filter: 'blur(0)' }],
          { duration: 420, delay, easing: 'cubic-bezier(.16,.8,.22,1)', fill: 'both' }
        );
      });
      await sleep(Number((slotCatalog[selectedSlotMachine]?.columns || 3)) * 110 + 250);
      setBalance(data.balance);
      if (slotStatus) slotStatus.textContent = data.label;
      if (slotWinDisplay) slotWinDisplay.textContent = data.payout ? `PAID ${formatCredits(data.payout)}` : 'NO WIN';
      const winSummary = (data.wins || []).slice(0, 2).map(win => win.label).join(' + ');
      showToast(
        data.payout ? `${winSummary || data.label} paid ${formatCredits(data.payout)} credits.` : `${data.machine_label}: no winning line.`,
        data.payout > wager ? 'success' : ''
      );
      loadState(false);
    } catch (error) {
      handleGameError(error);
    } finally {
      window.clearInterval(slotTicker);
      slotTicker = 0;
      slotMachine?.classList.remove('spinning');
      slotsBusy = false;
      if (slotButton) slotButton.disabled = false;
      slotMachineButtons.forEach(button => { button.disabled = false; });
      setWagerControlDisabled('slotsWager', false);
      window.setTimeout(() => slotLever?.classList.remove('pulled'), 250);
    }
  }

  slotMachineButtons.forEach(button => button.addEventListener('click', () => selectSlotMachine(button.dataset.slotMachine)));
  slotButton?.addEventListener('click', spinSlots);
  slotLever?.addEventListener('click', spinSlots);
  if (selectedSlotMachine) selectSlotMachine(selectedSlotMachine);

  // Roulette
  const rouletteOrder = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26];
  const rouletteRed = new Set([1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]);
  const rouletteBets = [...document.querySelectorAll('#rouletteBets button[data-bet-type]')];
  let rouletteBet = { type: 'color', value: 'red' };
  rouletteBets[0]?.classList.add('active');
  rouletteBets.forEach(button => button.addEventListener('click', event => {
    if (event.target instanceof HTMLInputElement) return;
    rouletteBets.forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    rouletteBet = { type: button.dataset.betType, value: button.dataset.betValue };
  }));
  const rouletteNumberInput = document.getElementById('rouletteNumber');
  rouletteNumberInput?.addEventListener('input', () => { rouletteNumberInput.value = rouletteNumberInput.value.replace(/[^0-9]/g, '').slice(0, 2); });
  rouletteNumberInput?.addEventListener('blur', () => {
    const value = Number(rouletteNumberInput.value);
    rouletteNumberInput.value = String(Number.isFinite(value) ? Math.max(0, Math.min(36, value)) : 0);
  });
  rouletteNumberInput?.addEventListener('focus', () => {
    const straight = rouletteBets.find(button => button.dataset.betType === 'straight');
    rouletteBets.forEach(item => item.classList.toggle('active', item === straight));
    rouletteBet = { type: 'straight', value: 'number' };
  });

  const rouletteButton = document.getElementById('rouletteSpinButton');
  const rouletteWheel = document.getElementById('rouletteWheel');
  const rouletteBallTrack = document.getElementById('rouletteBallTrack');
  const rouletteBall = document.getElementById('rouletteBall');
  const rouletteWheelStage = document.getElementById('rouletteWheelStage');
  const rouletteNumberResult = document.getElementById('rouletteResultNumber');
  const rouletteResult = document.getElementById('rouletteResult');
  const rouletteNumberRing = document.getElementById('rouletteNumberRing');
  let rouletteRotation = 0;
  let rouletteBallRotation = 0;

  if (rouletteNumberRing) {
    rouletteOrder.forEach((number, index) => {
      const label = document.createElement('span');
      label.textContent = String(number);
      label.className = number === 0 ? 'green' : (rouletteRed.has(number) ? 'red' : 'black');
      const angle = index * (360 / rouletteOrder.length);
      label.style.transform = `rotate(${angle}deg) translateY(-137px) rotate(${-angle}deg)`;
      rouletteNumberRing.appendChild(label);
    });
  }

  async function animateRouletteTo(wheelIndex) {
    if (!rouletteWheel || !rouletteBallTrack) return;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const duration = reduced ? 250 : 4300;
    const step = 360 / rouletteOrder.length;
    const currentMod = ((rouletteRotation % 360) + 360) % 360;
    const targetMod = ((360 - (Number(wheelIndex) * step)) % 360 + 360) % 360;
    const wheelEnd = rouletteRotation + (reduced ? 0 : 5 * 360) + ((targetMod - currentMod + 360) % 360);
    const ballEnd = rouletteBallRotation - (reduced ? 0 : 8 * 360);
    rouletteWheelStage?.classList.add('spinning');

    const wheelAnimation = rouletteWheel.animate?.(
      [{ transform: `rotate(${rouletteRotation}deg)` }, { transform: `rotate(${wheelEnd}deg)` }],
      { duration, easing: 'cubic-bezier(.08,.68,.08,1)', fill: 'forwards' }
    );
    const ballAnimation = rouletteBallTrack.animate?.([
      { transform: `rotate(${rouletteBallRotation}deg)`, offset: 0 },
      { transform: `rotate(${ballEnd + 100}deg)`, offset: .72 },
      { transform: `rotate(${ballEnd - 18}deg)`, offset: .91 },
      { transform: `rotate(${ballEnd}deg)`, offset: 1 },
    ], { duration: duration + (reduced ? 0 : 180), easing: 'cubic-bezier(.12,.55,.16,1)', fill: 'forwards' });
    const ballBounceAnimation = rouletteBall?.animate?.([
      { transform: 'translateX(-50%) translateY(-3px) scale(1)', offset: 0 },
      { transform: 'translateX(-50%) translateY(9px) scale(.96)', offset: .72 },
      { transform: 'translateX(-50%) translateY(2px) scale(1.04)', offset: .88 },
      { transform: 'translateX(-50%) translateY(6px) scale(1)', offset: 1 },
    ], { duration: duration + (reduced ? 0 : 180), easing: 'cubic-bezier(.2,.7,.18,1)', fill: 'forwards' });

    const waits = [];
    if (wheelAnimation?.finished) waits.push(wheelAnimation.finished.catch(() => {}));
    if (ballAnimation?.finished) waits.push(ballAnimation.finished.catch(() => {}));
    if (ballBounceAnimation?.finished) waits.push(ballBounceAnimation.finished.catch(() => {}));
    if (waits.length) await Promise.all(waits);
    else await sleep(duration);
    rouletteRotation = wheelEnd;
    rouletteBallRotation = ballEnd;
    rouletteWheel.style.transform = `rotate(${wheelEnd}deg)`;
    rouletteBallTrack.style.transform = `rotate(${ballEnd}deg)`;
    rouletteWheelStage?.classList.remove('spinning');
  }

  rouletteButton?.addEventListener('click', async () => {
    try {
      const wager = readWager('rouletteWager');
      const betValue = rouletteBet.type === 'straight' ? rouletteNumberInput?.value : rouletteBet.value;
      setBusy(rouletteButton, true, 'Spinning');
      rouletteBets.forEach(button => { button.disabled = true; });
      if (rouletteNumberInput) rouletteNumberInput.disabled = true;
      setWagerControlDisabled('rouletteWager', true);
      if (rouletteResult) rouletteResult.textContent = 'No more bets — ball in motion…';
      const data = await api('/api/casino/play/roulette', { method: 'POST', body: { wager, bet_type: rouletteBet.type, bet_value: betValue } });
      await animateRouletteTo(data.wheel_index);
      if (rouletteNumberResult) {
        rouletteNumberResult.textContent = data.number;
        rouletteNumberResult.style.color = data.color === 'red' ? '#ff7070' : data.color === 'green' ? '#68e5a2' : '#fff';
      }
      if (rouletteResult) rouletteResult.textContent = `${data.number} ${data.color.toUpperCase()} — ${data.won ? `PAID ${formatCredits(data.payout)}` : 'NO WIN'}`;
      setBalance(data.balance);
      showToast(data.won ? `Roulette paid ${formatCredits(data.payout)} credits.` : `${data.number} ${data.color}. Better luck next spin.`, data.won ? 'success' : '');
      loadState(false);
    } catch (error) {
      rouletteWheelStage?.classList.remove('spinning');
      handleGameError(error);
    } finally {
      setBusy(rouletteButton, false);
      rouletteBets.forEach(button => { button.disabled = false; });
      if (rouletteNumberInput) rouletteNumberInput.disabled = false;
      setWagerControlDisabled('rouletteWager', false);
    }
  });

  // Cards shared renderer
  function cardElement(card, extraClass = '') {
    const div = document.createElement('div');
    if (card?.hidden) {
      div.className = `playing-card card-back ${extraClass}`.trim();
      return div;
    }
    div.className = `playing-card ${card?.red ? 'red' : ''} ${extraClass}`.trim();
    const top = document.createElement('span');
    top.className = 'card-rank';
    top.textContent = card?.rank || '';
    const suit = document.createElement('span');
    suit.className = 'card-suit';
    suit.textContent = card?.suit || '';
    const bottom = document.createElement('span');
    bottom.className = 'card-rank bottom';
    bottom.textContent = card?.rank || '';
    div.append(top, suit, bottom);
    return div;
  }

  // Blackjack
  let blackjackGameId = '';
  let blackjackBusy = false;
  let blackjackLastGame = null;
  const blackjackDeal = document.getElementById('blackjackDeal');
  const blackjackHit = document.getElementById('blackjackHit');
  const blackjackStand = document.getElementById('blackjackStand');
  const blackjackDouble = document.getElementById('blackjackDouble');
  const blackjackMessage = document.getElementById('blackjackMessage');

  function renderBlackjack(game) {
    blackjackLastGame = game || null;
    const active = game?.status === 'active';
    blackjackGameId = active ? (game?.game_id || '') : '';
    const dealerCards = document.getElementById('dealerCards');
    const playerCards = document.getElementById('playerCards');
    if (dealerCards) {
      dealerCards.textContent = '';
      (game?.dealer || []).forEach(card => dealerCards.appendChild(cardElement(card)));
    }
    if (playerCards) {
      playerCards.textContent = '';
      (game?.player || []).forEach(card => playerCards.appendChild(cardElement(card)));
    }
    const dealerValue = document.getElementById('dealerValue');
    const playerValue = document.getElementById('playerValue');
    if (dealerValue) dealerValue.textContent = game?.dealer_value ?? 0;
    if (playerValue) playerValue.textContent = game?.player_value ?? 0;
    if (blackjackMessage) blackjackMessage.textContent = active ? 'Your move' : (game?.result || 'Place your wager and deal');
    if (blackjackHit) blackjackHit.disabled = !active;
    if (blackjackStand) blackjackStand.disabled = !active;
    if (blackjackDouble) blackjackDouble.disabled = !active || !game?.can_double;
    if (blackjackDeal) blackjackDeal.disabled = active;
    setWagerControlDisabled('blackjackWager', active);
  }

  blackjackDeal?.addEventListener('click', async () => {
    if (blackjackBusy) return;
    blackjackBusy = true;
    try {
      const wager = readWager('blackjackWager');
      setBusy(blackjackDeal, true, 'Dealing');
      const data = await api('/api/casino/blackjack/start', { method: 'POST', body: { wager } });
      setBalance(data.balance);
      renderBlackjack(data.game);
      if (data.restored) showToast('Restored your active Blackjack hand.', 'success');
      else if (data.game.status !== 'active') showToast(data.game.result + (data.game.payout ? ` • +${formatCredits(data.game.payout)}` : ''), data.game.payout ? 'success' : '');
      loadState(false);
    } catch (error) {
      handleGameError(error);
    } finally {
      blackjackBusy = false;
      if (blackjackDeal?.dataset.originalHtml) blackjackDeal.innerHTML = blackjackDeal.dataset.originalHtml;
      if (blackjackDeal && !blackjackGameId) blackjackDeal.disabled = false;
    }
  });

  async function blackjackAction(action, button) {
    if (!blackjackGameId || blackjackBusy) return;
    blackjackBusy = true;
    try {
      [blackjackHit, blackjackStand, blackjackDouble].forEach(control => { if (control) control.disabled = true; });
      setBusy(button, true, action === 'double' ? 'Doubling' : 'Playing');
      const data = await api('/api/casino/blackjack/action', { method: 'POST', body: { game_id: blackjackGameId, action } });
      setBalance(data.balance);
      renderBlackjack(data.game);
      if (data.game.status !== 'active') {
        showToast(data.game.result + (data.game.payout ? ` • +${formatCredits(data.game.payout)}` : ''), data.game.payout ? 'success' : '');
        blackjackGameId = '';
        if (blackjackDeal) blackjackDeal.disabled = false;
        loadState(false);
      }
    } catch (error) {
      handleGameError(error);
    } finally {
      blackjackBusy = false;
      if (button?.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
      if (blackjackGameId && blackjackLastGame) renderBlackjack(blackjackLastGame);
    }
  }
  blackjackHit?.addEventListener('click', () => blackjackAction('hit', blackjackHit));
  blackjackStand?.addEventListener('click', () => blackjackAction('stand', blackjackStand));
  blackjackDouble?.addEventListener('click', () => blackjackAction('double', blackjackDouble));

  // Mines
  let minesGameId = '';
  let minesActive = false;
  let minesRevealBusy = false;
  const minesBoard = document.getElementById('minesBoard');
  const minesTiles = [...document.querySelectorAll('[data-mine-tile]')];
  const minesStart = document.getElementById('minesStart');
  const minesCashout = document.getElementById('minesCashout');
  const minesMultiplier = document.getElementById('minesMultiplier');
  const minesPotential = document.getElementById('minesPotential');

  function resetMinesBoard() {
    minesTiles.forEach(tile => {
      tile.classList.remove('safe', 'mine');
      tile.disabled = !minesActive;
      const icon = tile.querySelector('i');
      if (icon) icon.className = 'fas fa-gem';
    });
  }

  minesStart?.addEventListener('click', async () => {
    try {
      const wager = readWager('minesWager');
      const mines = Number(document.getElementById('minesCount')?.value || 5);
      setBusy(minesStart, true, 'Starting');
      const data = await api('/api/casino/mines/start', { method: 'POST', body: { wager, mines } });
      setBalance(data.balance);
      restoreMinesRound({
        game_id: data.game_id,
        wager: data.wager || wager,
        mine_count: data.mine_count || mines,
        revealed: data.revealed || [],
        multiplier: data.multiplier || 1,
        potential_payout: data.potential_payout || data.wager || wager
      });
      showToast(data.restored ? 'Restored your active Mines round.' : 'Mines round started. Pick a tile.', 'success');
    } catch (error) {
      handleGameError(error);
    } finally {
      setBusy(minesStart, false);
      if (minesActive && minesStart) minesStart.disabled = true;
    }
  });

  minesTiles.forEach(tile => tile.addEventListener('click', async () => {
    if (!minesActive || !minesGameId || tile.disabled || minesRevealBusy) return;
    minesRevealBusy = true;
    minesTiles.forEach(item => { item.disabled = true; });
    try {
      const data = await api('/api/casino/mines/reveal', { method: 'POST', body: { game_id: minesGameId, tile: Number(tile.dataset.mineTile) } });
      if (data.hit_mine) {
        minesActive = false;
        tile.classList.add('mine');
        const icon = tile.querySelector('i');
        if (icon) icon.className = 'fas fa-bomb';
        (data.mines || []).forEach(index => {
          const mineTile = minesTiles[index];
          mineTile?.classList.add('mine');
          const mineIcon = mineTile?.querySelector('i');
          if (mineIcon) mineIcon.className = 'fas fa-bomb';
        });
        setBalance(data.balance);
        finishMinesControls();
        showToast('Mine hit — round lost.', 'error');
        loadState(false);
      } else {
        tile.classList.add('safe');
        const icon = tile.querySelector('i');
        if (icon) icon.className = 'fas fa-gem';
        if (minesMultiplier) minesMultiplier.textContent = `${Number(data.multiplier).toFixed(2)}×`;
        if (minesPotential) minesPotential.textContent = formatCredits(data.potential_payout);
        if (data.completed) {
          (data.mines || []).forEach(index => {
            const mineTile = minesTiles[index];
            mineTile?.classList.add('mine');
            const mineIcon = mineTile?.querySelector('i');
            if (mineIcon) mineIcon.className = 'fas fa-bomb';
          });
          setBalance(data.balance);
          finishMinesControls();
          showToast(`Board cleared — paid ${formatCredits(data.payout)} credits at ${Number(data.multiplier).toFixed(2)}×.`, 'success');
          loadState(false);
        } else if (minesCashout) {
          minesCashout.disabled = false;
        }
      }
    } catch (error) {
      handleGameError(error);
    } finally {
      minesRevealBusy = false;
      if (minesActive) {
        minesTiles.forEach(item => { item.disabled = item.classList.contains('safe') || item.classList.contains('mine'); });
      } else {
        minesTiles.forEach(item => { item.disabled = true; });
      }
    }
  }));

  function finishMinesControls() {
    minesGameId = '';
    minesActive = false;
    if (minesStart) minesStart.disabled = false;
    if (minesCashout) minesCashout.disabled = true;
    const countInput = document.getElementById('minesCount');
    setWagerControlDisabled('minesWager', false);
    if (countInput) countInput.disabled = false;
  }

  function restoreMinesRound(game) {
    if (!game?.game_id) return;
    minesGameId = game.game_id;
    minesActive = true;
    const wagerInput = document.getElementById('minesWager');
    const countInput = document.getElementById('minesCount');
    if (wagerInput) writeWager(wagerInput, game.wager || minWager);
    if (countInput) countInput.value = String(game.mine_count || 5);
    resetMinesBoard();
    (game.revealed || []).forEach(index => {
      const tile = minesTiles[index];
      tile?.classList.add('safe');
      if (tile) tile.disabled = true;
      const icon = tile?.querySelector('i');
      if (icon) icon.className = 'fas fa-gem';
    });
    if (minesMultiplier) minesMultiplier.textContent = `${Number(game.multiplier || 1).toFixed(2)}×`;
    if (minesPotential) minesPotential.textContent = formatCredits(game.potential_payout || game.wager || 0);
    if (minesStart) minesStart.disabled = true;
    if (minesCashout) minesCashout.disabled = !(game.revealed || []).length;
    setWagerControlDisabled('minesWager', true);
    if (countInput) countInput.disabled = true;
  }

  minesCashout?.addEventListener('click', async () => {
    if (!minesGameId) return;
    try {
      setBusy(minesCashout, true, 'Cashing out');
      const data = await api('/api/casino/mines/cashout', { method: 'POST', body: { game_id: minesGameId } });
      (data.mines || []).forEach(index => {
        const tile = minesTiles[index];
        if (tile && !tile.classList.contains('safe')) {
          tile.classList.add('mine');
          const icon = tile.querySelector('i');
          if (icon) icon.className = 'fas fa-bomb';
        }
      });
      minesTiles.forEach(tile => { tile.disabled = true; });
      setBalance(data.balance);
      showToast(`Cashed out ${formatCredits(data.payout)} credits at ${Number(data.multiplier).toFixed(2)}×`, 'success');
      finishMinesControls();
      loadState(false);
    } catch (error) {
      handleGameError(error);
      if (minesCashout) minesCashout.disabled = false;
    } finally {
      if (minesCashout?.dataset.originalHtml) minesCashout.innerHTML = minesCashout.dataset.originalHtml;
    }
  });
  resetMinesBoard();

  // Plinko canvas
  const plinkoCanvas = document.getElementById('plinkoCanvas');
  const plinkoCtx = plinkoCanvas?.getContext('2d');
  const plinkoButton = document.getElementById('plinkoDrop');
  const plinkoResult = document.getElementById('plinkoResult');
  const plinkoMultipliers = [14, 6, 2.4, 1.55, 1.08, .72, .55, .72, 1.08, 1.55, 2.4, 6, 14];
  let plinkoBall = null;
  let plinkoAnimation = 0;

  function drawPlinko() {
    if (!plinkoCtx || !plinkoCanvas) return;
    const width = plinkoCanvas.width;
    const height = plinkoCanvas.height;
    plinkoCtx.clearRect(0, 0, width, height);
    const grad = plinkoCtx.createRadialGradient(width / 2, height * .35, 10, width / 2, height * .35, width * .48);
    grad.addColorStop(0, 'rgba(168,85,247,.16)');
    grad.addColorStop(1, 'rgba(6,3,9,0)');
    plinkoCtx.fillStyle = grad;
    plinkoCtx.fillRect(0, 0, width, height);
    const rows = 12;
    const top = 70;
    const rowGap = (height - 145) / rows;
    const gap = width / 15;
    for (let row = 0; row < rows; row += 1) {
      const count = row + 2;
      const startX = width / 2 - ((count - 1) * gap) / 2;
      for (let col = 0; col < count; col += 1) {
        const x = startX + col * gap;
        const y = top + row * rowGap;
        plinkoCtx.beginPath();
        plinkoCtx.arc(x, y, 5.5, 0, Math.PI * 2);
        plinkoCtx.fillStyle = 'rgba(224,197,247,.9)';
        plinkoCtx.shadowColor = 'rgba(194,122,255,.7)';
        plinkoCtx.shadowBlur = 10;
        plinkoCtx.fill();
      }
    }
    plinkoCtx.shadowBlur = 0;
    if (plinkoBall) {
      plinkoCtx.beginPath();
      plinkoCtx.arc(plinkoBall.x, plinkoBall.y, 11, 0, Math.PI * 2);
      plinkoCtx.fillStyle = '#f5d47f';
      plinkoCtx.shadowColor = '#f5d47f';
      plinkoCtx.shadowBlur = 20;
      plinkoCtx.fill();
      plinkoCtx.shadowBlur = 0;
    }
  }

  function resizePlinko() {
    if (!plinkoCanvas) return;
    const rect = plinkoCanvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(320, Math.round(rect.width * ratio));
    const height = Math.round(width * .76);
    if (plinkoCanvas.width !== width || plinkoCanvas.height !== height) {
      plinkoCanvas.width = width;
      plinkoCanvas.height = height;
    }
    drawPlinko();
  }

  function animatePlinko(path) {
    return new Promise(resolve => {
      if (!plinkoCanvas) return resolve();
      cancelAnimationFrame(plinkoAnimation);
      const width = plinkoCanvas.width;
      const height = plinkoCanvas.height;
      const top = 38;
      const rowGap = (height - 120) / 12;
      const gap = width / 15;
      const points = [{ x: width / 2, y: top }];
      let x = width / 2;
      path.forEach((step, row) => {
        x += step ? gap / 2 : -gap / 2;
        points.push({ x, y: 70 + row * rowGap });
      });
      points.push({ x, y: height - 32 });
      const start = performance.now();
      const duration = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 220 : 1750;
      function frame(now) {
        const progress = Math.min(1, (now - start) / duration);
        const scaled = progress * (points.length - 1);
        const index = Math.min(points.length - 2, Math.floor(scaled));
        const local = scaled - index;
        const a = points[index];
        const b = points[index + 1];
        const bounce = Math.sin(local * Math.PI) * 7;
        plinkoBall = { x: a.x + (b.x - a.x) * local, y: a.y + (b.y - a.y) * local - bounce };
        drawPlinko();
        if (progress < 1) plinkoAnimation = requestAnimationFrame(frame);
        else {
          plinkoBall = null;
          drawPlinko();
          resolve();
        }
      }
      plinkoAnimation = requestAnimationFrame(frame);
    });
  }

  plinkoButton?.addEventListener('click', async () => {
    try {
      const wager = readWager('plinkoWager');
      setBusy(plinkoButton, true, 'Dropping');
      const data = await api('/api/casino/play/plinko', { method: 'POST', body: { wager } });
      await animatePlinko(data.path || []);
      setBalance(data.balance);
      if (plinkoResult) plinkoResult.textContent = `${Number(data.multiplier).toFixed(2)}×`;
      showToast(`${Number(data.multiplier).toFixed(2)}× slot paid ${formatCredits(data.payout)} credits`, data.payout >= wager ? 'success' : '');
      loadState(false);
    } catch (error) {
      handleGameError(error);
    } finally {
      setBusy(plinkoButton, false);
    }
  });
  window.addEventListener('resize', () => window.requestAnimationFrame(resizePlinko), { passive: true });
  resizePlinko();

  // Higher / Lower
  let hlToken = '';
  let hlReady = false;
  const hlDeal = document.getElementById('hlDeal');
  const hlHigher = document.getElementById('hlHigher');
  const hlLower = document.getElementById('hlLower');
  const hlStatus = document.getElementById('hlStatus');
  const hlCurrentCard = document.getElementById('hlCurrentCard');
  const hlNextCard = document.getElementById('hlNextCard');

  function replaceCard(container, card, extraClass = 'giant-card') {
    if (!container) return;
    const node = cardElement(card, extraClass);
    container.replaceWith(node);
    node.id = container.id;
    return node;
  }

  function renderCardInto(container, card) {
    if (!container) return;
    const node = cardElement(card, 'giant-card');
    container.className = node.className;
    container.textContent = '';
    [...node.childNodes].forEach(child => container.appendChild(child));
  }

  function restoreHigherLowerRound(round) {
    if (!round?.token || !round?.card) return;
    hlToken = round.token;
    hlReady = true;
    renderCardInto(hlCurrentCard, round.card);
    if (hlNextCard) {
      hlNextCard.className = 'playing-card giant-card mystery-card';
      hlNextCard.innerHTML = '<i class="fas fa-question"></i>';
    }
    const higher = Number(round.multipliers?.higher || 0);
    const lower = Number(round.multipliers?.lower || 0);
    const higherMultiplier = document.getElementById('hlHigherMultiplier');
    const lowerMultiplier = document.getElementById('hlLowerMultiplier');
    if (higherMultiplier) higherMultiplier.textContent = higher ? `${higher.toFixed(2)}×` : 'N/A';
    if (lowerMultiplier) lowerMultiplier.textContent = lower ? `${lower.toFixed(2)}×` : 'N/A';
    if (hlHigher) hlHigher.disabled = !higher;
    if (hlLower) hlLower.disabled = !lower;
    if (hlDeal) hlDeal.disabled = true;
    if (hlStatus) hlStatus.textContent = 'Will the next card be higher or lower? Ties lose.';
  }

  function resetHigherLowerRound() {
    hlToken = '';
    hlReady = false;
    if (hlHigher) hlHigher.disabled = true;
    if (hlLower) hlLower.disabled = true;
    if (hlDeal) hlDeal.disabled = false;
    const higherMultiplier = document.getElementById('hlHigherMultiplier');
    const lowerMultiplier = document.getElementById('hlLowerMultiplier');
    if (higherMultiplier) higherMultiplier.textContent = '—';
    if (lowerMultiplier) lowerMultiplier.textContent = '—';
    if (hlStatus) hlStatus.textContent = 'Deal a card to begin';
  }

  hlDeal?.addEventListener('click', async () => {
    try {
      setBusy(hlDeal, true, 'Dealing');
      const data = await api('/api/casino/higher-lower/start', { method: 'POST', body: {} });
      restoreHigherLowerRound(data);
      if (data.restored) showToast('Restored your active Higher / Lower round.', 'success');
    } catch (error) {
      handleGameError(error);
    } finally {
      setBusy(hlDeal, false);
      if (hlDeal) hlDeal.disabled = hlReady;
    }
  });

  async function makeHlGuess(direction, button) {
    if (!hlReady) return;
    try {
      const wager = readWager('hlWager');
      hlReady = false;
      if (hlHigher) hlHigher.disabled = true;
      if (hlLower) hlLower.disabled = true;
      setBusy(button, true, 'Revealing');
      const data = await api('/api/casino/higher-lower/guess', { method: 'POST', body: { wager, direction, token: hlToken } });
      renderCardInto(hlNextCard, data.next);
      hlToken = '';
      setBalance(data.balance);
      if (hlDeal) hlDeal.disabled = false;
      if (hlStatus) hlStatus.textContent = data.won ? `Correct — ${Number(data.multiplier).toFixed(2)}×` : (data.next.value === data.current.value ? 'Tie — ties lose' : 'Wrong guess');
      showToast(data.won ? `Correct — paid ${formatCredits(data.payout)} credits.` : 'Higher/Lower round lost.', data.won ? 'success' : '');
      loadState(false);
    } catch (error) {
      hlReady = true;
      handleGameError(error);
    } finally {
      if (button?.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
    }
  }
  hlHigher?.addEventListener('click', () => makeHlGuess('higher', hlHigher));
  hlLower?.addEventListener('click', () => makeHlGuess('lower', hlLower));

  // History and state
  const historyEl = document.getElementById('casinoHistory');
  const gameIcons = { slots: 'fa-dice', slots_classic: 'fa-dice', slots_neon: 'fa-dice', slots_vault: 'fa-gem', roulette: 'fa-circle-dot', blackjack: 'fa-club', blackjack_double: 'fa-club', mines: 'fa-bomb', plinko: 'fa-circle-nodes', higher_lower: 'fa-arrow-up-arrow-down' };

  function renderHistory(entries) {
    if (!historyEl) return;
    historyEl.textContent = '';
    if (!entries?.length) {
      const empty = document.createElement('div');
      empty.className = 'casino-history-empty';
      empty.textContent = 'Play a game and your results will appear here.';
      historyEl.appendChild(empty);
      return;
    }
    entries.forEach(entry => {
      const row = document.createElement('div');
      row.className = 'casino-history-row';
      const icon = document.createElement('div');
      icon.className = 'history-icon';
      icon.innerHTML = `<i class="fas ${gameIcons[entry.game] || 'fa-dice'}"></i>`;
      const details = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = String(entry.game || 'game').replaceAll('_', ' ');
      const meta = document.createElement('span');
      meta.textContent = `${formatTime(entry.created_at)} • wager ${formatCredits(entry.wager)}`;
      details.append(title, meta);
      const profit = document.createElement('span');
      const value = Number(entry.profit || 0);
      profit.className = `history-profit ${value >= 0 ? 'win' : 'loss'}`;
      profit.textContent = `${value >= 0 ? '+' : '−'}${formatCredits(Math.abs(value))}`;
      row.append(icon, details, profit);
      historyEl.appendChild(row);
    });
  }

  function syncActiveRounds(rounds = {}) {
    if (rounds.blackjack?.game_id) {
      if (!blackjackBusy) renderBlackjack(rounds.blackjack);
    } else if (blackjackGameId && !blackjackBusy) {
      renderBlackjack(null);
    }

    if (rounds.mines?.game_id) {
      if (!minesRevealBusy) restoreMinesRound(rounds.mines);
    } else if (minesActive && !minesRevealBusy) {
      finishMinesControls();
      resetMinesBoard();
      if (minesMultiplier) minesMultiplier.textContent = '1.00×';
      if (minesPotential) minesPotential.textContent = '0';
    }

    if (rounds.higher_lower?.token) {
      restoreHigherLowerRound(rounds.higher_lower);
    } else if (hlReady || hlToken) {
      resetHigherLowerRound();
    }
  }

  async function loadState(showErrors = true) {
    const startedAtMutation = mutationVersion;
    try {
      const data = await api('/api/casino/state');
      // Do not let a GET that started before a wager/action overwrite newer UI.
      if (startedAtMutation !== mutationVersion) return;
      setBalance(data.balance);
      renderHistory(data.history || []);
      syncActiveRounds(data.active_rounds || {});
      const online = document.getElementById('casinoOnlineCount');
      if (online) online.textContent = formatCredits(data.online || 1);
      if (dailyButton) {
        dailyButton.disabled = !data.daily_available;
        if (!data.daily_available) dailyButton.innerHTML = '<i class="fas fa-check"></i><span>Claimed today</span>';
      }
    } catch (error) {
      if (showErrors) showToast(error.message, 'error');
    }
  }
  document.getElementById('refreshHistory')?.addEventListener('click', () => loadState());

  // Lounge chat
  const chatMessages = document.getElementById('casinoChatMessages');
  const chatForm = document.getElementById('casinoChatForm');
  const chatInput = document.getElementById('casinoChatInput');
  const chatCount = document.getElementById('chatCharacterCount');
  let chatSignature = '';
  let chatPosting = false;

  function renderChat(messages) {
    if (!chatMessages) return;
    const signature = (messages || []).map(message => message.message_id).join('|');
    if (signature === chatSignature) return;
    const nearBottom = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight < 90;
    chatSignature = signature;
    chatMessages.textContent = '';
    if (!messages?.length) {
      const empty = document.createElement('div');
      empty.className = 'chat-loading';
      empty.textContent = 'No lounge messages yet. Say hello.';
      chatMessages.appendChild(empty);
      return;
    }
    messages.forEach(message => {
      const article = document.createElement('article');
      article.className = 'casino-chat-message';
      article.dataset.messageId = message.message_id;
      const img = document.createElement('img');
      img.src = message.avatar_url || '/static/default.jpg';
      img.alt = '';
      const body = document.createElement('div');
      body.className = 'chat-message-body';
      const meta = document.createElement('div');
      meta.className = 'chat-message-meta';
      const name = document.createElement('strong');
      name.textContent = message.username || 'Player';
      const time = document.createElement('time');
      time.textContent = formatTime(message.created_at);
      meta.append(name, time);
      const text = document.createElement('p');
      text.textContent = message.body || '';
      body.append(meta, text);
      const actions = document.createElement('div');
      actions.className = 'chat-message-actions';
      if (!message.own) {
        const report = document.createElement('button');
        report.type = 'button';
        report.title = 'Report message';
        report.innerHTML = '<i class="fas fa-flag"></i>';
        report.addEventListener('click', () => reportMessage(message.message_id, article));
        actions.appendChild(report);
      }
      if (message.own || message.can_moderate) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.title = 'Delete message';
        remove.innerHTML = '<i class="fas fa-trash"></i>';
        remove.addEventListener('click', () => deleteMessage(message.message_id, article));
        actions.appendChild(remove);
      }
      article.append(img, body, actions);
      chatMessages.appendChild(article);
    });
    if (nearBottom || !chatMessages.dataset.loaded) chatMessages.scrollTop = chatMessages.scrollHeight;
    chatMessages.dataset.loaded = '1';
  }

  async function loadChat() {
    try {
      const data = await api('/api/casino/chat');
      renderChat(data.messages || []);
    } catch (error) {
      if (!chatMessages?.dataset.loaded) {
        chatMessages.textContent = '';
        const empty = document.createElement('div');
        empty.className = 'chat-loading';
        empty.textContent = error.message;
        chatMessages?.appendChild(empty);
      }
    }
  }

  async function reportMessage(id, article) {
    try {
      await api(`/api/casino/chat/${encodeURIComponent(id)}/report`, { method: 'POST', body: {} });
      article?.remove();
      showToast('Message reported to moderators.', 'success');
    } catch (error) {
      showToast(error.message, 'error');
    }
  }

  async function deleteMessage(id, article) {
    try {
      await api(`/api/casino/chat/${encodeURIComponent(id)}/delete`, { method: 'POST', body: {} });
      article?.remove();
      chatSignature = '';
    } catch (error) {
      showToast(error.message, 'error');
    }
  }

  chatInput?.addEventListener('input', () => {
    if (chatCount) chatCount.textContent = `${chatInput.value.length}/300`;
  });
  chatInput?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      chatForm?.requestSubmit();
    }
  });
  chatForm?.addEventListener('submit', async event => {
    event.preventDefault();
    if (chatPosting) return;
    const message = chatInput?.value.trim() || '';
    if (message.length < 2) return showToast('Type a message first.', 'error');
    const submit = chatForm.querySelector('button[type="submit"]');
    chatPosting = true;
    if (submit) submit.disabled = true;
    try {
      await api('/api/casino/chat', { method: 'POST', body: { message } });
      if (chatInput) chatInput.value = '';
      if (chatCount) chatCount.textContent = '0/300';
      chatSignature = '';
      await loadChat();
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      chatPosting = false;
      if (submit) submit.disabled = false;
    }
  });

  loadState();
  loadChat();
  window.setInterval(() => {
    if (!document.hidden) loadChat();
  }, 6000);
  window.setInterval(() => {
    if (!document.hidden) loadState(false);
  }, 30000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { loadState(false); loadChat(); }
  });
  window.addEventListener('online', () => {
    showToast('Back online — casino state refreshed.', 'success');
    loadState(false); loadChat();
  });
  window.addEventListener('offline', () => showToast('You are offline. Your active server-side game is safe.', 'error'));
})();
