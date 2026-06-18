"""Build a self-contained HTML exploration report from windowed PVC data.

No external/CDN dependencies: the window data is embedded as JSON and all
charts are drawn with vanilla JS on <canvas>, so the file opens offline
from Dropbox. Each scatter has a toggle between the per-window dot view
(every 30 s window, to show heterogeneity) and a binned summary view.

Charts:
  1. PVC burden vs heart rate   (exercise association)
  2. PVC burden vs time of day  (diurnal pattern)
  3. PVC burden vs day          (day-to-day variability)
  4. Rhythm states over time    (normal / bigeminy / trigeminy clustering)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .windows import ActivityBurden, RHYTHM_STATES, Window


def _clean(x):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def _summary(windows: list[Window]) -> dict:
    total_beats = sum(w.n_beats for w in windows)
    total_pvc = sum(w.n_pvc for w in windows)
    dates = sorted({w.date for w in windows})
    coverage_s = len(windows) * 30.0
    per_day = []
    for d in dates:
        wd = [w for w in windows if w.date == d]
        nb = sum(w.n_beats for w in wd)
        npv = sum(w.n_pvc for w in wd)
        per_day.append(
            {
                "date": d,
                "n_windows": len(wd),
                "n_beats": nb,
                "n_pvc": npv,
                "burden_pct": round(100.0 * npv / nb, 2) if nb else None,
            }
        )
    return {
        "n_windows": len(windows),
        "total_beats": total_beats,
        "total_pvc": total_pvc,
        "overall_burden_pct": round(100.0 * total_pvc / total_beats, 2)
        if total_beats
        else None,
        "coverage_hours": round(coverage_s / 3600.0, 2),
        "dates": dates,
        "per_day": per_day,
    }


def build_report(
    windows: list[Window],
    out_path: str,
    title: str = "PVC exploration",
    activity: list[ActivityBurden] | None = None,
    viewers: dict[str, str] | None = None,
) -> dict:
    summary = _summary(windows)
    payload = {
        "title": title,
        "summary": summary,
        "viewers": viewers or {},
        "activity": [
            {
                "label": a.label,
                "burden": _clean(round(a.burden_pct, 2)),
                "mean_hr": _clean(round(a.mean_hr, 1)) if a.mean_hr == a.mean_hr else None,
                "n_beats": a.n_beats,
                "n_pvc": a.n_pvc,
                "n_windows": a.n_windows,
                "hours": round(a.hours, 2),
            }
            for a in (activity or [])
        ],
        "windows": [
            {
                "t": _clean(w.t_start),
                "hr": _clean(round(w.mean_hr, 1)) if w.mean_hr == w.mean_hr else None,
                "burden": _clean(round(w.burden_pct, 2)),
                "hour": _clean(round(w.hour_of_day, 3)),
                "day": w.date,
                "n_beats": w.n_beats,
                "n_pvc": w.n_pvc,
                "rec": w.source,
                "states": {s: round(w.state_fractions.get(s, 0.0), 4) for s in RHYTHM_STATES},
            }
            for w in windows
        ],
    }
    html = _HTML_TEMPLATE.replace("/*DATA*/", json.dumps(payload, allow_nan=False))
    with open(out_path, "w") as fh:
        fh.write(html)
    return summary


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PVC exploration</title>
<style>
  :root { --blue:#2563eb; --orange:#ea7317; --green:#1c9e63; --gray:#9aa0a6; --ink:#1a1a1a; --muted:#666; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: var(--ink); margin: 0; padding: 24px; background:#fafafa; }
  .wrap { max-width: 1000px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 16px; margin: 28px 0 6px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
  .cards { display:flex; flex-wrap:wrap; gap:12px; margin:12px 0 8px; }
  .card { background:#fff; border:1px solid #e6e6e6; border-radius:10px; padding:10px 14px; min-width:120px; }
  .card .v { font-size:20px; font-weight:600; }
  .card .k { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }
  .panel { background:#fff; border:1px solid #e6e6e6; border-radius:10px; padding:12px 14px; margin-top:10px; }
  .toggle { display:inline-flex; border:1px solid #d4d4d4; border-radius:8px; overflow:hidden; font-size:12px; }
  .toggle button { border:0; background:#fff; padding:5px 12px; cursor:pointer; color:var(--muted); }
  .toggle button.active { background:var(--blue); color:#fff; }
  .chartwrap { position:relative; }
  canvas { width:100%; display:block; }
  .legend { font-size:12px; color:var(--muted); margin-top:6px; }
  .legend span { display:inline-block; margin-right:14px; }
  .sw { display:inline-block; width:10px; height:10px; border-radius:2px; vertical-align:middle; margin-right:4px; }
  .tip { position:absolute; pointer-events:none; background:rgba(20,20,20,.92); color:#fff; font-size:12px;
         padding:6px 8px; border-radius:6px; transform:translate(-50%,-115%); white-space:nowrap; display:none; z-index:5; }
  table { border-collapse:collapse; font-size:13px; margin-top:6px; }
  th,td { text-align:right; padding:3px 10px; border-bottom:1px solid #eee; }
  th:first-child, td:first-child { text-align:left; }
  .note { font-size:12px; color:var(--muted); margin-top:6px; }
</style>
</head>
<body>
<div class="wrap">
  <h1 id="title">PVC exploration</h1>
  <div class="sub" id="subtitle"></div>
  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>PVC burden vs heart rate</h2>
    <div class="note">Each dot is one 30-second window. The classic question: does burden climb with exertion?</div>
    <div class="toggle" data-chart="hr"><button data-mode="dots" class="active">Windows</button><button data-mode="binned">Binned</button></div>
    <div class="chartwrap"><canvas id="hr"></canvas><div class="tip" id="hr-tip"></div></div>
  </div>

  <div class="panel">
    <h2>PVC burden vs time of day</h2>
    <div class="note">Windows pooled across all days by local clock time.</div>
    <div class="toggle" data-chart="tod"><button data-mode="dots" class="active">Windows</button><button data-mode="binned">Hourly</button></div>
    <div class="chartwrap"><canvas id="tod"></canvas><div class="tip" id="tod-tip"></div></div>
  </div>

  <div class="panel">
    <h2>PVC burden by day</h2>
    <div class="note">Day-to-day variability. Dots are windows (jittered within the day); binned shows each day's mean.</div>
    <div class="toggle" data-chart="day"><button data-mode="dots" class="active">Windows</button><button data-mode="binned">Per-day mean</button></div>
    <div class="chartwrap"><canvas id="day"></canvas><div class="tip" id="day-tip"></div></div>
  </div>

  <div class="panel">
    <h2>Day view</h2>
    <div class="note">One row per day, midnight to midnight. PVC burden on the left axis (capped at 50%); the grey line is heart rate (right axis, ticks at the zone cut-offs). Background is the 15-minute average heart-rate zone; white = no data. <b>Double-click</b> a point to open that recording's ECG viewer (if one has been generated).</div>
    <div class="toggle" id="dayview-toggle"><button data-mode="dots">Windows</button><button data-mode="avg" class="active">5-min average</button></div>
    <div class="legend">
      <span><span class="sw" style="background:#dcebff"></span>rest &lt;65</span>
      <span><span class="sw" style="background:#dff3df"></span>active &lt;95</span>
      <span><span class="sw" style="background:#ffeccc"></span>light exercise &lt;125</span>
      <span><span class="sw" style="background:#ffd9d9"></span>exercise &ge;125</span>
      <span><span class="sw" style="background:#e7daf6"></span>exercise recovery (2 h)</span>
    </div>
    <div id="dayview"></div>
  </div>

  <div class="panel">
    <h2>PVC burden by zone</h2>
    <div class="note">Average burden in each heart-rate zone, pooled across all days. Recovery is the 2 hours after any exercise block (&ge;125) ends.</div>
    <div class="chartwrap"><canvas id="zone"></canvas><div class="tip" id="zone-tip"></div></div>
  </div>

  <div class="panel" id="act-panel" style="display:none">
    <h2>PVC burden by activity</h2>
    <div class="note">Burden while each tagged activity was in effect (from the Tag button), pooled across all occurrences. An activity runs until the next tag or a break in recording.</div>
    <div class="chartwrap"><canvas id="act"></canvas><div class="tip" id="act-tip"></div></div>
  </div>

  <div class="panel">
    <h2>Rhythm states over time</h2>
    <div class="note">Fraction of beats in sustained bigeminy / trigeminy runs vs normal rhythm, per hour. PVCs tend to cluster.</div>
    <div class="chartwrap"><canvas id="rhythm"></canvas><div class="tip" id="rhythm-tip"></div></div>
    <div class="legend">
      <span><span class="sw" style="background:var(--blue)"></span>normal</span>
      <span><span class="sw" style="background:var(--orange)"></span>bigeminy</span>
      <span><span class="sw" style="background:var(--green)"></span>trigeminy</span>
      <span><span class="sw" style="background:var(--gray)"></span>other / isolated</span>
    </div>
  </div>

  <div class="panel">
    <h2>Per-day summary</h2>
    <table id="daytable"><thead><tr><th>Date</th><th>Hours</th><th>Beats</th><th>PVCs</th><th>Burden %</th></tr></thead><tbody></tbody></table>
  </div>

  <div class="note">Burden is computed only over recorded windows (>=5 beats); gaps from disconnections are excluded, not counted as zero. Times are local.</div>
</div>

<script>
const DATA = /*DATA*/;
const COL = {normal:'#2563eb', bigeminy:'#ea7317', trigeminy:'#1c9e63', other:'#9aa0a6'};

function setup(canvas, height) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
  const cssH = height;
  canvas.style.height = cssH + 'px';
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  return {ctx, w: cssW, h: cssH};
}
function nice(v){ return v.toLocaleString(); }
function lin(d0,d1,r0,r1){ const m=(r1-r0)/((d1-d0)||1); return v=>r0+(v-d0)*m; }

function axes(ctx, area, xd, yd, xlabel, ylabel, xticks, yticks, xfmt){
  const {x0,y0,x1,y1}=area;
  ctx.strokeStyle='#ccc'; ctx.fillStyle='#666'; ctx.font='11px sans-serif'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(x0,y0); ctx.lineTo(x0,y1); ctx.lineTo(x1,y1); ctx.stroke();
  const sx=lin(xd[0],xd[1],x0,x1), sy=lin(yd[0],yd[1],y1,y0);
  ctx.textAlign='center'; ctx.textBaseline='top';
  for(const t of xticks){ const px=sx(t);
    ctx.strokeStyle='#eee'; ctx.beginPath(); ctx.moveTo(px,y0); ctx.lineTo(px,y1); ctx.stroke();
    ctx.fillText(xfmt?xfmt(t):t, px, y1+4); }
  ctx.textAlign='right'; ctx.textBaseline='middle';
  for(const t of yticks){ const py=sy(t);
    ctx.strokeStyle='#eee'; ctx.beginPath(); ctx.moveTo(x0,py); ctx.lineTo(x1,py); ctx.stroke();
    ctx.fillStyle='#666'; ctx.fillText(t, x0-6, py); }
  ctx.save(); ctx.fillStyle='#444'; ctx.textAlign='center';
  ctx.fillText(xlabel,(x0+x1)/2,y1+22);
  ctx.translate(14,(y0+y1)/2); ctx.rotate(-Math.PI/2); ctx.fillText(ylabel,0,0); ctx.restore();
  return {sx,sy};
}
function ticks(min,max,n){ const out=[]; const step=(max-min)/n; for(let i=0;i<=n;i++) out.push(+(min+step*i).toFixed(2)); return out; }

function attachHover(canvas, tip, hit){
  canvas.onmousemove=(e)=>{ const r=canvas.getBoundingClientRect(); const mx=e.clientX-r.left, my=e.clientY-r.top;
    const h=hit(mx,my);
    if(h){ tip.style.display='block'; tip.style.left=h.px+'px'; tip.style.top=h.py+'px'; tip.innerHTML=h.html; }
    else tip.style.display='none'; };
  canvas.onmouseleave=()=>{ tip.style.display='none'; };
}

// ---- scatter of windows ----
function scatter(id, pts, opts){
  const canvas=document.getElementById(id); const {ctx,w,h}=setup(canvas,300);
  const area={x0:54,y0:12,x1:w-14,y1:h-34};
  const xd=opts.xdomain, yd=opts.ydomain;
  const xt=opts.xticks||ticks(xd[0],xd[1],6), yt=opts.yticks||ticks(yd[0],yd[1],5);
  const {sx,sy}=axes(ctx,area,xd,yd,opts.xlabel,opts.ylabel,xt,yt,opts.xfmt);
  const drawn=[];
  for(const p of pts){ if(p.x==null||p.y==null) continue; const px=sx(p.x), py=sy(p.y);
    if(px<area.x0||px>area.x1) continue;
    ctx.fillStyle=opts.color? opts.color(p):'rgba(37,99,235,.45)';
    ctx.beginPath(); ctx.arc(px,py,2.6,0,7); ctx.fill(); drawn.push({px,py,p}); }
  attachHover(canvas,document.getElementById(id+'-tip'),(mx,my)=>{
    let best=null,bd=64; for(const d of drawn){ const dd=(d.px-mx)**2+(d.py-my)**2; if(dd<bd){bd=dd;best=d;} }
    return best? {px:best.px,py:best.py,html:opts.tip(best.p)}:null; });
}

// ---- bar chart (binned) ----
function bars(id, bins, opts){
  const canvas=document.getElementById(id); const {ctx,w,h}=setup(canvas,300);
  const area={x0:54,y0:12,x1:w-14,y1:h-34};
  const xd=opts.xdomain, yd=opts.ydomain;
  const xt=opts.xticks||ticks(xd[0],xd[1],6), yt=opts.yticks||ticks(yd[0],yd[1],5);
  const {sx,sy}=axes(ctx,area,xd,yd,opts.xlabel,opts.ylabel,xt,yt,opts.xfmt);
  const bw=Math.max(3,(sx(xd[0]+opts.binw)-sx(xd[0]))*0.8);
  const drawn=[];
  for(const b of bins){ if(b.mean==null) continue; const cx=sx(b.center), top=sy(b.mean), base=sy(0);
    ctx.fillStyle='rgba(37,99,235,.7)'; ctx.fillRect(cx-bw/2, top, bw, base-top);
    if(b.lo!=null&&b.hi!=null){ ctx.strokeStyle='#1a3a8a'; ctx.beginPath();
      ctx.moveTo(cx,sy(b.lo)); ctx.lineTo(cx,sy(b.hi)); ctx.stroke(); }
    drawn.push({px:cx,py:top,b}); }
  attachHover(canvas,document.getElementById(id+'-tip'),(mx,my)=>{
    let best=null,bd=1e9; for(const d of drawn){ const dd=Math.abs(d.px-mx); if(dd<bw && dd<bd){bd=dd;best=d;} }
    return best? {px:best.px,py:best.py,html:opts.tip(best.b)}:null; });
}

function meanBurden(ws){ let nb=0,np=0; for(const w of ws){nb+=w.n_beats; np+=w.n_pvc;} return nb? 100*np/nb:null; }
function quantile(arr,q){ if(!arr.length) return null; const s=[...arr].sort((a,b)=>a-b); const i=(s.length-1)*q; const lo=Math.floor(i); return s[lo]+(s[Math.ceil(i)]-s[lo])*(i-lo); }

const W = DATA.windows;
const maxBurden = Math.max(10, Math.ceil(Math.max(...W.map(w=>w.burden||0))/10)*10);

// chart builders keyed by mode
const charts = {
  hr: {
    dots(){ scatter('hr', W.filter(w=>w.hr!=null).map(w=>({x:w.hr,y:w.burden,w})),
      {xdomain:[40,Math.max(160,Math.ceil(Math.max(...W.map(w=>w.hr||0))/10)*10)], ydomain:[0,maxBurden],
       xlabel:'Heart rate (bpm)', ylabel:'PVC burden (%)',
       color:w=>'rgba(37,99,235,.4)',
       tip:p=>`${p.w.burden.toFixed(1)}% @ ${p.x} bpm<br>${p.w.day} · ${p.w.n_pvc}/${p.w.n_beats} beats`}); },
    binned(){ const bw=5, lo=40, hi=Math.max(160,Math.ceil(Math.max(...W.map(w=>w.hr||0))/10)*10);
      const bins=[]; for(let c=lo;c<hi;c+=bw){ const ws=W.filter(w=>w.hr>=c&&w.hr<c+bw);
        const bs=ws.map(w=>w.burden); bins.push({center:c+bw/2, binw:bw, mean:meanBurden(ws),
          lo:quantile(bs,.25), hi:quantile(bs,.75), n:ws.length}); }
      bars('hr',bins,{xdomain:[lo,hi], ydomain:[0,maxBurden], binw:bw, xlabel:'Heart rate (bpm)', ylabel:'Mean PVC burden (%)',
        tip:b=>`${b.center} bpm bin<br>mean ${b.mean!=null?b.mean.toFixed(1):'-'}% · IQR ${b.lo!=null?b.lo.toFixed(0):'-'}–${b.hi!=null?b.hi.toFixed(0):'-'}%<br>${b.n} windows`}); }
  },
  tod: {
    dots(){ scatter('tod', W.map(w=>({x:w.hour,y:w.burden,w})),
      {xdomain:[0,24], ydomain:[0,maxBurden], xticks:[0,4,8,12,16,20,24], xlabel:'Time of day (h)', ylabel:'PVC burden (%)',
       color:w=>'rgba(37,99,235,.4)',
       tip:p=>`${p.w.burden.toFixed(1)}% @ ${Math.floor(p.x)}:${String(Math.round((p.x%1)*60)).padStart(2,'0')}<br>${p.w.day}`}); },
    binned(){ const bins=[]; for(let himr=0;himr<24;himr++){ const ws=W.filter(w=>Math.floor(w.hour)===himr);
        bins.push({center:himr+0.5, binw:1, mean:meanBurden(ws), n:ws.length}); }
      bars('tod',bins,{xdomain:[0,24], ydomain:[0,maxBurden], binw:1, xticks:[0,4,8,12,16,20,24], xlabel:'Hour of day', ylabel:'Mean PVC burden (%)',
        tip:b=>`${Math.floor(b.center)}:00–${Math.floor(b.center)+1}:00<br>mean ${b.mean!=null?b.mean.toFixed(1):'-'}% · ${b.n} windows`}); }
  },
  day: {
    dots(){ const days=DATA.summary.dates; const idx=Object.fromEntries(days.map((d,i)=>[d,i]));
      scatter('day', W.map(w=>({x:idx[w.day]+0.5+(Math.random()-0.5)*0.6,y:w.burden,w})),
      {xdomain:[0,days.length], ydomain:[0,maxBurden], xticks:days.map((d,i)=>i+0.5),
       xfmt:t=>days[Math.floor(t)]? days[Math.floor(t)].slice(5):'', xlabel:'Day', ylabel:'PVC burden (%)',
       color:w=>'rgba(37,99,235,.35)',
       tip:p=>`${p.w.burden.toFixed(1)}%<br>${p.w.day}`}); },
    binned(){ const days=DATA.summary.dates; const bins=days.map((d,i)=>{ const ws=W.filter(w=>w.day===d);
        const bs=ws.map(w=>w.burden); return {center:i+0.5, binw:1, mean:meanBurden(ws),
          lo:quantile(bs,.25), hi:quantile(bs,.75), n:ws.length, date:d}; });
      bars('day',bins,{xdomain:[0,days.length], ydomain:[0,maxBurden], binw:1, xticks:days.map((d,i)=>i+0.5),
        xfmt:t=>days[Math.floor(t)]? days[Math.floor(t)].slice(5):'', xlabel:'Day', ylabel:'Mean PVC burden (%)',
        tip:b=>`${b.date}<br>mean ${b.mean!=null?b.mean.toFixed(1):'-'}% · IQR ${b.lo!=null?b.lo.toFixed(0):'-'}–${b.hi!=null?b.hi.toFixed(0):'-'}%<br>${b.n} windows`}); }
  }
};

const modes = {hr:'dots', tod:'dots', day:'dots'};
function render(name){ charts[name][modes[name]](); }

document.querySelectorAll('.toggle').forEach(tg=>{
  const name=tg.dataset.chart;
  tg.querySelectorAll('button').forEach(btn=>{ btn.onclick=()=>{
    tg.querySelectorAll('button').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active'); modes[name]=btn.dataset.mode; render(name);
  };});
});

// ---- PVC burden by activity (categorical bars) ----
function renderActivity(){
  const A = DATA.activity || [];
  if(!A.length){ return; }
  document.getElementById('act-panel').style.display='';
  const labels = A.map(a=>a.label);
  const maxB = Math.max(5, Math.ceil(Math.max(...A.map(a=>a.burden||0))/5)*5);
  const bins = A.map((a,i)=>({center:i+0.5, binw:1, mean:a.burden, a}));
  bars('act', bins, {
    xdomain:[0, A.length], ydomain:[0, maxB], binw:1,
    xticks: A.map((a,i)=>i+0.5),
    xfmt:t=>{ const l=labels[Math.floor(t)]||''; return l.length>12? l.slice(0,11)+'…':l; },
    xlabel:'Activity', ylabel:'PVC burden (%)',
    tip:b=>`${b.a.label}<br>burden ${b.a.burden!=null?b.a.burden.toFixed(1):'-'}% · mean HR ${b.a.mean_hr!=null?Math.round(b.a.mean_hr):'-'}<br>${b.a.hours.toFixed(1)} h · ${b.a.n_pvc}/${nice(b.a.n_beats)} beats`});
}

// ---- heart-rate zones (incl. exercise recovery) ----
const ZONE_FILL = {rest:'#dcebff', active:'#dff3df', light:'#ffeccc', exercise:'#ffd9d9', recovery:'#e7daf6'};
const ZONE_ORDER = [['rest','rest <65'],['active','active <95'],['light','light exercise <125'],['exercise','exercise ≥125'],['recovery','exercise recovery']];
const RECOVERY_S = 2*3600;
let _zonesDone=false;
// Assign each window a zone from the 15-min average HR, then override the 2 h
// after any exercise block with 'recovery'. Cached on the window objects.
function computeZones(){
  if(_zonesDone) return; _zonesDone=true;
  const W=DATA.windows; const BIN=900; const agg={};
  for(const w of W){ if(w.hr==null) continue; const b=Math.floor(w.t/BIN); const a=agg[b]||(agg[b]={s:0,nb:0}); a.s+=w.hr*w.n_beats; a.nb+=w.n_beats; }
  const base=w=>{ if(w.hr==null) return 'none'; const a=agg[Math.floor(w.t/BIN)]; const hr=a&&a.nb?a.s/a.nb:w.hr;
    return hr<65?'rest':hr<95?'active':hr<125?'light':'exercise'; };
  const order=W.map((w,i)=>i).sort((a,b)=>W[a].t-W[b].t);
  let recUntil=-1;
  for(const i of order){ const w=W[i]; const z=base(w);
    if(z==='exercise'){ recUntil=w.t+RECOVERY_S; w._zone='exercise'; }
    else if(z!=='none' && w.t<recUntil){ w._zone='recovery'; }
    else { w._zone=z; } }
}

// ---- Day view: one chart per day, midnight..midnight ----
let dayMode='avg';
function renderDayView(){
  computeZones();
  const host=document.getElementById('dayview'); host.innerHTML='';
  const byDay={}; for(const w of DATA.windows){ (byDay[w.day]=byDay[w.day]||[]).push(w); }
  for(const d of DATA.summary.dates){
    const wins=(byDay[d]||[]).slice().sort((a,b)=>a.hour-b.hour);
    const lab=document.createElement('div'); lab.className='note'; lab.style.margin='10px 0 2px'; lab.textContent=d;
    const wrap=document.createElement('div'); wrap.className='chartwrap';
    const cv=document.createElement('canvas'); const tip=document.createElement('div'); tip.className='tip';
    wrap.appendChild(cv); wrap.appendChild(tip); host.appendChild(lab); host.appendChild(wrap);
    drawDay(cv,tip,wins);
    let nb=0,np=0; for(const x of wins){ nb+=x.n_beats; np+=x.n_pvc; }
    const cap=document.createElement('div'); cap.className='note'; cap.style.cssText='margin:2px 2px 0; text-align:right';
    cap.innerHTML = 'Day burden <b>'+(nb? (100*np/nb).toFixed(2)+'%':'—')+'</b>'+(nb? ' · '+nice(np)+'/'+nice(nb)+' beats':'');
    host.appendChild(cap);
  }
}
function drawDay(canvas, tip, wins){
  const {ctx,w,h}=setup(canvas,150);
  const area={x0:38,y0:10,x1:w-46,y1:h-22};
  const sx=lin(0,24,area.x0,area.x1);
  const syB=lin(0,50,area.y1,area.y0);          // burden % (left)
  const HRMIN=40,HRMAX=180, syH=lin(HRMIN,HRMAX,area.y1,area.y0); // HR (right)
  // background: contiguous same-zone runs (zone from 15-min HR, +recovery)
  let i=0;
  while(i<wins.length){ const z=wins[i]._zone; let j=i;
    while(j+1<wins.length && wins[j+1]._zone===z && (wins[j+1].hour-wins[j].hour)<0.1) j++;
    const col=ZONE_FILL[z];
    if(col){ const x0=sx(wins[i].hour), x1=sx(wins[j].hour+1/120);
      ctx.fillStyle=col; ctx.fillRect(x0,area.y0,Math.max(1,x1-x0),area.y1-area.y0); }
    i=j+1; }
  // dotted burden gridlines every 10%
  ctx.save(); ctx.setLineDash([2,3]); ctx.strokeStyle='#bbb'; ctx.fillStyle='#666'; ctx.font='10px sans-serif'; ctx.textAlign='right'; ctx.textBaseline='middle';
  for(const g of [10,20,30,40,50]){ const py=syB(g); ctx.beginPath(); ctx.moveTo(area.x0,py); ctx.lineTo(area.x1,py); ctx.stroke(); ctx.fillText(g+'%',area.x0-3,py); }
  ctx.restore();
  // axis box + x ticks (every 3h) + right HR ticks at the zone cut-offs
  ctx.strokeStyle='#ccc'; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(area.x0,area.y0); ctx.lineTo(area.x0,area.y1); ctx.lineTo(area.x1,area.y1); ctx.stroke();
  ctx.fillStyle='#666'; ctx.font='10px sans-serif'; ctx.textAlign='center'; ctx.textBaseline='top';
  for(let hh=0;hh<=24;hh+=3){ ctx.fillText(String(hh).padStart(2,'0'),sx(hh),area.y1+3); }
  ctx.textAlign='left'; ctx.textBaseline='middle'; ctx.fillStyle='#999';
  for(const hv of [65,95,125,155]){ const py=syH(hv); ctx.fillText(hv,area.x1+3,py);
    ctx.strokeStyle='#e3e3e3'; ctx.beginPath(); ctx.moveTo(area.x1-3,py); ctx.lineTo(area.x1,py); ctx.stroke(); }
  // HR line (grey), broken across recording gaps
  ctx.strokeStyle='rgba(110,110,110,0.55)'; ctx.lineWidth=1; ctx.beginPath(); let pen=false;
  for(let k=0;k<wins.length;k++){ const x=wins[k]; if(x.hr==null){pen=false;continue;}
    const px=sx(x.hour), py=syH(Math.max(HRMIN,Math.min(HRMAX,x.hr)));
    if(pen && (x.hour-wins[k-1].hour)<0.1) ctx.lineTo(px,py); else ctx.moveTo(px,py); pen=true; }
  ctx.stroke();
  const drawn=[];   // hover hit-targets: {px,py,hour,val,hr}
  if(dayMode==='avg'){
    // red 5-minute centred moving average of burden (beat-weighted), broken on gaps
    const HALF=2.5/60; ctx.strokeStyle='#d62828'; ctx.lineWidth=1.5; ctx.beginPath();
    let lo=0,hi=0,sP=0,sB=0,pen2=false;
    for(let k=0;k<wins.length;k++){ const c=wins[k].hour;
      while(hi<wins.length && wins[hi].hour<=c+HALF){ sP+=wins[hi].n_pvc; sB+=wins[hi].n_beats; hi++; }
      while(lo<wins.length && wins[lo].hour<c-HALF){ sP-=wins[lo].n_pvc; sB-=wins[lo].n_beats; lo++; }
      const avg=sB?100*sP/sB:0; const px=sx(c), py=syB(Math.min(50,avg));
      if(pen2 && (c-wins[k-1].hour)<0.1) ctx.lineTo(px,py); else ctx.moveTo(px,py); pen2=true;
      drawn.push({px,py,hour:c,val:avg,hr:wins[k].hr,rec:wins[k].rec,t:wins[k].t}); }
    ctx.stroke();
  } else {
    // per-window burden dots (left axis, capped at 50)
    for(const x of wins){ if(x.burden==null) continue; const px=sx(x.hour), py=syB(Math.min(50,x.burden));
      ctx.fillStyle = x.burden>0? 'rgba(37,99,235,.6)':'rgba(140,140,140,.3)';
      ctx.beginPath(); ctx.arc(px,py,1.8,0,7); ctx.fill(); drawn.push({px,py,hour:x.hour,val:x.burden,hr:x.hr,rec:x.rec,t:x.t}); }
  }
  const nearest=(mx,my)=>{ let best=null,bd=49; for(const dd of drawn){ const e=(dd.px-mx)**2+(dd.py-my)**2; if(e<bd){bd=e;best=dd;} } return best; };
  attachHover(canvas,tip,(mx,my)=>{ const best=nearest(mx,my);
    if(!best) return null; const hh=Math.floor(best.hour), mm=Math.round((best.hour%1)*60);
    const hasView=best.rec && (DATA.viewers||{})[best.rec];
    const foot=hasView? '<br><span style="opacity:.7">double-click: open ECG viewer</span>':'';
    return {px:best.px,py:best.py,html:`${String(hh).padStart(2,'0')}:${String(mm).padStart(2,'0')} · ${best.val.toFixed(1)}% · ${best.hr!=null?Math.round(best.hr)+' bpm':'-'}${foot}`}; });
  canvas.ondblclick=(e)=>{ const r=canvas.getBoundingClientRect(); const best=nearest(e.clientX-r.left,e.clientY-r.top);
    if(!best) return; const uri=best.rec && (DATA.viewers||{})[best.rec];
    if(uri) window.open(uri+'#t='+best.t,'_blank');
    else alert('No ECG viewer has been generated for this segment'+(best.rec?' ('+best.rec+')':'')+'.\n\nGenerate one with:\n  python scripts/make_viewer.py <path-to-'+(best.rec||'ecg_*.csv')+'>'); };
}
document.querySelectorAll('#dayview-toggle button').forEach(btn=>{ btn.onclick=()=>{
  document.querySelectorAll('#dayview-toggle button').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active'); dayMode=btn.dataset.mode; renderDayView(); }; });

// ---- PVC burden by heart-rate zone (categorical bars, pooled over all days) ----
function renderZone(){
  computeZones();
  const agg={}; for(const [k] of ZONE_ORDER) agg[k]=[0,0,0]; // n_beats, n_pvc, n_windows
  for(const w of DATA.windows){ const a=agg[w._zone]; if(!a) continue; a[0]+=w.n_beats; a[1]+=w.n_pvc; a[2]++; }
  const A=ZONE_ORDER.map(([k,lab])=>({key:k,label:lab,burden:agg[k][0]?100*agg[k][1]/agg[k][0]:null,
    n_beats:agg[k][0],n_pvc:agg[k][1],hours:agg[k][2]*30/3600})).filter(a=>a.n_beats>0);
  if(!A.length) return;
  const labels=A.map(a=>a.label);
  const maxB=Math.max(5, Math.ceil(Math.max(...A.map(a=>a.burden||0))/5)*5);
  const bins=A.map((a,i)=>({center:i+0.5,binw:1,mean:a.burden,a}));
  bars('zone',bins,{xdomain:[0,A.length], ydomain:[0,maxB], binw:1,
    xticks:A.map((a,i)=>i+0.5), xfmt:t=>{ const l=labels[Math.floor(t)]||''; return l.length>13?l.slice(0,12)+'…':l; },
    xlabel:'Heart-rate zone', ylabel:'PVC burden (%)',
    tip:b=>`${b.a.label}<br>burden ${b.a.burden!=null?b.a.burden.toFixed(1):'-'}%<br>${b.a.hours.toFixed(1)} h · ${b.a.n_pvc}/${nice(b.a.n_beats)} beats`});
}

// ---- rhythm states stacked per hour ----
function renderRhythm(){
  const canvas=document.getElementById('rhythm'); const {ctx,w,h}=setup(canvas,260);
  const area={x0:54,y0:12,x1:w-14,y1:h-34};
  // aggregate windows into hourly buckets along absolute time
  const t0=Math.min(...W.map(w=>w.t)), t1=Math.max(...W.map(w=>w.t));
  const hour=3600; const nb=Math.max(1,Math.ceil((t1-t0)/hour));
  const buckets=Array.from({length:nb},()=>({normal:0,bigeminy:0,trigeminy:0,other:0,beats:0,t:0}));
  for(const win of W){ const bi=Math.min(nb-1,Math.floor((win.t-t0)/hour)); const b=buckets[bi];
    for(const s of ['normal','bigeminy','trigeminy','other']) b[s]+=win.states[s]*win.n_beats;
    b.beats+=win.n_beats; b.t=win.t; }
  const xd=[0,nb], yd=[0,1];
  const dayTicks=[]; for(let i=0;i<nb;i++){ const d=new Date((t0+i*hour)*1000); if(d.getHours()===0||i===0) dayTicks.push(i); }
  const {sx,sy}=axes(ctx,area,xd,yd,'Time (hourly buckets)','Fraction of beats',dayTicks,[0,.25,.5,.75,1],
    t=>{ const d=new Date((t0+t*hour)*1000); return (d.getMonth()+1)+'/'+d.getDate(); });
  const bw=Math.max(2,(sx(1)-sx(0))*0.9); const drawn=[];
  buckets.forEach((b,i)=>{ if(!b.beats) return; let acc=0; const cx=sx(i+0.5);
    for(const s of ['normal','bigeminy','trigeminy','other']){ const f=b[s]/b.beats; if(f<=0){continue;}
      const yTop=sy(acc+f), yBot=sy(acc); ctx.fillStyle=COL[s]; ctx.fillRect(cx-bw/2,yTop,bw,yBot-yTop); acc+=f; }
    drawn.push({px:cx,py:sy(1),b}); });
  attachHover(canvas,document.getElementById('rhythm-tip'),(mx,my)=>{
    let best=null,bd=1e9; for(const d of drawn){ const dd=Math.abs(d.px-mx); if(dd<bw&&dd<bd){bd=dd;best=d;} }
    if(!best) return null; const b=best.b; const f=s=>(100*b[s]/b.beats).toFixed(0);
    const dt=new Date(b.t*1000);
    return {px:best.px,py:best.py,html:`${dt.toLocaleString()}<br>normal ${f('normal')}% · bigem ${f('bigeminy')}% · trigem ${f('trigeminy')}% · other ${f('other')}%`}; });
}

// ---- header / table ----
function header(){
  document.getElementById('title').textContent=DATA.title;
  const s=DATA.summary;
  const span = s.dates.length? `${s.dates[0]} → ${s.dates[s.dates.length-1]} · ${s.dates.length} day(s)` : 'no data';
  document.getElementById('subtitle').textContent=`${span} · ${s.coverage_hours} h recorded`;
  const cards=[['Overall burden', (s.overall_burden_pct!=null? s.overall_burden_pct+'%':'-')],
    ['Total beats', nice(s.total_beats)], ['Total PVCs', nice(s.total_pvc)],
    ['Recorded', s.coverage_hours+' h'], ['Windows', nice(s.n_windows)]];
  document.getElementById('cards').innerHTML=cards.map(c=>`<div class="card"><div class="v">${c[1]}</div><div class="k">${c[0]}</div></div>`).join('');
  const tb=document.querySelector('#daytable tbody');
  tb.innerHTML=s.per_day.map(d=>`<tr><td>${d.date}</td><td>${(d.n_windows*30/3600).toFixed(1)}</td><td>${nice(d.n_beats)}</td><td>${nice(d.n_pvc)}</td><td>${d.burden_pct!=null?d.burden_pct:'-'}</td></tr>`).join('');
}

function renderAll(){ header(); render('hr'); render('tod'); render('day'); renderDayView(); renderZone(); renderActivity(); renderRhythm(); }
renderAll();
let rt; window.addEventListener('resize',()=>{ clearTimeout(rt); rt=setTimeout(renderAll,150); });
</script>
</body>
</html>
"""
