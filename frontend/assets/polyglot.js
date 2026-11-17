/* Kavach CTI - shared drawing helpers for Labs 16-18 (MongoDB, Cassandra, Neo4j, vector search). */
window.KavachPG = (() => {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const DBC = { mongodb: "#13aa52", cassandra: "#1287b1", neo4j: "#4581c3", vector: "#ffb000" };
  const DBN = { mongodb: "MongoDB", cassandra: "Cassandra", neo4j: "Neo4j" };
  const ms = (v) => (v == null ? "-" : `${Number(v) < 10 ? Number(v).toFixed(2) : Number(v) < 100 ? Number(v).toFixed(1) : Math.round(v).toLocaleString("en-IN")} ms`);

  function bars(items, { unit = "", w = 620, rowH = 30, fmt = (v) => v, label = 170 } = {}) {
    if (!items.length) return "";
    const max = Math.max(...items.map((i) => i.value || 0), 1e-9), R = 90, H = items.length * rowH + 6;
    return `<svg viewBox="0 0 ${w} ${H}" class="lchart" role="img">${items.map((it, i) => {
      const bw = ((w - label - R) * (it.value || 0)) / max, y = i * rowH + 4;
      return `<text x="${label - 10}" y="${y + 16}" text-anchor="end" class="ax">${esc(it.label)}</text>
        <rect x="${label}" y="${y + 3}" width="${Math.max(bw, 2).toFixed(1)}" height="${rowH - 12}" rx="4" style="fill:${it.color || "var(--accent)"}"><title>${esc(it.label)}: ${fmt(it.value)}${unit}</title></rect>
        <text x="${label + bw + 8}" y="${y + 16}" class="ax val">${fmt(it.value)}${unit}</text>`;
    }).join("")}</svg>`;
  }

  /* grouped bars: rows = [{label, values: {mongodb: x, cassandra: y, neo4j: z}}] */
  function grouped(rows, { w = 680, h = 250, fmt = (v) => v, log = false } = {}) {
    const keys = ["mongodb", "cassandra", "neo4j"];
    const tr = (v) => (log ? Math.log10(Math.max(v, 0.01)) + 2 : v);
    const max = Math.max(...rows.flatMap((r) => keys.map((k) => tr(r.values[k] || 0))), 1e-9);
    const L = 20, B = 46, T = 18, gw = (w - L - 10) / rows.length, ph = h - B - T;
    let g = `<line x1="${L}" x2="${w - 6}" y1="${T + ph}" y2="${T + ph}" class="grid"/>`;
    rows.forEach((r, i) => {
      const bw = (gw * 0.72) / 3;
      keys.forEach((k, j) => {
        const v = r.values[k], hh = (ph * tr(v || 0)) / max, x = L + i * gw + gw * 0.14 + j * bw;
        g += `<rect x="${x.toFixed(1)}" y="${(T + ph - hh).toFixed(1)}" width="${(bw - 3).toFixed(1)}" height="${Math.max(hh, 1).toFixed(1)}" rx="3" style="fill:${DBC[k]}"><title>${DBN[k]} - ${esc(r.label)}: ${fmt(v)}</title></rect>
          <text x="${(x + bw / 2 - 1.5).toFixed(1)}" y="${(T + ph - hh - 4).toFixed(1)}" text-anchor="middle" class="ax" style="font-size:9.5px">${fmt(v)}</text>`;
      });
      g += `<text x="${(L + i * gw + gw / 2).toFixed(1)}" y="${h - 26}" text-anchor="middle" class="ax">${esc(r.label)}</text>`;
    });
    return `<svg viewBox="0 0 ${w} ${h}" class="lchart" role="img">${g}</svg>${legend()}${log ? `<p class="muted small" style="margin:4px 0 0">Logarithmic height - the gaps are large.</p>` : ""}`;
  }

  const legend = () => `<div class="lg">${Object.keys(DBN).map((k) => `<span><i style="background:${DBC[k]}"></i>${DBN[k]}</span>`).join("")}</div>`;

  /* latency (x) vs recall (y) curves: series = [{name, color, points: [{latency_ms, recall, param}]}] */
  function curve(series, { w = 680, h = 300 } = {}) {
    const pts = series.flatMap((s) => s.points);
    if (!pts.length) return `<p class="muted">No measurements yet.</p>`;
    const xmax = Math.max(...pts.map((p) => p.latency_ms)) * 1.08, ymin = Math.max(0, Math.min(...pts.map((p) => p.recall)) - 5);
    const L = 48, B = 38, T = 12, R = 14, pw = w - L - R, ph = h - B - T;
    const X = (v) => L + (pw * v) / xmax, Y = (v) => T + ph - (ph * (v - ymin)) / (100 - ymin);
    let g = "";
    for (let k = 0; k <= 4; k++) {
      const v = ymin + ((100 - ymin) * k) / 4, x = (xmax * k) / 4;
      g += `<line x1="${L}" x2="${w - R}" y1="${Y(v)}" y2="${Y(v)}" class="grid"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end" class="ax">${v.toFixed(0)}%</text>
        <text x="${X(x)}" y="${h - 20}" text-anchor="middle" class="ax">${x.toFixed(x < 10 ? 1 : 0)}</text>`;
    }
    g += `<text x="${L + pw / 2}" y="${h - 4}" text-anchor="middle" class="ax">average query latency (ms)</text>`;
    series.forEach((s) => {
      const p = [...s.points].sort((a, b) => a.latency_ms - b.latency_ms);
      g += `<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${p.map((q) => `${X(q.latency_ms).toFixed(1)},${Y(q.recall).toFixed(1)}`).join(" ")}"/>`;
      g += p.map((q) => `<circle cx="${X(q.latency_ms).toFixed(1)}" cy="${Y(q.recall).toFixed(1)}" r="4" fill="${s.color}"><title>${esc(s.name)} ${esc(q.param)}: ${q.latency_ms} ms, recall ${q.recall}%</title></circle>`).join("");
    });
    return `<svg viewBox="0 0 ${w} ${h}" class="lchart" role="img">${g}</svg><div class="lg">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
  }

  const code = (t, max = 260) => `<pre class="term" style="height:auto;max-height:${max}px;margin:8px 0">${esc(t)}</pre>`;
  const logBox = (l) => (l && l.length ? `<details style="margin-top:10px"><summary class="muted">Output (${l.length} lines)</summary><pre class="term" style="height:auto;max-height:320px;margin:8px 0">${esc(l.join("\n"))}</pre></details>` : "");
  const table = (head, rows, numCols = []) => `<div style="overflow-x:auto"><table class="table"><thead><tr>${head.map((x, i) => `<th class="${numCols.includes(i) ? "num" : ""}">${x}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${numCols.includes(i) ? "num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  const badge = (k) => `<span class="pill" style="margin:0;border-color:${DBC[k]};color:${DBC[k]}">${DBN[k] || esc(k)}</span>`;
  function backends(b) {
    if (!b) return "";
    return `<div class="row-gap" style="flex-wrap:wrap;margin:14px 0 4px">${["mongodb", "cassandra", "neo4j", "vector"].filter((k) => b[k]).map((k) =>
      `<span class="pill" style="margin:0"><span class="dot ${b[k].ok ? "ok" : "err"}"></span> ${{ mongodb: "MongoDB rs0", cassandra: "Cassandra", neo4j: "Neo4j", vector: "Atlas Local (vector)" }[k]}
       ${b[k].ok ? esc(b[k].version || "") : "offline"}</span>`).join("")}</div>`;
  }
  return { esc, DBC, DBN, ms, bars, grouped, legend, curve, code, logBox, table, badge, backends };
})();
