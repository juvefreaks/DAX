"""Render one self-contained HTML page: BTC chart + decision terminal + SVI vol surface."""
from __future__ import annotations

import json
import math
import pathlib

PLOTLY = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.2/plotly.min.js"


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "tolist") and getattr(o, "ndim", 0) > 0:
        return _clean(o.tolist())
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            o = o.item()
        except Exception:
            pass
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


def render(data: dict, path: pathlib.Path, refresh: int | None = None):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    html = TEMPLATE.replace("__REFRESH__", meta).replace("__PLOTLY__", PLOTLY).replace("__DATA__", json.dumps(_clean(data)))
    path.write_text(html)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTC Paper Desk</title>
__REFRESH__
<script src="__PLOTLY__"></script>
<style>
:root{--bg:#0b0e14;--panel:#11151e;--line:#1c2230;--tx:#c9d1d9;--mut:#7d8590;--up:#26a69a;--dn:#ef5350;--acc:#f5a623;--blue:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:13px/1.45 ui-monospace,Menlo,Consolas,monospace}
header{padding:14px 16px 6px}h1{margin:0;font-size:16px;letter-spacing:.04em}
.sub{color:var(--mut);font-size:12px}.warn{color:var(--acc)}
.chips{display:flex;flex-wrap:wrap;gap:8px;padding:8px 16px}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
.chip b{display:block;font-size:15px}.chip>span{color:var(--mut);font-size:11px}
.pos{color:var(--up)}.neg{color:var(--dn)}
.grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,1fr);gap:10px;padding:8px 16px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px;min-width:0}
.panel h2{margin:0 0 6px;font-size:12px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
#term{max-height:560px;overflow:auto}
.rd{border-bottom:1px solid var(--line);padding:6px 2px}
.rd .h{display:flex;justify-content:space-between;gap:8px}
.tag{padding:0 6px;border-radius:3px;font-weight:700}.HOLD{background:#1b3a35;color:var(--up)}.FLAT{background:#3d1f1f;color:var(--dn)}
.why{color:var(--mut);font-size:11.5px}.fill{font-size:11px;color:#9aa4b2;margin-left:8px}
table{width:100%;border-collapse:collapse;font-size:11.5px}td,th{padding:3px 6px;text-align:right;border-bottom:1px solid var(--line)}th{color:var(--mut);font-weight:600}
td:first-child,th:first-child{text-align:left}
footer{padding:10px 16px 24px;color:var(--mut);font-size:11.5px}
</style></head><body>
<header><h1>BTC PAPER DESK</h1>
<div class="sub" id="sub"></div>
<div class="sub warn">Paper trading only. A replay of recent candles is not evidence of future profit.</div></header>
<div class="chips" id="chips"></div>
<div class="grid">
  <div class="panel"><h2>BTCUSDT 1m · EMA20 fair value · ±1 ATR band · rounds</h2><div id="chart" style="height:520px"></div></div>
  <div class="panel"><h2>Decision terminal</h2><div id="term"></div></div>
</div>
<div class="grid">
  <div class="panel"><h2>Deribit implied-vol surface (SVI fit per expiry)</h2><div id="surf" style="height:520px"></div></div>
  <div class="panel"><h2>SVI parameters</h2><div id="svi"></div>
    <h2 style="margin-top:14px">Profiler (replay hot spots)</h2><div id="prof"></div></div>
</div>
<footer id="foot"></footer>
<script>
const D=__DATA__;
const f=(x,d=2)=>x==null?'–':Number(x).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const sg=x=>(x>=0?'+':'')+f(x);const cl=x=>x>=0?'pos':'neg';
document.getElementById('sub').textContent=`${D.meta.symbol} · ${D.meta.rounds} rounds × ${D.meta.bars_per_round} min · fee ${D.meta.fee_bps} bps · cap ${D.meta.cap} BTC · data: ${D.meta.source}`;
const S=D.summary,V=D.surface;
const chips=[['BTC last','$'+f(S.last)],['Book A (always on)',`<span class="${cl(S.pnl_a)}">${sg(S.pnl_a)} USD</span>`],
['Book B (gated)',`<span class="${cl(S.pnl_b)}">${sg(S.pnl_b)} USD</span>`],['Fees A / B',`${f(S.fees_a)} / ${f(S.fees_b)}`],
['HOLD / FLAT calls',`${S.holds} / ${S.flats}`],['2nd opinion agrees',`${S.agree} / ${D.meta.rounds}`]];
if(V)chips.push(['SVI fits kept / rejected',`${V.n_expiries} / ${V.n_rejected}`],['1M ATM vol',f(V.atm_1m)+'%'],['25Δ skew (1M)',f(V.skew25_1m)+' pts'],['Strikes / expiries',`${V.n_strikes} / ${V.n_expiries}`]);
document.getElementById('chips').innerHTML=chips.map(c=>`<div class="chip"><span>${c[0]}</span><b>${c[1]}</b></div>`).join('');
// chart
const C=D.candles,x=C.map(r=>r.ts);
const tr=[{type:'candlestick',x,open:C.map(r=>r.open),high:C.map(r=>r.high),low:C.map(r=>r.low),close:C.map(r=>r.close),
 increasing:{line:{color:'#26a69a'}},decreasing:{line:{color:'#ef5350'}},name:'BTCUSDT'},
 {x,y:C.map(r=>r.ema),mode:'lines',line:{color:'#f5a623',width:1.5},name:'fair (EMA20)'},
 {x,y:C.map(r=>r.ema+r.atr),mode:'lines',line:{color:'#58a6ff',width:1,dash:'dot'},name:'+1 ATR'},
 {x,y:C.map(r=>r.ema-r.atr),mode:'lines',line:{color:'#58a6ff',width:1,dash:'dot'},name:'−1 ATR'}];
for(const bk of ['A','B']){const F=D.fills.filter(q=>q.book===bk);
 for(const side of ['BUY','SELL']){const g=F.filter(q=>q.side===side);
  tr.push({x:g.map(q=>q.ts),y:g.map(q=>q.price),mode:'markers',name:`Book ${bk} ${side}`,
   text:g.map(q=>`R${q.round} ${q.side} ${q.qty} @ ${f(q.price)} fee ${f(q.fee)}<br>${q.reason}`),hoverinfo:'text',
   marker:{symbol:side==='BUY'?'triangle-up':'triangle-down',size:bk==='A'?8:12,color:side==='BUY'?'#26a69a':'#ef5350',
   line:{color:bk==='B'?'#fff':'rgba(0,0,0,0)',width:1}}})}}
const shapes=[],ann=[];
for(const r of D.rounds){shapes.push({type:'rect',xref:'x',yref:'paper',x0:r.start,x1:r.end,y0:0,y1:1,
 fillcolor:r.call==='FLAT'?'rgba(239,83,80,.10)':'rgba(0,0,0,0)',line:{color:'#1c2230',width:1}});
 ann.push({x:r.start,y:1,xref:'x',yref:'paper',text:`R${r.round} ${r.call}`,showarrow:false,xanchor:'left',yanchor:'bottom',
 font:{size:9,color:r.call==='FLAT'?'#ef5350':'#7d8590'}})}
Plotly.newPlot('chart',tr,{paper_bgcolor:'#11151e',plot_bgcolor:'#0b0e14',font:{color:'#c9d1d9',size:11},margin:{l:10,r:60,t:18,b:30},
 xaxis:{rangeslider:{visible:false},gridcolor:'#1c2230'},yaxis:{side:'right',gridcolor:'#1c2230'},shapes,annotations:ann,
 legend:{orientation:'h',y:-0.08,font:{size:10}}},{responsive:true,displaylogo:false});
// terminal
document.getElementById('term').innerHTML=D.rounds.slice().reverse().map(r=>`<div class="rd"><div class="h"><span>R${r.round} <span class="tag ${r.call}">${r.call}</span>
 ${r.second_call?`<span class="why">2nd: ${r.second_call} (${f(r.second_prob*100,0)}%) ${r.second_call===r.call?'✓':'✗'}</span>`:''}</span>
 <span>A <span class="${cl(r.pnl_a)}">${sg(r.pnl_a)}</span> · B <span class="${cl(r.pnl_b)}">${sg(r.pnl_b)}</span></span></div>
 <div class="why">${r.reason}</div>${r.fills.map(q=>`<div class="fill">[${q.book}] ${q.side} ${q.qty} @ ${f(q.price)} · fee ${f(q.fee)} · ${q.reason}</div>`).join('')}</div>`).join('');
// surface
if(V){const kx=V.k_grid.map(k=>Math.exp(k)*100);
 Plotly.newPlot('surf',[{type:'surface',x:kx,y:V.fits.map(q=>q.T*365),z:V.fits.map(q=>q.iv_grid),colorscale:'Viridis',
 colorbar:{title:{text:'IV %'},len:.6},contours:{z:{show:true,usecolormap:true,project:{z:false}}}}],
 {paper_bgcolor:'#11151e',font:{color:'#c9d1d9',size:11},margin:{l:0,r:0,t:0,b:0},
 scene:{xaxis:{title:{text:'Strike K/F %'}},yaxis:{title:{text:'Days to expiry'}},zaxis:{title:{text:'IV %'}},bgcolor:'#0b0e14',camera:{eye:{x:1.6,y:-1.6,z:.9}}}},{responsive:true,displaylogo:false});
 document.getElementById('svi').innerHTML='<table><tr><th>Expiry</th><th>n</th><th>a</th><th>b</th><th>ρ</th><th>m</th><th>σ</th><th>ATM</th><th>RMSE</th></tr>'+
 V.fits.map(q=>`<tr><td>${q.expiry.slice(0,10)}</td><td>${q.n}</td>${q.params.map(v=>`<td>${f(v,3)}</td>`).join('')}<td>${f(q.atm_iv*100,1)}%</td><td>${f(q.rmse_vol_pts,2)}</td></tr>`).join('')+'</table>';
}else document.getElementById('surf').innerHTML='<div class="why">Deribit surface unavailable (network).</div>';
document.getElementById('prof').innerHTML=D.profile.length?'<table><tr><th>Function</th><th>calls</th><th>total s</th><th>cum s</th></tr>'+
 D.profile.map(p=>`<tr><td>${p.func}</td><td>${p.calls}</td><td>${f(p.tot,4)}</td><td>${f(p.cum,4)}</td></tr>`).join('')+'</table>':'<div class="why">run with --profile</div>';
document.getElementById('foot').textContent='Generated '+D.meta.generated+'. Not financial advice.';
</script></body></html>
"""
