/* Decision Replay: animates the rules engine's path for one airfield against its TAF.
   Uses globals from index.html: AF, WX, evaluate, etaDate, parseTAF, at, esc, fmtC, fmtV, OUT. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const N = {
    n1: { x: 140, y: 40, t: "Prevailing ±1 h above MSA, vis ≥ 5 km?" },
    n2: { x: 140, y: 115, t: "Offshore to onshore?" },
    gA: { x: 140, y: 195, t: "Gate A: minima +700 / 2500 m" },
    co: { x: 140, y: 275, t: "Coastal aerodrome?" },
    dy: { x: 140, y: 350, t: "Daylight? (SR−30 to SS+30)" },
    cg: { x: 140, y: 425, t: "Coastal gate: +400 ft / 4000 m" },
    se: { x: 140, y: 500, t: "Let-down over sea: 600 ft / 4000 m" },
    gB: { x: 650, y: 195, t: "Gate B (±2 h): +400 ft / 3000 m" },
    ap: { x: 650, y: 290, t: "Two independent approaches?" },
    gC: { x: 650, y: 390, t: "Gate C (±1 h): at approach minima?" },
    t1: { x: 385, y: 40, t: "No alternate", term: 1, w: 120 },
    t2: { x: 385, y: 195, t: "No alternate", term: 1, w: 120 },
    t3: { x: 385, y: 555, t: "No alt + coastal fuel", term: 1, w: 150 },
    t4: { x: 850, y: 290, t: "No alt + 15 min", term: 1, w: 120 },
    one: { x: 575, y: 500, t: "1 alternate", term: 1, w: 130 },
    two: { x: 735, y: 500, t: "2 alternates", term: 1, w: 130 },
  };
  const E = [
    ["n1", "t1", "M255 40 H325", "YES"], ["n1", "n2", "M140 62 V93", "NO"],
    ["n2", "gA", "M140 137 V173", "YES"], ["n2", "gB", "M255 115 H650 V173", "NO"],
    ["gA", "t2", "M255 195 H325", "YES"], ["gA", "co", "M140 217 V253", "NO"],
    ["co", "dy", "M140 297 V328", "YES"], ["co", "gB", "M255 275 H460 V195 H535", "NO"],
    ["dy", "cg", "M140 372 V403", "YES"], ["dy", "gB", "M255 350 H460 V195 H535", "NO"],
    ["cg", "se", "M140 447 V478", "YES"], ["cg", "gB", "M255 425 H460 V195 H535", "NO"],
    ["se", "t3", "M140 522 V555 H310", "OK"], ["se", "gB", "M255 500 H460 V195 H535", "NO"],
    ["gB", "ap", "M650 217 V268", "YES"], ["gB", "gC", "M765 195 H800 V390 H765", "NO"],
    ["ap", "t4", "M765 290 H790", "YES"], ["ap", "gC", "M650 312 V368", "NO"],
    ["gC", "one", "M600 412 V478", "YES"], ["gC", "two", "M700 412 V478", "NO"],
  ];
  const W = 230, H = 44;
  const ov = document.createElement("div");
  ov.id = "replay";
  ov.hidden = true;
  ov.setAttribute("role", "dialog");
  ov.setAttribute("aria-modal", "true");
  ov.innerHTML = `
  <div class="rp-wrap">
    <div class="rp-head"><div><span class="rp-icao" id="rpIcao"></span> <span class="rp-meta" id="rpMeta"></span></div>
      <span class="rp-pill" id="rpPill"></span><button class="btn" id="rpClose" aria-label="Close replay">Close</button></div>
    <div class="rp-stage">
      <div class="rp-box"><svg id="rpFlow" viewBox="0 0 920 590" role="img" aria-label="Decision flowchart"></svg></div>
      <div class="rp-box rp-cap" aria-live="polite">
        <div class="rp-tafbox"><div class="rp-k" id="rpTafK">TAF used</div><div class="rp-taf" id="rpTaf"></div></div>
        <div class="rp-k" id="rpStep"></div><h2 id="rpTitle"></h2><p id="rpText"></p>
        <div class="rp-checks" id="rpChecks"></div><div class="rp-verdict" id="rpVerdict"></div>
      </div>
    </div>
    <div class="rp-box rp-tl"><svg id="rpTl" viewBox="0 0 920 278" role="img" aria-label="TAF timeline"></svg></div>
    <div class="rp-controls">
      <button class="btn" id="rpBack">◀ Back</button><button class="btn primary" id="rpPlay">Pause</button><button class="btn" id="rpNext">Next ▶</button>
      <div class="rp-prog" id="rpProg"></div>
      <label class="rp-k" for="rpSpeed">Speed</label>
      <select id="rpSpeed"><option value="1.6">Slow (training)</option><option value="1" selected>Normal</option><option value="0.45">Fast</option></select>
    </div>
  </div>`;
  document.body.appendChild(ov);
  const $ = (s) => ov.querySelector(s);
  const flow = $("#rpFlow"), tl = $("#rpTl");
  const mk = (t, a, p) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };

  // flowchart, drawn once
  const edgeEls = {}, nodeEls = {};
  E.forEach(([a, b, d, l]) => {
    const p = mk("path", { d, class: "rp-edge" }, flow); edgeEls[a + ">" + b] = p;
    const pt = p.getPointAtLength(Math.min(18, p.getTotalLength() / 2)), horiz = /^M[\d.]+ [\d.]+ H/.test(d);
    mk("text", { x: horiz ? pt.x : pt.x + 6, y: horiz ? pt.y - 7 : pt.y + 4, class: "rp-elab", "text-anchor": horiz ? "middle" : "start" }, flow).textContent = l;
  });
  for (const [k, n] of Object.entries(N)) {
    const w = n.w || W, g = mk("g", { class: "rp-node" + (n.term ? " term" : "") }, flow);
    mk("rect", { x: n.x - w / 2, y: n.y - H / 2, width: w, height: H, rx: n.term ? 20 : 8 }, g);
    const ring = mk("rect", { x: n.x - w / 2, y: n.y - H / 2, width: w, height: H, rx: n.term ? 20 : 8, fill: "none", stroke: "var(--gold)", "stroke-opacity": 0 }, g);
    mk("text", { x: n.x, y: n.y + 4, "text-anchor": "middle" }, g).textContent = n.t;
    nodeEls[k] = { g, ring };
  }
  const dot = mk("circle", { r: 6, cx: -20, cy: -20, class: "rp-dot" }, flow);

  // timeline scaffolding, rebuilt per airfield
  // Stepped scales: equal spacing between operationally meaningful values, so fog and low cloud are readable.
  const X0 = 96, X1 = 900;
  let CT = [0, 200, 500, 1000, 1500, 3000], VT = [0, 500, 1000, 1500, 3000, 5000, 10000], ZONES = [];
  const CY0 = 128, CY1 = 25, VY0 = 249, VY1 = 142;
  const step = (ticks, v, y0, y1, top) => {
    if (v === Infinity || v == null) return top;
    const n = ticks.length - 1, seg = (y0 - y1) / (n + 1);
    if (v >= ticks[n]) return y0 - seg * n - Math.min(0.7, (v - ticks[n]) / ticks[n] * 0.7) * seg;
    let i = 0; while (v > ticks[i + 1]) i++;
    return y0 - seg * (i + (v - ticks[i]) / (ticks[i + 1] - ticks[i]));
  };
  const cy = (f) => step(CT, f, CY0, CY1, CY1);
  const vy = (m) => step(VT, Math.min(m ?? 10000, 10000), VY0, VY1, VY1);
  const cLab = (f) => (f === Infinity || f == null ? "no ceiling" : f + " ft");
  const vLab = (m) => (m == null || m >= 10000 ? "10 km+" : m >= 5000 ? m / 1000 + " km" : m + " m");
  let T0, T1, tx, win, wl, wr, wlab, thrC, thrCL, thrV, thrVL, wC, wV;

  // Zone boundaries come from this airfield's own minima and gates, so each band has equal height.
  function zonesFor(af) {
    const mh = af.dh != null ? af.dh : af.mdh, rvr = af.dh != null && af.rvrB != null ? af.rvrB : af.rvrA;
    const gA = mh != null ? Math.max(mh + 700, 1000) : 1000, msa = af.msa != null ? af.msa - af.elev : null;
    const uniq = (a) => [...new Set(a.filter((v) => v != null && isFinite(v)))].sort((x, y) => x - y);
    CT = uniq([0, mh, mh != null ? mh + 400 : null, gA, msa]);
    VT = uniq([0, rvr, 2500, 3000, 4000, 5000, 10000]);
    const cName = (lo) => lo === 0 ? "below minima" : lo === mh ? "minima to +400 ft" : lo === mh + 400 ? "+400 ft to Gate A" : lo === gA ? "Gate A to MSA" : "above MSA";
    const vName = (lo) => ({ 0: "below minima", [rvr]: "minima to 2500 m", 2500: "Gate A to Gate B", 3000: "Gate B to coastal", 4000: "coastal to 5 km", 5000: "5 km +", 10000: "" })[lo] ?? "";
    ZONES = [];
    CT.forEach((lo, i) => ZONES.push({ k: "c", lo, hi: CT[i + 1] ?? Infinity, name: cName(lo), rank: i, n: CT.length }));
    VT.slice(0, -1).forEach((lo, i) => ZONES.push({ k: "v", lo, hi: VT[i + 1], name: vName(lo), rank: i, n: VT.length - 1 }));
  }
  function paintZones(thr) {
    ZONES.forEach((z) => {
      let cls = "rp-z rp-z" + Math.min(4, Math.round((z.rank / Math.max(1, z.n - 1)) * 4));
      const need = thr ? (z.k === "c" ? thr.c : thr.v) : null;
      if (need != null) cls = "rp-z " + (z.lo >= need ? "rp-zok" : "rp-zbad");
      z.el.setAttribute("class", cls);
      z.lab.setAttribute("class", "rp-zlab rp-halo" + (need != null ? (z.lo >= need ? " ok" : " bad") : ""));
    });
  }

  function buildTimeline(taf, eta, af) {
    tl.innerHTML = "";
    zonesFor(af);
    T0 = Math.max(+taf.from, +eta - 6 * 36e5); T1 = Math.min(+taf.to, +eta + 12 * 36e5);
    tx = (t) => X0 + ((Math.min(Math.max(+t, T0), T1) - T0) / (T1 - T0)) * (X1 - X0);
    const lane = (y, txt) => mk("text", { x: 8, y, class: "rp-lane" }, tl).textContent = txt;
    lane(CY1 - 14, "CEILING"); lane(VY1 - 14, "VISIBILITY");
    ZONES.forEach((z) => {
      const f = z.k === "c" ? cy : vy, y0 = f(z.lo), y1 = f(z.hi === Infinity ? Infinity : z.hi);
      z.el = mk("rect", { x: X0, y: Math.min(y0, y1), width: X1 - X0, height: Math.abs(y0 - y1) }, tl);
      z.lab = mk("text", { x: X1 - 6, y: (y0 + y1) / 2 + 4, "text-anchor": "end" }, tl);
      z.lab.textContent = z.name;
    });
    paintZones(null);
    mk("text", { x: X0 - 8, y: CY1 + 4, "text-anchor": "end", class: "rp-ax" }, tl).textContent = "none";
    CT.forEach((f) => { mk("line", { x1: X0, x2: X1, y1: cy(f), y2: cy(f), class: "rp-grid" }, tl); mk("text", { x: X0 - 8, y: cy(f) + 4, "text-anchor": "end", class: "rp-ax" }, tl).textContent = f ? f + " ft" : "0"; });
    VT.forEach((m) => { mk("line", { x1: X0, x2: X1, y1: vy(m), y2: vy(m), class: "rp-grid" }, tl); mk("text", { x: X0 - 8, y: vy(m) + 4, "text-anchor": "end", class: "rp-ax" }, tl).textContent = m ? (m >= 5000 ? m / 1000 + " km" : m + " m") : "0"; });
    for (let t = Math.ceil(T0 / 72e5) * 72e5; t <= T1; t += 72e5) {
      mk("line", { x1: tx(t), x2: tx(t), y1: CY1 - 4, y2: VY0, class: "rp-vgrid" }, tl);
      mk("text", { x: tx(t), y: VY0 + 20, "text-anchor": "middle", class: "rp-ax" }, tl).textContent = new Date(t).toISOString().slice(11, 13) + "Z";
    }
    // sample every 10 minutes: prevailing line, plus TEMPO/PROB as a shaded band down to the worse value
    const P = [], Q = [];
    for (let t = T0; t <= T1; t += 6e5) { const c = at(taf, new Date(t)); P.push([t, c.prev]); Q.push([t, c.worst]); }
    const line = (arr, f, k) => arr.map(([t, v], i) => `${i ? "L" : "M"}${tx(t).toFixed(1)} ${f(v[k]).toFixed(1)}`).join("");
    const band = (f, k, dflt) => {
      let d = "", run = [];
      const flush = () => { if (run.length > 1) d += "M" + run.map(([t, a]) => `${tx(t).toFixed(1)} ${f(a).toFixed(1)}`).join("L") + "L" + run.slice().reverse().map(([t, , b]) => `${tx(t).toFixed(1)} ${f(b).toFixed(1)}`).join("L") + "Z"; run = []; };
      P.forEach(([t, p], i) => { const a = p[k] ?? dflt, b = Q[i][1][k] ?? dflt; if (b < a) run.push([t, a, b]); else flush(); });
      flush(); return d;
    };
    const bc = band(cy, "cig", Infinity), bv = band(vy, "vis", 10000);
    if (bc) mk("path", { d: bc, class: "rp-tempo" }, tl);
    if (bv) mk("path", { d: bv, class: "rp-tempo" }, tl);
    mk("path", { d: line(P, cy, "cig"), class: "rp-prev-line" }, tl);
    mk("path", { d: line(P, vy, "vis"), class: "rp-prev-line" }, tl);
    const lg = mk("g", {}, tl);
    mk("line", { x1: X1 - 250, x2: X1 - 228, y1: 10, y2: 10, class: "rp-prev-line" }, lg); mk("text", { x: X1 - 222, y: 14, class: "rp-ax" }, lg).textContent = "forecast";
    mk("rect", { x: X1 - 150, y: 4, width: 22, height: 12, class: "rp-tempo" }, lg); mk("text", { x: X1 - 122, y: 14, class: "rp-ax" }, lg).textContent = "TEMPO / PROB";
    // window and ETA
    win = mk("rect", { x: tx(eta), y: CY1 - 6, width: 0, height: VY0 - CY1 + 6, class: "rp-win" }, tl);
    wl = mk("line", { x1: tx(eta), x2: tx(eta), y1: CY1 - 6, y2: VY0, class: "rp-winedge" }, tl);
    wr = mk("line", { x1: tx(eta), x2: tx(eta), y1: CY1 - 6, y2: VY0, class: "rp-winedge" }, tl);
    wlab = mk("text", { x: tx(eta), y: CY1 - 10, "text-anchor": "middle", class: "rp-winlab" }, tl);
    mk("line", { x1: tx(eta), x2: tx(eta), y1: CY1 - 6, y2: VY0, class: "rp-eta" }, tl);
    mk("text", { x: tx(eta), y: VY0 + 34, "text-anchor": "middle", class: "rp-thrlab" }, tl).textContent = "ETA " + new Date(eta).toISOString().slice(11, 16) + "Z";
    // forecast values at ETA
    const c0 = at(taf, eta), lx = tx(eta) + 6;
    mk("text", { x: lx, y: cy(c0.prev.cig ?? Infinity) - 6, class: "rp-val" }, tl).textContent = cLab(c0.prev.cig ?? Infinity);
    mk("text", { x: lx, y: vy(c0.prev.vis ?? 10000) - 6, class: "rp-val" }, tl).textContent = vLab(c0.prev.vis ?? 10000);
    thrC = mk("line", { x1: X0, x2: X1, y1: cy(0), y2: cy(0), class: "rp-thr", opacity: 0 }, tl);
    thrCL = mk("text", { x: X0 + 6, y: cy(0) - 5, "text-anchor": "start", class: "rp-thrlab rp-halo", opacity: 0 }, tl);
    thrV = mk("line", { x1: X0, x2: X1, y1: vy(0), y2: vy(0), class: "rp-thr", opacity: 0 }, tl);
    thrVL = mk("text", { x: X0 + 6, y: vy(0) - 5, "text-anchor": "start", class: "rp-thrlab rp-halo", opacity: 0 }, tl);
    wC = mk("circle", { r: 6, class: "rp-worst", opacity: 0 }, tl);
    wV = mk("circle", { r: 6, class: "rp-worst", opacity: 0 }, tl);
  }

  // which TAF words produced a value: cloud/visibility token plus its change-group header
  function tokensFor(text, cig, vis, mode) {
    const tk = text.toUpperCase().replace(/=/g, "").split(/\s+/).filter(Boolean);
    const grp = []; let g = { start: 0, temp: false };
    tk.forEach((t, i) => {
      if (/^(TEMPO|BECMG|PROB\d\d|FM\d{6})$/.test(t) && !(t === "TEMPO" && /^PROB\d\d$/.test(tk[i - 1] || ""))) g = { start: i, temp: /^(TEMPO|PROB)/.test(t) };
      grp[i] = g;
    });
    const used = new Set();
    const pick = (test) => {
      let best = -1;
      tk.forEach((t, i) => { if (test(t) && (mode !== "prev" || !grp[i].temp)) best = i; });
      if (best < 0) return;
      used.add(best);
      const h = grp[best];
      if (h.start > 0) { used.add(h.start); if (/^\d{4}\/\d{4}$/.test(tk[h.start + 1] || "")) used.add(h.start + 1); if (tk[h.start + 1] === "TEMPO") { used.add(h.start + 1); used.add(h.start + 2); } }
    };
    if (cig != null && cig !== Infinity) pick((t) => { const m = /^(BKN|OVC|VV)(\d{3})/.exec(t); return !!m && +m[2] * 100 === cig; });
    if (vis != null) pick((t) => (vis >= 10000 ? t === "9999" || t === "CAVOK" : t === String(vis).padStart(4, "0")));
    return { tk, used };
  }

  // turn the engine result into an animation script
  function script(r, af) {
    const S = [];
    r.path.forEach((p, i) => {
      const w = p.win === 2 ? r.w2 : r.w1;
      const v = w && !w.outside ? (p.use === "prev" ? w.prev : w.worst) : null;
      const next = r.path[i + 1];
      let edge = null;
      if (next && next.n !== p.n) edge = p.n + ">" + next.n;
      else if (!next) edge = p.n + ">" + ({ n1: "t1", gA: "t2", se: "t3", ap: "t4" }[p.n] || (r.out === 1 ? "one" : r.out === 2 ? "two" : "t3"));
      if (!next && p.n === "gC") edge = r.out === 1 ? "gC>one" : "gC>two";
      if (!next && p.n === "se" && r.out !== 0) edge = null;
      const rows = [];
      if (p.thr && v) {
        if (p.thr.c != null) rows.push(["Ceiling", "≥ " + p.thr.c + " ft", fmtC(v.cig), v.cig >= p.thr.c]);
        if (p.thr.v != null) rows.push([p.n === "gC" ? "Vis (as RVR)" : "Visibility", "≥ " + p.thr.v + " m", fmtV(v.vis), v.vis >= p.thr.v]);
      }
      S.push({ ...p, v, edge, rows });
    });
    if (r.out == null) { if (S.length) S.push({ n: S[S.length - 1].n, final: true, stop: true, q: "Can't complete", detail: r.miss.join(" · ") }); return S; }
    const fin = r.out === 1 ? "one" : r.out === 2 ? "two" : (S.length && edgeEls[S[S.length - 1].edge] ? S[S.length - 1].edge.split(">")[1] : null);
    if (fin) S.push({ n: fin, final: true, q: OUT[r.out].t, detail: r.fuel.length ? "Fuel: " + r.fuel.join("; ") : "" });
    return S;
  }

  let S = [], cur = 0, playing = true, timers = [], raf = 0, R = null, taf = null;
  const spd = () => +$("#rpSpeed").value;
  const later = (ms, f) => timers.push(setTimeout(f, ms * spd()));
  const clearT = () => { timers.forEach(clearTimeout); timers = []; cancelAnimationFrame(raf); };
  function setWin(w) { const e = +R.eta, a = tx(e - (w || 0) * 36e5), b = tx(e + (w || 0) * 36e5); win.setAttribute("x", a); win.setAttribute("width", Math.max(0, b - a)); wl.setAttribute("x1", a); wl.setAttribute("x2", a); wr.setAttribute("x1", b); wr.setAttribute("x2", b); wlab.setAttribute("x", (a + b) / 2); wlab.textContent = w ? "ETA ±" + w + " h" : ""; }
  function setThr(t) {
    if (ZONES.length && ZONES[0].el) paintZones(t);
    const c = t && t.c != null, v = t && t.v != null;
    [thrC, thrCL].forEach((e) => e.setAttribute("opacity", c ? 1 : 0)); [thrV, thrVL].forEach((e) => e.setAttribute("opacity", v ? 1 : 0));
    if (c) { thrC.setAttribute("y1", cy(t.c)); thrC.setAttribute("y2", cy(t.c)); thrCL.setAttribute("y", cy(t.c) - 5); thrCL.textContent = "needs " + t.cl; }
    if (v) { thrV.setAttribute("y1", vy(t.v)); thrV.setAttribute("y2", vy(t.v)); thrVL.setAttribute("y", vy(t.v) - 5); thrVL.textContent = "needs " + t.vl; }
  }
  function tafHTML(used) {
    const { tk } = tokensFor(R.taf, null, null);
    return tk.map((w, i) => (/^(TEMPO|BECMG|PROB\d\d|FM\d{6})$/.test(w) && !(tk[i - 1] || "").startsWith("PROB") ? "<br>" : "") + `<b class="${used && used.has(i) ? "use" : ""}">${esc(w)}</b>`).join(" ");
  }
  function setWorst(s) {
    const v = s && s.v && s.thr ? s.v : null;
    const showC = v && s.thr.c != null, showV = v && s.thr.v != null;
    wC.setAttribute("opacity", showC ? 1 : 0); wV.setAttribute("opacity", showV ? 1 : 0);
    let used = null;
    if (v) {
      used = tokensFor(R.taf, showC ? v.cig : null, showV ? v.vis : null, s.use).used;
      // place markers at the first time in the window that hits the worst value
      const w = s.win || 1, e = +R.eta;
      let tC = e, tV = e;
      for (let t = e - w * 36e5, fc = 0, fv = 0; t <= e + w * 36e5; t += 6e5) {
        const c = at(taf, new Date(t)), q = s.use === "prev" ? c.prev : c.worst;
        if (!fc && (q.cig ?? Infinity) === v.cig) { tC = t; fc = 1; }
        if (!fv && (q.vis ?? 10000) === v.vis) { tV = t; fv = 1; }
      }
      if (showC) { wC.setAttribute("cx", tx(tC)); wC.setAttribute("cy", cy(v.cig)); }
      if (showV) { wV.setAttribute("cx", tx(tV)); wV.setAttribute("cy", vy(v.vis)); }
    }
    $("#rpTaf").innerHTML = tafHTML(used);
  }
  function paintStatic(upto) {
    for (const k in nodeEls) { nodeEls[k].g.setAttribute("class", "rp-node" + (N[k].term ? " term" : "")); nodeEls[k].ring.classList.remove("rp-pulse"); }
    for (const k in edgeEls) edgeEls[k].setAttribute("class", "rp-edge");
    for (let i = 0; i < upto; i++) { const s = S[i]; if (s.final) continue; nodeEls[s.n].g.classList.add(s.ok ? "yes" : "no"); if (s.edge && edgeEls[s.edge]) edgeEls[s.edge].classList.add("taken"); }
    $("#rpProg").querySelectorAll("i").forEach((b, i) => (b.className = i < upto ? "done" : i === upto ? "cur" : ""));
    dot.setAttribute("cx", -20);
  }
  function caption(s, i, show) {
    $("#rpStep").textContent = `Step ${i + 1} of ${S.length}`;
    $("#rpTitle").textContent = s.final ? "Result: " + s.q : s.q;
    $("#rpText").textContent = s.final ? s.detail : s.pilot ? "Pilot answer: " + s.detail : s.detail;
    $("#rpChecks").innerHTML = s.rows && s.rows.length ? `<span class="h">Check</span><span class="h">Needs</span><span class="h">Forecast</span>` + s.rows.map((r) => `<span>${r[0]}</span><span>${r[1]}</span><span class="${show ? (r[3] ? "ok" : "bad") : ""}">${show ? r[2] : "…"}</span>`).join("") : "";
    const v = $("#rpVerdict"); v.className = "rp-verdict";
    if (!s.final && show) { v.textContent = s.ok ? "YES" : "NO"; v.classList.add("show", s.ok ? "y" : "n"); }
  }
  function moveDot(path, ms, done) {
    const L = path.getTotalLength(), t0 = performance.now(), dur = ms * spd();
    const f = (now) => { const k = Math.min(1, (now - t0) / dur), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2, p = path.getPointAtLength(L * e); dot.setAttribute("cx", p.x); dot.setAttribute("cy", p.y); if (k < 1) raf = requestAnimationFrame(f); else { dot.setAttribute("cx", -20); done && done(); } };
    raf = requestAnimationFrame(f);
  }
  function run(i, auto) {
    clearT(); cur = i; paintStatic(i);
    const s = S[i], ne = nodeEls[s.n];
    setThr(null); setWorst(null); if (s.win) setWin(s.win);
    ne.g.classList.add("active"); ne.ring.classList.add("rp-pulse"); caption(s, i, false);
    if (s.final) {
      ne.g.classList.remove("active"); if (!s.stop) ne.g.classList.add("final", "out" + (R.out ?? 0)); ne.ring.classList.remove("rp-pulse"); caption(s, i, true);
      for (const k in nodeEls) if (!/yes|no|final/.test(nodeEls[k].g.getAttribute("class"))) nodeEls[k].g.classList.add("dim");
      for (const k in edgeEls) if (!edgeEls[k].classList.contains("taken")) edgeEls[k].classList.add("dim");
      setPlay(false); return;
    }
    const hasWx = !!(s.thr && s.v);
    later(hasWx ? 500 : 200, () => setThr(s.thr));
    later(hasWx ? 1300 : 300, () => setWorst(s));
    later(hasWx ? 2100 : 900, () => { caption(s, i, true); ne.ring.classList.remove("rp-pulse"); ne.g.classList.remove("active"); ne.g.classList.add(s.ok ? "yes" : "no"); });
    later(hasWx ? 2900 : 1500, () => {
      const p = s.edge && edgeEls[s.edge];
      const go = () => { if (playing && auto !== false) later(250, () => run(i + 1)); };
      if (p) { p.classList.add("taken"); moveDot(p, 900, go); } else go();
    });
  }
  function setPlay(p) { playing = p; $("#rpPlay").textContent = p ? "Pause" : cur >= S.length - 1 ? "Replay" : "Play"; }
  $("#rpPlay").onclick = () => { if (cur >= S.length - 1 && !playing) { setPlay(true); run(0); return; } setPlay(!playing); if (playing) run(cur); };
  $("#rpNext").onclick = () => { setPlay(false); run(Math.min(S.length - 1, cur + 1), false); };
  $("#rpBack").onclick = () => { setPlay(false); run(Math.max(0, cur - 1), false); };
  $("#rpProg").onclick = (e) => { const i = e.target.dataset.i; if (i == null) return; setPlay(false); run(+i, false); };
  const close = () => { clearT(); ov.hidden = true; document.body.style.overflow = ""; };
  $("#rpClose").onclick = close;
  ov.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); if (e.key === "ArrowRight") $("#rpNext").click(); if (e.key === "ArrowLeft") $("#rpBack").click(); });

  window.openReplay = function (icao) {
    const af = AF.find((a) => a.icao === icao), eta = etaDate();
    R = evaluate(af, WX[icao] || {}, eta);
    taf = parseTAF(R.taf);
    if (!R.path.length || !taf || taf.error) return;
    S = script(R, af);
    buildTimeline(taf, eta, af);
    const o = R.out == null ? { t: "Can't assess", c: "na" } : OUT[R.out];
    $("#rpIcao").textContent = af.icao;
    $("#rpMeta").textContent = `${af.name} · ETA ${eta.toISOString().slice(11, 16)}Z · ${state.off ? "offshore to onshore" : "not offshore to onshore"}${state.off ? (state.sea ? " · letting down over the sea" : " · not letting down over the sea") : ""}`;
    const pill = $("#rpPill"); pill.textContent = o.t; pill.style.setProperty("--st", `var(--${o.c})`); pill.style.setProperty("--st-bg", `var(--${o.c}-bg)`);
    $("#rpTafK").textContent = "TAF used · " + af.icao;
    $("#rpProg").innerHTML = S.map((_, i) => `<i data-i="${i}" title="Step ${i + 1}"></i>`).join("");
    ov.hidden = false; document.body.style.overflow = "hidden"; $("#rpClose").focus();
    setWin(1);
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { setPlay(false); run(0, false); } else { setPlay(true); run(0); }
  };
})();
