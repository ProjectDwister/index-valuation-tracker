const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const pct = (x, d=1) => x == null ? '—' : `${(Number(x)*100).toFixed(d)}%`;
const num = (x, d=2) => x == null ? '—' : Number(x).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtDate = (s) => s ? new Date(`${s}T00:00:00`).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'}) : '—';
const signedPct = (x, d=1) => {
  if(x == null || Number.isNaN(Number(x))) return '—';
  const v=Number(x)*100; return `${v>=0?'+':''}${v.toFixed(d)}%`;
};

function signalText(s){
  if(s==='BUY') return 'Valuation and earnings conditions support adding long-horizon NIFTY exposure.';
  if(s==='HOLD') return 'Valuation and earnings conditions are broadly neutral for fresh allocation.';
  return 'Conditions are unfavourable for fresh allocation; this is not a short-selling signal.';
}
function boundarySentence(signal,bh,hs){
  if(!bh || !hs) return 'Run the cloud refresh once after uploading this update to calculate the signal boundaries.';
  const bhPe=Number(bh.pe).toFixed(2), hsPe=Number(hs.pe).toFixed(2);
  const bhM=signedPct(bh.move_from_current), hsM=signedPct(hs.move_from_current);
  if(signal==='BUY') return `With earnings unchanged, the model has about ${bhM} valuation headroom before BUY becomes HOLD, and about ${hsM} before SELL.`;
  if(signal==='HOLD') return `With earnings unchanged, BUY returns near ${bhPe}× P/E; SELL begins near ${hsPe}×.`;
  return `With earnings unchanged, HOLD returns near ${hsPe}× P/E and BUY near ${bhPe}×.`;
}
function csvParse(text){
  const lines=text.trim().split(/\r?\n/); if(lines.length<2) return [];
  const heads=lines[0].split(',');
  return lines.slice(1).filter(Boolean).map(line=>{
    const a=line.split(','); const o={}; heads.forEach((h,i)=>o[h]=a[i]??''); return o;
  });
}
function setFreshness(asOf){
  const host=$('freshness');
  const d=new Date(`${asOf}T00:00:00`); const now=new Date();
  const days=Math.max(0,Math.floor((now-d)/86400000));
  host.classList.remove('fresh','stale'); host.classList.add(days<=3?'fresh':'stale');
  host.querySelector('span:last-child').textContent=`${fmtDate(asOf)} · latest market close`;
}
function setSignalTone(signal){
  $('signalPill').textContent=signal; $('signalPill').className=`signal-pill ${signal}`;
  $('currentBoundarySignal').textContent=signal; $('currentBoundarySignal').className=`delta-pill signal-delta ${signal}`;
}
function setBoundaryStrip(current,bh,hs){
  if(!bh || !hs) return;
  const values=[Number(current),Number(bh.pe),Number(hs.pe)];
  const lo=Math.min(...values), hi=Math.max(...values), span=Math.max(.5,hi-lo);
  const min=lo-span*.28, max=hi+span*.20;
  const pos=v=>clamp((Number(v)-min)/(max-min)*100,0,100);
  const pBh=pos(bh.pe), pHs=pos(hs.pe), pCur=pos(current);
  $('buyHoldTick').style.left=`${pBh}%`; $('holdSellTick').style.left=`${pHs}%`; $('currentPeTick').style.left=`${pCur}%`;
  const zones=document.querySelector('.boundary-zones');
  zones.children[0].style.width=`${pBh}%`;
  zones.children[1].style.width=`${Math.max(0,pHs-pBh)}%`;
  zones.children[2].style.width=`${Math.max(0,100-pHs)}%`;
  $('boundaryMin').textContent=`${min.toFixed(1)}×`;
  $('boundaryMax').textContent=`${max.toFixed(1)}×`;
}
function heatStyle(v,type='return'){
  if(v==null || Number.isNaN(Number(v))) return '';
  const n=Number(v);
  if(type==='loss'){
    const a=clamp(Math.abs(n)/.25*.20,.03,.20);
    return `background:rgba(239,68,68,${a.toFixed(3)})`;
  }
  const positive=n>=0; const a=clamp(Math.abs(n)/.35*.22,.025,.22);
  return `background:${positive?`rgba(34,197,94,${a.toFixed(3)})`:`rgba(239,68,68,${a.toFixed(3)})`}`;
}
function drawChart(rows){
  const host=$('chart');
  const data=rows.filter(r=>r.composite_score!=='').slice(-120).map(r=>({d:r.date,v:+r.composite_score,s:r.signal}));
  if(!data.length){host.innerHTML='<div class="hint">History will build automatically after each cloud refresh.</div>';return;}
  const W=760,H=230,p={l:34,r:12,t:13,b:29},iw=W-p.l-p.r,ih=H-p.t-p.b;
  const x=i=>p.l+(data.length===1?iw/2:(i/(data.length-1))*iw), y=v=>p.t+(100-v)/100*ih;
  const path=data.map((o,i)=>`${i?'L':'M'} ${x(i).toFixed(1)} ${y(o.v).toFixed(1)}`).join(' ');
  const zoneRect=(top,bottom,fill)=>`<rect x="${p.l}" y="${y(top)}" width="${iw}" height="${Math.max(0,y(bottom)-y(top))}" fill="${fill}"/>`;
  const zones=zoneRect(100,70,'rgba(34,197,94,.045)')+zoneRect(70,40,'rgba(245,158,11,.035)')+zoneRect(40,0,'rgba(239,68,68,.035)');
  const grid=[40,70].map(v=>`<line x1="${p.l}" x2="${W-p.r}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(255,255,255,.10)" stroke-dasharray="4 5"/><text x="6" y="${y(v)+3}" fill="#617791" font-size="9">${v}</text>`).join('');
  const circles=data.length===1?`<circle cx="${x(0)}" cy="${y(data[0].v)}" r="3.5" fill="#f3f7fc"/>`:data.map((o,i)=>i===0||o.s!==data[i-1].s?`<circle cx="${x(i)}" cy="${y(o.v)}" r="3" fill="#f3f7fc" opacity=".85"/>`:'').join('');
  const start=fmtDate(data[0].d), end=fmtDate(data[data.length-1].d);
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${zones}${grid}<path d="${path}" fill="none" stroke="#78b5ff" stroke-width="2.2" vector-effect="non-scaling-stroke"/>${circles}<text x="${p.l}" y="${H-4}" fill="#617791" font-size="9">${start}</text><text x="${W-p.r}" y="${H-4}" text-anchor="end" fill="#617791" font-size="9">${end}</text></svg>`;
}

async function boot(){
  try{
    const [latest,historyText,bt]=await Promise.all([
      fetch('data/latest.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('latest.json');return r.json()}),
      fetch('data/history.csv',{cache:'no-store'}).then(r=>r.ok?r.text():''),
      fetch('data/backtest_summary.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('backtest');return r.json()})
    ]);
    const history=historyText?csvParse(historyText):[];
    setFreshness(latest.as_of);
    setSignalTone(latest.signal);
    $('signalText').textContent=signalText(latest.signal);
    $('score').textContent=Number(latest.composite_score).toFixed(1);
    const sc=clamp(+latest.composite_score,0,100); $('scoreFill').style.width=`${sc}%`; $('scoreMarker').style.left=`${sc}%`;

    $('nifty').textContent=num(latest.nifty_close,2);
    $('pe').textContent=`${Number(latest.pe).toFixed(2)}×`;
    $('peVsMedian').textContent=`Median ${Number(latest.era_median_pe).toFixed(2)}×`;
    const peDelta=Number(latest.pe)/Number(latest.era_median_pe)-1;
    $('peDelta').textContent=`${signedPct(peDelta)} vs median`; $('peDelta').classList.add(peDelta<=0?'positive':'negative');
    $('percentile').textContent=pct(latest.pe_percentile,1);
    $('percentileMarker').style.left=`${clamp(+latest.pe_percentile*100,0,100)}%`;
    $('quintile').textContent=latest.valuation_quintile;
    $('epsGrowth').textContent=pct(latest.yoy_eps_growth,1);
    const epsTone=$('epsTone'); const eg=+latest.yoy_eps_growth;
    epsTone.textContent=eg>=.12?'Strong':eg>=.06?'Healthy':eg>=0?'Moderate':'Contracting';
    epsTone.classList.add(eg>=.06?'positive':eg<0?'negative':'');

    $('valuationScore').textContent=Number(latest.valuation_score).toFixed(1);
    $('growthScore').textContent=Number(latest.growth_score).toFixed(1);
    $('valuationBar').style.width=`${clamp(+latest.valuation_score,0,100)}%`;
    $('growthBar').style.width=`${clamp(+latest.growth_score,0,100)}%`;
    $('earningsYield').textContent=pct(latest.earnings_yield,2);
    $('medianPe').textContent=`${Number(latest.era_median_pe).toFixed(2)}×`;
    $('impliedEps').textContent=num(latest.implied_eps,1);
    $('modelVersion').textContent=latest.model_version;

    const bounds=latest.signal_boundaries||{},bh=bounds.buy_hold,hs=bounds.hold_sell;
    $('currentBoundaryPe').textContent=`${Number(latest.pe).toFixed(2)}× P/E`;
    $('currentBoundaryNifty').textContent=`NIFTY ${num(latest.nifty_close,0)}`;
    if(bh&&hs){
      $('buyHoldPe').textContent=`${Number(bh.pe).toFixed(2)}× P/E`;
      $('buyHoldNifty').textContent=`NIFTY ${num(bh.nifty_level,0)}`;
      $('buyHoldMove').textContent=`${signedPct(bh.move_from_current)} vs current`;
      $('holdSellPe').textContent=`${Number(hs.pe).toFixed(2)}× P/E`;
      $('holdSellNifty').textContent=`NIFTY ${num(hs.nifty_level,0)}`;
      $('holdSellMove').textContent=`${signedPct(hs.move_from_current)} vs current`;
      setBoundaryStrip(latest.pe,bh,hs);
    }else $('boundaryCard').classList.add('unavailable');
    $('boundaryNarrative').textContent=boundarySentence(latest.signal,bh,hs);

    $('backtestBody').innerHTML=bt.rows.map(r=>{
      const current=r.quintile===latest.valuation_quintile;
      const label=`${r.quintile}${current?'<span class="current-badge">CURRENT</span>':''}`;
      return `<tr class="${current?'current':''}"><td>${label}</td><td class="heat" style="${heatStyle(r.median_1y)}">${pct(r.median_1y)}</td><td class="heat" style="${heatStyle(r.median_3y)}">${pct(r.median_3y)}</td><td class="heat" style="${heatStyle(r.median_5y)}">${pct(r.median_5y)}</td><td class="heat" style="${heatStyle(r.median_10y)}">${pct(r.median_10y)}</td><td class="heat" style="${heatStyle(r.loss_3y,'loss')}">${pct(r.loss_3y)}</td><td>${r.n_3y}</td></tr>`;
    }).join('');

    const logRows=history.length?history.slice(-8).reverse():[{date:latest.as_of,pe:latest.pe,composite_score:latest.composite_score,signal:latest.signal}];
    $('historyBody').innerHTML=logRows.map(r=>`<tr><td>${fmtDate(r.date)}</td><td>${Number(r.pe).toFixed(2)}×</td><td>${Number(r.composite_score).toFixed(1)}</td><td><span class="badge ${r.signal}">${r.signal}</span></td></tr>`).join('');
    drawChart(history.length?history:[{date:latest.as_of,composite_score:latest.composite_score,signal:latest.signal}]);
  }catch(e){
    document.querySelector('.shell').insertAdjacentHTML('afterbegin','<div class="error">Dashboard data could not be loaded. Open the repository Actions tab and run “Refresh NIFTY tracker and deploy Pages” manually.</div>');
    console.error(e);
  }
}
boot();
