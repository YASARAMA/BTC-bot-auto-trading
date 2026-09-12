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

  function showAlert(text, kind = 'error', ms = 6000) {
    const el = $('alert'); el.textContent = text; el.className = `alert ${kind}`; el.classList.remove('hidden');
    clearTimeout(showAlert.t); if (ms) showAlert.t = setTimeout(() => el.classList.add('hidden'), ms);
  }

  // ----- tabs -----
  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${b.dataset.tab}`));
    if (b.dataset.tab === 'trades') loadTrades();
    if (b.dataset.tab === 'settings') loadSettings();
    if (b.dataset.tab === 'backtest') loadSamples();
    if (b.dataset.tab === 'chart') loadChart(); else disconnectStream();
  }));

  // ----- chart -----
  function drawLine(canvas, points, opts = {}) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 600, h = canvas.getAttribute('height') | 0 || 240;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
    if (!points || points.length < 2) { ctx.fillStyle = '#8b98a8'; ctx.font = '13px system-ui'; ctx.fillText(opts.empty || 'no data', 12, 24); return; }
    const pad = { l: 64, r: 12, t: 12, b: 24 };
    const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
    const xmin = Math.min(...xs), xmax = Math.max(...xs); let ymin = Math.min(...ys), ymax = Math.max(...ys);
    if (ymin === ymax) { ymin -= 1; ymax += 1; }
    const yr = (ymax - ymin) * 0.08; ymin -= yr; ymax += yr;
    const X = (x) => pad.l + (x - xmin) / (xmax - xmin || 1) * (w - pad.l - pad.r);
    const Y = (y) => pad.t + (1 - (y - ymin) / (ymax - ymin)) * (h - pad.t - pad.b);
    ctx.strokeStyle = '#273241'; ctx.fillStyle = '#8b98a8'; ctx.font = '11px system-ui'; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = ymin + (ymax - ymin) * i / 4, py = Y(y);
      ctx.beginPath(); ctx.moveTo(pad.l, py); ctx.lineTo(w - pad.r, py); ctx.stroke();
      ctx.fillText(fmt.money(y, 0), 6, py + 4);
    }
    ctx.fillText(fmt.time(xmin).slice(0, 10), pad.l, h - 6); const lbl = fmt.time(xmax).slice(0, 10); ctx.fillText(lbl, w - pad.r - ctx.measureText(lbl).width, h - 6);
    const base = opts.baseline; if (base != null) { ctx.strokeStyle = '#4a3a12'; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(pad.l, Y(base)); ctx.lineTo(w - pad.r, Y(base)); ctx.stroke(); ctx.setLineDash([]); }
    const last = ys[ys.length - 1], first = ys[0];
    ctx.strokeStyle = last >= first ? '#2ecc71' : '#ff5c5c'; ctx.lineWidth = 1.8; ctx.beginPath();
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
  function cls(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : ''; }
  function renderStatus(s) {
    status = s;
    const c = s.config || {};
    $('cfg-summary').textContent = c.symbol ? `${c.exchange} · ${c.symbol} · ${c.timeframe} · ${c.strategy}` : (s.config_error || '—');
    const dot = $('state-dot'); dot.className = `dot ${s.state}`;
    $('state-text').textContent = s.state === 'running' && s.replay ? 'running (demo replay)' : s.state + (s.error ? ` · ${s.error}` : '');
    const mode = s.state === 'stopped' || s.state === 'error' ? (s.live_possible ? 'live armed' : 'paper') : (s.replay ? 'replay' : (s.mode || '—'));
    const badge = $('mode-badge'); badge.textContent = mode.toUpperCase(); badge.className = `badge ${mode.split(' ')[0]}`;
    const running = s.state === 'running' || s.state === 'starting';
    $('btn-start').disabled = running || s.state === 'stopping'; $('btn-demo').disabled = running || s.state === 'stopping';
    $('btn-stop').disabled = !running;
    $('btn-start').textContent = s.live_possible ? 'Start LIVE' : 'Start (paper)';
    $('btn-start').className = s.live_possible ? 'danger' : 'primary';
    const kill = $('btn-kill'); kill.textContent = `Kill switch: ${s.kill_switch ? 'ON' : 'off'}`; kill.classList.toggle('on', !!s.kill_switch);

    const lc = s.last_cycle || {};
    const price = s.price;
    $('t-price').textContent = fmt.money(price);
    $('t-equity').textContent = fmt.money(s.equity);
    const init = c.initial_cash; const ret = (s.equity != null && init) ? (s.equity / init - 1) * 100 : null;
    $('t-equity-sub').textContent = ret == null ? '' : `${fmt.pct(ret)} vs start`; $('t-equity-sub').className = `sub ${cls(ret)}`;
    $('t-cash').textContent = fmt.money(s.cash);
    const p = s.position;
    if (p) {
      $('t-position').textContent = `${fmt.qty(p.qty)} @ ${fmt.money(p.entry_price)}`;
      $('t-position-sub').textContent = `stop ${fmt.money(p.stop_loss)} · tp ${fmt.money(p.take_profit)}`;
      const u = price ? (price - p.entry_price) * p.qty : null;
      $('t-upnl').textContent = u == null ? '—' : `${fmt.money(u)}`; $('t-upnl').className = `value ${cls(u)}`;
    } else { $('t-position').textContent = 'flat'; $('t-position-sub').textContent = ''; $('t-upnl').textContent = '—'; $('t-upnl').className = 'value'; }
    const r = s.risk || {};
    $('t-daily').textContent = r.daily_drawdown_pct == null ? '—' : fmt.pct(r.daily_drawdown_pct); $('t-daily').className = `value ${cls(r.daily_drawdown_pct)}`;
    $('t-daily-sub').textContent = r.trades_today != null ? `${r.trades_today} trade(s) today` : '';
    $('t-cycles').textContent = s.cycles || 0; $('t-candle').textContent = lc.candle_time ? `last candle ${lc.candle_time.replace('T', ' ').slice(0, 16)}` : (lc.now ? `tick ${fmt.ts(lc.now)}` : '');
    const sig = s.signal;
    if (sig) {
      $('sig-action').textContent = sig.action; $('sig-action').className = `badge big ${sig.action}`;
      $('sig-conf').textContent = sig.action === 'HOLD' ? '' : `confidence ${(sig.confidence * 100).toFixed(0)}%`;
      $('sig-reason').textContent = sig.reason;
      const d = s.decision || {};
      $('sig-decision').textContent = d.intent ? `→ order intent: ${d.intent.side} ${fmt.qty(d.intent.qty)} (${d.intent.kind})` : (d.rejected && d.rejected !== 'hold') ? `→ not traded (${d.rejected}): ${d.reason}` : '';
    }
    $('r-halted').textContent = r.halted ? `yes · ${r.halt_reason || ''}` : 'no';
    $('r-cooldown').textContent = r.cooldown ? 'yes' : 'no';
    $('r-losses').textContent = r.consecutive_losses ?? '—'; $('r-trades').textContent = r.trades_today ?? '—';
    $('r-kill').textContent = s.kill_switch ? 'ON — all orders blocked' : 'off';
    const alerts = (s.alerts || []).slice().reverse();
    $('alerts').innerHTML = alerts.length ? alerts.map((a) => `<li><span class="t">${fmt.ts(a.ts)}</span><span>${describe(a)}</span></li>`).join('') : '<li class="muted">Nothing yet.</li>';
    $('version').textContent = `BTC Bot ${s.version}`; $('log-file').textContent = s.paths ? s.paths.log : '';
    if (s.paths) $('env-path').textContent = s.secrets.env_file;
  }
  function describe(a) {
    switch (a.event) {
      case 'position_opened': return `Opened ${fmt.qty(a.qty)} @ ${fmt.money(a.entry_price)} (stop ${fmt.money(a.stop_loss)}, tp ${fmt.money(a.take_profit)})`;
      case 'trade_closed': return `<span class="${cls(a.pnl)}">Closed ${a.exit_reason}: P/L ${fmt.money(a.pnl)} (${fmt.pct(a.pnl_pct)})</span>`;
      case 'risk_event': return `<span class="neg">Risk: ${a.detail}</span>`;
      case 'cycle_error': return `<span class="neg">Error: ${a.error}</span>`;
      case 'reconcile': return `Reconciled with exchange · cash ${fmt.money(a.cash)}${(a.warnings || []).length ? ' · ' + a.warnings.join('; ') : ''}`;
      case 'replay_finished': return `Demo replay finished after ${a.cycles} cycles`;
      case 'shutdown': return `Stopped (${a.reason})`;
      default: return `${a.event}: ${a.msg || ''}`;
    }
  }
  async function poll() {
    try {
      const s = await api('/api/status');
      renderStatus(s);
      const v = (s.cycles || 0) + ':' + (s.state);
      if (v !== equityVersion) { equityVersion = v; loadEquity(); }
    } catch (e) { $('state-text').textContent = `disconnected: ${e.message}`; $('state-dot').className = 'dot error'; }
    try {
      const { events } = await api(`/api/events?since=${lastEventId}&limit=300`);
      if (events.length) { appendLog(events); lastEventId = events[events.length - 1].id; }
    } catch (e) { /* ignore */ }
  }

  // ----- log -----
  const IMPORTANT = new Set(['order_filled', 'position_opened', 'trade_closed', 'risk_event', 'cycle_error', 'reconcile', 'startup', 'ui_start', 'shutdown', 'replay_finished', 'candle_gap', 'kill_switch_on', 'kill_switch_off', 'protective_exit_blocked', 'order_partial_fill', 'candles_missed', 'live_mode']);
  const logBuf = [];
  function appendLog(events) {
    logBuf.push(...events); if (logBuf.length > 2000) logBuf.splice(0, logBuf.length - 2000);
    renderLog(events);
  }
  function fmtEvent(e) {
    const skip = new Set(['id', 'ts', 'level', 'logger', 'msg', 'event', 'config']);
    const rest = Object.entries(e).filter(([k]) => !skip.has(k)).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' ');
    return `<span class="ts">${fmt.ts(e.ts)}</span> <b>${e.event || e.msg}</b> ${rest.length > 400 ? rest.slice(0, 400) + '…' : rest}`;
  }
  function renderLog(events, reset = false) {
    const box = $('log'); if (reset) box.innerHTML = '';
    const important = $('log-important').checked;
    events.forEach((e) => {
      if (important && !(IMPORTANT.has(e.event) || e.level === 'WARNING' || e.level === 'ERROR')) return;
      const div = document.createElement('div'); div.className = e.level; div.innerHTML = fmtEvent(e); box.appendChild(div);
    });
    while (box.children.length > 1500) box.removeChild(box.firstChild);
    if ($('log-follow').checked) box.scrollTop = box.scrollHeight;
  }
  $('log-important').addEventListener('change', () => renderLog(logBuf, true));

  // ----- trades -----
  async function loadTrades() {
    try {
      const { trades } = await api('/api/trades?limit=500');
      $('trades-count').textContent = trades.length ? `${trades.length} trades · total P/L ${fmt.money(trades.reduce((a, t) => a + t.pnl, 0))}` : '';
      const tb = $('trades-table').querySelector('tbody');
      tb.innerHTML = trades.length ? trades.slice().reverse().map((t) => `<tr><td>${fmt.time(t.entry_ts)}</td><td>${fmt.time(t.exit_ts)}</td><td class="num">${fmt.qty(t.qty)}</td><td class="num">${fmt.money(t.entry_price)}</td><td class="num">${fmt.money(t.exit_price)}</td><td class="num">${fmt.money(t.fees)}</td><td class="num ${cls(t.pnl)}">${fmt.money(t.pnl)}</td><td class="num ${cls(t.pnl)}">${fmt.pct(t.pnl_pct)}</td><td>${t.exit_reason}</td></tr>`).join('') : '<tr><td colspan="9" class="muted">No trades yet.</td></tr>';
    } catch (e) { showAlert(e.message); }
  }

  // ----- controls -----
  $('btn-start').addEventListener('click', async () => {
    if (status.live_possible) { $('live-exchange').textContent = `${status.config.exchange} (${status.config.symbol})`; $('live-typed').value = ''; $('live-go').disabled = true; $('confirm-live').showModal(); return; }
    try { await api('/api/start', {}); showAlert('Bot started in paper mode. It fetches real market data; fills are simulated.', 'info'); } catch (e) { showAlert(e.message); }
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
      showAlert(`Demo: replaying ${pick} in paper mode, several candles per second.`, 'info');
    } catch (e) { showAlert(e.message); }
    poll();
  });
  $('btn-stop').addEventListener('click', async () => { try { await api('/api/stop', {}); } catch (e) { showAlert(e.message); } poll(); });
  $('btn-kill').addEventListener('click', async () => {
    const on = !status.kill_switch;
    if (on && !confirm('Turn the kill switch ON? The bot will not place ANY order, including stop-loss exits, until you turn it off.')) return;
    try { await api('/api/kill', { active: on }); } catch (e) { showAlert(e.message); } poll();
  });
  $('btn-quit').addEventListener('click', async () => {
    if (!confirm('Stop the bot and close the app?')) return;
    try { await api('/api/quit', {}); } catch (e) { /* server going away */ }
    document.body.innerHTML = '<main><div class="card"><h3>BTC Bot is shutting down.</h3><p class="muted">You can close this window.</p></div></main>';
  });

  // ----- settings -----
  const FIELD_HELP = {
    'exchange.id': 'ccxt exchange id (binance, kraken, coinbase, bybit, okx…)', 'exchange.symbol': 'BASE/QUOTE', 'exchange.timeframe': 'candle size (1m, 5m, 15m, 1h, 4h, 1d)',
    'exchange.live': 'ON + LIVE_TRADING in keys = real orders', 'exchange.fee_rate': 'taker fee as a fraction, 0.001 = 0.1%', 'exchange.slippage_bps': 'simulated slippage in basis points',
    'risk.risk_per_trade_pct': '% of equity risked between entry and stop', 'risk.max_position_pct': 'max position size as % of equity', 'risk.max_daily_loss_pct': 'stop entering after this daily loss',
    'risk.max_consecutive_losses': 'losses in a row before a cooldown', 'risk.cooldown_minutes': 'cooldown length', 'risk.min_seconds_between_trades': 'minimum spacing between entries',
    'risk.min_confidence': 'signals below this confidence are ignored', 'risk.kill_switch_file': 'file that blocks all orders while it exists', 'paper.initial_cash': 'starting cash for paper trading',
  };
  const SECTIONS = ['exchange', 'strategy', 'risk', 'paper', 'notify', 'logging', 'state'];
  let cfgData = null;
  function renderCfg(cfg) {
    cfgData = cfg; const form = $('cfg-form'); form.innerHTML = '';
    SECTIONS.forEach((sec) => {
      const fs = document.createElement('fieldset'); const lg = document.createElement('legend'); lg.textContent = sec; fs.appendChild(lg);
      const obj = cfg[sec] || {};
      const addField = (path, key, val) => {
        const lab = document.createElement('label'); const help = FIELD_HELP[path];
        lab.innerHTML = `${key}${help ? ` <span class="small">· ${help}</span>` : ''}`;
        let input;
        if (typeof val === 'boolean') { input = document.createElement('input'); input.type = 'checkbox'; input.checked = val; lab.classList.add('check'); }
        else { input = document.createElement('input'); input.type = typeof val === 'number' ? 'number' : 'text'; input.value = val; if (typeof val === 'number') input.step = 'any'; }
        input.dataset.path = path; input.dataset.type = typeof val; lab.appendChild(input); fs.appendChild(lab);
      };
      Object.entries(obj).forEach(([k, v]) => {
        if (k === 'params' && v && typeof v === 'object') Object.entries(v).forEach(([pk, pv]) => addField(`${sec}.params.${pk}`, pk, pv));
        else if (v !== null && typeof v === 'object') return; else addField(`${sec}.${k}`, k, v);
      });
      form.appendChild(fs);
    });
  }
  function collectCfg() {
    const out = JSON.parse(JSON.stringify(cfgData));
    $('cfg-form').querySelectorAll('input[data-path]').forEach((inp) => {
      const parts = inp.dataset.path.split('.'); let cur = out;
      for (let i = 0; i < parts.length - 1; i++) cur = cur[parts[i]];
      const k = parts[parts.length - 1];
      cur[k] = inp.dataset.type === 'boolean' ? inp.checked : inp.dataset.type === 'number' ? Number(inp.value) : inp.value;
    });
    return out;
  }
  async function loadSettings() {
    try { const { config } = await api('/api/config'); renderCfg(config); } catch (e) { showAlert(e.message); }
    try {
      const s = await api('/api/secrets');
      $('sec-key-state').textContent = s.api_key_set ? 'saved' : 'not set'; $('sec-secret-state').textContent = s.api_secret_set ? 'saved' : 'not set';
      $('sec-live').checked = s.live_trading_env; $('notif-state').textContent = `telegram ${s.telegram_set ? 'on' : 'off'} · discord ${s.discord_set ? 'on' : 'off'}`;
    } catch (e) { /* ignore */ }
  }
  $('cfg-save').addEventListener('click', async (e) => { e.preventDefault(); try { const r = await api('/api/config', { config: collectCfg() }); renderCfg(r.config); showAlert('Settings saved. They apply on the next Start.', 'info'); poll(); } catch (err) { showAlert(err.message, 'error', 12000); } });
  $('cfg-reload').addEventListener('click', (e) => { e.preventDefault(); loadSettings(); });
  $('sec-save').addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      await api('/api/secrets', { EXCHANGE_API_KEY: $('sec-key').value, EXCHANGE_API_SECRET: $('sec-secret').value, EXCHANGE_API_PASSWORD: $('sec-password').value, LIVE_TRADING: $('sec-live').checked });
      $('sec-key').value = $('sec-secret').value = $('sec-password').value = ''; showAlert('Keys saved to .env', 'info'); loadSettings(); poll();
    } catch (err) { showAlert(err.message); }
  });
  $('sec-clear').addEventListener('click', async (e) => {
    e.preventDefault(); if (!confirm('Remove the saved API keys and turn LIVE_TRADING off?')) return;
    try { await api('/api/secrets', { EXCHANGE_API_KEY: null, EXCHANGE_API_SECRET: null, EXCHANGE_API_PASSWORD: null, LIVE_TRADING: null }); showAlert('Keys removed.', 'info'); loadSettings(); poll(); } catch (err) { showAlert(err.message); }
  });
  $('notif-save').addEventListener('click', async (e) => {
    e.preventDefault();
    try { await api('/api/secrets', { TELEGRAM_BOT_TOKEN: $('n-tg-token').value, TELEGRAM_CHAT_ID: $('n-tg-chat').value, DISCORD_WEBHOOK_URL: $('n-discord').value }); $('n-tg-token').value = $('n-discord').value = ''; showAlert('Notification settings saved.', 'info'); loadSettings(); } catch (err) { showAlert(err.message); }
  });

  // ----- backtest -----
  async function loadSamples() {
    try {
      const { samples } = await api('/api/samples'); const sel = $('bt-source'); const cur = sel.value; sel.innerHTML = '';
      samples.forEach((s) => { const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o); });
      const dl = document.createElement('option'); dl.value = ''; dl.textContent = 'Download from the configured exchange (needs internet + dates)'; sel.appendChild(dl);
      if (cur) sel.value = cur;
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
    const m = bt.metrics; const tiles = [['Total return', fmt.pct(m.total_return_pct), cls(m.total_return_pct)], ['Buy & hold', fmt.pct(m.buy_hold_return_pct), cls(m.buy_hold_return_pct)], ['Max drawdown', fmt.pct(m.max_drawdown_pct), 'neg'], ['Sharpe', m.sharpe, cls(m.sharpe)], ['Trades', m.trades, ''], ['Win rate', `${m.win_rate_pct}%`, ''], ['Profit factor', m.profit_factor == null ? 'n/a' : m.profit_factor, cls((m.profit_factor || 1) - 1)], ['Exposure', `${m.exposure_pct}%`, '']];
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

  // ----- price chart -----
  const C = { up: '#0ca30c', down: '#d03b3b', emaFast: '#3987e5', emaSlow: '#d95926', grid: '#273241', text: '#8b98a8', last: '#e6edf3', stop: '#d03b3b', tp: '#0ca30c', entry: '#3987e5' };
  const chart = { data: null, visible: 200, timer: null, hover: null, layout: null, live: null, ws: null, wsKey: null, wsRetry: 0, lastDraw: 0 };
  const chartActive = () => document.querySelector('#tab-chart').classList.contains('active');
  async function loadChart() {
    try {
      const limit = Number($('chart-limit').value);
      const d = await api(`/api/candles?limit=${limit}`);
      chart.data = d; chart.visible = Math.min(chart.visible, d.candles.length) || d.candles.length;
      $('chart-empty').classList.add('hidden');
      const src = { feed: 'live feed from the running bot', replay: 'demo replay data', exchange: 'public data, bot stopped' }[d.source] || d.source;
      $('chart-title').textContent = `${d.exchange} · ${d.symbol} · ${d.timeframe}`;
      $('chart-source').textContent = `${src} · ${d.candles.length} candles · updated ${new Date(d.now).toLocaleTimeString()}`;
      if (chart.live && d.candles.length && chart.live.t <= d.candles[d.candles.length - 1][0]) chart.live = null; // now closed
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
    const live = chart.live && (!n || chart.live.t > d.candles[n - 1][0]) ? chart.live : null;
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
    ticks.forEach((p) => { const y = Y(p); ctx.strokeStyle = C.grid; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke(); ctx.fillStyle = C.text; ctx.textAlign = 'left'; ctx.fillText(fmt.money(p, p >= 100 ? 0 : 2), w - pad.r + 6, y); });
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
    const line = (arr, color) => { if (!arr) return; ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath(); let started = false; rows.forEach((r, i) => { const v = arr[start + i]; if (v == null) return; const x = X(i), y = Y(v); started ? ctx.lineTo(x, y) : ctx.moveTo(x, y); started = true; }); ctx.stroke(); };
    if (showEma && d.indicators) { line(d.indicators.ema_fast, C.emaFast); line(d.indicators.ema_slow, C.emaSlow); }
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
    $('chart-tip').innerHTML = `<b>${fmt.time(r[0])}</b><br>O ${fmt.money(r[1])} &nbsp; H ${fmt.money(r[2])}<br>L ${fmt.money(r[3])} &nbsp; C <b class="${cls(chg)}">${fmt.money(r[4])}</b> (${fmt.pct(chg)})<br>Vol ${fmt.money(r[5], 1)}` + (g('ema_fast') != null ? `<br>EMA fast ${fmt.money(g('ema_fast'))} · slow ${fmt.money(g('ema_slow'))}<br>RSI ${g('rsi') == null ? '—' : g('rsi').toFixed(1)} · ATR ${fmt.money(g('atr'))}` : '');
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

  // ----- realtime stream (Binance public WebSocket, no keys) -----
  const BINANCE_WS = 'wss://stream.binance.com:9443/ws/';
  function streamKey(d) { return d.exchange === 'binance' ? `${d.symbol.replace('/', '').split(':')[0].toLowerCase()}@kline_${d.timeframe}` : null; }
  function setLiveBadge(state) { const b = $('chart-live'); b.classList.toggle('hidden', state === 'off'); b.classList.toggle('reconnecting', state === 'reconnecting'); b.textContent = state === 'reconnecting' ? '● reconnecting' : '● LIVE'; }
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
      chart.live = { t: k.t, o: +k.o, h: +k.h, l: +k.l, c: +k.c, v: +k.v, closed: !!k.x };
      $('t-price').textContent = fmt.money(+k.c);
      if (k.x) setTimeout(loadChart, 1500); // candle closed: fetch it with indicators from the bot
      const now = performance.now(); if (now - chart.lastDraw > 250 && chartActive()) { chart.lastDraw = now; renderChart(); }
    };
    ws.onclose = () => { if (chart.ws !== ws) return; chart.ws = null; if (!chartActive()) { setLiveBadge('off'); return; } setLiveBadge('reconnecting'); const delay = Math.min(30000, 1000 * Math.pow(2, chart.wsRetry++)); setTimeout(() => { if (chartActive() && chart.data) connectStream(chart.data); }, delay); };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* ignore */ } };
  }
  function disconnectStream() { if (chart.ws) { const ws = chart.ws; chart.ws = null; try { ws.close(); } catch (e) { /* ignore */ } } chart.wsKey = null; setLiveBadge('off'); }

  // ----- boot -----
  poll(); setInterval(poll, 2000); setInterval(chartTick, 10000);
  window.addEventListener('resize', () => { loadEquity(); renderChart(); });
})();
