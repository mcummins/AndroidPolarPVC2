"""Self-contained HTML ECG viewer for manual review of beat classification.

The whole ECG signal, the detected beats, and their features/labels are
embedded as JSON; rendering and interaction are vanilla JS on <canvas>, so
the file opens offline straight from Dropbox (no CDN, like report.py).

What it's for: scroll the raw ECG, see every detected beat marked and
colour-coded (normal / PVC / artifact), inspect a beat's features, and
record your own verdict (normal / PVC / artifact / unsure) for each beat.
Your verdicts persist in the browser (localStorage, keyed by recording)
and drive a live agreement panel (sensitivity / precision of the classifier
over the beats you've reviewed) plus a disagreement list. Export your
verdicts as CSV to re-tune ClassifierConfig against real ground truth.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

import numpy as np

from .classify import CLASSIFIER_VERSION
from .io import Segment
from .pipeline import LabeledBeats


def _num(x):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return round(float(x), 4)


def build_viewer(
    result: LabeledBeats,
    out_path: str,
    title: str = "ECG review",
    rec_id: str | None = None,
    classifier_version: str | None = None,
    generated: str | None = None,
) -> dict:
    """Write a standalone HTML reviewer for one labeled recording.

    classifier_version/generated are shown in the viewer header so a stale
    viewer (one skipped because the file already existed) can be told apart
    from a current one. They default to the running classifier version and
    the current local time."""
    rec = result.record
    fs = float(rec.fs)
    sig = np.round(np.asarray(rec.mv) * 1000.0).astype(int)  # integer microvolts

    segs = rec.segments or [Segment(t=rec.t, mv=rec.mv, start_index=0)]
    segments = [
        {"s": int(s.start_index), "n": int(len(s.mv)), "t0": float(s.t[0])}
        for s in segs
    ]

    beats = []
    for k, (f, lab) in enumerate(zip(result.features, result.labels)):
        beats.append(
            {
                "k": k,
                "i": int(f.index),
                "t": round(float(f.t), 3),
                "pvc": 1 if lab.is_pvc else 0,
                "r": lab.reasons,
                "rrb": _num(f.rr_before),
                "rra": _num(f.rr_after),
                "base": _num(f.baseline_rr),
                "prem": _num(f.prematurity),
                "comp": _num(f.compensatory),
                "qw": round(float(f.qrs_width_ms), 1),
                "amp": round(float(f.amplitude_mv), 3),
                "pol": int(f.polarity),
                "corr": round(float(f.template_corr), 3),
                "nr": round(float(f.noise_ratio), 2),
            }
        )

    payload = {
        "title": title,
        "rec_id": rec_id or title,
        "classifier_version": classifier_version or CLASSIFIER_VERSION,
        "generated": generated or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "fs": fs,
        "t0": float(rec.t[0]) if len(rec.t) else 0.0,
        "sig": sig.tolist(),
        "segments": segments,
        "beats": beats,
        "n_beats": result.n_beats,
        "n_pvc": result.n_pvc,
        "burden": round(result.burden_pct, 2),
    }

    html = _HTML_TEMPLATE.replace("/*DATA*/", json.dumps(payload, allow_nan=False))
    with open(out_path, "w") as fh:
        fh.write(html)
    return {
        "n_beats": result.n_beats,
        "n_pvc": result.n_pvc,
        "burden_pct": result.burden_pct,
    }


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ECG review</title>
<style>
  :root { --blue:#2563eb; --red:#d62828; --green:#1c9e63; --gray:#9aa0a6; --amber:#e0a800;
          --violet:#7c3aed; --ink:#1a1a1a; --muted:#666; --panel:#fff; --line:#e6e6e6; }
  * { box-sizing: border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         color:var(--ink); margin:0; padding:16px; background:#fafafa; }
  .wrap { max-width:1180px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 2px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:10px; }
  .cards { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:6px 12px; }
  .card .v { font-size:18px; font-weight:600; }
  .card .k { font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px 12px; margin-top:10px; }
  .ctl { display:flex; flex-wrap:wrap; align-items:center; gap:14px; font-size:13px; margin-bottom:8px; }
  .ctl label { color:var(--muted); display:inline-flex; align-items:center; gap:6px; }
  .ctl input[type=range]{ width:120px; vertical-align:middle; }
  button { font:inherit; font-size:13px; border:1px solid #d4d4d4; background:#fff; color:var(--ink);
           border-radius:7px; padding:4px 10px; cursor:pointer; }
  button:hover { background:#f2f2f2; }
  button.pri { background:var(--blue); color:#fff; border-color:var(--blue); }
  button.on  { background:var(--ink); color:#fff; border-color:var(--ink); }
  .verdicts button { padding:6px 12px; font-weight:600; }
  .vb-normal.sel { background:var(--green); color:#fff; border-color:var(--green); }
  .vb-pvc.sel    { background:var(--red); color:#fff; border-color:var(--red); }
  .vb-artifact.sel { background:var(--gray); color:#fff; border-color:var(--gray); }
  .vb-unsure.sel { background:var(--amber); color:#fff; border-color:var(--amber); }
  .vb-badpeak.sel { background:var(--violet); color:#fff; border-color:var(--violet); }
  canvas { width:100%; display:block; }
  #ov { height:64px; cursor:pointer; }
  #ecg { height:360px; cursor:crosshair; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  @media (max-width:820px){ .grid2 { grid-template-columns:1fr; } }
  table.kv { border-collapse:collapse; font-size:13px; width:100%; }
  table.kv td { padding:2px 8px; border-bottom:1px solid #f0f0f0; }
  table.kv td:first-child { color:var(--muted); white-space:nowrap; }
  table.kv td:last-child { text-align:right; font-variant-numeric:tabular-nums; }
  .legend { font-size:12px; color:var(--muted); }
  .legend span { margin-right:14px; white-space:nowrap; }
  .sw { display:inline-block; width:10px; height:10px; border-radius:2px; vertical-align:middle; margin-right:4px; }
  .tag { display:inline-block; font-size:11px; font-weight:600; padding:1px 7px; border-radius:10px; color:#fff; }
  .tag.pvc{ background:var(--red); } .tag.normal{ background:var(--green); } .tag.artifact{ background:var(--gray); }
  .disagree { max-height:230px; overflow:auto; font-size:12.5px; }
  .disagree div { padding:3px 4px; border-bottom:1px solid #f0f0f0; cursor:pointer; display:flex; justify-content:space-between; gap:8px; }
  .disagree div:hover { background:#f6f6f6; }
  .stat { font-size:13px; }
  .stat b { font-size:17px; }
  .hint { font-size:11.5px; color:var(--muted); margin-top:6px; line-height:1.5; }
  kbd { background:#eee; border:1px solid #ccc; border-bottom-width:2px; border-radius:4px; padding:0 5px; font-size:11px; font-family:inherit; }
</style>
</head>
<body>
<div class="wrap">
  <h1 id="title">ECG review</h1>
  <div class="sub" id="subtitle"></div>
  <div class="cards" id="cards"></div>

  <div class="panel">
    <div class="ctl">
      <span style="font-weight:600">View</span>
      <label>window <input id="winRange" type="range" min="2" max="20" step="1" value="6"><span id="winVal">6 s</span></label>
      <label>gain <input id="gainRange" type="range" min="1" max="12" step="0.5" value="4"><span id="gainVal">4 mV</span></label>
      <button id="prevBeat" title="previous beat (,)">‹ beat</button>
      <button id="nextBeat" title="next beat (.)">beat ›</button>
      <button id="prevPvc" title="previous PVC ([)">‹ PVC</button>
      <button id="nextPvc" title="next PVC (])">PVC ›</button>
      <label><input type="checkbox" id="advance" checked> auto-advance after verdict</label>
    </div>
    <canvas id="ov"></canvas>
    <canvas id="ecg"></canvas>
    <div class="legend" style="margin-top:6px">
      <span><span class="sw" style="background:var(--green)"></span>classifier: normal</span>
      <span><span class="sw" style="background:var(--red)"></span>classifier: PVC</span>
      <span><span class="sw" style="background:var(--gray)"></span>classifier: artifact</span>
      <span>▲ your verdict</span>
      <span><span class="sw" style="background:var(--violet)"></span>verdict: bad peak (not a real beat)</span>
      <span>grid: 0.2 s × 0.5 mV</span>
    </div>
  </div>

  <div class="grid2">
    <div class="panel">
      <div style="display:flex; justify-content:space-between; align-items:baseline">
        <h3 style="margin:2px 0 8px; font-size:15px">Selected beat <span id="beatPos" class="legend"></span></h3>
        <span id="beatTag"></span>
      </div>
      <table class="kv"><tbody id="kv"></tbody></table>
      <div class="verdicts" style="margin-top:10px; display:flex; gap:6px; flex-wrap:wrap">
        <span class="legend" style="align-self:center">Your verdict:</span>
        <button class="vb-normal"   data-v="normal">Normal <kbd>N</kbd></button>
        <button class="vb-pvc"      data-v="pvc">PVC <kbd>P</kbd></button>
        <button class="vb-artifact" data-v="artifact">Artifact <kbd>A</kbd></button>
        <button class="vb-badpeak"  data-v="badpeak">Bad Peak <kbd>B</kbd></button>
        <button class="vb-unsure"   data-v="unsure">Unsure <kbd>U</kbd></button>
        <button class="vb-clear"    data-v="">Clear <kbd>X</kbd></button>
      </div>
    </div>

    <div class="panel">
      <div style="display:flex; justify-content:space-between; align-items:baseline">
        <h3 style="margin:2px 0 8px; font-size:15px">Classifier vs your review</h3>
        <button id="export" class="pri">Export verdicts CSV</button>
      </div>
      <div id="agree" class="stat"></div>
      <div style="display:flex; gap:8px; align-items:center; margin:8px 0 4px">
        <strong style="font-size:13px">Disagreements</strong>
        <span id="dgCount" class="legend"></span>
        <button id="onlyDg" style="margin-left:auto; padding:2px 8px">jump: next disagreement <kbd>D</kbd></button>
      </div>
      <div id="disagree" class="disagree"></div>
    </div>
  </div>

  <div class="hint">
    <b>How to review:</b> jump beat-to-beat or PVC-to-PVC, judge each beat from the strip + features, and press
    <kbd>N</kbd>/<kbd>P</kbd>/<kbd>A</kbd>/<kbd>B</kbd>/<kbd>U</kbd> to record your call (<kbd>B</kbd> = bad peak: a spurious
    detection that isn't a real beat). The agreement panel updates over the beats you've
    reviewed only. &nbsp; <b>Keys:</b> <kbd>←</kbd>/<kbd>→</kbd> pan · <kbd>,</kbd>/<kbd>.</kbd> prev/next beat ·
    <kbd>[</kbd>/<kbd>]</kbd> prev/next PVC · <kbd>D</kbd> next disagreement · <kbd>N P A B U X</kbd> verdict. Verdicts are saved
    in this browser; use Export to save them as CSV for re-tuning.
  </div>
</div>

<script>
const DATA = /*DATA*/;
const SIG = DATA.sig, FS = DATA.fs, BEATS = DATA.beats, SEGS = DATA.segments;
const NS = SIG.length;
const COLOR = {normal:'#1c9e63', pvc:'#d62828', artifact:'#9aa0a6', unsure:'#e0a800', badpeak:'#7c3aed'};
const STORE_KEY = 'pvcreview:' + DATA.rec_id;

// ---- per-sample absolute time via segment table ----
function timeAt(i){
  let seg = SEGS[0];
  for(const s of SEGS){ if(i >= s.s) seg = s; else break; }
  return seg.t0 + (i - seg.s)/FS;
}
function clock(t){ const d=new Date(t*1000);
  return d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}); }

// ---- verdict store ----
let verdicts = {};
try { verdicts = JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch(e){ verdicts = {}; }
function saveVerdicts(){ try { localStorage.setItem(STORE_KEY, JSON.stringify(verdicts)); } catch(e){} }
function classifierLabel(b){ return b.r && b.r.indexOf('artifact')>=0 ? 'artifact' : (b.pvc ? 'pvc' : 'normal'); }

// ---- view state ----
let winSec = 6, mvSpan = 4;       // visible width (s) and full vertical span (mV)
let viewStart = 0;                // left-edge sample (float)
let sel = BEATS.length ? 0 : -1;  // selected beat index

function winSamples(){ return winSec * FS; }
function clampView(){ viewStart = Math.max(0, Math.min(viewStart, NS - winSamples())); if(NS<winSamples()) viewStart=0; }
function centerOn(i){ viewStart = i - winSamples()/2; clampView(); }

// ---------- main ECG canvas ----------
const ecg = document.getElementById('ecg');
function setup(canvas, h){
  const dpr = window.devicePixelRatio||1, w = canvas.clientWidth||canvas.parentElement.clientWidth;
  canvas.width=Math.round(w*dpr); canvas.height=Math.round(h*dpr);
  const ctx=canvas.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); return {ctx,w,h};
}
let ECGAREA=null;
function drawEcg(){
  const {ctx,w,h}=setup(ecg,360);
  const A={x0:54,y0:8,x1:w-10,y1:h-22}; ECGAREA=A;
  const aw=A.x1-A.x0, ah=A.y1-A.y0, yc=(A.y0+A.y1)/2;
  const wsamp=winSamples();
  const xOf = i => A.x0 + (i-viewStart)/wsamp*aw;
  const pxPerMv = ah/mvSpan;
  const yOf = mv => yc - mv*pxPerMv;
  ctx.clearRect(0,0,w,h);
  // ECG grid: big 0.2s x 0.5mV, small 0.04s x 0.1mV
  const t0s = viewStart/FS, t1s=(viewStart+wsamp)/FS;
  ctx.lineWidth=1;
  for(let small=0; small<2; small++){
    const dt = small? 0.04:0.2, dmv = small? 0.1:0.5;
    ctx.strokeStyle = small? '#fbe9ea':'#f3c0c4';
    ctx.beginPath();
    let t=Math.ceil(t0s/dt)*dt;
    for(; t<=t1s+1e-9; t+=dt){ const px=xOf(t*FS); ctx.moveTo(px,A.y0); ctx.lineTo(px,A.y1); }
    const half=mvSpan/2;
    let mv=Math.ceil(-half/dmv)*dmv;
    for(; mv<=half+1e-9; mv+=dmv){ const py=yOf(mv); if(py<A.y0||py>A.y1) continue; ctx.moveTo(A.x0,py); ctx.lineTo(A.x1,py); }
    ctx.stroke();
  }
  // axis frame + mV labels
  ctx.strokeStyle='#ccc'; ctx.beginPath(); ctx.moveTo(A.x0,A.y0); ctx.lineTo(A.x0,A.y1); ctx.lineTo(A.x1,A.y1); ctx.stroke();
  ctx.fillStyle='#888'; ctx.font='10px sans-serif'; ctx.textAlign='right'; ctx.textBaseline='middle';
  for(let mv=-Math.floor(mvSpan/2*2)/2; mv<=mvSpan/2; mv+=0.5){ const py=yOf(mv); if(py<A.y0||py>A.y1) continue; ctx.fillText(mv.toFixed(1), A.x0-5, py); }
  // x time ticks (clock)
  ctx.textAlign='center'; ctx.textBaseline='top'; ctx.fillStyle='#666';
  const tickStep = winSec<=4?0.5:(winSec<=10?1:2);
  for(let t=Math.ceil(t0s/tickStep)*tickStep; t<=t1s; t+=tickStep){ const px=xOf(t*FS);
    if(px<A.x0||px>A.x1) continue; ctx.fillText(clock(timeAt(t*FS)), px, A.y1+4); }
  // segment-gap dividers
  ctx.strokeStyle='#c026d3'; ctx.setLineDash([4,3]);
  for(const s of SEGS){ if(s.s<=viewStart || s.s>=viewStart+wsamp) continue; const px=xOf(s.s);
    ctx.beginPath(); ctx.moveTo(px,A.y0); ctx.lineTo(px,A.y1); ctx.stroke(); }
  ctx.setLineDash([]);
  // signal
  ctx.strokeStyle='#111'; ctx.lineWidth=1.1; ctx.beginPath();
  const i0=Math.max(0,Math.floor(viewStart)), i1=Math.min(NS-1,Math.ceil(viewStart+wsamp));
  let started=false;
  for(let i=i0;i<=i1;i++){ const px=xOf(i), py=yOf(SIG[i]/1000);
    if(!started){ ctx.moveTo(px,py); started=true; } else ctx.lineTo(px,py); }
  ctx.stroke();
  // beats in view
  ctx.textAlign='center';
  for(const b of BEATS){ if(b.i<viewStart-2 || b.i>viewStart+wsamp+2) continue;
    const px=xOf(b.i), cl=classifierLabel(b), col=COLOR[cl];
    const isSel = b.k===sel;
    if(isSel){ ctx.fillStyle='rgba(37,99,235,.10)'; ctx.fillRect(px-9,A.y0,18,A.y1-A.y0); }
    ctx.strokeStyle=col; ctx.globalAlpha=isSel?0.95:0.5; ctx.lineWidth=isSel?2:1;
    ctx.beginPath(); ctx.moveTo(px,A.y0); ctx.lineTo(px,A.y1); ctx.stroke(); ctx.globalAlpha=1;
    // R-peak dot
    const py=yOf(SIG[b.i]/1000); ctx.fillStyle=col; ctx.beginPath(); ctx.arc(px,py,isSel?4:2.6,0,7); ctx.fill();
    // classifier tag at top
    if(cl!=='normal'){ ctx.fillStyle=col; ctx.font='bold 10px sans-serif'; ctx.textBaseline='top';
      ctx.fillText(cl==='pvc'?'V':'×', px, A.y0+1); }
    // user verdict marker (triangle below top)
    const v=verdicts[b.k];
    if(v){ ctx.fillStyle=COLOR[v]||'#000'; ctx.beginPath();
      const ty=A.y0+14; ctx.moveTo(px,ty+7); ctx.lineTo(px-5,ty); ctx.lineTo(px+5,ty); ctx.closePath(); ctx.fill(); }
  }
}

// ---------- overview canvas ----------
let ovEnv=null, ovW=0;
function buildEnv(w){
  const cols=Math.max(1,Math.floor(w)); ovEnv=new Array(cols);
  const per=NS/cols;
  for(let c=0;c<cols;c++){ let lo=1e9,hi=-1e9; const a=Math.floor(c*per), bnd=Math.floor((c+1)*per);
    for(let i=a;i<bnd;i++){ const v=SIG[i]; if(v<lo)lo=v; if(v>hi)hi=v; }
    ovEnv[c]=[lo,hi]; }
  ovW=cols;
}
function drawOv(){
  const {ctx,w,h}=setup(document.getElementById('ov'),64);
  if(!ovEnv||ovW!==Math.floor(w)) buildEnv(w);
  ctx.clearRect(0,0,w,h);
  const mid=h/2, half=h/2-3;
  let amp=1; for(const e of ovEnv){ amp=Math.max(amp,Math.abs(e[0]),Math.abs(e[1])); }
  ctx.strokeStyle='#c9ccd1'; ctx.beginPath();
  for(let c=0;c<ovEnv.length;c++){ const e=ovEnv[c]; ctx.moveTo(c+0.5, mid-(e[1]/amp)*half); ctx.lineTo(c+0.5, mid-(e[0]/amp)*half); }
  ctx.stroke();
  // PVC / artifact ticks
  for(const b of BEATS){ const cl=classifierLabel(b); if(cl==='normal') continue;
    const x=b.i/NS*w; ctx.strokeStyle=COLOR[cl]; ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,h); ctx.stroke(); }
  // view window box
  const x0=viewStart/NS*w, x1=(viewStart+winSamples())/NS*w;
  ctx.fillStyle='rgba(37,99,235,.16)'; ctx.fillRect(x0,0,Math.max(2,x1-x0),h);
  ctx.strokeStyle='var(--blue)'; ctx.strokeStyle='#2563eb'; ctx.strokeRect(x0,0,Math.max(2,x1-x0),h);
}

// ---------- selection + feature panel ----------
function fmt(x,d){ return x==null? '—' : (typeof x==='number'? x.toFixed(d) : x); }
function selectBeat(k, recenter){
  if(k<0||k>=BEATS.length) return; sel=k; const b=BEATS[k];
  if(recenter!==false) centerOn(b.i);
  const cl=classifierLabel(b);
  document.getElementById('beatPos').textContent = `(${k+1} of ${BEATS.length})`;
  document.getElementById('beatTag').innerHTML = `<span class="tag ${cl}">classifier: ${cl}</span>`;
  const rows=[
    ['time', clock(b.t) + '  (' + b.t.toFixed(2) + 's)'],
    ['heart rate', b.rrb? Math.round(60/b.rrb)+' bpm  (instantaneous)' : '—'],
    ['RR before', b.rrb==null?'—':b.rrb.toFixed(3)+' s'],
    ['RR after', b.rra==null?'—':b.rra.toFixed(3)+' s'],
    ['baseline RR', b.base==null?'—':b.base.toFixed(3)+' s'],
    ['prematurity', b.prem==null?'—':b.prem.toFixed(2)+'  (RR/baseline)'],
    ['compensatory', b.comp==null?'—':b.comp.toFixed(2)],
    ['QRS width', b.qw.toFixed(0)+' ms'],
    ['amplitude', b.amp.toFixed(2)+' mV  ('+(b.pol>0?'upright':'inverted')+')'],
    ['template corr', b.corr.toFixed(2)],
    ['noise ratio', b.nr==null?'—':b.nr.toFixed(2)+'×'],
    ['reasons fired', b.r||'—'],
  ];
  document.getElementById('kv').innerHTML = rows.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('');
  // highlight active verdict button
  const v=verdicts[k]||'';
  document.querySelectorAll('.verdicts button').forEach(btn=>btn.classList.toggle('sel', btn.dataset.v===v));
  draw();
}
function setVerdict(v){
  if(sel<0) return;
  if(v) verdicts[sel]=v; else delete verdicts[sel];
  saveVerdicts(); updateStats();
  document.querySelectorAll('.verdicts button').forEach(btn=>btn.classList.toggle('sel', btn.dataset.v===v));
  if(v && document.getElementById('advance').checked){ if(sel<BEATS.length-1) selectBeat(sel+1); else draw(); }
  else draw();
}

// ---------- navigation ----------
function nextBeat(d){ let k=sel+d; if(k<0)k=0; if(k>=BEATS.length)k=BEATS.length-1; selectBeat(k); }
function nextPvc(d){ let k=sel;
  for(let j=sel+d; j>=0 && j<BEATS.length; j+=d){ if(BEATS[j].pvc){ k=j; break; } }
  selectBeat(k);
}
function nextDisagreement(){
  for(let off=1; off<=BEATS.length; off++){ const j=(sel+off)%BEATS.length; const v=verdicts[j];
    if(v && v!=='unsure' && disagrees(BEATS[j],v)){ selectBeat(j); return; } }
}

// ---------- agreement stats ----------
function userIsPvc(v){ return v==='pvc'; }
// 'badpeak' (a spurious detection, not a real beat) and 'unsure' are detection-
// quality labels, not PVC calls, so they are excluded from the PVC confusion
// matrix and disagreement list (but still recorded and exported as ground truth).
function disagrees(b,v){ if(!v||v==='unsure'||v==='badpeak') return false; return userIsPvc(v) !== !!b.pvc; }
function updateStats(){
  let tp=0,fp=0,fn=0,tn=0,reviewed=0,unsure=0,badpeak=0;
  for(const b of BEATS){ const v=verdicts[b.k]; if(!v) continue;
    if(v==='unsure'){ unsure++; continue; }
    if(v==='badpeak'){ badpeak++; continue; } reviewed++;
    const up=userIsPvc(v), cp=!!b.pvc;
    if(up&&cp)tp++; else if(!up&&cp)fp++; else if(up&&!cp)fn++; else tn++;
  }
  const sens = (tp+fn)? tp/(tp+fn) : null;
  const prec = (tp+fp)? tp/(tp+fp) : null;
  const acc  = reviewed? (tp+tn)/reviewed : null;
  const pct=x=>x==null?'—':(100*x).toFixed(0)+'%';
  document.getElementById('agree').innerHTML =
    `Reviewed <b>${reviewed}</b> beats (${unsure} unsure, ${badpeak} bad peaks) of ${BEATS.length}.<br>`+
    `<div style="margin-top:6px; display:flex; gap:18px; flex-wrap:wrap">`+
    `<span>Sensitivity <b>${pct(sens)}</b><br><span class="legend">PVCs you found that it caught</span></span>`+
    `<span>Precision <b>${pct(prec)}</b><br><span class="legend">its PVC calls you agree with</span></span>`+
    `<span>Accuracy <b>${pct(acc)}</b></span></div>`+
    `<div class="legend" style="margin-top:6px">TP ${tp} · FP ${fp} · FN ${fn} · TN ${tn} `+
    `&nbsp; (FP = it cried PVC, you said no; FN = it missed a PVC you saw)</div>`;
  // disagreement list
  const dg=[];
  for(const b of BEATS){ const v=verdicts[b.k]; if(disagrees(b,v)) dg.push({b,v}); }
  document.getElementById('dgCount').textContent = dg.length? `(${dg.length})` : '(none yet)';
  document.getElementById('disagree').innerHTML = dg.map(({b,v})=>{
    const kind = b.pvc? 'FP: classifier PVC, you said '+v
      : (classifierLabel(b)==='artifact' ? 'FN: you said PVC, classifier excluded (bad signal)'
        : 'FN: you said PVC, classifier normal');
    return `<div data-k="${b.k}"><span>${clock(b.t)} · beat ${b.k+1}</span><span style="color:${b.pvc?COLOR.pvc:COLOR.normal}">${kind}</span></div>`;
  }).join('') || '<div class="legend" style="cursor:default">No disagreements among reviewed beats.</div>';
  document.querySelectorAll('#disagree div[data-k]').forEach(el=>{
    el.onclick=()=>selectBeat(+el.dataset.k); });
}

// ---------- export ----------
function exportCsv(){
  const head=['beat_index','sample_index','time_s','clock','classifier_is_pvc','classifier_reasons','your_verdict','agree'];
  const lines=[head.join(',')];
  for(const b of BEATS){ const v=verdicts[b.k]||''; if(!v) continue;
    const agree = (v==='unsure'||v==='badpeak')? '' : (disagrees(b,v)? '0':'1');
    lines.push([b.k,b.i,b.t.toFixed(3),clock(b.t),b.pvc,'"'+(b.r||'')+'"',v,agree].join(','));
  }
  const blob=new Blob([lines.join('\n')],{type:'text/csv'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download = (DATA.rec_id||'review').replace(/[^\w.-]+/g,'_') + '_verdicts.csv';
  document.body.appendChild(a); a.click(); a.remove();
}

// ---------- interaction wiring ----------
function draw(){ clampView(); drawEcg(); drawOv(); }

const ovEl=document.getElementById('ov');
function ovSeek(e){ const r=ovEl.getBoundingClientRect(); const f=(e.clientX-r.left)/r.width;
  viewStart = f*NS - winSamples()/2; clampView(); draw(); }
ovEl.onmousedown=e=>{ ovSeek(e); const mv=ev=>ovSeek(ev); const up=()=>{document.removeEventListener('mousemove',mv);document.removeEventListener('mouseup',up);};
  document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up); };

// click main canvas -> select nearest beat; drag -> pan
let dragX=null, dragView=0, moved=false;
ecg.onmousedown=e=>{ dragX=e.clientX; dragView=viewStart; moved=false; };
window.addEventListener('mousemove',e=>{ if(dragX==null) return; const dx=e.clientX-dragX;
  if(Math.abs(dx)>3) moved=true; const aw=(ECGAREA.x1-ECGAREA.x0);
  viewStart=dragView - dx/aw*winSamples(); clampView(); draw(); });
window.addEventListener('mouseup',e=>{ if(dragX==null) return;
  if(!moved && ECGAREA){ const r=ecg.getBoundingClientRect(); const mx=e.clientX-r.left;
    const aw=ECGAREA.x1-ECGAREA.x0; const samp=viewStart+(mx-ECGAREA.x0)/aw*winSamples();
    // nearest beat
    let best=-1,bd=1e18; for(const b of BEATS){ const d=Math.abs(b.i-samp); if(d<bd){bd=d;best=b.k;} }
    if(best>=0 && bd < winSamples()*0.25) selectBeat(best,false);
  } dragX=null; });
ecg.addEventListener('wheel',e=>{ e.preventDefault(); const f=e.deltaY>0?1.15:0.87;
  winSec=Math.max(2,Math.min(20,winSec*f)); document.getElementById('winRange').value=Math.round(winSec);
  document.getElementById('winVal').textContent=winSec.toFixed(0)+' s'; draw(); }, {passive:false});

document.getElementById('winRange').oninput=e=>{ winSec=+e.target.value; document.getElementById('winVal').textContent=winSec+' s'; draw(); };
document.getElementById('gainRange').oninput=e=>{ mvSpan=+e.target.value; document.getElementById('gainVal').textContent=mvSpan+' mV'; draw(); };
document.getElementById('prevBeat').onclick=()=>nextBeat(-1);
document.getElementById('nextBeat').onclick=()=>nextBeat(1);
document.getElementById('prevPvc').onclick=()=>nextPvc(-1);
document.getElementById('nextPvc').onclick=()=>nextPvc(1);
document.getElementById('onlyDg').onclick=nextDisagreement;
document.getElementById('export').onclick=exportCsv;
document.querySelectorAll('.verdicts button').forEach(btn=>btn.onclick=()=>setVerdict(btn.dataset.v));

document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT') return;
  const k=e.key, panStep=winSamples()*(e.shiftKey?0.9:0.18);
  if(k==='ArrowRight'){ viewStart+=panStep; clampView(); draw(); e.preventDefault(); }
  else if(k==='ArrowLeft'){ viewStart-=panStep; clampView(); draw(); e.preventDefault(); }
  else if(k===','){ nextBeat(-1); } else if(k==='.'){ nextBeat(1); }
  else if(k==='['){ nextPvc(-1); } else if(k===']'){ nextPvc(1); }
  else if(k==='d'||k==='D'){ nextDisagreement(); }
  else if(k==='n'||k==='N'){ setVerdict('normal'); }
  else if(k==='p'||k==='P'){ setVerdict('pvc'); }
  else if(k==='a'||k==='A'){ setVerdict('artifact'); }
  else if(k==='b'||k==='B'){ setVerdict('badpeak'); }
  else if(k==='u'||k==='U'){ setVerdict('unsure'); }
  else if(k==='x'||k==='X'){ setVerdict(''); }
});

// ---------- header ----------
function header(){
  document.getElementById('title').textContent=DATA.title;
  const dur=NS/FS; let span = (DATA.t0? clock(DATA.t0)+' → '+clock(DATA.t0+dur)+' · ':'') + (dur/60).toFixed(1)+' min · '+FS+' Hz';
  if(DATA.classifier_version) span += ' · classifier v'+DATA.classifier_version;
  if(DATA.generated) span += ' · generated '+DATA.generated;
  document.getElementById('subtitle').textContent=span;
  const cards=[['Beats',DATA.n_beats],['Classifier PVCs',DATA.n_pvc],['Burden',DATA.burden+'%'],
    ['Segments',SEGS.length]];
  document.getElementById('cards').innerHTML=cards.map(c=>`<div class="card"><div class="v">${c[1]}</div><div class="k">${c[0]}</div></div>`).join('');
}

header();
if(BEATS.length){
  // deep-link: #t=<absolute epoch seconds> jumps to the nearest beat (used by
  // the exploration report's Day-view double-click drill-down)
  let start=0;
  const ht=new URLSearchParams(location.hash.slice(1)).get('t');
  if(ht!=null && ht!==''){ const target=+ht; let bd=Infinity;
    for(const b of BEATS){ const d=Math.abs(b.t-target); if(d<bd){ bd=d; start=b.k; } } }
  selectBeat(start);
} else { draw(); }
updateStats();
let rt; window.addEventListener('resize',()=>{ clearTimeout(rt); rt=setTimeout(()=>{ ovEnv=null; draw(); },150); });
</script>
</body>
</html>
"""
