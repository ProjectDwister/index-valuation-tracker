const $ = id => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const pct = (x, d=1) => x == null || Number.isNaN(Number(x)) ? '—' : `${(Number(x)*100).toFixed(d)}%`;
const num = (x, d=2) => x == null || Number.isNaN(Number(x)) ? '—' : Number(x).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtDate = s => s ? new Date(`${s}T00:00:00`).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'}) : '—';
const signedPct = (x, d=1) => x == null || Number.isNaN(Number(x)) ? '—' : `${Number(x)>=0?'+':''}${(Number(x)*100).toFixed(d)}%`;
const signedPoints = (x, d=1) => x == null || Number.isNaN(Number(x)) ? '—' : `${Number(x)>=0?'+':''}${Number(x).toFixed(d)}`;

let catalog = null;
let latestBundle = null;
let backtestBundle = null;
let allHistory = [];
let selectedSlug = null;

function displayQuintile(q){
  const map={'Q1 Cheapest':'Cheapest 20%','Q2':'20–40%','Q3':'40–60%','Q4':'60–80%','Q5 Most Expensive':'Most expensive 20%'};
  return map[q] || q || '—';
}

function csvParse(text){
  const lines = String(text||'').trim().split(/\r?\n/);
  if(lines.length<2) return [];
  const heads=lines[0].split(',');
  return lines.slice(1).filter(Boolean).map(line=>{
    const a=line.split(','); const o={}; heads.forEach((h,i)=>o[h]=a[i]??''); return o;
  });
}

let marketStatusTimer = null;
let nseHolidayCalendar = {covered_years:[], holidays:{}};

async function loadHolidayCalendar(){
  try{
    const r=await fetch('data/nse_market_holidays.json',{cache:'no-store'});
    if(!r.ok) throw new Error(`holiday calendar HTTP ${r.status}`);
    const data=await r.json();
    if(data && typeof data==='object'){
      nseHolidayCalendar={
        covered_years:Array.isArray(data.covered_years)?data.covered_years.map(Number):[],
        holidays:(data.holidays && typeof data.holidays==='object')?data.holidays:{},
        regular_session:data.regular_session||{open:'09:15',close:'15:30'}
      };
    }
  }catch(e){
    // Fail conservatively: without a calendar for the current year the indicator
    // will remain red rather than incorrectly claiming that the market is open.
    console.warn('NSE holiday calendar could not be loaded.',e);
  }
}

function istParts(now = new Date()){
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone:'Asia/Kolkata',
    year:'numeric', month:'2-digit', day:'2-digit', weekday:'short',
    hour:'2-digit', minute:'2-digit', hourCycle:'h23'
  }).formatToParts(now);
  const get = type => parts.find(p => p.type === type)?.value;
  return {
    year:get('year'), month:get('month'), day:get('day'), weekday:get('weekday'),
    hour:Number(get('hour')), minute:Number(get('minute'))
  };
}

function nseCalendarCovers(now = new Date()){
  const p=istParts(now);
  return nseHolidayCalendar.covered_years.includes(Number(p.year));
}

function nseHolidayName(now = new Date()){
  const p=istParts(now);
  const key=`${p.year}-${p.month}-${p.day}`;
  return nseHolidayCalendar.holidays[key] || null;
}

function nseMarketIsOpen(now = new Date()){
  // NSE regular cash-market session: Monday-Friday, 09:15-15:30 India time,
  // excluding holidays listed in docs/data/nse_market_holidays.json.
  const p=istParts(now);
  if(!nseCalendarCovers(now)) return false;
  if(!['Mon','Tue','Wed','Thu','Fri'].includes(p.weekday)) return false;
  if(nseHolidayName(now)) return false;
  const minutes = p.hour * 60 + p.minute;
  return minutes >= (9 * 60 + 15) && minutes < (15 * 60 + 30);
}

function updateMarketStatusDot(){
  const host=$('freshness'); if(!host) return;
  const now=new Date();
  const covered=nseCalendarCovers(now);
  const open=nseMarketIsOpen(now);
  const holiday=nseHolidayName(now);
  const statusText=$('marketStatusText');
  host.classList.remove('fresh','stale','market-open','market-closed');
  host.classList.add(open ? 'market-open' : 'market-closed');
  const dot=host.querySelector('.status-dot');
  if(dot){
    dot.style.background=open ? '#22c55e' : '#ef4444';
    dot.style.boxShadow=open ? '0 0 0 4px rgba(34,197,94,.13)' : '0 0 0 4px rgba(239,68,68,.13)';
  }
  let closedReason='NSE regular market is closed';
  if(!covered) closedReason=`NSE holiday calendar is not configured for ${istParts(now).year}`;
  else if(holiday) closedReason=`NSE market is closed — ${holiday}`;
  if(statusText){
    if(open) statusText.textContent='Market Open · closes 3:30 PM';
    else if(!covered) statusText.textContent='Market Closed · calendar update required';
    else if(holiday) statusText.textContent=`Market Closed · ${holiday}`;
    else statusText.textContent='Market Closed';
  }
  host.title=open ? 'NSE regular market is open' : closedReason;
  host.setAttribute('aria-label', open ? 'NSE regular market open' : closedReason);
}

function setFreshness(asOf){
  const host=$('freshness'); if(!host) return;
  const dateEl=$('freshnessDate');
  if(dateEl) dateEl.textContent=`Latest close ${fmtDate(asOf)}`;
  updateMarketStatusDot();
  if(!marketStatusTimer){
    marketStatusTimer=window.setInterval(updateMarketStatusDot,30000);
  }
}

function setDialCallouts(signal){
  const map={SELL:'calloutSell',HOLD:'calloutHold',BUY:'calloutBuy'};
  ['calloutSell','calloutHold','calloutBuy'].forEach(id=>$(id)?.classList.remove('active'));
  if(signal && map[signal]) $(map[signal])?.classList.add('active');
}

function setSignalTone(signal){
  if(signal){
    document.body.dataset.signal=signal.toLowerCase();
    $('score').className=`signal-score ${signal}`;
    $('stickyScore').className=`sticky-score ${signal}`;
  } else {
    document.body.removeAttribute('data-signal');
    $('score').className='signal-score';
    $('stickyScore').className='sticky-score';
  }
  setDialCallouts(signal);
}

function animateNumber(el,target,duration=650,decimals=1){
  if(!el || !Number.isFinite(Number(target))){ if(el) el.textContent='—'; return; }
  const final=Number(target), start=performance.now(), ease=t=>1-Math.pow(1-t,3);
  const frame=now=>{const t=clamp((now-start)/duration,0,1);el.textContent=(final*ease(t)).toFixed(decimals);if(t<1)requestAnimationFrame(frame);};
  requestAnimationFrame(frame);
}

function setDial(score){
  const s=Number.isFinite(Number(score))?clamp(Number(score),0,100):50;
  const theta=Math.PI-(s/100)*Math.PI;
  const cx=110+80*Math.cos(theta), cy=110-80*Math.sin(theta);
  ['dialMarker','dialMarkerHalo'].forEach(id=>{const el=$(id);if(el){el.setAttribute('cx',cx.toFixed(2));el.setAttribute('cy',cy.toFixed(2));el.style.opacity=Number.isFinite(Number(score))?'1':'.25';}});
}

function scoreBufferText(x){
  if(x.composite_score==null || !x.signal) return '—';
  const s=Number(x.composite_score);
  if(x.signal==='BUY') return `+${Math.max(0,s-70).toFixed(1)} pts vs 70`;
  if(x.signal==='SELL') return `${Math.max(0,40-s).toFixed(1)} pts below 40`;
  return `${Math.min(Math.max(0,s-40),Math.max(0,70-s)).toFixed(1)} pts to nearest edge`;
}

function nextBoundaryText(x){
  const b=x.signal_boundaries; if(!b || !x.signal) return '—';
  if(x.signal==='BUY') return `${Number(b.buy_hold.pe).toFixed(2)}× · ${signedPct(b.buy_hold.move_from_current)}`;
  if(x.signal==='SELL') return `${Number(b.hold_sell.pe).toFixed(2)}× · ${signedPct(b.hold_sell.move_from_current)}`;
  const a=b.buy_hold,c=b.hold_sell;
  return Math.abs(Number(a.move_from_current))<=Math.abs(Number(c.move_from_current))?`${Number(a.pe).toFixed(2)}× · ${signedPct(a.move_from_current)}`:`${Number(c.pe).toFixed(2)}× · ${signedPct(c.move_from_current)}`;
}

function setStrip(ids,current,bh,hs){
  if(!bh || !hs || current==null){
    [ids.buyHoldTick,ids.holdSellTick,ids.currentTick].forEach(id=>{const el=$(id);if(el)el.style.display='none';});
    return;
  }
  [ids.buyHoldTick,ids.holdSellTick,ids.currentTick].forEach(id=>{const el=$(id);if(el)el.style.display='block';});
  const vals=[Number(current),Number(bh.pe),Number(hs.pe)],lo=Math.min(...vals),hi=Math.max(...vals),span=Math.max(.5,hi-lo),min=lo-span*.28,max=hi+span*.20;
  const pos=v=>clamp((Number(v)-min)/(max-min)*100,0,100),pBh=pos(bh.pe),pHs=pos(hs.pe),pCur=pos(current);
  $(ids.buyHoldTick).style.left=`${pBh}%`;$(ids.holdSellTick).style.left=`${pHs}%`;$(ids.currentTick).style.left=`${pCur}%`;
  const curSpan=$(ids.currentTick).querySelector('span'); if(curSpan)curSpan.innerHTML=`Current <b>${Number(current).toFixed(2)}×</b>`;
  const zones=$(ids.wrap)?.querySelector('.boundary-zones');
  if(zones){zones.children[0].style.width=`${pBh}%`;zones.children[1].style.width=`${Math.max(0,pHs-pBh)}%`;zones.children[2].style.width=`${Math.max(0,100-pHs)}%`;}
  $(ids.min).textContent=`${min.toFixed(1)}×`;$(ids.max).textContent=`${max.toFixed(1)}×`;
}

function sparkRows(slug,current){
  const rows=allHistory.filter(r=>r.slug===slug);
  if(!rows.length || rows[rows.length-1].date!==current.as_of) rows.push({date:current.as_of,close:current.nifty_close,pe:current.pe,pe_percentile:current.pe_percentile,yoy_eps_growth:current.yoy_eps_growth,composite_score:current.composite_score,signal:current.signal});
  return rows.slice(-30);
}

function drawSparkline(id,rows,key){
  const host=$(id); if(!host)return;
  const vals=rows.map(r=>Number(r[key])).filter(Number.isFinite); if(!vals.length){host.innerHTML='';return;}
  const W=160,H=34,p=3;let lo=Math.min(...vals),hi=Math.max(...vals);if(lo===hi){lo-=1;hi+=1;}const pad=(hi-lo)*.18;lo-=pad;hi+=pad;
  const x=i=>p+(vals.length===1?(W-2*p):i/(vals.length-1)*(W-2*p)),y=v=>p+(hi-v)/(hi-lo)*(H-2*p);
  const path=vals.map((v,i)=>`${i?'L':'M'} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' '),lx=x(vals.length-1),ly=y(vals[vals.length-1]);
  const area=vals.length>1?`${path} L ${lx.toFixed(1)} ${(H-p).toFixed(1)} L ${x(0).toFixed(1)} ${(H-p).toFixed(1)} Z`:'';
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path class="spark-area" d="${area}"></path><path class="spark-path" d="${path}"></path><circle class="spark-dot" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="2.4"></circle></svg>`;
}

function renderSparklines(slug,current){
  const rows=sparkRows(slug,current),ready=rows.length>=5;
  ['sparkNifty','sparkPe','sparkPercentile','sparkEps'].forEach(id=>{const h=$(id);const card=h?.closest('.spark-card');if(card)card.classList.toggle('sparkline-ready',ready);if(h&&!ready)h.innerHTML='';});
  if(!ready)return;
  drawSparkline('sparkNifty',rows,'close');drawSparkline('sparkPe',rows,'pe');drawSparkline('sparkPercentile',rows,'pe_percentile');drawSparkline('sparkEps',rows,'yoy_eps_growth');
}

function renderHistory(slug,current){
  const rows=allHistory.filter(r=>r.slug===slug && r.composite_score!=='').slice(-120);
  const host=$('scoreHistoryHost'),mode=$('scoreHistoryMode');
  const minPoints=10;
  if(rows.length>=minPoints){
    mode.textContent=`${rows.length} observations`; mode.className='history-mode';
    const W=760,H=210,p={l:34,r:12,t:12,b:28},iw=W-p.l-p.r,ih=H-p.t-p.b;
    const x=i=>p.l+(rows.length===1?iw/2:i/(rows.length-1)*iw),y=v=>p.t+(100-v)/100*ih;
    const path=rows.map((o,i)=>`${i?'L':'M'} ${x(i).toFixed(1)} ${y(Number(o.composite_score)).toFixed(1)}`).join(' ');
    const grid=[40,70].map(v=>`<line x1="${p.l}" x2="${W-p.r}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(255,255,255,.10)" stroke-dasharray="4 5"/><text x="6" y="${y(v)+3}" fill="#617791" font-size="9">${v}</text>`).join('');
    host.innerHTML=`<div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}<path d="${path}" fill="none" stroke="#78b5ff" stroke-width="2.2" vector-effect="non-scaling-stroke"/><circle cx="${x(rows.length-1)}" cy="${y(Number(rows[rows.length-1].composite_score))}" r="3.6" fill="#f3f7fc"/><text x="${p.l}" y="${H-4}" fill="#617791" font-size="9">${fmtDate(rows[0].date)}</text><text x="${W-p.r}" y="${H-4}" text-anchor="end" fill="#617791" font-size="9">${fmtDate(rows[rows.length-1].date)}</text></svg></div>`;
  } else {
    mode.textContent=`${rows.length}/${minPoints} observations`;
    const score=Number(current.composite_score),change5=rows.length>=6?score-Number(rows[rows.length-6].composite_score):null,range20=rows.length>=20?rows.slice(-20).map(r=>Number(r.composite_score)):null;
    let days=1;for(let i=rows.length-1;i>0;i--){if(rows[i].signal===current.signal)days++;else break;}
    host.innerHTML=`<div class="score-summary"><div class="summary-score ${current.signal||''}">${Number.isFinite(score)?score.toFixed(1):'—'}</div><div class="summary-stat-grid"><div class="summary-stat"><span>5-day change</span><strong>${change5==null?'—':signedPoints(change5)}</strong></div><div class="summary-stat"><span>20-day range</span><strong>${range20?`${Math.min(...range20).toFixed(1)}–${Math.max(...range20).toFixed(1)}`:'—'}</strong></div><div class="summary-stat"><span>Days in current signal</span><strong>${current.signal?days:'—'}</strong></div></div></div>`;
  }
  const log=allHistory.filter(r=>r.slug===slug).slice(-5).reverse();
  $('historyBody').innerHTML=(log.length?log:[{date:current.as_of,pe:current.pe,composite_score:current.composite_score,signal:current.signal}]).map(r=>`<tr><td>${fmtDate(r.date)}</td><td>${r.pe?Number(r.pe).toFixed(2)+'×':'—'}</td><td class="log-score ${r.signal||''}">${r.composite_score?Number(r.composite_score).toFixed(1):'—'}</td><td>${r.signal?`<span class="badge ${r.signal}">${r.signal}</span>`:'—'}</td></tr>`).join('');
}

function renderBacktest(slug,current){
  const bt=backtestBundle?.indices?.[slug] || {rows:[],meta:{}};
  const meta=bt.meta||{}, rows=bt.rows||[];
  $('backtestMeta').textContent=meta.valid_pe_quarters?`${meta.valid_pe_quarters} PE-bearing quarter-end observations · ${meta.first_quarter||''} to ${meta.last_quarter||''}`:'Insufficient historical P/E observations.';
  $('backtestBody').innerHTML=rows.map(r=>{const isCurrent=r.quintile===current.valuation_quintile;const f=v=>pct(v,1);return `<tr class="${isCurrent?'current':''}"><td>${displayQuintile(r.quintile)}${isCurrent?'<span class="current-badge">CURRENT</span>':''}</td><td>${f(r.median_1y)}</td><td>${f(r.median_3y)}</td><td>${f(r.median_5y)}</td><td>${f(r.median_10y)}</td><td>${f(r.loss_3y)}</td><td>${r.n_3y??0}</td></tr>`;}).join('') || '<tr><td colspan="7">Insufficient history for a reliable backtest.</td></tr>';

  const chart=$('backtestChart');
  if(!chart) return;
  const values=rows.map(r=>Number(r.median_3y)).filter(Number.isFinite);
  if(!values.length){chart.innerHTML='<div class="empty-history">Not enough 3-year history for the visual.</div>';return;}
  const max=Math.max(...values.map(v=>Math.max(v,0)),.01);
  chart.innerHTML=rows.map(r=>{
    const v=Number(r.median_3y), isCurrent=r.quintile===current.valuation_quintile;
    const h=Number.isFinite(v)?clamp(Math.max(0,v)/max*100,2,100):0;
    return `<div class="backtest-bar-item ${isCurrent?'current':''}"><div class="backtest-bar-track"><div class="backtest-bar-fill" style="height:${h}%"></div></div><div class="backtest-bar-value">${pct(r.median_3y,1)}</div><div class="backtest-bar-label">${displayQuintile(r.quintile)}</div></div>`;
  }).join('');
}

function renderAvailability(x){
  const b=$('availabilityBanner');
  if(x.pe==null){b.hidden=false;b.textContent='Current aggregate P/E is unavailable for this index, so the live score is not computed.';}
  else if(x.composite_score==null){b.hidden=false;b.textContent='This index does not yet have enough comparable P/E / earnings history for a live score. Historical data will continue to accumulate automatically.';}
  else b.hidden=true;
}

function clearThresholds(){
  ['ovBuyHoldPe','ovHoldSellPe','buyHoldPe','holdSellPe','buyHoldNifty','holdSellNifty','buyHoldMove','holdSellMove','boundaryMin','boundaryMax','ovBoundaryMin','ovBoundaryMax'].forEach(id=>{if($(id))$(id).textContent='—';});
}

function updateExcelDownload(x, slug){
  const link=$('indexExcelDownload'), text=$('indexExcelText');
  if(!link) return;
  link.href=`downloads/indices/${encodeURIComponent(slug)}.xlsx`;
  link.setAttribute('download',`${x.index_name} Valuation Tracker.xlsx`);
  link.setAttribute('aria-label',`Download ${x.index_name} Excel model`);
  link.title=`Download ${x.index_name} Excel model`;
  if(text) text.textContent=`${x.index_name} Excel`;
}

function renderSelected(slug){
  const x=latestBundle?.indices?.[slug]; if(!x)return;
  selectedSlug=slug; $('indexSelect').value=slug;
  try{localStorage.setItem('niftySelectedIndex',slug);}catch(_e){}
  const u=new URL(window.location.href);
  if(slug==='nifty-50') u.searchParams.delete('index');
  else u.searchParams.set('index',slug);
  history.replaceState(null,'',u);
  $('desktopIndexTitle').textContent='Index Valuation Tracker'; $('stickyTitle').textContent='Index Valuation Tracker';
  if($('heroIndexName')) $('heroIndexName').textContent=x.index_name;
  if($('heroIndexGroup')) $('heroIndexGroup').textContent=x.group||'NSE Equity Index';
  if($('indexSummaryLine')) $('indexSummaryLine').textContent=`${x.index_name} · ${x.group||'NSE Equity Index'} · ${x.composite_score==null?'Score unavailable':`Score ${Number(x.composite_score).toFixed(1)}`} · ${x.pe?`${Number(x.pe).toFixed(2)}× P/E`:'P/E unavailable'}`;
  document.title=`${x.index_name} | Index Valuation Tracker`;
  updateExcelDownload(x,slug);
  setFreshness(x.as_of);setSignalTone(x.signal);renderAvailability(x);
  animateNumber($('score'),x.composite_score,650,1);setDial(x.composite_score);
  const sc=Number.isFinite(Number(x.composite_score))?clamp(Number(x.composite_score),0,100):0;$('scoreFill').style.width=`${sc}%`;$('scoreMarker').style.left=`${sc}%`;
  $('scoreBuffer').textContent=scoreBufferText(x);$('nextBoundary').textContent=nextBoundaryText(x);
  $('nifty').textContent=num(x.nifty_close,2);$('pe').textContent=x.pe?`${Number(x.pe).toFixed(2)}×`:'—';$('percentile').textContent=pct(x.pe_percentile,1);$('quintile').textContent=displayQuintile(x.valuation_quintile);$('epsGrowth').textContent=pct(x.yoy_eps_growth,1);
  $('peVsMedian').textContent=x.era_median_pe?`vs median ${Number(x.era_median_pe).toFixed(2)}×`:'—';
  const d=x.pe&&x.era_median_pe?Number(x.pe)/Number(x.era_median_pe)-1:null;$('peDelta').className='micro-chip';$('peDelta').textContent=signedPct(d);if(d!=null)$('peDelta').classList.add(d<=0?'positive':'negative');$('percentileMarker').style.left=`${x.pe_percentile==null?50:clamp(Number(x.pe_percentile)*100,0,100)}%`;
  $('stickyNifty').textContent=num(x.nifty_close,0);$('stickyPe').textContent=x.pe?`${Number(x.pe).toFixed(2)}×`:'—';$('stickyScore').textContent=x.composite_score==null?'—':Number(x.composite_score).toFixed(1);
  $('valuationScore').textContent=x.valuation_score==null?'—':Number(x.valuation_score).toFixed(1);$('growthScore').textContent=x.growth_score==null?'—':Number(x.growth_score).toFixed(1);$('valuationBar').style.width=`${x.valuation_score==null?0:clamp(Number(x.valuation_score),0,100)}%`;$('growthBar').style.width=`${x.growth_score==null?0:clamp(Number(x.growth_score),0,100)}%`;
  $('earningsYield').textContent=pct(x.earnings_yield,2);$('medianPe').textContent=x.era_median_pe?`${Number(x.era_median_pe).toFixed(2)}×`:'—';$('impliedEps').textContent=num(x.implied_eps,1);
  $('ovCurrentPe').textContent=x.pe?`${Number(x.pe).toFixed(2)}×`:'—';$('currentBoundaryPe').textContent=x.pe?`${Number(x.pe).toFixed(2)}× P/E`:'—';$('currentBoundaryNifty').textContent=`INDEX ${num(x.nifty_close,0)}`;
  clearThresholds();
  const b=x.signal_boundaries;
  if(b&&x.pe){
    $('ovBuyHoldPe').textContent=`${Number(b.buy_hold.pe).toFixed(2)}×`;$('ovHoldSellPe').textContent=`${Number(b.hold_sell.pe).toFixed(2)}×`;
    $('buyHoldPe').textContent=`${Number(b.buy_hold.pe).toFixed(2)}× P/E`;$('buyHoldNifty').textContent=`INDEX ${num(b.buy_hold.nifty_level,0)}`;$('buyHoldMove').textContent=`${signedPct(b.buy_hold.move_from_current)} vs current`;
    $('holdSellPe').textContent=`${Number(b.hold_sell.pe).toFixed(2)}× P/E`;$('holdSellNifty').textContent=`INDEX ${num(b.hold_sell.nifty_level,0)}`;$('holdSellMove').textContent=`${signedPct(b.hold_sell.move_from_current)} vs current`;
    setStrip({wrap:'overviewBoundaryStrip',buyHoldTick:'ovBuyHoldTick',holdSellTick:'ovHoldSellTick',currentTick:'ovCurrentPeTick',min:'ovBoundaryMin',max:'ovBoundaryMax'},x.pe,b.buy_hold,b.hold_sell);
    setStrip({wrap:'boundaryStrip',buyHoldTick:'buyHoldTick',holdSellTick:'holdSellTick',currentTick:'currentPeTick',min:'boundaryMin',max:'boundaryMax'},x.pe,b.buy_hold,b.hold_sell);
  } else {
    setStrip({wrap:'overviewBoundaryStrip',buyHoldTick:'ovBuyHoldTick',holdSellTick:'ovHoldSellTick',currentTick:'ovCurrentPeTick',min:'ovBoundaryMin',max:'ovBoundaryMax'},null,null,null);
    setStrip({wrap:'boundaryStrip',buyHoldTick:'buyHoldTick',holdSellTick:'holdSellTick',currentTick:'currentPeTick',min:'boundaryMin',max:'boundaryMax'},null,null,null);
  }
  renderSparklines(slug,x);renderBacktest(slug,x);renderHistory(slug,x);
  $('coverageStats').innerHTML=`<div><span>Monthly P/E observations</span><strong>${x.live_pe_history_months||0}</strong></div><div><span>Quarter-end P/E observations</span><strong>${x.backtest_valid_pe_quarters||0}</strong></div>`;
}

function buildSelector(){
  const sel=$('indexSelect');sel.innerHTML='';
  const groups={};catalog.items.forEach(i=>(groups[i.group]??=[]).push(i));
  catalog.groups.forEach(g=>{if(!groups[g]?.length)return;const og=document.createElement('optgroup');og.label=g;groups[g].forEach(i=>{const o=document.createElement('option');o.value=i.slug;o.textContent=i.status==='ok'?i.name:`${i.name} · ${i.status==='pe_unavailable'?'P/E unavailable':'limited history'}`;og.appendChild(o);});sel.appendChild(og);});
  sel.addEventListener('change',()=>renderSelected(sel.value));
}

function renderHeatmap(){
  const search=($('heatmapSearch').value||'').toLowerCase(),group=$('heatmapGroup').value,sort=$('heatmapSort')?.value||'score-desc';
  const rows=catalog.items.map(i=>({...i,...latestBundle.indices[i.slug]})).filter(x=>(group==='All'||x.group===group)&&(!search||x.name.toLowerCase().includes(search)));
  const n=v=>Number.isFinite(Number(v))?Number(v):null;
  const sorters={
    'score-desc':(a,b)=>(n(b.composite_score)??-Infinity)-(n(a.composite_score)??-Infinity),
    'valuation-asc':(a,b)=>(n(a.pe_percentile)??Infinity)-(n(b.pe_percentile)??Infinity),
    'eps-desc':(a,b)=>(n(b.yoy_eps_growth)??-Infinity)-(n(a.yoy_eps_growth)??-Infinity),
    'pe-asc':(a,b)=>(n(a.pe)??Infinity)-(n(b.pe)??Infinity),
    'name-asc':(a,b)=>a.name.localeCompare(b.name)
  };
  rows.sort(sorters[sort]||sorters['score-desc']);
  $('heatmapBody').innerHTML=rows.map(x=>{
    const pp=n(x.pe_percentile), width=pp==null?0:clamp(pp*100,0,100);
    return `<tr data-slug="${x.slug}"><td><strong>${x.name}</strong></td><td class="muted-cell">${x.group}</td><td>${x.pe?Number(x.pe).toFixed(2)+'×':'—'}</td><td><div class="heatmap-percentile"><span>${pct(x.pe_percentile,1)}</span><i><b style="width:${width}%"></b></i></div></td><td>${pct(x.yoy_eps_growth,1)}</td><td class="score-cell ${x.signal||''}">${x.composite_score==null?'—':Number(x.composite_score).toFixed(1)}</td><td>${x.signal?`<span class="badge ${x.signal}">${x.signal}</span>`:'—'}</td></tr>`;
  }).join('');
  $('heatmapBody').querySelectorAll('tr[data-slug]').forEach(tr=>tr.addEventListener('click',()=>{renderSelected(tr.dataset.slug);activateTab('overview');window.scrollTo({top:0,behavior:'smooth'});}));
}

function activateTab(name){document.querySelectorAll('.tab-button').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.dataset.panel===name));try{localStorage.setItem('niftyActiveTab',name);}catch(_e){} if(name==='heatmap')renderHeatmap();}
function initTabs(){document.querySelectorAll('.tab-button').forEach(b=>b.addEventListener('click',()=>activateTab(b.dataset.tab)));let name='overview';try{name=localStorage.getItem('niftyActiveTab')||'overview';}catch(_e){}activateTab(name);}
function installSticky(){const s=$('stickySummary'),topbar=document.querySelector('.topbar');if(!s||!topbar)return;const u=()=>{const show=topbar.getBoundingClientRect().bottom<0;s.classList.toggle('visible',show);s.setAttribute('aria-hidden',show?'false':'true');};u();addEventListener('scroll',u,{passive:true});addEventListener('resize',u,{passive:true});}

async function loadLegacyFallback(){
  const [l,h,b]=await Promise.all([fetch('data/latest.json',{cache:'no-store'}).then(r=>r.json()),fetch('data/history.csv',{cache:'no-store'}).then(r=>r.ok?r.text():''),fetch('data/backtest_summary.json',{cache:'no-store'}).then(r=>r.json())]);
  const slug='nifty-50';
  catalog={default_slug:slug,groups:['Broad Market'],items:[{slug,name:'NIFTY 50',group:'Broad Market',status:'ok'}]};
  latestBundle={indices:{[slug]:{...l,index_name:'NIFTY 50',group:'Broad Market',slug}}};backtestBundle={indices:{[slug]:b}};
  allHistory=csvParse(h).map(r=>({...r,slug,close:r.nifty_close||r.close}));
}

async function boot(){
  initTabs();
  await loadHolidayCalendar();
  try{
    try{
      const [c,l,b,h]=await Promise.all([
        fetch('data/multi_index_catalog.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('catalog');return r.json()}),
        fetch('data/multi_index_latest.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('latest');return r.json()}),
        fetch('data/multi_index_backtests.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('backtests');return r.json()}),
        fetch('data/multi_index_history.csv',{cache:'no-store'}).then(r=>r.ok?r.text():'')
      ]);catalog=c;latestBundle=l;backtestBundle=b;allHistory=csvParse(h);
    }catch(_multiErr){await loadLegacyFallback();}
    buildSelector();
    $('heatmapSearch').addEventListener('input',renderHeatmap);$('heatmapGroup').addEventListener('change',renderHeatmap);$('heatmapSort')?.addEventListener('change',renderHeatmap);
    const requestedSlug=new URLSearchParams(location.search).get('index');
    let slug=(requestedSlug && latestBundle.indices[requestedSlug]) ? requestedSlug : 'nifty-50';
    if(!latestBundle.indices[slug]) slug=catalog.default_slug;
    renderSelected(slug);renderHeatmap();installSticky();
  }catch(e){document.querySelector('.shell').insertAdjacentHTML('afterbegin','<div class="error">Dashboard data could not be loaded. Run the GitHub Action “Refresh Index Valuation Tracker and deploy Pages” once to refresh the data.</div>');console.error(e);}
}
boot();
