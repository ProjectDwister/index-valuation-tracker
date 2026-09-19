const $ = (id) => document.getElementById(id);
const pct = (x, d=1) => x == null ? '—' : `${(x*100).toFixed(d)}%`;
const num = (x, d=2) => x == null ? '—' : Number(x).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtDate = (s) => s ? new Date(`${s}T00:00:00`).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'}) : '—';

function csvParse(text){
  const lines=text.trim().split(/\r?\n/); if(lines.length<2) return [];
  const heads=lines[0].split(',');
  return lines.slice(1).map(line=>{
    const a=line.split(','); const o={}; heads.forEach((h,i)=>o[h]=a[i]??''); return o;
  });
}
function signalText(s){
  if(s==='BUY') return 'Valuation and earnings conditions support adding long-horizon NIFTY exposure under this model.';
  if(s==='HOLD') return 'Valuation and earnings conditions are broadly neutral under this model.';
  return 'Conditions are unfavourable for fresh allocation under this model; this is not a short-selling signal.';
}
function drawChart(rows){
  const host=$('chart');
  const data=rows.filter(r=>r.composite_score!=='').slice(-120).map(r=>({d:r.date,v:+r.composite_score}));
  if(!data.length){host.innerHTML='<div class="hint">History will build automatically after each cloud refresh.</div>';return;}
  const W=720,H=245,p={l:34,r:10,t:15,b:30}; const iw=W-p.l-p.r, ih=H-p.t-p.b;
  const x=i=>p.l+(data.length===1?iw/2:(i/(data.length-1))*iw); const y=v=>p.t+(100-v)/100*ih;
  const path=data.map((o,i)=>`${i?'L':'M'} ${x(i).toFixed(1)} ${y(o.v).toFixed(1)}`).join(' ');
  const circles=data.length===1?`<circle cx="${x(0)}" cy="${y(data[0].v)}" r="4" fill="#eaf2ff"/>`:'';
  const grid=[40,70].map(v=>`<line x1="${p.l}" x2="${W-p.r}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(255,255,255,.13)" stroke-dasharray="5 5"/><text x="6" y="${y(v)+4}" fill="#758aa5" font-size="10">${v}</text>`).join('');
  const start=fmtDate(data[0].d), end=fmtDate(data[data.length-1].d);
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}<path d="${path}" fill="none" stroke="#9dc5ff" stroke-width="3" vector-effect="non-scaling-stroke"/>${circles}<text x="${p.l}" y="${H-5}" fill="#758aa5" font-size="10">${start}</text><text x="${W-p.r}" y="${H-5}" text-anchor="end" fill="#758aa5" font-size="10">${end}</text></svg>`;
}

async function boot(){
  try{
    const [latest, historyText, bt] = await Promise.all([
      fetch('data/latest.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('latest.json');return r.json()}),
      fetch('data/history.csv',{cache:'no-store'}).then(r=>r.ok?r.text():''),
      fetch('data/backtest_summary.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('backtest');return r.json()})
    ]);
    const history=historyText?csvParse(historyText):[];
    $('asOf').textContent=fmtDate(latest.as_of);
    $('signal').textContent=latest.signal; $('signal').className=`signal ${latest.signal}`;
    $('signalText').textContent=signalText(latest.signal);
    $('score').textContent=Number(latest.composite_score).toFixed(1);
    const sc=Math.max(0,Math.min(100,+latest.composite_score)); $('scoreFill').style.width=`${sc}%`; $('scoreMarker').style.left=`${sc}%`;
    $('nifty').textContent=num(latest.nifty_close,2);
    $('pe').textContent=`${Number(latest.pe).toFixed(2)}×`;
    $('peVsMedian').textContent=`Era median ${Number(latest.era_median_pe).toFixed(2)}×`;
    $('percentile').textContent=pct(latest.pe_percentile,1);
    $('quintile').textContent=latest.valuation_quintile;
    $('epsGrowth').textContent=pct(latest.yoy_eps_growth,1);
    $('valuationScore').textContent=Number(latest.valuation_score).toFixed(1);
    $('growthScore').textContent=Number(latest.growth_score).toFixed(1);
    $('valuationBar').style.width=`${Math.min(100,+latest.valuation_score)}%`;
    $('growthBar').style.width=`${Math.min(100,+latest.growth_score)}%`;
    $('earningsYield').textContent=pct(latest.earnings_yield,2);
    $('medianPe').textContent=`${Number(latest.era_median_pe).toFixed(2)}×`;
    $('impliedEps').textContent=num(latest.implied_eps,1);
    $('modelVersion').textContent=latest.model_version;

    $('backtestBody').innerHTML=bt.rows.map(r=>`<tr class="${r.quintile===latest.valuation_quintile?'current':''}"><td>${r.quintile}</td><td>${pct(r.median_1y)}</td><td>${pct(r.median_3y)}</td><td>${pct(r.median_5y)}</td><td>${pct(r.median_10y)}</td><td>${pct(r.loss_3y)}</td><td>${r.n_3y}</td></tr>`).join('');
    $('historyBody').innerHTML=(history.length?history.slice(-8).reverse():[{date:latest.as_of,pe:latest.pe,composite_score:latest.composite_score,signal:latest.signal}]).map(r=>`<tr><td>${fmtDate(r.date)}</td><td>${Number(r.pe).toFixed(2)}×</td><td>${Number(r.composite_score).toFixed(1)}</td><td><span class="badge ${r.signal}">${r.signal}</span></td></tr>`).join('');
    drawChart(history.length?history:[{date:latest.as_of,composite_score:latest.composite_score}]);
  }catch(e){
    document.querySelector('.shell').insertAdjacentHTML('afterbegin',`<div class="error">Dashboard data could not be loaded. Open the repository's Actions tab and run “Refresh NIFTY tracker and deploy Pages” manually.</div>`);
    console.error(e);
  }
}
boot();
