/* BTC Bot dashboard. Plain JS, no dependencies, works offline. */
(() => {
  const TOKEN = document.querySelector('meta[name=token]').content;
  const $ = (id) => document.getElementById(id);
  const fmt = {
    money: (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }),
    pct: (v, d = 2) => (v == null || isNaN(v)) ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`,
    qty: (v) => (v == null) ? '—' : Number(v).toFixed(6),
    time: (ms) => ms ? new Date(ms).toISOString().replace('T', ' ').slice(0, 16) + 'Z' : '—',
    ts: (iso) => iso ? iso.replace('T', ' ').slice(11, 19) : '',
  };
  const cls = (v) => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
  let status = {};
  let lastEventId = 0;
  let equityVersion = 0;

  async function api(path, body) {
    const opts = { headers: { 'X-Token': TOKEN } };
    if (body !== undefined) { opts.method = 'POST'; opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
    return data;
  }

  // ----- theme & motion -----
  const THEMES = [
    { id: 'obsidian', name: 'Obsidian' }, { id: 'midnight', name: 'Midnight' },
    { id: 'terminal', name: 'Terminal' }, { id: 'daylight', name: 'Daylight' },
  ];
  const store = {
    get(k, d) { try { const v = localStorage.getItem('btcbot.' + k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('btcbot.' + k, v); } catch (e) { /* private window */ } },
  };
  function applyTheme(id) {
    document.documentElement.dataset.theme = id; store.set('theme', id);
    document.querySelectorAll('#theme-picker button').forEach((b) => b.classList.toggle('active', b.dataset.theme === id));
    const sel = $('theme-select'); if (sel) sel.value = id;
    // The chart module is defined later in this file; redraw only once it exists.
    if (window.__btcbot && window.__btcbot.chart.data) renderChart();
  }
  function applyMotion() {
    const on = store.get('anim', '1') === '1';
    document.body.classList.toggle('no-anim', !on);
    document.documentElement.style.setProperty('--speed', store.get('speed', '1'));
    const t = $('anim-toggle'); if (t) t.checked = on;
    const s = $('anim-speed'); if (s) s.value = store.get('speed', '1');
  }
  $('theme-picker').innerHTML = THEMES.map((t) => `<button data-theme="${t.id}" title="${t.name}">${t.name}</button>`).join('');
  $('theme-picker').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => applyTheme(b.dataset.theme)));
  $('theme-select').innerHTML = THEMES.map((t) => `<option value="${t.id}">${t.name}</option>`).join('');
  $('theme-select').addEventListener('change', (e) => applyTheme(e.target.value));
  $('anim-toggle').addEventListener('change', (e) => { store.set('anim', e.target.checked ? '1' : '0'); applyMotion(); });
  $('anim-speed').addEventListener('change', (e) => { store.set('speed', e.target.value); applyMotion(); });
  applyTheme(store.get('theme', 'obsidian')); applyMotion();

  // ripple on every button click
  document.addEventListener('pointerdown', (e) => {
    const btn = e.target.closest('button'); if (!btn || btn.disabled || document.body.classList.contains('no-anim')) return;
    const r = btn.getBoundingClientRect(); const size = Math.max(r.width, r.height);
    const span = document.createElement('span'); span.className = 'ripple';
    span.style.width = span.style.height = `${size}px`;
    span.style.left = `${e.clientX - r.left - size / 2}px`; span.style.top = `${e.clientY - r.top - size / 2}px`;
    btn.appendChild(span); setTimeout(() => span.remove(), 700);
  });

  // ----- alerts & toasts -----
  function showAlert(text, kind = 'error', ms = 6000) {
    const el = $('alert'); el.textContent = text; el.className = `alert ${kind}`; el.classList.remove('hidden');
    clearTimeout(showAlert.t); if (ms) showAlert.t = setTimeout(() => el.classList.add('hidden'), ms);
  }
  function toast(title, body = '', kind = '') {
    const el = document.createElement('div'); el.className = `toast ${kind}`;
    el.innerHTML = `<div class="t">${title}</div>${body ? `<div class="muted small">${body}</div>` : ''}`;
    $('toasts').appendChild(el);
    setTimeout(() => { el.classList.add('leaving'); setTimeout(() => el.remove(), 400); }, 6000);
  }
  function flash(el, up) { if (!el || document.body.classList.contains('no-anim')) return; el.classList.remove('flash-up', 'flash-down'); void el.offsetWidth; el.classList.add(up ? 'flash-up' : 'flash-down'); }

  // ----- tabs -----
  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${b.dataset.tab}`));
    store.set('tab', b.dataset.tab);
    if (b.dataset.tab === 'trades') loadTrades();
    if (b.dataset.tab === 'settings') loadSettings();
    if (b.dataset.tab === 'backtest') loadSamples();
    if (b.dataset.tab === 'chart') loadChart();
    if (b.dataset.tab === 'modes') renderModes();
    if (b.dataset.tab === 'trade') renderTrade();
    if (b.dataset.tab === 'ai') renderAi();
    if (b.dataset.tab === 'research') loadResearch();
    if (b.dataset.tab === 'analytics') loadAnalytics();
  }));

  // ----- equity chart -----
  function themeColor(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  function drawLine(canvas, points, opts = {}) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 600, h = canvas.getAttribute('height') | 0 || 240;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
    const gridC = themeColor('--border', '#273241'), textC = themeColor('--text-3', '#8b98a8');
    if (!points || points.length < 2) { ctx.fillStyle = textC; ctx.font = '13px system-ui'; ctx.fillText(opts.empty || 'no data', 12, 24); return; }
    const pad = { l: 66, r: 12, t: 12, b: 24 };
    const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
    const xmin = Math.min(...xs), xmax = Math.max(...xs); let ymin = Math.min(...ys), ymax = Math.max(...ys);
    if (ymin === ymax) { ymin -= 1; ymax += 1; }
    const yr = (ymax - ymin) * 0.08; ymin -= yr; ymax += yr;
    const X = (x) => pad.l + (x - xmin) / (xmax - xmin || 1) * (w - pad.l - pad.r);
    const Y = (y) => pad.t + (1 - (y - ymin) / (ymax - ymin)) * (h - pad.t - pad.b);
    ctx.strokeStyle = gridC; ctx.fillStyle = textC; ctx.font = '11px system-ui'; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = ymin + (ymax - ymin) * i / 4, py = Y(y);
      ctx.beginPath(); ctx.moveTo(pad.l, py); ctx.lineTo(w - pad.r, py); ctx.stroke();
      ctx.fillText(fmt.money(y, 0), 6, py + 4);
    }
    ctx.fillText(fmt.time(xmin).slice(0, 10), pad.l, h - 6);
    const lbl = fmt.time(xmax).slice(0, 10); ctx.fillText(lbl, w - pad.r - ctx.measureText(lbl).width, h - 6);
    const base = opts.baseline;
    if (base != null) { ctx.strokeStyle = themeColor('--warn', '#f5b342'); ctx.setLineDash([4, 4]); ctx.globalAlpha = .6; ctx.beginPath(); ctx.moveTo(pad.l, Y(base)); ctx.lineTo(w - pad.r, Y(base)); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1; }
    const last = ys[ys.length - 1], first = ys[0];
    const col = last >= first ? themeColor('--up', '#2ecc71') : themeColor('--down', '#ff5c5c');
    const grad = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
    grad.addColorStop(0, col + '44'); grad.addColorStop(1, col + '00');
    ctx.beginPath(); points.forEach((p, i) => { const px = X(p.x), py = Y(p.y); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
    ctx.lineTo(X(xs[xs.length - 1]), h - pad.b); ctx.lineTo(X(xs[0]), h - pad.b); ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
    ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.beginPath();
    points.forEach((p, i) => { const px = X(p.x), py = Y(p.y); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
    ctx.stroke();
  }
  async function loadEquity() {
    try {
      const { equity } = await api('/api/equity?limit=1500');
      const pts = equity.map((e) => ({ x: e.ts, y: e.equity }));
      drawLine($('equity-chart'), pts, { baseline: status.config && status.config.initial_cash, empty: 'No equity data yet' });
      $('equity-note').textContent = pts.length ? `${pts.length} points · last ${fmt.time(pts[pts.length - 1].x)}` : 'No equity data yet. Start the bot or run a demo.';
    } catch (e) { /* ignore */ }
  }

  // ----- status -----
  let lastPrice = null;
  function renderStatus(s) {
    status = s;
    const c = s.config || {};
    $('cfg-summary').textContent = c.symbol ? `${c.exchange} · ${c.symbol} · ${c.timeframe} · ${c.strategy}` : (s.config_error || '—');
    $('state-dot').className = `dot ${s.state}`;
    $('state-text').textContent = s.state === 'running' && s.replay ? 'running (demo replay)' : s.state + (s.error ? ` · ${s.error}` : '');
    const mode = s.state === 'stopped' || s.state === 'error' ? (s.live_possible ? 'live armed' : 'paper') : (s.replay ? 'replay' : (s.mode || '—'));
    const badge = $('mode-badge'); badge.textContent = mode.toUpperCase(); badge.className = `badge ${mode.split(' ')[0]}`;
    const tm = s.trading_mode;
    const tmb = $('trading-mode-badge');
    if (tm) { tmb.textContent = c.mode_matches === false ? `${tm} (custom)` : tm; tmb.className = `badge mode-${tm}`; tmb.classList.remove('hidden'); } else tmb.classList.add('hidden');
    const aiOn = s.ai && s.ai.enabled;
    $('ai-badge').classList.toggle('hidden', !aiOn);
    if (aiOn) $('ai-badge').textContent = s.ai.key_set ? 'AI' : 'AI (no key)';
    const running = s.state === 'running' || s.state === 'starting';
    $('btn-start').disabled = running || s.state === 'stopping'; $('btn-demo').disabled = running || s.state === 'stopping';
    $('btn-stop').disabled = !running;
    $('btn-start').textContent = s.live_possible ? 'Start LIVE' : 'Start (paper)';
    $('btn-start').className = s.live_possible ? 'danger' : 'primary';
    const kill = $('btn-kill'); kill.textContent = `Kill switch: ${s.kill_switch ? 'ON' : 'off'}`; kill.classList.toggle('on', !!s.kill_switch);

    const lc = s.last_cycle || {};
    const price = s.price;
    if (price != null && lastPrice != null && price !== lastPrice) flash($('t-price'), price > lastPrice);
    if (price != null) lastPrice = price;
    $('t-price').textContent = fmt.money(price);
    $('t-price-sub').textContent = c.symbol || '';
    $('t-equity').textContent = fmt.money(s.equity);
    const init = c.initial_cash; const ret = (s.equity != null && init) ? (s.equity / init - 1) * 100 : null;
    $('t-equity-sub').textContent = ret == null ? '' : `${fmt.pct(ret)} vs start`; $('t-equity-sub').className = `sub ${cls(ret)}`;
    $('t-cash').textContent = fmt.money(s.cash);
    const p = s.position;
    if (p) {
      $('t-position').textContent = `${fmt.qty(p.qty)}`;
      $('t-position-sub').textContent = `@ ${fmt.money(p.entry_price)} · stop ${fmt.money(p.stop_loss)} · tp ${fmt.money(p.take_profit)}`;
      const u = price ? (price - p.entry_price) * p.qty : null;
      $('t-upnl').textContent = u == null ? '—' : `${fmt.money(u)}`; $('t-upnl').className = `value ${cls(u)}`;
    } else { $('t-position').textContent = 'flat'; $('t-position-sub').textContent = ''; $('t-upnl').textContent = '—'; $('t-upnl').className = 'value'; }
    const r = s.risk || {};
    $('t-daily').textContent = r.daily_drawdown_pct == null ? '—' : fmt.pct(r.daily_drawdown_pct); $('t-daily').className = `value ${cls(r.daily_drawdown_pct)}`;
    $('t-daily-sub').textContent = r.trades_today != null ? `${r.trades_today} trade(s) today` : '';
    $('t-cycles').textContent = s.cycles || 0;
    $('t-candle').textContent = lc.candle_time ? `last candle ${lc.candle_time.replace('T', ' ').slice(0, 16)}` : (lc.now ? `tick ${fmt.ts(lc.now)}` : '');
    const sig = s.signal;
    if (sig) {
      $('sig-action').textContent = sig.action; $('sig-action').className = `badge big ${sig.action}`;
      $('sig-conf').textContent = sig.action === 'HOLD' ? '' : `confidence ${(sig.confidence * 100).toFixed(0)}%`;
      const meter = $('conf-meter'); meter.className = `meter ${sig.action === 'BUY' ? 'up' : sig.action === 'SELL' ? 'down' : ''}`;
      meter.firstElementChild.style.width = `${Math.round((sig.confidence || 0) * 100)}%`;
      $('sig-reason').textContent = sig.reason;
      const d = s.decision || {};
      $('sig-decision').textContent = d.intent ? `→ order intent: ${d.intent.side} ${fmt.qty(d.intent.qty)} (${d.intent.kind})` : (d.rejected && d.rejected !== 'hold') ? `→ not traded (${d.rejected}): ${d.reason}` : '';
    }
    $('r-mode').textContent = tm ? (c.mode_matches === false ? `${tm} (edited)` : tm) : '—';
    $('r-halted').textContent = r.halted ? `yes · ${r.halt_reason || ''}` : 'no';
    $('r-cooldown').textContent = r.cooldown ? 'yes' : 'no';
    $('r-losses').textContent = r.consecutive_losses ?? '—'; $('r-trades').textContent = r.trades_today ?? '—';
    $('r-kill').textContent = s.kill_switch ? 'ON — all orders blocked' : 'off';
    const alerts = (s.alerts || []).slice().reverse();
    $('alerts').innerHTML = alerts.length ? alerts.map((a) => `<li><span class="t">${fmt.ts(a.ts)}</span><span>${describe(a)}</span></li>`).join('') : '<li class="muted">Nothing yet.</li>';
    $('version').textContent = `BTC Bot ${s.version}`; $('log-file').textContent = s.paths ? s.paths.log : '';
    if (s.secrets) $('env-path').textContent = s.secrets.env_file;
    renderUpdate(s.update);
    renderLicense(s.license);
    const svc = s.services || {};
    const tg = $('tg-state');
    if (tg) tg.innerHTML = svc.telegram_commands
      ? `<span class="pos">Phone control is active.</span>${svc.telegram_error ? ` <span class="neg">Last error: ${svc.telegram_error}</span>` : ''}`
      : 'Phone control is off: add a token and chat id below, then restart the app.';
    if (document.querySelector('#tab-trade').classList.contains('active')) renderTrade();
    if (document.querySelector('#tab-ai').classList.contains('active')) renderAi();
    const tw = $('temp-warning');
    if (s.paths && s.paths.temporary) { tw.textContent = `You are running the app from a temporary folder (${s.paths.root}). Settings, keys and trade history saved here can disappear. Extract the zip to a permanent folder such as C:\\BTCBot and start it from there.`; tw.classList.remove('hidden'); } else tw.classList.add('hidden');
  }
  function describe(a) {
    switch (a.event) {
      case 'position_opened': return `Opened ${fmt.qty(a.qty)} @ ${fmt.money(a.entry_price)} (stop ${fmt.money(a.stop_loss)}, tp ${fmt.money(a.take_profit)})`;
      case 'trade_closed': return `<span class="${cls(a.pnl)}">Closed ${a.exit_reason}: P/L ${fmt.money(a.pnl)} (${fmt.pct(a.pnl_pct)})</span>`;
      case 'manual_order': return `Manual ${String(a.side || '').toUpperCase()} ${fmt.qty(a.qty)} @ ${fmt.money(a.price)}`;
      case 'levels_changed': return `Levels changed: stop ${fmt.money(a.stop_loss)}, tp ${fmt.money(a.take_profit)}`;
      case 'mode_changed': return `<span class="pos">${a.detail}</span>`;
      case 'risk_event': return `<span class="neg">Risk: ${a.detail}</span>`;
      case 'cycle_error': return `<span class="neg">Error: ${a.error}</span>`;
      case 'update_available': case 'update_installed': return `<span class="pos">${a.detail}</span>`;
      case 'reconcile': return `Reconciled with exchange · cash ${fmt.money(a.cash)}${(a.warnings || []).length ? ' · ' + a.warnings.join('; ') : ''}`;
      case 'replay_finished': return `Demo replay finished after ${a.cycles} cycles`;
      case 'shutdown': return `Stopped (${a.reason})`;
      default: return `${a.event}: ${a.msg || ''}`;
    }
  }
  async function poll() {
    try {
      const s = await api('/api/status');
      bootStep('connect');
      bootStep('config', !s.config_error, s.config_error ? `Settings problem: ${s.config_error}` : null);
      bootStep('state');
      bootStep('ready');
      renderStatus(s);
      setTimeout(bootFinish, 500);
      const v = (s.cycles || 0) + ':' + (s.state);
      if (v !== equityVersion) { equityVersion = v; loadEquity(); loadWhy(); }
      ensureStream();
    } catch (e) {
      $('state-text').textContent = `disconnected: ${e.message}`; $('state-dot').className = 'dot error';
      bootStep('connect', false, `Cannot reach the engine: ${e.message}`);
    }
    try {
      const { events } = await api(`/api/events?since=${lastEventId}&limit=300`);
      if (events.length) { appendLog(events); lastEventId = events[events.length - 1].id; }
    } catch (e) { /* ignore */ }
  }

  // ----- log & debug channels -----
  const logBuf = [];
  function appendLog(events) {
    logBuf.push(...events); if (logBuf.length > 3000) logBuf.splice(0, logBuf.length - 3000);
    renderChannel('log', events); renderChannel('debug', events);
    events.forEach((e) => {
      if (e.event === 'trade_closed') toast(`Trade closed: ${fmt.money(e.pnl)}`, `${e.exit_reason} · ${fmt.pct(e.pnl_pct)}`, e.pnl >= 0 ? 'up' : 'down');
      else if (e.event === 'position_opened') toast('Position opened', `${fmt.qty(e.qty)} @ ${fmt.money(e.entry_price)}`, 'up');
      else if (e.event === 'risk_event') toast('Risk limit hit', e.detail, 'warn');
      else if (e.event === 'cycle_error') toast('Error', e.error, 'down');
      else if (e.event === 'mode_changed') toast('Trading mode changed', e.detail, 'warn');
    });
  }
  function fmtEvent(e) {
    const skip = new Set(['id', 'ts', 'level', 'logger', 'msg', 'event', 'config', 'channel']);
    const rest = Object.entries(e).filter(([k]) => !skip.has(k)).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' ');
    return `<span class="ts">${fmt.ts(e.ts)}</span> <b>${e.event || e.msg}</b> ${rest.length > 600 ? rest.slice(0, 600) + '…' : rest}`;
  }
  function renderChannel(channel, events, reset = false) {
    const box = channel === 'log' ? $('log') : $('debug-log');
    if (reset) box.innerHTML = '';
    const filter = channel === 'debug' ? ($('debug-filter').value || '').toLowerCase() : '';
    events.forEach((e) => {
      if (channel === 'log' && e.channel !== 'log') return;
      if (filter && !JSON.stringify(e).toLowerCase().includes(filter)) return;
      const div = document.createElement('div'); div.className = e.level; div.innerHTML = fmtEvent(e); box.appendChild(div);
    });
    while (box.children.length > 1500) box.removeChild(box.firstChild);
    const follow = channel === 'log' ? $('log-follow').checked : $('debug-follow').checked;
    if (follow) box.scrollTop = box.scrollHeight;
  }
  $('log-clear').addEventListener('click', () => { $('log').innerHTML = ''; });
  $('debug-clear').addEventListener('click', () => { $('debug-log').innerHTML = ''; });
  $('debug-filter').addEventListener('input', () => renderChannel('debug', logBuf, true));
  $('debug-copy').addEventListener('click', async () => {
    const text = logBuf.slice(-200).map((e) => JSON.stringify(e)).join('\n');
    try { await navigator.clipboard.writeText(text); toast('Copied', 'Last 200 debug lines are on the clipboard.'); }
    catch (e) { showAlert('Could not copy; select the text manually.', 'warn'); }
  });

  // ----- trades -----
  async function loadTrades() {
    try {
      const { trades } = await api('/api/trades?limit=500');
      $('trades-count').textContent = trades.length ? `${trades.length} trades · total P/L ${fmt.money(trades.reduce((a, t) => a + t.pnl, 0))}` : '';
      const tb = $('trades-table').querySelector('tbody');
      tb.innerHTML = trades.length ? trades.slice().reverse().map((t) => `<tr><td>${fmt.time(t.entry_ts)}</td><td>${fmt.time(t.exit_ts)}</td><td class="num">${fmt.qty(t.qty)}</td><td class="num">${fmt.money(t.entry_price)}</td><td class="num">${fmt.money(t.exit_price)}</td><td class="num">${fmt.money(t.fees)}</td><td class="num ${cls(t.pnl)}">${fmt.money(t.pnl)}</td><td class="num ${cls(t.pnl)}">${fmt.pct(t.pnl_pct)}</td><td>${t.exit_reason}</td></tr>`).join('') : '<tr><td colspan="9" class="muted">No trades yet.</td></tr>';
    } catch (e) { showAlert(e.message); }
  }

  // ----- manual trading -----
  const PRESETS = [0.1, 0.25, 0.5, 1];
  $('tr-presets').innerHTML = PRESETS.map((p) => `<button type="button" class="ghost" data-frac="${p}">${p * 100}% of cash</button>`).join('');
  $('tr-presets').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
    const cash = status.cash || 0; $('tr-amount').value = (cash * Number(b.dataset.frac)).toFixed(2); previewTrade();
  }));
  $('tr-stop-presets').innerHTML = [1, 2, 3, 5].map((p) => `<button type="button" class="ghost" data-stop="${p}">stop −${p}%</button>`).join('')
    + [2, 4, 6].map((p) => `<button type="button" class="ghost" data-tp="${p}">target +${p}%</button>`).join('');
  $('tr-stop-presets').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
    const price = status.price; if (!price) return;
    if (b.dataset.stop) $('tr-stop').value = (price * (1 - Number(b.dataset.stop) / 100)).toFixed(2);
    if (b.dataset.tp) $('tr-tp').value = (price * (1 + Number(b.dataset.tp) / 100)).toFixed(2);
    previewTrade();
  }));
  ['tr-amount', 'tr-stop', 'tr-tp'].forEach((id) => $(id).addEventListener('input', previewTrade));
  function previewTrade() {
    const price = status.price, amount = Number($('tr-amount').value || 0);
    const stop = Number($('tr-stop').value || 0), tp = Number($('tr-tp').value || 0);
    if (!price || !amount) { $('tr-preview').textContent = 'Enter an amount to see what the order will do.'; return; }
    const qty = amount / price;
    const lines = [`Buy about <b>${fmt.qty(qty)} ${(status.config.symbol || 'BTC/USDT').split('/')[0]}</b> for ${fmt.money(amount)} at ~${fmt.money(price)}.`];
    if (stop > 0) lines.push(`If the stop at ${fmt.money(stop)} is hit you lose about <b class="neg">${fmt.money(qty * (price - stop))}</b>.`);
    if (tp > 0) lines.push(`If the target at ${fmt.money(tp)} is hit you gain about <b class="pos">${fmt.money(qty * (tp - price))}</b>.`);
    if (status.equity) lines.push(`That is ${(amount / status.equity * 100).toFixed(1)}% of your equity.`);
    $('tr-preview').innerHTML = lines.join('<br>');
  }
  function renderTrade() {
    const s = status, running = s.state === 'running';
    $('tr-price').textContent = fmt.money(s.price);
    $('tr-cash').textContent = fmt.money(s.cash);
    $('tr-pos').textContent = s.position ? fmt.qty(s.position.qty) : 'flat';
    const blocked = $('trade-blocked');
    if (!running) { blocked.textContent = 'Start the bot first. Manual orders use its exchange connection, so the fill, fees and history are recorded the same way.'; blocked.classList.remove('hidden'); }
    else if (s.kill_switch) { blocked.textContent = 'The kill switch is on: no order will be placed, by you or by the bot. Turn it off in the top bar.'; blocked.classList.remove('hidden'); }
    else blocked.classList.add('hidden');
    const canTrade = running && !s.kill_switch;
    $('tr-buy').disabled = !canTrade || !!s.position; $('tr-sell').disabled = !canTrade || !s.position;
    $('tr-buy').textContent = s.position ? 'Buy (already in a position)' : 'Buy';
    const p = s.position;
    $('pos-qty').textContent = p ? fmt.qty(p.qty) : '—';
    $('pos-entry').textContent = p ? fmt.money(p.entry_price) : '—';
    $('pos-stop').textContent = p ? fmt.money(p.stop_loss) : '—';
    $('pos-tp').textContent = p ? fmt.money(p.take_profit) : '—';
    const u = p && s.price ? (s.price - p.entry_price) * p.qty : null;
    $('pos-upnl').innerHTML = u == null ? '—' : `<span class="${cls(u)}">${fmt.money(u)}</span>`;
    $('lv-save').disabled = !p || !running;
    if (p && !$('lv-stop').matches(':focus')) { $('lv-stop').placeholder = fmt.money(p.stop_loss); $('lv-tp').placeholder = fmt.money(p.take_profit); }
    previewTrade();
  }
  function confirmDialog(title, body) {
    return new Promise((resolve) => {
      $('co-title').textContent = title; $('co-body').innerHTML = body;
      const dlg = $('confirm-order');
      const done = (v) => { dlg.close(); $('co-go').onclick = null; $('co-cancel').onclick = null; resolve(v); };
      $('co-go').onclick = () => done(true); $('co-cancel').onclick = () => done(false);
      dlg.showModal();
    });
  }
  $('tr-buy').addEventListener('click', async () => {
    const amount = Number($('tr-amount').value || 0);
    if (!amount) { showAlert('Enter how much you want to spend.', 'warn'); return; }
    const live = status.mode === 'live';
    const ok = await confirmDialog(live ? 'Place a REAL buy order?' : 'Place a paper buy order?', $('tr-preview').innerHTML +
      (live ? '<br><br><strong class="danger-text">This is live trading with real money.</strong>' : ''));
    if (!ok) return;
    try {
      const out = await api('/api/order', { side: 'buy', quote_amount: amount, stop_loss: $('tr-stop').value || null, take_profit: $('tr-tp').value || null });
      toast('Bought', `${fmt.qty(out.order.filled)} @ ${fmt.money(out.order.avg_price)}`, 'up');
      $('tr-amount').value = '';
      poll();
    } catch (e) { showAlert(e.message, 'error', 12000); }
  });
  $('tr-sell').addEventListener('click', async () => {
    const p = status.position; if (!p) return;
    const u = status.price ? (status.price - p.entry_price) * p.qty : 0;
    const ok = await confirmDialog('Sell the whole position?',
      `Sell <b>${fmt.qty(p.qty)}</b> at about ${fmt.money(status.price)}.<br>That realises a profit/loss of about <b class="${cls(u)}">${fmt.money(u)}</b> before fees.`);
    if (!ok) return;
    try {
      const out = await api('/api/order', { side: 'sell' });
      toast('Sold', `${fmt.qty(out.order.filled)} @ ${fmt.money(out.order.avg_price)}`, 'down');
      poll();
    } catch (e) { showAlert(e.message, 'error', 12000); }
  });
  $('lv-save').addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      await api('/api/levels', { stop_loss: $('lv-stop').value || null, take_profit: $('lv-tp').value || null });
      $('lv-stop').value = ''; $('lv-tp').value = '';
      toast('Levels updated', 'The bot will use the new stop and target.');
      poll();
    } catch (err) { showAlert(err.message, 'error', 10000); }
  });

  // ----- AI -----
  function renderAi() {
    const ai = status.ai || {};
    const orb = $('ai-orb');
    $('ai-state').textContent = !ai.enabled ? 'off' : (ai.key_set ? 'active' : 'no API key');
    $('ai-state').className = `value ${ai.enabled && ai.key_set ? 'pos' : ai.enabled ? 'neg' : ''}`;
    $('ai-model').textContent = ai.model || '—';
    $('ai-calls').textContent = ai.calls || 0;
    $('ai-failures').textContent = ai.failures ? `${ai.failures} failed` : '';
    const last = ai.last_call || {};
    $('ai-latency').textContent = last.seconds != null ? `${last.seconds}s` : '—';
    $('ai-tokens').textContent = last.usage ? `${last.usage.input || 0} in / ${last.usage.output || 0} out tokens` : (last.error ? 'last call failed' : '');
    orb.classList.toggle('thinking', status.state === 'running' && ai.enabled && ai.key_set);
    const sig = status.signal;
    if (last.error) { $('ai-reason').textContent = `Last call failed: ${last.error}`; }
    else if (last.decision) {
      const d = last.decision;
      $('ai-reason').innerHTML = `<span class="badge ${d.action}">${d.action}</span> <span class="muted">confidence ${(Number(d.confidence || 0) * 100).toFixed(0)}%</span><br>${d.reason || ''}`;
      $('ai-detail').textContent = `stop ${fmt.money(d.stop_loss)} · target ${fmt.money(d.take_profit)} · technical strategy said ${(last.technical || {}).action || '—'}`;
    } else if (sig && sig.reason && sig.reason.startsWith('AI')) { $('ai-reason').textContent = sig.reason; }
    else if (!ai.enabled) { $('ai-reason').textContent = 'The AI strategy is off. Turn it on with an Anthropic API key to let Claude decide.'; }
    $('ai-key-state').textContent = ai.key_set ? 'saved' : 'not set';
    if (!$('ai-enable').matches(':focus')) $('ai-enable').checked = !!ai.enabled;
    if (ai.model && !$('ai-model-select').matches(':focus')) $('ai-model-select').value = ai.model;
  }
  $('ai-save').addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      if ($('ai-key').value.trim()) { await api('/api/secrets', { ANTHROPIC_API_KEY: $('ai-key').value.trim() }); $('ai-key').value = ''; }
      const { config } = await api('/api/config');
      const cfg = JSON.parse(JSON.stringify(config));
      if ($('ai-enable').checked) {
        cfg.strategy.name = 'ai';
        const params = cfg.strategy.params && cfg.strategy.params.model !== undefined ? cfg.strategy.params : {};
        cfg.strategy.params = {
          ...params, model: $('ai-model-select').value,
          only_on_technical_setup: $('ai-only-setup').checked, fallback_to_technical: $('ai-fallback').checked,
          mode: status.trading_mode || 'balanced',
        };
      } else if (cfg.strategy.name === 'ai') {
        cfg.strategy.name = 'ema_rsi'; cfg.strategy.params = {};
      }
      await api('/api/config', { config: cfg });
      toast('AI settings saved', 'They apply the next time you press Start.');
      poll();
    } catch (err) { showAlert(err.message, 'error', 12000); }
  });

  // ----- modes -----
  function renderModes() {
    const modes = status.trading_modes || [];
    const active = status.trading_mode;
    $('mode-cards').innerHTML = modes.map((m) => `
      <button class="mode-card ${m.id === active ? 'active' : ''}" data-mode="${m.id}">
        <span class="mc-title"><i class="dot-mode ${m.id}"></i>${m.label}${m.id === active ? ' ✓' : ''}</span>
        <span class="mc-sum">${m.summary}</span>
        <span class="mc-nums"><span>risk/trade <b>${m.risk.risk_per_trade_pct}%</b></span><span>max position <b>${m.risk.max_position_pct}%</b></span><span>daily stop <b>${m.risk.max_daily_loss_pct}%</b></span></span>
      </button>`).join('');
    $('mode-cards').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => applyMode(b.dataset.mode)));
    const note = status.config && status.config.mode_matches === false
      ? `Your settings no longer match "${active}" exactly because you edited them by hand. Clicking a mode will overwrite those values.`
      : 'Clicking a mode rewrites the risk limits and strategy parameters. It applies the next time you press Start.';
    $('mode-note').textContent = note;
    const rows = [
      ['Risk per trade', 'risk_per_trade_pct', '%'], ['Max position', 'max_position_pct', '% of equity'],
      ['Daily loss stop', 'max_daily_loss_pct', '%'], ['Losses before cooldown', 'max_consecutive_losses', ''],
      ['Cooldown', 'cooldown_minutes', ' min'], ['Min gap between entries', 'min_seconds_between_trades', ' s'],
      ['Min signal confidence', 'min_confidence', ''],
    ];
    const by = Object.fromEntries(modes.map((m) => [m.id, m.risk]));
    $('mode-table').querySelector('tbody').innerHTML = rows.map(([label, key, unit]) =>
      `<tr><td>${label}</td>${['safe', 'balanced', 'aggressive'].map((m) => `<td class="num">${by[m] ? by[m][key] + unit : '—'}</td>`).join('')}</tr>`).join('');
  }
  async function applyMode(mode) {
    const m = (status.trading_modes || []).find((x) => x.id === mode);
    const ok = await confirmDialog(`Switch to ${m ? m.label : mode} mode?`,
      `${m ? m.detail : ''}<br><br>This overwrites your risk limits and strategy parameters. It applies the next time you press Start.`);
    if (!ok) return;
    try { await api('/api/mode', { mode }); toast('Mode applied', m ? m.summary : mode, 'warn'); await poll(); renderModes(); loadSettings(); }
    catch (e) { showAlert(e.message); }
  }

  // ----- controls -----
  $('btn-start').addEventListener('click', async () => {
    if (status.live_possible) { $('live-exchange').textContent = `${status.config.exchange} (${status.config.symbol})`; $('live-typed').value = ''; $('live-go').disabled = true; $('confirm-live').showModal(); return; }
    try { await api('/api/start', {}); toast('Bot started', 'Paper mode: real market data, simulated fills.'); } catch (e) { showAlert(e.message); }
    poll();
  });
  $('live-typed').addEventListener('input', (e) => { $('live-go').disabled = e.target.value.trim() !== 'LIVE'; });
  $('live-cancel').addEventListener('click', () => $('confirm-live').close());
  $('live-go').addEventListener('click', async () => {
    $('confirm-live').close();
    try { await api('/api/start', { confirm_live: true }); showAlert('LIVE trading started. Real orders can now be placed.', 'error', 10000); } catch (e) { showAlert(e.message); }
    poll();
  });
  $('btn-demo').addEventListener('click', async () => {
    try {
      const { samples } = await api('/api/samples');
      if (!samples.length) throw new Error('No sample CSV files found in data/samples');
      const pick = samples.find((s) => s.includes('binance')) || samples[0];
      await api('/api/start', { replay: pick, replay_delay: 0.15 });
      toast('Demo running', `Replaying ${pick.split('/').pop()} in paper mode.`);
    } catch (e) { showAlert(e.message); }
    poll();
  });
  $('btn-stop').addEventListener('click', async () => { try { await api('/api/stop', {}); } catch (e) { showAlert(e.message); } poll(); });
  $('btn-kill').addEventListener('click', async () => {
    const on = !status.kill_switch;
    if (on && !(await confirmDialog('Turn the kill switch ON?', 'The bot will not place <b>any</b> order, including stop-loss exits, until you turn it off.'))) return;
    try { await api('/api/kill', { active: on }); toast(on ? 'Kill switch ON' : 'Kill switch off', on ? 'No orders will be placed.' : 'Trading can resume.', on ? 'down' : 'up'); } catch (e) { showAlert(e.message); }
    poll();
  });
  $('btn-quit').addEventListener('click', async () => {
    if (!(await confirmDialog('Close BTC Bot?', 'The bot is stopped cleanly and its state is saved.'))) return;
    try { await api('/api/quit', {}); } catch (e) { /* server going away */ }
    document.body.innerHTML = '<main><div class="card"><h3>BTC Bot is shutting down.</h3><p class="muted">You can close this window.</p></div></main>';
  });

  // ----- settings -----
  const SECTIONS = ['exchange', 'strategy', 'risk', 'exits', 'filters', 'paper', 'notify', 'update', 'logging', 'state'];
  const SECTION_TITLES = { exits: ['Exit rules', 'how an open position is managed'], filters: ['Entry filters', 'when not to enter, whatever the strategy says'], update: ['Updates', 'self-update from GitHub releases'], exchange: ['Exchange & market', 'where and what the bot trades'], strategy: ['Strategy', 'signal parameters'], risk: ['Risk limits', 'what the bot may never exceed'], paper: ['Paper trading', 'simulated account'], notify: ['Notifications', 'which events are sent'], logging: ['Logging', ''], state: ['Storage', ''] };
  const FIELD_SPEC = {
    'exchange.id': { label: 'Exchange', type: 'suggest', meta: 'exchanges' }, 'exchange.symbol': { label: 'Market (BASE/QUOTE)' },
    'exchange.timeframe': { label: 'Timeframe the bot trades on', type: 'select', meta: 'timeframes' },
    'exchange.live': { label: 'Live trading switch (config)', help: 'real orders also need LIVE_TRADING in the keys panel' },
    'exchange.sandbox': { label: 'Use the exchange testnet', help: 'where ccxt supports it' },
    'exchange.fee_rate': { label: 'Taker fee (fraction)' }, 'exchange.slippage_bps': { label: 'Simulated slippage (bps)' },
    'exchange.maker_fee_rate': { label: 'Maker fee (fraction)', help: 'charged when a posted order fills' },
    'exchange.order_type': { label: 'How orders are placed', type: 'select', options: ['market', 'limit'],
      help: 'market always fills and pays the taker fee · limit posts away from the price, pays the maker fee, and may miss the trade' },
    'exchange.limit_offset_bps': { label: 'Post this far from the price (bps)', help: 'below to buy, above to sell' },
    'exchange.limit_fallback_market': { label: 'Take the trade anyway if the limit order misses', help: 'crosses the spread and pays the taker fee' },
    'exchange.candle_history': { label: 'Candles kept for indicators' }, 'exchange.poll_interval_seconds': { label: 'Poll interval (s)' },
    'exchange.candle_close_grace_seconds': { label: 'Wait after candle close (s)' }, 'exchange.order_timeout_seconds': { label: 'Order timeout (s)' },
    'exchange.order_poll_seconds': { label: 'Order status poll (s)' }, 'exchange.max_retries': { label: 'Network retries' },
    'exchange.backoff_base_seconds': { label: 'Retry backoff base (s)' }, 'exchange.backoff_max_seconds': { label: 'Retry backoff max (s)' },
    'strategy.name': { label: 'Strategy', type: 'select', meta: 'strategies', help: 'ema_rsi/breakout/mean_reversion = rules, regime = switches between the last two, ai = Claude decides, buy_hold = benchmark' },
    'strategy.params.entry_period': { label: 'Breakout: buy above the high of N candles' },
    'strategy.params.exit_period': { label: 'Breakout: sell below the low of N candles' },
    'strategy.params.min_breakout_atr': { label: 'Breakout: minimum break size (× ATR)', help: '0 = accept any break' },
    'strategy.params.bb_period': { label: 'Bollinger period' }, 'strategy.params.bb_std': { label: 'Bollinger width (standard deviations)' },
    'strategy.params.exit_band': { label: 'Mean reversion: close at which band', type: 'select', options: ['middle', 'upper'] },
    'strategy.params.trend_slope_lookback': { label: 'Trend must have risen over (candles)' },
    'strategy.params.max_stretch_atr': { label: 'Skip dips deeper than (× ATR below the band)', help: '0 = off' },
    'strategy.params.adx_period': { label: 'ADX period (regime strength)' },
    'strategy.params.trend_above': { label: 'ADX above this = trend (use breakout)' },
    'strategy.params.range_below': { label: 'ADX below this = range (use mean reversion)' },
    'strategy.params.stop_loss_pct': { label: 'Benchmark stop loss (%)', help: '0 = hold through everything' },
    'strategy.params.warmup': { label: 'Candles to wait before buying' },
    'strategy.params.model': { label: 'Claude model' }, 'strategy.params.effort': { label: 'Thinking effort', type: 'select', options: ['low', 'medium', 'high', 'xhigh', 'max'] },
    'strategy.params.mode': { label: 'AI risk posture', type: 'select', options: ['safe', 'balanced', 'aggressive'] },
    'strategy.params.max_stop_atr': { label: 'Widest stop the AI may ask for (× ATR)' },
    'strategy.params.only_on_technical_setup': { label: 'Ask the AI only on a technical setup' },
    'strategy.params.fallback_to_technical': { label: 'Fall back to the technical strategy on failure' },
    'strategy.params.ema_fast': { label: 'EMA fast period' }, 'strategy.params.ema_slow': { label: 'EMA slow period' }, 'strategy.params.rsi_period': { label: 'RSI period' },
    'strategy.params.rsi_buy_min': { label: 'RSI minimum to buy' }, 'strategy.params.rsi_buy_max': { label: 'RSI maximum to buy' }, 'strategy.params.rsi_midline': { label: 'RSI midline (confirmation)' },
    'strategy.params.atr_period': { label: 'ATR period' }, 'strategy.params.atr_stop_mult': { label: 'Stop loss (× ATR)' }, 'strategy.params.atr_tp_mult': { label: 'Take profit (× ATR)' },
    'strategy.params.trend_lookback': { label: 'Trend lookback (candles)' }, 'strategy.params.confidence_base': { label: 'Confidence base' },
    'strategy.params.confidence_per_confirmation': { label: 'Confidence per confirmation' }, 'strategy.params.warmup_factor': { label: 'Warmup factor' },
    'risk.risk_per_trade_pct': { label: 'Risk per trade (% of equity)' }, 'risk.max_position_pct': { label: 'Max position (% of equity)' },
    'risk.allow_entry_without_stop': { label: 'Allow entries with no stop loss', help: 'sized by the position cap; used by the buy_hold benchmark' }, 'risk.max_daily_loss_pct': { label: 'Max daily loss (%)' },
    'risk.max_consecutive_losses': { label: 'Losses in a row before cooldown' }, 'risk.cooldown_minutes': { label: 'Cooldown (minutes)' }, 'risk.min_seconds_between_trades': { label: 'Min seconds between entries' },
    'risk.min_confidence': { label: 'Minimum signal confidence' }, 'risk.min_order_notional': { label: 'Minimum order size (quote)' }, 'risk.kill_switch_file': { label: 'Kill switch file' },
    'paper.initial_cash': { label: 'Starting cash' }, 'paper.initial_base': { label: 'Starting coins' },
    'notify.on_fill': { label: 'Notify on fills' }, 'notify.on_halt': { label: 'Notify on halts and cooldowns' }, 'notify.on_error': { label: 'Notify on errors' }, 'notify.timeout_seconds': { label: 'Webhook timeout (s)' },
    'logging.level': { label: 'Log level', type: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] }, 'logging.format': { label: 'Log format', type: 'select', options: ['json', 'text'] },
    'state.db_path': { label: 'Database file' },
    'exits.trailing_atr_mult': { label: 'Trailing stop (× ATR)', help: 'follows the highest price; 0 = off' },
    'exits.breakeven_after_atr': { label: 'Move stop to breakeven after (× ATR)', help: '0 = off' },
    'exits.partial_take_fraction': { label: 'Partial take profit: fraction to sell', help: '0 = off, 0.5 = half' },
    'exits.partial_take_atr': { label: 'Partial take profit at (× ATR)' },
    'exits.time_stop_candles': { label: 'Close a trade going nowhere after (candles)', help: '0 = off' },
    'exits.time_stop_min_atr': { label: '...unless it is this far in profit (× ATR)' },
    'filters.htf_factor': { label: 'Confirm against a longer timeframe (×)', help: '24 = daily when trading 1h · 0 = off · needs that much more history' },
    'filters.htf_period': { label: 'Trend EMA on that timeframe' },
    'filters.htf_mode': { label: 'What the higher timeframe must show', type: 'select', options: ['rising', 'above', 'both'], help: 'rising trend line, price above it, or both' },
    'filters.htf_slope_lookback': { label: 'Rising measured over (blocks)' },
    'filters.min_atr_pct': { label: 'Skip when volatility is below (% of price)', help: '0 = off' },
    'filters.max_atr_pct': { label: 'Skip when volatility is above (% of price)', help: '0 = off' },
    'filters.atr_period': { label: 'Volatility ATR period' },
    'filters.hours_utc': { label: 'Only enter in these UTC hours', help: 'e.g. 6-22 or 0,1,2 · empty = all day' },
    'filters.skip_weekends': { label: 'No new entries at weekends', help: 'thin books move price without meaning it' },
    'strategy.params.trend_filter_period': { label: 'Trend filter EMA period', help: 'breakout: only buy above it · mean reversion: only buy while it rises · 0 = off' },
    'notify.telegram_commands': { label: 'Obey Telegram commands from your chat' },
    'notify.watchdog_minutes': { label: 'Alert if no cycle for (minutes)', help: '0 = off' },
    'notify.daily_report_hour_utc': { label: 'Daily report hour (UTC)', help: '-1 = off' },
    'update.enabled': { label: 'Check GitHub for new builds' }, 'update.auto_install': { label: 'Install updates automatically while the bot is stopped' },
    'update.check_interval_minutes': { label: 'Check interval (minutes)' }, 'update.repo': { label: 'GitHub repository (owner/name)' },
  };
  let cfgMeta = { strategies: [], timeframes: [], exchanges: [] };
  let cfgData = null;
  function renderCfg(cfg) {
    cfgData = cfg; const form = $('cfg-form'); form.innerHTML = '';
    SECTIONS.forEach((sec) => {
      const fs = document.createElement('fieldset'); const lg = document.createElement('legend');
      const [title, sub] = SECTION_TITLES[sec] || [sec, '']; lg.innerHTML = `${title}${sub ? `<span class="sub">${sub}</span>` : ''}`; fs.appendChild(lg);
      const obj = cfg[sec] || {};
      const addField = (path, key, val) => {
        const parts = path.split('.');
        const spec = FIELD_SPEC[path] || (parts.length === 4 ? FIELD_SPEC[`${parts[0]}.${parts[1]}.${parts[3]}`] : null) || {};
        const help = spec.help || '';
        const name = FIELD_SPEC[path] ? (spec.label || key) : (spec.label ? `${key.split(':')[0]}: ${spec.label}` : key);
        const lab = document.createElement('label');
        let input;
        if (typeof val === 'boolean') {
          input = document.createElement('input'); input.type = 'checkbox'; input.checked = val; lab.classList.add('check');
          lab.appendChild(input); const txt = document.createElement('span');
          txt.innerHTML = `<span class="name">${name}</span>${help ? ` <span class="help">· ${help}</span>` : ''} <span class="help">(${path})</span>`; lab.appendChild(txt);
        } else {
          const options = spec.options || (spec.meta ? cfgMeta[spec.meta] : null);
          lab.innerHTML = `<span class="name">${name}</span>`;
          if (spec.type === 'select' && options && options.length) {
            input = document.createElement('select');
            options.forEach((o) => { const opt = document.createElement('option'); opt.value = o; opt.textContent = o; input.appendChild(opt); });
            if (!options.includes(String(val))) { const opt = document.createElement('option'); opt.value = val; opt.textContent = val; input.appendChild(opt); }
            input.value = String(val);
          } else {
            input = document.createElement('input'); input.type = typeof val === 'number' ? 'number' : 'text'; input.value = val;
            if (typeof val === 'number') input.step = 'any';
            if (spec.type === 'suggest' && options && options.length) {
              const dl = document.createElement('datalist'); dl.id = `dl-${path.replace(/\./g, '-')}`;
              options.forEach((o) => { const opt = document.createElement('option'); opt.value = o; dl.appendChild(opt); });
              lab.appendChild(dl); input.setAttribute('list', dl.id);
            }
          }
          lab.appendChild(input); const h = document.createElement('span'); h.className = 'help'; h.textContent = help ? `${help} · ${path}` : path; lab.appendChild(h);
        }
        input.dataset.path = path; input.dataset.type = typeof val; fs.appendChild(lab);
      };
      Object.entries(obj).forEach(([k, v]) => {
        if (k === 'params' && v && typeof v === 'object') Object.entries(v).forEach(([pk, pv]) => {
          if (pv !== null && typeof pv === 'object' && !Array.isArray(pv)) {
            // one level deeper: regime's trend_params / range_params, the AI's indicator_params
            Object.entries(pv).forEach(([sk, sv]) => {
              if (sv !== null && typeof sv === 'object') return;
              addField(`${sec}.params.${pk}.${sk}`, `${pk.replace(/_/g, ' ')}: ${sk}`, sv);
            });
            return;
          }
          addField(`${sec}.params.${pk}`, pk, pv);
        });
        else if (v !== null && typeof v === 'object') return; else addField(`${sec}.${k}`, k, v);
      });
      form.appendChild(fs);
    });
  }
  function collectCfg() {
    const out = JSON.parse(JSON.stringify(cfgData));
    $('cfg-form').querySelectorAll('input[data-path], select[data-path]').forEach((inp) => {
      const parts = inp.dataset.path.split('.'); let cur = out;
      for (let i = 0; i < parts.length - 1; i++) cur = cur[parts[i]];
      const k = parts[parts.length - 1];
      cur[k] = inp.dataset.type === 'boolean' ? inp.checked : inp.dataset.type === 'number' ? Number(inp.value) : inp.value;
    });
    return out;
  }
  async function loadSettings() {
    try {
      const r = await api('/api/config');
      cfgMeta = { strategies: r.strategies || [], timeframes: r.timeframes || [], exchanges: r.exchanges || [] };
      renderCfg(r.config);
    } catch (e) { showAlert(e.message); }
    try {
      const s = await api('/api/secrets');
      $('sec-key-state').textContent = s.api_key_set ? 'saved' : 'not set';
      $('sec-secret-state').textContent = s.api_secret_set ? 'saved' : 'not set';
      $('sec-live').checked = s.live_trading_env;
      $('notif-state').textContent = `telegram ${s.telegram_set ? 'on' : 'off'} · discord ${s.discord_set ? 'on' : 'off'}`;
    } catch (e) { /* ignore */ }
  }
  $('cfg-save').addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      const r = await api('/api/config', { config: collectCfg() });
      renderCfg(r.config);
      const gone = (r.dropped || []);
      toast('Settings saved', gone.length
        ? `Removed ${gone.length} setting(s) belonging to another strategy: ${gone.join(', ')}. They apply on the next Start.`
        : 'They apply on the next Start.');
      poll();
    }
    catch (err) { showAlert(err.message, 'error', 12000); }
  });
  $('cfg-reload').addEventListener('click', (e) => { e.preventDefault(); loadSettings(); });
  $('sec-save').addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      await api('/api/secrets', { EXCHANGE_API_KEY: $('sec-key').value, EXCHANGE_API_SECRET: $('sec-secret').value, EXCHANGE_API_PASSWORD: $('sec-password').value, LIVE_TRADING: $('sec-live').checked });
      $('sec-key').value = $('sec-secret').value = $('sec-password').value = '';
      toast('Keys saved', 'Stored in .env next to the app.'); loadSettings(); poll();
    } catch (err) { showAlert(err.message); }
  });
  $('sec-clear').addEventListener('click', async (e) => {
    e.preventDefault();
    if (!(await confirmDialog('Remove the saved API keys?', 'LIVE_TRADING is turned off as well.'))) return;
    try { await api('/api/secrets', { EXCHANGE_API_KEY: null, EXCHANGE_API_SECRET: null, EXCHANGE_API_PASSWORD: null, LIVE_TRADING: null }); toast('Keys removed'); loadSettings(); poll(); }
    catch (err) { showAlert(err.message); }
  });
  $('notif-save').addEventListener('click', async (e) => {
    e.preventDefault();
    try { await api('/api/secrets', { TELEGRAM_BOT_TOKEN: $('n-tg-token').value, TELEGRAM_CHAT_ID: $('n-tg-chat').value, DISCORD_WEBHOOK_URL: $('n-discord').value }); $('n-tg-token').value = $('n-discord').value = ''; toast('Notifications saved'); loadSettings(); }
    catch (err) { showAlert(err.message); }
  });

  // ----- backtest -----
  let sampleDetails = [];
  function applySampleRange() {
    const sel = $('bt-source').value;
    const d = sampleDetails.find((x) => x.path === sel);
    const from = $('bt-from'), to = $('bt-to');
    if (!sel) {  // downloading: any date, both required
      $('bt-range').textContent = 'downloads need a start and an end date';
      $('bt-hint').textContent = 'Candles are downloaded from the exchange in Settings and cached.';
      [from, to].forEach((i) => { i.removeAttribute('min'); i.removeAttribute('max'); });
      return;
    }
    if (!d || d.error) { $('bt-range').textContent = d && d.error ? `unreadable: ${d.error}` : ''; return; }
    $('bt-range').textContent = `covers ${d.from} → ${d.to} · ${d.candles.toLocaleString()} candles`;
    [from, to].forEach((i) => { i.min = d.from; i.max = d.to; });
    const outside = (v) => v && (v < d.from || v > d.to);
    if (outside(from.value) || outside(to.value)) {
      from.value = ''; to.value = '';
      $('bt-hint').textContent = `Dates outside ${d.from} → ${d.to} were cleared; the whole file will be used.`;
    } else {
      $('bt-hint').textContent = (from.value || to.value) ? '' : 'No dates: the whole file is used.';
    }
  }
  $('bt-whole').addEventListener('click', () => { $('bt-from').value = ''; $('bt-to').value = ''; applySampleRange(); });
  $('bt-source').addEventListener('change', applySampleRange);
  ['bt-from', 'bt-to'].forEach((id) => $(id).addEventListener('change', () => {
    const d = sampleDetails.find((x) => x.path === $('bt-source').value);
    const v = $(id).value;
    if (d && d.from && v && (v < d.from || v > d.to)) {
      $('bt-hint').textContent = `That date is outside the file (${d.from} → ${d.to}).`;
    } else $('bt-hint').textContent = '';
  }));
  async function loadSamples() {
    try {
      const r = await api('/api/samples'); sampleDetails = r.details || [];
      const sel = $('bt-source'); const cur = sel.value; sel.innerHTML = '';
      (r.samples || []).forEach((s) => {
        const d = sampleDetails.find((x) => x.path === s) || {};
        const o = document.createElement('option'); o.value = s;
        o.textContent = `${d.name || s}${d.from ? `  (${d.from} → ${d.to})` : ''}`;
        sel.appendChild(o);
      });
      const dl = document.createElement('option'); dl.value = ''; dl.textContent = 'Download from the configured exchange (needs internet + dates)'; sel.appendChild(dl);
      if (cur) sel.value = cur;
      applySampleRange();
    } catch (e) { showAlert(e.message); }
    const bt = await api('/api/backtest').catch(() => null); if (bt) renderBacktest(bt);
  }
  function renderBacktest(bt) {
    const st = $('bt-status');
    if (bt.state === 'running') { st.textContent = 'running…'; $('bt-run').disabled = true; setTimeout(async () => renderBacktest(await api('/api/backtest')), 1000); return; }
    $('bt-run').disabled = false;
    if (bt.state === 'error') { st.textContent = ''; showAlert(`Backtest failed: ${bt.error}`, 'error', 12000); return; }
    if (bt.state !== 'done') { st.textContent = ''; return; }
    st.textContent = `done · ${bt.candles} candles from ${bt.source}`;
    if ((bt.notes || []).length) showAlert(bt.notes.join(' · '), 'warn', 14000);
    const m = bt.metrics;
    const tiles = [['Total return', fmt.pct(m.total_return_pct), cls(m.total_return_pct)], ['Buy & hold', fmt.pct(m.buy_hold_return_pct), cls(m.buy_hold_return_pct)],
      ['Max drawdown', fmt.pct(m.max_drawdown_pct), 'neg'], ['Sharpe', m.sharpe, cls(m.sharpe)], ['Trades', m.trades, ''],
      ['Win rate', `${m.win_rate_pct}%`, ''], ['Per trade', fmt.money(m.expectancy), cls(m.expectancy)],
      ['Profit factor', m.profit_factor == null ? 'n/a' : m.profit_factor, cls((m.profit_factor || 1) - 1)], ['Exposure', `${m.exposure_pct}%`, '']];
    $('bt-tiles').innerHTML = tiles.map(([l, v, c]) => `<div class="tile"><div class="label">${l}</div><div class="value ${c}">${v}</div></div>`).join('');
    $('bt-result').classList.remove('hidden');
    drawLine($('bt-chart'), bt.equity.map((e) => ({ x: e.ts, y: e.equity })), { baseline: m.initial_equity });
    $('bt-report').textContent = bt.report;
  }
  $('bt-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = { csv: $('bt-source').value || null, from: $('bt-from').value || null, to: $('bt-to').value || null, cash: $('bt-cash').value || null };
    try { renderBacktest(await api('/api/backtest', body)); } catch (err) { showAlert(err.message); }
  });

  // ----- updates -----
  let updating = false;
  function renderUpdate(u) {
    if (!u) return;
    const banner = $('update-banner');
    if (u.available && !updating) {
      $('update-text').textContent = `New version ${u.latest.label} is available (you run ${u.current.label}).` + (u.can_install ? (status.state === 'running' ? ' The bot will be stopped, updated and restarted.' : '') : ' Running from source: git pull to update.');
      $('update-install').classList.toggle('hidden', !u.can_install); $('update-notes').href = u.latest.url || '#';
      banner.classList.remove('hidden');
    } else if (!updating) banner.classList.add('hidden');
    const bi = u.build_info || {};
    $('upd-current').textContent = `${u.current.label}${bi.commit ? ' · ' + String(bi.commit).slice(0, 7) : ''}`;
    $('upd-latest').textContent = u.latest ? `${u.latest.label}${u.latest.published_at ? ' · ' + u.latest.published_at.slice(0, 16).replace('T', ' ') : ''}` : (u.error ? 'unknown' : 'not checked yet');
    $('upd-checked').textContent = u.checked_at ? new Date(u.checked_at).toLocaleTimeString() : '—';
    const st = u.state === 'downloading' && u.progress.total ? `downloading ${Math.round(u.progress.done / u.progress.total * 100)}%` : u.state;
    $('upd-state').textContent = u.error ? `${st} · ${u.error}` : (u.available ? `${st} · update available` : `${st} · up to date`);
    $('upd-install').disabled = !(u.available && u.can_install) || updating;
  }
  async function installUpdate() {
    updating = true;
    $('update-install').classList.add('hidden'); $('update-banner').classList.remove('hidden');
    $('update-text').textContent = 'Downloading and verifying the update…';
    $('upd-install').disabled = true;
    const tick = setInterval(async () => {
      try {
        const u = await api('/api/update');
        if (u.state === 'downloading' && u.progress.total) {
          $('update-text').textContent = `Downloading the update… ${Math.round(u.progress.done / u.progress.total * 100)}%`;
        } else if (u.state === 'installing') $('update-text').textContent = 'Installing…';
        else if (u.state === 'restarting') $('update-text').textContent = 'Restarting the app…';
      } catch (e) { /* the server is going away, that is expected */ }
    }, 700);
    try {
      await api('/api/update', { action: 'install' });
      $('update-text').textContent = 'Updated. The new version is starting — this window closes by itself.';
      setTimeout(() => { clearInterval(tick); document.body.innerHTML = '<div id="boot"><div class="boot-card"><div class="boot-logo"><img src="icon.png" width="84" height="84" alt=""></div><div class="boot-title">Updated</div><div class="boot-sub">The new version is starting. You can close this window.</div></div></div>'; }, 2500);
    } catch (e) {
      clearInterval(tick); updating = false; $('upd-install').disabled = false;
      showAlert(`Update failed: ${e.message}`, 'error', 15000);
    }
  }
  $('update-install').addEventListener('click', installUpdate);
  $('upd-install').addEventListener('click', installUpdate);
  $('upd-check').addEventListener('click', async () => { try { renderUpdate({ ...(await api('/api/update', { action: 'check' })), build_info: (status.update || {}).build_info }); } catch (e) { showAlert(e.message); } });
  $('gh-save').addEventListener('click', async (e) => { e.preventDefault(); try { await api('/api/secrets', { GITHUB_TOKEN: $('gh-token').value }); $('gh-token').value = ''; toast('GitHub token saved'); } catch (err) { showAlert(err.message); } });

  // ----- price chart -----
  // key in the strategy's indicator frame -> colour, dash. Price-scale series only.
  const PRICE_SERIES = [
    ['ema_fast', 'emaFast', null], ['ema_slow', 'emaSlow', null], ['ema_trend', 'trend', [6, 4]],
    ['channel_high', 'emaFast', null], ['channel_low', 'emaSlow', null],
    ['bb_upper', 'band', [4, 3]], ['bb_mid', 'trend', [6, 4]], ['bb_lower', 'band', [4, 3]],
  ];
  const INDICATOR_LABELS = {
    ema_fast: 'EMA fast', ema_slow: 'EMA slow', ema_trend: 'Trend EMA', channel_high: 'Channel high',
    channel_low: 'Channel low', bb_upper: 'Band upper', bb_mid: 'Band middle', bb_lower: 'Band lower',
    rsi: 'RSI', atr: 'ATR', adx: 'ADX',
  };
  const C = new Proxy({}, { get: (_t, k) => ({
    up: themeColor('--up', '#0ca30c'), down: themeColor('--down', '#d03b3b'),
    emaFast: themeColor('--info', '#3987e5'), emaSlow: themeColor('--accent', '#d95926'),
    band: themeColor('--text-3', '#8b98a8'), trend: themeColor('--warn', '#c79a2e'),
    grid: themeColor('--border', '#273241'), text: themeColor('--text-3', '#8b98a8'),
    last: themeColor('--text', '#e6edf3'), stop: themeColor('--down', '#d03b3b'),
    tp: themeColor('--up', '#0ca30c'), entry: themeColor('--info', '#3987e5'),
  }[k]) });
  const chart = { data: null, visible: 200, timer: null, hover: null, layout: null, live: null, wsClosed: {}, ws: null, wsKey: null, wsRetry: 0, lastDraw: 0, lastMsg: 0, tf: null };
  const CHART_TFS = ['1m', '5m', '15m', '1h', '4h', '1d'];
  window.__btcbot = { chart };
  const chartActive = () => document.querySelector('#tab-chart').classList.contains('active');
  async function loadChart() {
    try {
      const limit = Number($('chart-limit').value);
      const d = await api(`/api/candles?limit=${limit}${chart.tf ? `&timeframe=${chart.tf}` : ''}`);
      renderTfControl(d.timeframe, d.bot_timeframe);
      if (chart.data && chart.data.timeframe !== d.timeframe) { chart.live = null; chart.wsClosed = {}; }
      chart.data = d; chart.visible = Math.min(chart.visible, d.candles.length) || d.candles.length;
      $('chart-empty').classList.add('hidden');
      const src = { feed: 'live feed from the running bot', replay: 'demo replay data', exchange: d.timeframe === d.bot_timeframe ? 'public exchange data' : `public exchange data (bot trades on ${d.bot_timeframe})` }[d.source] || d.source;
      $('chart-title').textContent = `${d.exchange} · ${d.symbol} · ${d.timeframe}`;
      $('chart-source').textContent = `${src} · ${d.candles.length} candles · updated ${new Date(d.now).toLocaleTimeString()}`;
      const lastTs = d.candles.length ? d.candles[d.candles.length - 1][0] : 0;
      if (chart.live && chart.live.t <= lastTs) chart.live = null; // the API now carries it
      Object.keys(chart.wsClosed).forEach((t) => { if (Number(t) <= lastTs) delete chart.wsClosed[t]; });
      renderChart();
      connectStream(d);
    } catch (e) {
      $('chart-empty').classList.remove('hidden'); $('chart-empty').textContent = `Could not load candles: ${e.message}`;
    }
  }
  function renderChart() {
    const d = chart.data; const canvas = $('price-chart'); if (!d || !d.candles.length) return;
    const dpr = window.devicePixelRatio || 1; const w = canvas.clientWidth || 800, h = 520;
    canvas.width = w * dpr; canvas.height = h * dpr; const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
    const showVol = $('chart-vol').checked, showEma = $('chart-ema').checked, showTrades = $('chart-trades').checked;
    const n = d.candles.length, start = Math.max(0, n - chart.visible), rows = d.candles.slice(start);
    const lastApiTs = n ? d.candles[n - 1][0] : 0;
    Object.values(chart.wsClosed).filter((k) => k.t > lastApiTs).sort((a, b) => a.t - b.t).forEach((k) => rows.push([k.t, k.o, k.h, k.l, k.c, k.v]));
    const lastRowTs = rows.length ? rows[rows.length - 1][0] : 0;
    const live = chart.live && chart.live.t > lastRowTs ? chart.live : null;
    if (live) rows.push([live.t, live.o, live.h, live.l, live.c, live.v, true]);
    const liveIdx = live ? rows.length - 1 : -1;
    const pad = { l: 8, r: 74, t: 10, b: 22 }; const volH = showVol ? 80 : 0;
    const priceTop = pad.t, priceBot = h - pad.b - volH - (showVol ? 8 : 0), volTop = priceBot + 8, volBot = h - pad.b;
    const plotW = w - pad.l - pad.r, slot = plotW / rows.length, bodyW = Math.max(1, Math.min(14, slot * 0.68));
    let lo = Infinity, hi = -Infinity, vmax = 0;
    rows.forEach((r) => { lo = Math.min(lo, r[3]); hi = Math.max(hi, r[2]); vmax = Math.max(vmax, r[5] || 0); });
    const pos = d.position; if (pos) { [pos.stop_loss, pos.take_profit, pos.entry_price].forEach((v) => { if (v) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); }
    if (live) d.last_price = live.c;
    if (d.last_price) { lo = Math.min(lo, d.last_price); hi = Math.max(hi, d.last_price); }
    const span = (hi - lo) || 1; lo -= span * 0.05; hi += span * 0.05;
    const X = (i) => pad.l + (i + 0.5) * slot, Y = (p) => priceTop + (1 - (p - lo) / (hi - lo)) * (priceBot - priceTop);
    chart.layout = { start, rows, slot, X, Y, pad, priceTop, priceBot, w, h };
    // grid + price axis (recessive)
    ctx.font = '11px system-ui'; ctx.textBaseline = 'middle';
    const ticks = niceTicks(lo, hi, 6);
    const labelYs = [pos && pos.stop_loss, pos && pos.take_profit, pos && pos.entry_price, d.last_price].filter((v) => v).map(Y);
    ticks.forEach((p) => { const y = Y(p); if (labelYs.some((ly) => Math.abs(ly - y) < 10)) return; ctx.strokeStyle = C.grid; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke(); ctx.fillStyle = C.text; ctx.textAlign = 'left'; ctx.fillText(fmt.money(p, p >= 100 ? 0 : 2), w - pad.r + 6, y); });
    // time axis
    const every = Math.max(1, Math.round(rows.length / Math.max(3, Math.floor(plotW / 110))));
    ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    const stepMs = rows.length > 1 ? (rows[rows.length - 1][0] - rows[0][0]) / (rows.length - 1) : 3600000;
    const dateOnly = every * stepMs >= 86400000;
    rows.forEach((r, i) => { if (i % every === 0) { const t = new Date(r[0]).toISOString(); const lbl = dateOnly ? t.slice(0, 10) : t.slice(5, 16).replace('T', ' '); const tw = ctx.measureText(lbl).width; const lx = Math.min(w - pad.r - tw / 2, Math.max(pad.l + tw / 2, X(i))); ctx.fillStyle = C.text; ctx.fillText(lbl, lx, h - 6); ctx.strokeStyle = C.grid; ctx.beginPath(); ctx.moveTo(X(i), priceTop); ctx.lineTo(X(i), volBot); ctx.stroke(); } });
    // volume
    if (showVol) rows.forEach((r, i) => { const up = r[4] >= r[1]; const vh = vmax ? (r[5] / vmax) * (volBot - volTop) : 0; ctx.fillStyle = up ? 'rgba(12,163,12,.35)' : 'rgba(208,59,59,.35)'; ctx.fillRect(X(i) - bodyW / 2, volBot - vh, bodyW, vh); });
    // candles: up = hollow, down = filled (polarity is not color-alone)
    rows.forEach((r, i) => {
      const [ts, o, hh, ll, c] = r; const up = c >= o; const x = X(i); const col = up ? C.up : C.down;
      ctx.globalAlpha = i === liveIdx ? 0.55 : 1; ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, Y(hh)); ctx.lineTo(x, Y(Math.max(o, c))); ctx.moveTo(x, Y(Math.min(o, c))); ctx.lineTo(x, Y(ll)); ctx.stroke();
      const top = Y(Math.max(o, c)), bh = Math.max(1, Y(Math.min(o, c)) - top);
      if (up) { ctx.lineWidth = 1.5; ctx.strokeRect(x - bodyW / 2, top, bodyW, bh); } else ctx.fillRect(x - bodyW / 2, top, bodyW, bh);
      ctx.globalAlpha = 1;
    });
    if (live) { ctx.fillStyle = C.text; ctx.font = '10px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText('forming', X(liveIdx), Y(live.h) - 4); ctx.font = '11px system-ui'; }
    // EMA lines
    const line = (arr, color, dash) => { if (!arr) return; ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash(dash || []); ctx.beginPath(); let started = false; rows.forEach((r, i) => { const v = arr[start + i]; if (v == null) return; const x = X(i), y = Y(v); started ? ctx.lineTo(x, y) : ctx.moveTo(x, y); started = true; }); ctx.stroke(); ctx.setLineDash([]); };
    // Every strategy returns its own indicators: EMAs, a Donchian channel, Bollinger bands.
    // Only the ones measured in price belong on this panel (RSI, ATR and ADX are not).
    if (showEma && d.indicators) {
      PRICE_SERIES.forEach(([key, colorKey, dash]) => {
        if (d.indicators[key]) line(d.indicators[key], C[colorKey], dash);
      });
    }
    // horizontal levels: position + last price
    const level = (p, color, label, dash) => { if (!p) return; const y = Y(p); ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash); ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = color; ctx.fillRect(w - pad.r + 1, y - 8, pad.r - 2, 16); ctx.fillStyle = '#fff'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.font = 'bold 11px system-ui'; ctx.fillText(label, w - pad.r + 5, y); ctx.font = '11px system-ui'; };
    if (pos) { level(pos.stop_loss, C.stop, `SL ${fmt.money(pos.stop_loss, 0)}`, [5, 4]); level(pos.take_profit, C.tp, `TP ${fmt.money(pos.take_profit, 0)}`, [5, 4]); level(pos.entry_price, C.entry, `IN ${fmt.money(pos.entry_price, 0)}`, []); }
    if (d.last_price) { const prev = n ? d.candles[n - 1][4] : d.last_price; level(d.last_price, live ? (d.last_price >= prev ? '#0a7a0a' : '#9e2b2b') : '#4b5563', fmt.money(d.last_price, 0), [2, 3]); }
    // trade markers (shape + label, color for P/L sign)
    if (showTrades) {
      const idxFor = (ts) => { let k = -1; for (let i = 0; i < rows.length; i++) { if (rows[i][0] <= ts) k = i; else break; } return k; };
      ctx.textAlign = 'center'; ctx.font = 'bold 12px system-ui';
      d.trades.forEach((t) => {
        const ei = idxFor(t.entry_ts), xi = idxFor(t.exit_ts);
        if (ei >= 0) { ctx.fillStyle = C.entry; ctx.textBaseline = 'top'; ctx.fillText('▲', X(ei), Y(rows[ei][3]) + 4); }
        if (xi >= 0) { ctx.fillStyle = t.pnl >= 0 ? C.up : C.down; ctx.textBaseline = 'bottom'; ctx.fillText('▼', X(xi), Y(rows[xi][2]) - 4); ctx.font = '10px system-ui'; ctx.fillStyle = C.last; ctx.fillText(fmt.money(t.pnl, 0), X(xi), Y(rows[xi][2]) - 19); ctx.font = 'bold 12px system-ui'; }
      });
    }
    if (chart.hover != null) drawCrosshair();
  }
  function niceTicks(lo, hi, count) { const raw = (hi - lo) / count; const mag = Math.pow(10, Math.floor(Math.log10(raw))); const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10; const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v); return out; }
  function drawCrosshair() {
    const L = chart.layout, d = chart.data; if (!L || chart.hover == null) return;
    const i = chart.hover, r = L.rows[i]; if (!r) return;
    const canvas = $('price-chart'), ctx = canvas.getContext('2d'); const x = L.X(i);
    ctx.save(); ctx.strokeStyle = '#8b98a8'; ctx.setLineDash([3, 3]); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, L.priceTop); ctx.lineTo(x, L.h - L.pad.b); ctx.stroke(); ctx.restore();
    const g = (k) => d.indicators && d.indicators[k] ? d.indicators[k][L.start + i] : null;
    const chg = ((r[4] / r[1] - 1) * 100);
    const shown = Object.keys(d.indicators || {})
      .filter((k) => g(k) != null && INDICATOR_LABELS[k])
      .map((k) => `${INDICATOR_LABELS[k]} ${['rsi', 'adx'].includes(k) ? g(k).toFixed(1) : fmt.money(g(k))}`);
    $('chart-tip').innerHTML = `<b>${fmt.time(r[0])}</b><br>O ${fmt.money(r[1])} &nbsp; H ${fmt.money(r[2])}<br>L ${fmt.money(r[3])} &nbsp; C <b class="${cls(chg)}">${fmt.money(r[4])}</b> (${fmt.pct(chg)})<br>Vol ${fmt.money(r[5], 1)}`
      + (shown.length ? `<br>${shown.join(' · ')}` : '');
    const tip = $('chart-tip'); tip.classList.remove('hidden');
    const left = x + 14 + 190 > L.w ? x - 14 - 190 : x + 14; tip.style.left = `${left}px`; tip.style.top = `${Math.max(4, L.priceTop + 4)}px`;
  }
  $('price-chart').addEventListener('mousemove', (e) => { const L = chart.layout; if (!L) return; const rect = e.currentTarget.getBoundingClientRect(); const i = Math.floor((e.clientX - rect.left - L.pad.l) / L.slot); if (i < 0 || i >= L.rows.length) return; if (i !== chart.hover) { chart.hover = i; renderChart(); } });
  $('price-chart').addEventListener('mouseleave', () => { chart.hover = null; $('chart-tip').classList.add('hidden'); renderChart(); });
  $('price-chart').addEventListener('wheel', (e) => { if (!chart.data) return; e.preventDefault(); const n = chart.data.candles.length; chart.visible = Math.max(30, Math.min(n, Math.round(chart.visible * (e.deltaY > 0 ? 1.2 : 0.83)))); renderChart(); }, { passive: false });
  ['chart-ema', 'chart-vol', 'chart-trades'].forEach((id) => $(id).addEventListener('change', renderChart));
  $('chart-limit').addEventListener('change', () => { chart.visible = Number($('chart-limit').value); loadChart(); });
  $('chart-refresh').addEventListener('click', loadChart);
  $('chart-tv').addEventListener('click', () => {
    const wrap = $('tv-wrap'); const on = wrap.classList.toggle('hidden') === false; $('chart-tv').classList.toggle('on', on);
    if (on && !wrap.firstChild) {
      const c = status.config || {}; const sym = `${(c.exchange || 'binance').toUpperCase()}:${(c.symbol || 'BTC/USDT').replace('/', '').split(':')[0]}`;
      const iv = { '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30', '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720', '1d': 'D', '1w': 'W' }[c.timeframe] || '60';
      const f = document.createElement('iframe'); f.src = `https://www.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=${iv}&theme=dark&style=1&locale=en&withdateranges=1&hide_side_toolbar=0&allow_symbol_change=1`; f.allow = 'fullscreen'; wrap.appendChild(f);
    }
  });
  function chartTick() { if (chartActive()) loadChart(); }
  function renderTfControl(active, botTf) {
    const box = $('chart-tf'); const tfs = CHART_TFS.includes(botTf) ? CHART_TFS : [botTf, ...CHART_TFS];
    box.innerHTML = tfs.map((tf) => `<button data-tf="${tf}" class="${tf === active ? 'active' : ''} ${tf === botTf ? 'bot' : ''}" title="${tf === botTf ? 'the bot trades on this timeframe' : ''}">${tf}</button>`).join('');
    box.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { chart.tf = b.dataset.tf; chart.visible = Number($('chart-limit').value); loadChart(); }));
  }

  // ----- realtime stream (Binance public WebSocket, no keys) -----
  const BINANCE_WS = 'wss://stream.binance.com:9443/ws/';
  function streamKey(d) { return d.exchange === 'binance' ? `${d.symbol.replace('/', '').split(':')[0].toLowerCase()}@kline_${d.timeframe}` : null; }
  function setLiveBadge(state) { const b = $('chart-live'); b.classList.toggle('hidden', state === 'off'); b.classList.toggle('reconnecting', state === 'reconnecting'); b.textContent = state === 'reconnecting' ? '● reconnecting' : `● LIVE${chart.lastMsg ? ' ' + new Date(chart.lastMsg).toLocaleTimeString() : ''}`; }
  function ensureStream() { const c = status.config || {}; if (c.exchange && c.symbol && c.timeframe && !(chart.ws && chart.ws.readyState <= 1)) connectStream({ exchange: c.exchange, symbol: c.symbol, timeframe: chart.tf || c.timeframe }); }
  function connectStream(d) {
    const key = streamKey(d);
    if (!key || !window.WebSocket) { disconnectStream(); return; }
    if (chart.ws && chart.wsKey === key && chart.ws.readyState <= 1) return;
    disconnectStream();
    chart.wsKey = key;
    let ws;
    try { ws = new WebSocket(BINANCE_WS + key); } catch (e) { setLiveBadge('off'); return; }
    chart.ws = ws;
    ws.onopen = () => { chart.wsRetry = 0; setLiveBadge('on'); };
    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      const k = m.k; if (!k) return;
      const candle = { t: k.t, o: +k.o, h: +k.h, l: +k.l, c: +k.c, v: +k.v, closed: !!k.x };
      chart.lastMsg = Date.now();
      if (k.x) { chart.wsClosed[k.t] = candle; if (chart.live && chart.live.t === k.t) chart.live = null; setTimeout(loadChart, 1500); setTimeout(loadChart, 12000); } // closed: keep it, then fetch it with indicators
      else chart.live = candle;
      $('t-price').textContent = fmt.money(+k.c);
      if (chartActive()) { setLiveBadge('on'); const now = performance.now(); if (now - chart.lastDraw > 250) { chart.lastDraw = now; renderChart(); } }
    };
    ws.onclose = () => { if (chart.ws !== ws) return; chart.ws = null; setLiveBadge('reconnecting'); const delay = Math.min(30000, 1000 * Math.pow(2, chart.wsRetry++)); setTimeout(() => connectStream(d), delay); };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* ignore */ } };
  }
  function disconnectStream() { if (chart.ws) { const ws = chart.ws; chart.ws = null; try { ws.close(); } catch (e) { /* ignore */ } } chart.wsKey = null; setLiveBadge('off'); }


  // ----- research: parameter search and walk-forward -----
  async function loadResearch() {
    try {
      const r = await api('/api/samples'); sampleDetails = r.details || [];
      const sel = $('rs-source'); const cur = sel.value; sel.innerHTML = '';
      (r.samples || []).forEach((s) => {
        const d = sampleDetails.find((x) => x.path === s) || {};
        const o = document.createElement('option'); o.value = s;
        o.textContent = `${d.name || s}${d.from ? `  (${d.from} → ${d.to}, ${d.candles} candles)` : ''}`;
        sel.appendChild(o);
      });
      if (cur) sel.value = cur;
      const d = sampleDetails.find((x) => x.path === sel.value);
      $('rs-range').textContent = d && d.candles ? `${d.candles.toLocaleString()} candles · ${d.from} → ${d.to}` : '';
      const tf = $('dl-timeframe');
      if (!tf.options.length) {
        (cfgMeta.timeframes.length ? cfgMeta.timeframes : ['1m','5m','15m','1h','4h','1d']).forEach((t) => {
          const o = document.createElement('option'); o.value = o.textContent = t; tf.appendChild(o);
        });
        tf.value = (status.config && status.config.timeframe) || '1h';
      }
      if (!$('dl-symbol').value) $('dl-symbol').value = (status.config && status.config.symbol) || 'BTC/USDT';
      renderResearch(await api('/api/research'));
    } catch (e) { showAlert(e.message); }
  }
  $('rs-source').addEventListener('change', () => {
    const d = sampleDetails.find((x) => x.path === $('rs-source').value);
    $('rs-range').textContent = d && d.candles ? `${d.candles.toLocaleString()} candles · ${d.from} → ${d.to}` : '';
  });
  let lastResearch = null;
  function renderResearch(r) {
    if (!r) return;
    const st = $('rs-status'), meter = $('rs-meter');
    if (r.state === 'running') {
      const p = r.progress || {};
      st.textContent = p.phase ? `${p.phase}${p.total ? ` (${p.done}/${p.total})` : ''}` : 'running…';
      meter.classList.remove('hidden');
      meter.firstElementChild.style.width = p.total ? `${Math.round(p.done / p.total * 100)}%` : '10%';
      $('rs-run').disabled = true;
      return;  // the shared refresher below keeps polling; a failed fetch must not end the chain
    }
    $('rs-run').disabled = false; meter.classList.add('hidden');
    if (r.state === 'error') { st.textContent = ''; showAlert(`Search failed: ${r.error}`, 'error', 15000); return; }
    if (r.state !== 'done') { st.textContent = ''; return; }
    lastResearch = r;
    st.textContent = `done · ${r.candles.toLocaleString()} candles from ${r.source}`;
    $('rs-result').classList.remove('hidden');
    const head = $('rs-table').querySelector('thead'), body = $('rs-table').querySelector('tbody');
    const second = $('rs-verdict2');
    second.classList.add('hidden');
    if (r.mode === 'robustness') {
      const mc = r.monte_carlo, sn = r.sensitivity, ret = mc.returns_pct, dd = mc.drawdowns_pct;
      const v = $('rs-verdict');
      v.textContent = mc.verdict;
      v.className = `confirm-box ${mc.prob_ruin_pct >= 5 || mc.prob_loss_pct >= 40 ? 'bad' : mc.prob_loss_pct >= 20 || ret.median <= 0 ? 'mixed' : 'good'}`;
      second.textContent = sn.verdict;
      second.className = `confirm-box ${sn.verdict.startsWith('Fitted') || sn.verdict.startsWith('No setting') ? 'mixed' : 'good'}`;
      second.classList.remove('hidden');
      $('rs-tiles').innerHTML = [
        ['This backtest', fmt.pct(mc.actual_return_pct), cls(mc.actual_return_pct)],
        ['Median re-deal', fmt.pct(ret.median), cls(ret.median)],
        ['Bad fifth', fmt.pct(ret.p05), cls(ret.p05)],
        ['Good fifth', fmt.pct(ret.p95), cls(ret.p95)],
        ['Worst re-deal', fmt.pct(ret.worst), 'neg'],
        ['Ended down', `${mc.prob_loss_pct}%`, mc.prob_loss_pct >= 40 ? 'neg' : ''],
        ['Lost half', `${mc.prob_ruin_pct}%`, mc.prob_ruin_pct > 0 ? 'neg' : ''],
        ['Typical drawdown', `${dd.median}%`, 'neg'],
        ['Worst drawdown', `${dd.worst}%`, 'neg'],
        ['Trades', mc.trades, ''],
      ].map(([l, val, c]) => `<div class="tile"><div class="label">${l}</div><div class="value ${c}">${val}</div></div>`).join('');
      // The spread of re-dealt outcomes, cheapest useful picture: sorted final returns.
      drawLine($('rs-chart'), [[5, ret.p05], [25, ret.p25], [50, ret.median], [75, ret.p75], [95, ret.p95]]
        .map(([x, y]) => ({ x, y })), { empty: 'no re-deals' });
      head.innerHTML = '<tr><th>Setting</th><th>Now</th><th>Best here</th><th>Shape</th><th class="num">Drop vs neighbours</th><th>Tried</th></tr>';
      body.innerHTML = (sn.sweeps || []).map((sw) => {
        const badge = sw.shape === 'spike' ? 'neg' : sw.shape === 'plateau' ? 'pos' : 'muted';
        const tried = sw.values.map((val, i) => {
          const sc = sw.scores[i];
          return `<span class="${sc === null ? 'muted' : ''}">${val}${sc === null ? '' : `:${sc}`}</span>`;
        }).join(' · ');
        return `<tr><td>${sw.param}</td><td class="muted">${sw.baseline_value === null || sw.baseline_value === undefined ? 'default' : sw.baseline_value}</td>`
          + `<td>${sw.best_value === null ? '—' : sw.best_value}${sw.at_edge ? ' <span class="muted small">(edge)</span>' : ''}</td>`
          + `<td class="${badge}">${sw.shape}</td><td class="num">${sw.neighbour_drop_pct === null ? '—' : `${sw.neighbour_drop_pct}%`}</td>`
          + `<td class="muted small">${tried}</td></tr>`;
      }).join('');
      $('rs-apply-note').textContent = 'Nothing to apply: this test judges the settings you already have.';
      $('rs-apply').disabled = true;
      return;
    }
    $('rs-apply').disabled = false;
    if (r.mode === 'walk-forward') {
      const m = r.out_of_sample, s = r.stability;
      const v = $('rs-verdict');
      v.textContent = r.verdict;
      v.className = `confirm-box ${m.total_return_pct <= 0 ? 'bad' : (m.total_return_pct < m.buy_hold_return_pct || s.profitable_folds < s.folds / 2) ? 'mixed' : 'good'}`;
      $('rs-tiles').innerHTML = [
        ['Out of sample', fmt.pct(m.total_return_pct), cls(m.total_return_pct)],
        ['Buy &amp; hold', fmt.pct(m.buy_hold_return_pct), cls(m.buy_hold_return_pct)],
        ['Max drawdown', fmt.pct(m.max_drawdown_pct), 'neg'],
        ['Sharpe', m.sharpe, cls(m.sharpe)],
        ['Trades', m.trades, ''],
        ['Blocks in profit', `${s.profitable_folds}/${s.folds}`, ''],
        ['In-sample avg', fmt.pct(s.avg_in_sample_return_pct), ''],
        ['Out-of-sample avg', fmt.pct(s.avg_out_of_sample_return_pct), cls(s.avg_out_of_sample_return_pct)],
      ].map(([l, val, c]) => `<div class="tile"><div class="label">${l}</div><div class="value ${c}">${val}</div></div>`).join('');
      drawLine($('rs-chart'), (r.equity || []).map((e) => ({ x: e.ts, y: e.equity })), { empty: 'no out-of-sample equity' });
      head.innerHTML = '<tr><th>Block</th><th>Trained on</th><th class="num">In sample</th><th>Tested on</th><th class="num">Out of sample</th><th>Settings it chose</th></tr>';
      body.innerHTML = r.folds.map((f) => `<tr><td>${f.index}</td><td>${f.train.from.slice(0,10)} → ${f.train.to.slice(0,10)}</td>`
        + `<td class="num ${cls(f.train_metrics.total_return_pct)}">${fmt.pct(f.train_metrics.total_return_pct)}</td>`
        + `<td>${f.test.from.slice(0,10)} → ${f.test.to.slice(0,10)}</td>`
        + `<td class="num ${cls(f.test_metrics.total_return_pct)}">${fmt.pct(f.test_metrics.total_return_pct)}</td>`
        + `<td class="muted small">${Object.entries(f.chosen).map(([k, v]) => `${k}=${v}`).join(' ')}</td></tr>`).join('');
      $('rs-apply-note').textContent = 'Applies the settings the last block chose.';
    } else {
      const v = $('rs-verdict'); v.textContent = r.note; v.className = 'confirm-box mixed';
      $('rs-tiles').innerHTML = '';
      drawLine($('rs-chart'), [], { empty: 'run a walk-forward test to see an out-of-sample curve' });
      head.innerHTML = '<tr><th class="num">#</th><th class="num">Return</th><th class="num">Drawdown</th><th class="num">Sharpe</th><th class="num">Trades</th><th class="num">Win rate</th><th>Settings</th></tr>';
      body.innerHTML = (r.top || []).map((c, i) => `<tr><td class="num">${i + 1}</td>`
        + `<td class="num ${cls(c.metrics.total_return_pct)}">${fmt.pct(c.metrics.total_return_pct)}</td>`
        + `<td class="num neg">${fmt.pct(c.metrics.max_drawdown_pct)}</td><td class="num">${c.metrics.sharpe}</td>`
        + `<td class="num">${c.metrics.trades}</td><td class="num">${c.metrics.win_rate_pct}%</td>`
        + `<td class="muted small">${Object.entries(c.params).map(([k, v]) => `${k}=${v}`).join(' ')}</td></tr>`).join('');
      $('rs-apply-note').textContent = 'Applies the best in-sample settings. Test them walk-forward first.';
    }
  }
  // One timer refreshes whatever long job is in flight, and survives a failed request.
  setInterval(async () => {
    if (!document.querySelector('#tab-research').classList.contains('active')) return;
    try { renderResearch(await api('/api/research')); } catch (e) { /* try again next tick */ }
  }, 1500);

  $('rs-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = { csv: $('rs-source').value, mode: $('rs-mode').value, folds: Number($('rs-folds').value),
                   sample: Number($('rs-sample').value), objective: $('rs-objective').value,
                   min_win_rate: Number($('rs-minwin').value || 0), runs: 2000, method: 'resample' };
    try { renderResearch(await api('/api/research', body)); } catch (err) { showAlert(err.message); }
  });
  $('rs-apply').addEventListener('click', async () => {
    if (!lastResearch) return;
    const params = lastResearch.mode === 'walk-forward'
      ? (lastResearch.folds[lastResearch.folds.length - 1] || {}).chosen
      : ((lastResearch.top || [])[0] || {}).params;
    if (!params) { showAlert('No settings to apply.'); return; }
    const text = Object.entries(params).map(([k, v]) => `${k} = ${v}`).join('<br>');
    if (!(await confirmDialog('Use these settings?', `${text}<br><br>They are written to your configuration and apply the next time you press Start.`))) return;
    try { await api('/api/research/apply', { params }); toast('Settings applied', 'They apply on the next Start.'); loadSettings(); poll(); }
    catch (e) { showAlert(e.message); }
  });
  $('dl-run').addEventListener('click', async (e) => {
    e.preventDefault();
    const body = { symbol: $('dl-symbol').value, timeframe: $('dl-timeframe').value,
                   from: $('dl-from').value || null, to: $('dl-to').value || null };
    try {
      await api('/api/download', body);
      $('dl-status').textContent = 'downloading…';
      const poll2 = setInterval(async () => {
        const d = await api('/api/download');
        if (d.state === 'running') { $('dl-status').textContent = 'downloading…'; return; }
        clearInterval(poll2);
        if (d.state === 'error') { $('dl-status').textContent = ''; showAlert(`Download failed: ${d.error}`, 'error', 15000); return; }
        $('dl-status').textContent = `${d.candles.toLocaleString()} candles saved`;
        toast('History downloaded', `${d.candles.toLocaleString()} candles · ${d.from.slice(0,10)} → ${d.to.slice(0,10)}`);
        loadResearch(); loadSamples();
      }, 1500);
    } catch (err) { showAlert(err.message); }
  });

  // ----- analytics -----
  async function loadAnalytics() {
    try {
      const a = await api('/api/analytics');
      $('an-tiles').innerHTML = [
        ['Realised P/L', fmt.money(a.pnl), cls(a.pnl)], ['Trades', a.trades, ''],
        ['Win rate', a.trades ? `${a.win_rate_pct}%` : '—', ''],
        ['Expectancy / trade', a.trades ? fmt.money(a.expectancy) : '—', cls(a.expectancy)],
        ['Fees paid', fmt.money(a.fees), ''],
        ['Best / worst', a.trades ? `${fmt.money(a.best)} / ${fmt.money(a.worst)}` : '—', ''],
        ['Median hold', a.durations && a.durations.median_hours != null ? `${a.durations.median_hours}h` : '—', ''],
        ['Streaks', a.streaks ? `${a.streaks.longest_winning}W / ${a.streaks.longest_losing}L` : '—', ''],
      ].map(([l, v, c]) => `<div class="tile"><div class="label">${l}</div><div class="value ${c}" style="font-size:19px">${v}</div></div>`).join('');
      const mb = $('an-monthly').querySelector('tbody');
      mb.innerHTML = (a.monthly || []).length ? a.monthly.map((m) => `<tr><td>${m.month}</td><td class="num">${m.trades}</td>`
        + `<td class="num ${cls(m.pnl)}">${fmt.money(m.pnl)}</td><td class="num">${fmt.money(m.fees)}</td>`
        + `<td class="num">${m.win_rate_pct}%</td><td class="num pos">${fmt.money(m.best)}</td><td class="num neg">${fmt.money(m.worst)}</td></tr>`).join('')
        : '<tr><td colspan="7" class="muted">No closed trades yet.</td></tr>';
      const eb = $('an-exits').querySelector('tbody');
      const exits = Object.entries(a.by_exit || {});
      eb.innerHTML = exits.length ? exits.map(([k, v]) => `<tr><td>${k}</td><td class="num">${v.trades}</td>`
        + `<td class="num ${cls(v.pnl)}">${fmt.money(v.pnl)}</td><td class="num">${v.win_rate_pct}%</td>`
        + `<td class="num ${cls(v.avg_pnl)}">${fmt.money(v.avg_pnl)}</td></tr>`).join('')
        : '<tr><td colspan="5" class="muted">Nothing yet.</td></tr>';
      $('ex-trades').href = `/api/export?what=trades&token=${encodeURIComponent(TOKEN)}`;
      $('ex-equity').href = `/api/export?what=equity&token=${encodeURIComponent(TOKEN)}`;
    } catch (e) { showAlert(e.message); }
  }

  // ----- why no trades -----
  async function loadWhy() {
    try {
      const w = await api('/api/why');
      const total = w.candles_evaluated || 0;
      $('why-count').textContent = total ? `${total} candle${total === 1 ? '' : 's'} evaluated` : '';
      const parts = [];
      (w.blockers || []).forEach((b) => parts.push(`<div class="why-block">■ ${b}</div>`));
      if (w.waiting && !(w.blockers || []).length) parts.push(`<div class="why-wait">${w.waiting}</div>`);
      const entries = Object.entries(w.decisions || {}).sort((a, b) => b[1] - a[1]);
      if (entries.length) {
        const label = {
          'no entry signal': 'No entry signal (no crossover)', 'order placed': 'Orders placed',
          'warming up': 'Warming up (not enough history)', 'no_position': 'Sell signal but nothing to sell',
          'already_long': 'Already in a position', 'low_confidence': 'Signal confidence too low',
          'min_interval': 'Too soon after the last entry', 'cooldown': 'In cooldown after losses',
          'halted': 'Halted by the daily loss limit', 'too_small': 'Order would be below the minimum size',
          'kill_switch': 'Blocked by the kill switch', 'bad_stop': 'Signal had no usable stop loss',
          'AI not called': 'AI not asked (no technical setup)',
        };
        parts.push('<ul class="why-list">' + entries.map(([k, v]) =>
          `<li><span>${label[k] || k}</span><b>${v}</b></li>`).join('') + '</ul>');
      }
      if (w.ema_gap_pct != null) {
        parts.push(`<div class="muted small" style="margin-top:8px">EMA gap ${w.ema_gap_pct > 0 ? '+' : ''}${w.ema_gap_pct}% · RSI ${w.indicators && w.indicators.rsi != null ? w.indicators.rsi.toFixed(1) : '—'} · ${w.timeframe} candles · ${w.mode} mode</div>`);
      }
      if (total && !((w.decisions || {})['order placed'])) {
        parts.push('<ul class="why-hints">' + (w.hints || []).map((h) => `<li>${h}</li>`).join('') + '</ul>');
      }
      $('why-body').innerHTML = parts.join('') || 'Start the bot to see what it is waiting for.';
    } catch (e) { /* the card is informational */ }
  }

  // ----- license -----
  function renderLicense(l) {
    if (!l) return;
    const badge = $('lic-badge');
    badge.textContent = l.licensed ? 'licensed' : 'unlicensed';
    badge.classList.toggle('ok', !!l.licensed);
    $('lic-state').textContent = l.licensed
      ? `${l.masked}${l.activated_at ? ' · activated ' + l.activated_at.slice(0, 10) : ''}`
      : (l.reason || '');
    $('lic-remove').classList.toggle('hidden', !l.licensed);
    $('lic-key').placeholder = l.licensed ? l.masked : 'BTCB-XXXXX-XXXXX-XXXXX-XXXXX';
    $('license-foot').innerHTML = l.licensed
      ? `<span class="pos">licensed</span>`
      : `<a href="#" id="license-link">unlicensed — paper trading only</a>`;
    const link = $('license-link');
    if (link) link.addEventListener('click', (e) => {
      e.preventDefault();
      document.querySelector('.tabs button[data-tab=settings]').click();
      $('license-card').scrollIntoView({ behavior: 'smooth', block: 'center' });
      $('lic-key').focus();
    });
  }
  $('lic-activate').addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      const out = await api('/api/license', { action: 'activate', key: $('lic-key').value });
      $('lic-key').value = ''; renderLicense(out);
      toast('License activated', 'Live trading is unlocked on this computer.', 'up');
      poll();
    } catch (err) { showAlert(err.message, 'error', 10000); }
  });
  $('lic-remove').addEventListener('click', async (e) => {
    e.preventDefault();
    if (!(await confirmDialog('Remove the license from this computer?', 'Live trading stops working until a key is entered again. Paper trading is unaffected.'))) return;
    try { renderLicense(await api('/api/license', { action: 'remove' })); toast('License removed'); poll(); }
    catch (err) { showAlert(err.message); }
  });

  // ----- what's new -----
  function mdToHtml(body) {
    // Bullets wrap across lines in the changelog: a line that is not a new '- ' item
    // continues the previous one.
    const items = [];
    body.split('\n').forEach((line) => {
      const t = line.trim();
      if (!t) return;
      if (t.startsWith('- ')) items.push(t.slice(2));
      else if (items.length) items[items.length - 1] += ' ' + t;
      else items.push(t);
    });
    const esc = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const render = (t) => esc(t).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    if (!items.length) return `<p class="muted">${render(body)}</p>`;
    return '<ul>' + items.map((i) => `<li>${render(i)}</li>`).join('') + '</ul>';
  }
  async function showWhatsNew(all = false) {
    try {
      const data = await api('/api/changelog');
      const entries = all ? data.entries : data.entries.slice(0, 1);
      if (!entries.length) { showAlert('No changelog found in this build.', 'warn'); return; }
      $('wn-title').innerHTML = all ? 'Release history' : `What&rsquo;s new in ${entries[0].version}`;
      $('wn-body').innerHTML = entries.map((e) => `${all ? `<div class="wn-version">Version ${e.version}</div>` : ''}${mdToHtml(e.body)}`).join('');
      $('wn-all').textContent = all ? 'Newest only' : 'Older versions';
      $('wn-all').onclick = () => showWhatsNew(!all);
      $('whats-new').showModal();
      store.set('seenVersion', data.current || '');
    } catch (e) { showAlert(e.message); }
  }
  $('wn-close').addEventListener('click', () => $('whats-new').close());
  $('show-whats-new').addEventListener('click', (e) => { e.preventDefault(); showWhatsNew(false); });

  // ----- loading screen -----
  const BOOT_STEPS = [
    ['connect', 'Connecting to the trading engine'],
    ['config', 'Reading settings and license'],
    ['state', 'Loading positions and history'],
    ['ready', 'Ready'],
  ];
  const boot = { done: new Set(), finished: false };
  function bootInit() {
    $('boot-steps').innerHTML = BOOT_STEPS.map(([id, label]) =>
      `<li data-step="${id}"><span class="mark">○</span><span>${label}</span></li>`).join('');
  }
  function bootStep(id, ok = true, label = null) {
    if (boot.finished) return;
    boot.done.add(id);
    const li = $('boot-steps').querySelector(`[data-step="${id}"]`);
    if (li) {
      li.classList.toggle('done', ok); li.classList.toggle('fail', !ok);
      li.querySelector('.mark').textContent = ok ? '✓' : '✕';
      if (label) li.lastElementChild.textContent = label;
    }
    $('boot-bar').style.width = `${Math.round(boot.done.size / BOOT_STEPS.length * 100)}%`;
    const next = BOOT_STEPS.find(([s]) => !boot.done.has(s));
    $('boot-sub').textContent = ok ? (next ? next[1] + '…' : 'Ready') : (label || 'Something went wrong');
  }
  function bootFinish() {
    if (boot.finished) return;
    boot.finished = true;
    const el = $('boot');
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 600);
    const seen = store.get('seenVersion', null);
    const current = (status.update && status.update.current) ? status.update.current.version : null;
    if (current && seen !== current && seen !== null) setTimeout(() => showWhatsNew(false), 900);
    else if (current && seen === null) store.set('seenVersion', current);
  }
  bootInit();

  // ----- boot -----
  const startTab = store.get('tab', 'dashboard');
  const startBtn = document.querySelector(`.tabs button[data-tab="${startTab}"]`);
  if (startBtn && startTab !== 'dashboard') startBtn.click();
  poll(); setInterval(poll, 2000); setInterval(chartTick, 10000);
  window.addEventListener('resize', () => { loadEquity(); renderChart(); });
})();
