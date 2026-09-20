const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const pct = (x, d=1) => x == null ? '—' : `${(Number(x)*100).toFixed(d)}%`;
const num = (x, d=2) => x == null ? '—' : Number(x).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtDate = (s) => s ? new Date(`${s}T00:00:00`).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'}) : '—';
const signedPct = (x, d=1) => {
  if(x == null || Number.isNaN(Number(x))) return '—';
  const v = Number(x) * 100;
  return `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;
};
const signedPoints = (x, d=1) => {
  if(x == null || Number.isNaN(Number(x))) return '—';
  const v = Number(x);
  return `${v >= 0 ? '+' : ''}${v.toFixed(d)}`;
};

function signalText(s){
  if(s === 'BUY') return 'Conditions support adding exposure.';
  if(s === 'HOLD') return 'Neutral for fresh allocation.';
  return 'Unfavourable for fresh allocation.';
}

function displayQuintile(q){
  const map = {
    'Q1 Cheapest':'Cheapest 20%',
    'Q1':'Cheapest 20%',
    'Q2':'20–40%',
    'Q3':'40–60%',
    'Q4':'60–80%',
    'Q5 Most Expensive':'Most expensive 20%',
    'Q5 Most expensive':'Most expensive 20%',
    'Q5':'Most expensive 20%'
  };
  return map[q] || q || '—';
}

function csvParse(text){
  const lines = text.trim().split(/\r?\n/);
  if(lines.length < 2) return [];
  const heads = lines[0].split(',');
  return lines.slice(1).filter(Boolean).map(line => {
    const a = line.split(',');
    const o = {};
    heads.forEach((h, i) => o[h] = a[i] ?? '');
    return o;
  });
}

function setFreshness(asOf){
  const host = $('freshness');
  if(!host) return;
  const d = new Date(`${asOf}T00:00:00`);
  const now = new Date();
  const days = Math.max(0, Math.floor((now - d) / 86400000));
  host.classList.remove('fresh','stale');
  host.classList.add(days <= 3 ? 'fresh' : 'stale');
  host.querySelector('span:last-child').textContent = `${fmtDate(asOf)} · NSE close`;
}

function setDialCallouts(signal){
  const map = {SELL:'calloutSell', HOLD:'calloutHold', BUY:'calloutBuy'};
  ['calloutSell','calloutHold','calloutBuy'].forEach(id => {
    const el = $(id);
    if(el) el.classList.remove('active');
  });
  const active = $(map[signal]);
  if(active) active.classList.add('active');
  const dial = document.querySelector('.score-dial');
  if(dial) dial.dataset.signal = (signal || 'HOLD').toLowerCase();
}

function setSignalTone(signal){
  document.body.dataset.signal = (signal || 'HOLD').toLowerCase();
  if($('score')) $('score').className = `signal-score ${signal}`;
  if($('stickyScore')) $('stickyScore').className = `sticky-score ${signal}`;
  setDialCallouts(signal);

  try{
    const previous = localStorage.getItem('niftySignal');
    if(previous && previous !== signal){
      document.body.classList.add('signal-shift');
      window.setTimeout(() => document.body.classList.remove('signal-shift'), 1300);
    }
    localStorage.setItem('niftySignal', signal);
  }catch(_e){}
}

function animateNumber(el, target, duration = 720, decimals = 1){
  if(!el || !Number.isFinite(Number(target))) return;
  const final = Number(target);
  const start = performance.now();
  const ease = t => 1 - Math.pow(1 - t, 3);
  const frame = now => {
    const t = clamp((now - start) / duration, 0, 1);
    el.textContent = (final * ease(t)).toFixed(decimals);
    if(t < 1) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function setDial(score){
  const s = clamp(Number(score), 0, 100);
  const theta = Math.PI - (s / 100) * Math.PI;
  const cx = 110 + 80 * Math.cos(theta);
  const cy = 110 - 80 * Math.sin(theta);
  ['dialMarker','dialMarkerHalo'].forEach(id => {
    const el = $(id);
    if(!el) return;
    el.setAttribute('cx', cx.toFixed(2));
    el.setAttribute('cy', cy.toFixed(2));
  });
}

function setHeroDistance(latest, bh, hs){
  const score = Number(latest.composite_score);
  const signal = latest.signal;
  let buffer = '—';
  if(signal === 'BUY') buffer = `+${Math.max(0, score - 70).toFixed(1)} pts vs 70`;
  else if(signal === 'SELL') buffer = `${Math.max(0, 40 - score).toFixed(1)} pts below 40`;
  else {
    const toSell = Math.max(0, score - 40);
    const toBuy = Math.max(0, 70 - score);
    buffer = `${Math.min(toSell, toBuy).toFixed(1)} pts to nearest edge`;
  }
  if($('scoreBuffer')) $('scoreBuffer').textContent = buffer;

  let boundary = '—';
  if(bh && hs){
    if(signal === 'BUY') boundary = `${Number(bh.pe).toFixed(2)}× · ${signedPct(bh.move_from_current)}`;
    else if(signal === 'SELL') boundary = `${Number(hs.pe).toFixed(2)}× · ${signedPct(hs.move_from_current)}`;
    else {
      const buyMove = Math.abs(Number(bh.move_from_current));
      const sellMove = Math.abs(Number(hs.move_from_current));
      boundary = buyMove <= sellMove
        ? `${Number(bh.pe).toFixed(2)}× · ${signedPct(bh.move_from_current)}`
        : `${Number(hs.pe).toFixed(2)}× · ${signedPct(hs.move_from_current)}`;
    }
  }
  if($('nextBoundary')) $('nextBoundary').textContent = boundary;
}

function setStrip(containerIds, current, bh, hs){
  if(!bh || !hs) return;
  const values = [Number(current), Number(bh.pe), Number(hs.pe)];
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(.5, hi - lo);
  const min = lo - span * .28;
  const max = hi + span * .20;
  const pos = v => clamp((Number(v) - min) / (max - min) * 100, 0, 100);
  const pBh = pos(bh.pe), pHs = pos(hs.pe), pCur = pos(current);

  const bhTick = $(containerIds.buyHoldTick);
  const hsTick = $(containerIds.holdSellTick);
  const curTick = $(containerIds.currentTick);
  const minEl = $(containerIds.min);
  const maxEl = $(containerIds.max);
  const wrap = $(containerIds.wrap);

  if(bhTick) bhTick.style.left = `${pBh}%`;
  if(hsTick) hsTick.style.left = `${pHs}%`;
  if(curTick){
    curTick.style.left = `${pCur}%`;
    const s = curTick.querySelector('span');
    if(s) s.innerHTML = `Current <b>${Number(current).toFixed(2)}×</b>`;
  }

  const zones = wrap?.querySelector('.boundary-zones');
  if(zones && zones.children.length >= 3){
    zones.children[0].style.width = `${pBh}%`;
    zones.children[1].style.width = `${Math.max(0, pHs - pBh)}%`;
    zones.children[2].style.width = `${Math.max(0, 100 - pHs)}%`;
  }
  if(minEl) minEl.textContent = `${min.toFixed(1)}×`;
  if(maxEl) maxEl.textContent = `${max.toFixed(1)}×`;
}

function heatStyle(v, type = 'return', current = false){
  if(v == null || Number.isNaN(Number(v))) return '';
  const n = Number(v);
  const boost = current ? 1.30 : .74;
  if(type === 'loss'){
    const a = clamp(Math.abs(n) / .25 * .20 * boost, .02, current ? .24 : .14);
    return `background:rgba(239,68,68,${a.toFixed(3)})`;
  }
  const positive = n >= 0;
  const a = clamp(Math.abs(n) / .35 * .22 * boost, .018, current ? .26 : .15);
  return `background:${positive ? `rgba(34,197,94,${a.toFixed(3)})` : `rgba(239,68,68,${a.toFixed(3)})`}`;
}

function sparkRows(history, latest){
  const rows = [...history];
  if(!rows.length || rows[rows.length-1].date !== latest.as_of){
    rows.push({
      date: latest.as_of,
      nifty_close: latest.nifty_close,
      pe: latest.pe,
      pe_percentile: latest.pe_percentile,
      yoy_eps_growth: latest.yoy_eps_growth,
      composite_score: latest.composite_score,
      signal: latest.signal
    });
  }
  return rows.slice(-30);
}

function drawSparkline(hostId, rows, key){
  const host = $(hostId);
  if(!host) return;
  const vals = rows.filter(r => r[key] !== '' && r[key] != null).map(r => Number(r[key])).filter(Number.isFinite);
  if(!vals.length){ host.innerHTML = ''; return; }
  const W = 160, H = 34, p = 3;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if(lo === hi){ lo -= 1; hi += 1; }
  const pad = (hi - lo) * .18;
  lo -= pad; hi += pad;
  const x = i => p + (vals.length === 1 ? (W - 2 * p) : i / (vals.length - 1) * (W - 2 * p));
  const y = v => p + (hi - v) / (hi - lo) * (H - 2 * p);
  const path = vals.map((v, i) => `${i ? 'L' : 'M'} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const lastX = x(vals.length - 1), lastY = y(vals[vals.length - 1]);
  const area = vals.length > 1 ? `${path} L ${lastX.toFixed(1)} ${(H-p).toFixed(1)} L ${x(0).toFixed(1)} ${(H-p).toFixed(1)} Z` : '';
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path class="spark-area" d="${area}"></path><path class="spark-path" d="${path}"></path><circle class="spark-dot" cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="2.4"></circle></svg>`;
}

function renderSparklines(history, latest){
  const rows = sparkRows(history, latest);
  const ids = ['sparkNifty','sparkPe','sparkPercentile','sparkEps'];
  const ready = rows.length >= 5;

  ids.forEach(id => {
    const host = $(id);
    if(!host) return;
    const card = host.closest('.spark-card');
    if(card) card.classList.toggle('sparkline-ready', ready);
    if(!ready) host.innerHTML = '';
  });

  if(!ready) return;
  drawSparkline('sparkNifty', rows, 'nifty_close');
  drawSparkline('sparkPe', rows, 'pe');
  drawSparkline('sparkPercentile', rows, 'pe_percentile');
  drawSparkline('sparkEps', rows, 'yoy_eps_growth');
}

function drawChart(rows){
  const host = $('scoreHistoryHost');
  if(!host) return;
  const data = rows.filter(r => r.composite_score !== '').slice(-120).map(r => ({d:r.date, v:+r.composite_score, s:r.signal}));
  if(!data.length){
    host.innerHTML = '<div class="empty-history">History will build automatically after each cloud refresh.</div>';
    return;
  }
  const W = 760, H = 210, p = {l:34, r:12, t:12, b:28}, iw = W-p.l-p.r, ih = H-p.t-p.b;
  const x = i => p.l + (data.length === 1 ? iw/2 : (i/(data.length-1))*iw);
  const y = v => p.t + (100-v)/100*ih;
  const path = data.map((o,i) => `${i?'L':'M'} ${x(i).toFixed(1)} ${y(o.v).toFixed(1)}`).join(' ');
  const zoneRect = (top,bottom,fill) => `<rect x="${p.l}" y="${y(top)}" width="${iw}" height="${Math.max(0, y(bottom)-y(top))}" fill="${fill}"/>`;
  const zones = zoneRect(100,70,'rgba(34,197,94,.045)') + zoneRect(70,40,'rgba(245,158,11,.035)') + zoneRect(40,0,'rgba(239,68,68,.035)');
  const grid = [40,70].map(v => `<line x1="${p.l}" x2="${W-p.r}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(255,255,255,.10)" stroke-dasharray="4 5"/><text x="6" y="${y(v)+3}" fill="#617791" font-size="9">${v}</text>`).join('');
  const circles = data.map((o,i) => i===0 || i===data.length-1 || o.s!==data[i-1].s ? `<circle cx="${x(i)}" cy="${y(o.v)}" r="${i===data.length-1?3.8:2.7}" fill="#f3f7fc" opacity="${i===data.length-1?1:.82}"/>` : '').join('');
  const start = fmtDate(data[0].d), end = fmtDate(data[data.length-1].d);
  host.innerHTML = `<div class="chart" role="img" aria-label="Composite model score history"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${zones}${grid}<path d="${path}" fill="none" stroke="#78b5ff" stroke-width="2.2" vector-effect="non-scaling-stroke"/>${circles}<text x="${p.l}" y="${H-4}" fill="#617791" font-size="9">${start}</text><text x="${W-p.r}" y="${H-4}" text-anchor="end" fill="#617791" font-size="9">${end}</text></svg></div>`;
}

function countCurrentSignalDays(rows, currentSignal){
  let count = 0;
  for(let i = rows.length-1; i >= 0; i--){
    if(rows[i].signal === currentSignal) count++;
    else break;
  }
  return Math.max(1, count);
}

function renderScoreHistory(rows, latest){
  const clean = rows.filter(r => r.composite_score !== '' && !Number.isNaN(Number(r.composite_score)));
  const host = $('scoreHistoryHost');
  const mode = $('scoreHistoryMode');
  if(!host || !mode) return;
  const minPointsForChart = 10;

  if(clean.length >= minPointsForChart){
    mode.textContent = `${clean.length} observations`;
    mode.className = 'history-mode ready';
    drawChart(clean);
    return;
  }

  mode.textContent = `${clean.length}/${minPointsForChart} observations`;
  mode.className = 'history-mode';

  const current = Number(latest.composite_score);
  const change5 = clean.length >= 6 ? current - Number(clean[clean.length-6].composite_score) : null;
  const range20 = clean.length >= 20 ? clean.slice(-20).map(r => Number(r.composite_score)) : null;
  const low20 = range20 ? Math.min(...range20) : null;
  const high20 = range20 ? Math.max(...range20) : null;
  const days = countCurrentSignalDays(clean.length ? clean : [{signal:latest.signal}], latest.signal);
  const signal = latest.signal;

  host.innerHTML = `
    <div class="score-summary">
      <div class="summary-score-top">
        <div class="summary-score ${signal}">${current.toFixed(1)}</div>
      </div>
      <div class="summary-stat-grid">
        <div class="summary-stat"><span>5-day change</span><strong>${change5==null?'—':signedPoints(change5,1)}</strong></div>
        <div class="summary-stat"><span>20-day range</span><strong>${range20?`${low20.toFixed(1)}–${high20.toFixed(1)}`:'—'}</strong></div>
        <div class="summary-stat"><span>Days in current signal</span><strong>${days}</strong></div>
      </div>
      <div class="summary-score-track" aria-label="Current composite score ${current.toFixed(1)} out of 100">
        <div class="summary-zones"><span></span><span></span><span></span></div>
        <div class="summary-fill" style="width:${clamp(current,0,100)}%"></div>
        <div class="summary-marker" style="left:${clamp(current,0,100)}%"></div>
      </div>
      <div class="summary-score-labels"><span>&lt;40</span><span>40–70</span><span>≥70</span></div>
    </div>`;
}

function installStickyBehavior(){
  const sticky = $('stickySummary');
  const hero = $('hero');
  if(!sticky || !hero) return;
  const update = () => {
    const show = hero.getBoundingClientRect().bottom < 24;
    sticky.classList.toggle('visible', show);
    sticky.setAttribute('aria-hidden', show ? 'false' : 'true');
  };
  update();
  window.addEventListener('scroll', update, {passive:true});
  window.addEventListener('resize', update, {passive:true});
}

function activateTab(name){
  document.querySelectorAll('.tab-button').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.toggle('active', panel.dataset.panel === name));
  try{ localStorage.setItem('niftyActiveTab', name); }catch(_e){}
}

function initTabs(){
  document.querySelectorAll('.tab-button').forEach(btn => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });
  let saved = 'overview';
  try{
    const fromStorage = localStorage.getItem('niftyActiveTab');
    if(fromStorage) saved = fromStorage;
  }catch(_e){}
  activateTab(saved);
}

async function boot(){
  initTabs();
  try{
    const [latest, historyText, bt] = await Promise.all([
      fetch('data/latest.json',{cache:'no-store'}).then(r => { if(!r.ok) throw new Error('latest.json'); return r.json(); }),
      fetch('data/history.csv',{cache:'no-store'}).then(r => r.ok ? r.text() : ''),
      fetch('data/backtest_summary.json',{cache:'no-store'}).then(r => { if(!r.ok) throw new Error('backtest'); return r.json(); })
    ]);
    const history = historyText ? csvParse(historyText) : [];

    setFreshness(latest.as_of);
    setSignalTone(latest.signal);
    if($('signalText')) $('signalText').textContent = signalText(latest.signal);
    animateNumber($('score'), latest.composite_score, 760, 1);
    if($('stickyScore')) $('stickyScore').textContent = Number(latest.composite_score).toFixed(1);
    setDial(latest.composite_score);
    const sc = clamp(+latest.composite_score, 0, 100);
    if($('scoreFill')) $('scoreFill').style.width = `${sc}%`;
    if($('scoreMarker')) $('scoreMarker').style.left = `${sc}%`;

    if($('nifty')) $('nifty').textContent = num(latest.nifty_close, 2);
    if($('pe')) $('pe').textContent = `${Number(latest.pe).toFixed(2)}×`;
    if($('peVsMedian')) $('peVsMedian').textContent = `vs median ${Number(latest.era_median_pe).toFixed(2)}×`;
    const peDelta = Number(latest.pe) / Number(latest.era_median_pe) - 1;
    if($('peDelta')){
      $('peDelta').textContent = signedPct(peDelta);
      $('peDelta').classList.add(peDelta <= 0 ? 'positive' : 'negative');
    }
    if($('percentile')) $('percentile').textContent = pct(latest.pe_percentile, 1);
    if($('percentileMarker')) $('percentileMarker').style.left = `${clamp(+latest.pe_percentile * 100, 0, 100)}%`;
    if($('quintile')) $('quintile').textContent = displayQuintile(latest.valuation_quintile);
    if($('epsGrowth')) $('epsGrowth').textContent = pct(latest.yoy_eps_growth, 1);

    if($('stickyNifty')) $('stickyNifty').textContent = num(latest.nifty_close, 0);
    if($('stickyPe')) $('stickyPe').textContent = `${Number(latest.pe).toFixed(2)}×`;

    if($('valuationScore')) $('valuationScore').textContent = Number(latest.valuation_score).toFixed(1);
    if($('growthScore')) $('growthScore').textContent = Number(latest.growth_score).toFixed(1);
    if($('valuationBar')) $('valuationBar').style.width = `${clamp(+latest.valuation_score, 0, 100)}%`;
    if($('growthBar')) $('growthBar').style.width = `${clamp(+latest.growth_score, 0, 100)}%`;
    if($('earningsYield')) $('earningsYield').textContent = pct(latest.earnings_yield, 2);
    if($('medianPe')) $('medianPe').textContent = `${Number(latest.era_median_pe).toFixed(2)}×`;
    if($('impliedEps')) $('impliedEps').textContent = num(latest.implied_eps, 1);

    const bounds = latest.signal_boundaries || {}, bh = bounds.buy_hold, hs = bounds.hold_sell;
    setHeroDistance(latest, bh, hs);

    if($('currentBoundaryPe')) $('currentBoundaryPe').textContent = `${Number(latest.pe).toFixed(2)}× P/E`;
    if($('currentBoundaryNifty')) $('currentBoundaryNifty').textContent = `NIFTY ${num(latest.nifty_close, 0)}`;
    if($('ovCurrentPe')) $('ovCurrentPe').textContent = `${Number(latest.pe).toFixed(2)}×`;

    if(bh && hs){
      if($('buyHoldPe')) $('buyHoldPe').textContent = `${Number(bh.pe).toFixed(2)}× P/E`;
      if($('buyHoldNifty')) $('buyHoldNifty').textContent = `NIFTY ${num(bh.nifty_level, 0)}`;
      if($('buyHoldMove')) $('buyHoldMove').textContent = `${signedPct(bh.move_from_current)} vs current`;
      if($('holdSellPe')) $('holdSellPe').textContent = `${Number(hs.pe).toFixed(2)}× P/E`;
      if($('holdSellNifty')) $('holdSellNifty').textContent = `NIFTY ${num(hs.nifty_level, 0)}`;
      if($('holdSellMove')) $('holdSellMove').textContent = `${signedPct(hs.move_from_current)} vs current`;
      if($('ovBuyHoldPe')) $('ovBuyHoldPe').textContent = `${Number(bh.pe).toFixed(2)}×`;
      if($('ovHoldSellPe')) $('ovHoldSellPe').textContent = `${Number(hs.pe).toFixed(2)}×`;

      setStrip({
        wrap:'boundaryStrip', buyHoldTick:'buyHoldTick', holdSellTick:'holdSellTick', currentTick:'currentPeTick', min:'boundaryMin', max:'boundaryMax'
      }, latest.pe, bh, hs);
      setStrip({
        wrap:'overviewBoundaryStrip', buyHoldTick:'ovBuyHoldTick', holdSellTick:'ovHoldSellTick', currentTick:'ovCurrentPeTick', min:'ovBoundaryMin', max:'ovBoundaryMax'
      }, latest.pe, bh, hs);
    } else {
      if($('boundaryCard')) $('boundaryCard').classList.add('unavailable');
    }

    if($('backtestBody')) $('backtestBody').innerHTML = bt.rows.map(r => {
      const current = r.quintile === latest.valuation_quintile;
      const label = `${displayQuintile(r.quintile)}${current?'<span class="current-badge">CURRENT</span>':''}`;
      return `<tr class="${current?'current':''}"><td>${label}</td><td class="heat" style="${heatStyle(r.median_1y,'return',current)}">${pct(r.median_1y)}</td><td class="heat" style="${heatStyle(r.median_3y,'return',current)}">${pct(r.median_3y)}</td><td class="heat" style="${heatStyle(r.median_5y,'return',current)}">${pct(r.median_5y)}</td><td class="heat" style="${heatStyle(r.median_10y,'return',current)}">${pct(r.median_10y)}</td><td class="heat" style="${heatStyle(r.loss_3y,'loss',current)}">${pct(r.loss_3y)}</td><td>${r.n_3y}</td></tr>`;
    }).join('');

    const logRows = history.length ? history.slice(-5).reverse() : [{date:latest.as_of, pe:latest.pe, composite_score:latest.composite_score, signal:latest.signal}];
    if($('historyBody')) $('historyBody').innerHTML = logRows.map(r => `<tr><td>${fmtDate(r.date)}</td><td>${Number(r.pe).toFixed(2)}×</td><td class="log-score ${r.signal}">${Number(r.composite_score).toFixed(1)}</td><td><span class="badge ${r.signal}">${r.signal}</span></td></tr>`).join('');

    renderSparklines(history, latest);
    renderScoreHistory(history.length ? history : [{date:latest.as_of, composite_score:latest.composite_score, signal:latest.signal}], latest);
    installStickyBehavior();
  }catch(e){
    document.querySelector('.shell').insertAdjacentHTML('afterbegin', '<div class="error">Dashboard data could not be loaded. Open the repository Actions tab and run “Refresh NIFTY tracker and deploy Pages” manually.</div>');
    console.error(e);
  }
}
boot();
