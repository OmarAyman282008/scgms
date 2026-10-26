/* SCGMS charts (Plotly) and calendar (FullCalendar). */
(function () {
  const RTL = document.documentElement.dir === 'rtl';
  const FONT = getComputedStyle(document.body).fontFamily;
  const C = {
    good: '#1d7a3a', warn: '#d4900a', crit: '#c0392b', blue: '#2a6fdb', lightblue: '#8db4ef',
    grey: '#b8c2d1', cyan: '#3aa0b5', orange: '#e07b39', ink: '#4a5568', grid: '#e8edf4', navy: '#1f3b6e'
  };
  const base = (extra) => Object.assign({
    font: {family: FONT, size: 12, color: C.ink}, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    margin: {l: 10, r: 10, t: 10, b: 30}, hoverlabel: {font: {family: FONT}}, showlegend: false
  }, extra || {});
  const cfg = {displayModeBar: false, responsive: true};
  const band = (v) => v == null ? C.grey : v >= 80 ? C.good : v >= 60 ? C.warn : C.crit;
  const link = (el, urls) => el.on('plotly_click', (ev) => {
    const p = ev.points[0]; const u = p.customdata || (urls && urls[p.pointIndex]);
    if (u) window.location = u;
  });
  const labelMargin = (labels) => Math.min(280, 30 + 7.2 * Math.max(0, ...labels.map(l => String(l).length)));
  const sideMargin = (labels, extra) => {
    const w = labelMargin(labels);
    return RTL ? {l: 20 + (extra || 0), r: w, t: 10, b: 30} : {l: w, r: 20 + (extra || 0), t: 10, b: 30};
  };
  const hbarAxes = (n, labels) => ({
    xaxis: {gridcolor: C.grid, zeroline: false, autorange: RTL ? 'reversed' : true, fixedrange: true},
    yaxis: {side: RTL ? 'right' : 'left', autorange: 'reversed', fixedrange: true, ticksuffix: ' '},
    height: Math.max(260, 34 * n + 60), margin: sideMargin(labels || [])
  });

  window.drawOverview = function (D, L) {
    // 1. performance comparison
    let el = document.getElementById('ch-perf');
    if (el) {
      const v = D.performance.values;
      Plotly.newPlot(el, [{
        type: 'bar', orientation: 'h', y: D.performance.labels, x: v.map(x => x == null ? 0 : x),
        marker: {color: v.map(band)}, customdata: D.performance.urls,
        text: v.map(x => x == null ? '—' : x + '%'), textposition: 'outside', cliponaxis: false,
        hovertemplate: '%{y}<br>' + L.score + ': %{text}<extra></extra>'
      }], base(Object.assign(hbarAxes(v.length, D.performance.labels), {xaxis: {range: RTL ? [110, 0] : [0, 110], gridcolor: C.grid, fixedrange: true}})), cfg);
      link(el);
    }
    // 2. decision implementation donut
    el = document.getElementById('ch-dec');
    if (el) {
      const keys = ['implemented', 'in_progress', 'assigned', 'registered', 'overdue'];
      const cols = [C.good, C.blue, C.lightblue, C.grey, C.crit];
      const vals = keys.map(k => D.decisions[k] || 0);
      const total = vals.reduce((a, b) => a + b, 0);
      Plotly.newPlot(el, [{
        type: 'pie', hole: .62, labels: keys.map(k => L[k]), values: vals, marker: {colors: cols, line: {color: '#fff', width: 2}},
        sort: false, direction: 'clockwise', textinfo: 'value', hovertemplate: '%{label}: %{value} (%{percent})<extra></extra>'
      }], base({showlegend: true, legend: {orientation: 'h', y: -0.1}, height: 320,
        annotations: [{text: (total ? Math.round(100 * (D.decisions.implemented || 0) / total) : 0) + '%<br><span style="font-size:11px">' + L.implemented + '</span>',
          showarrow: false, font: {size: 22, color: C.navy}}]}), cfg);
    }
    // 3. task completion stacked bar
    el = document.getElementById('ch-tasks');
    if (el) {
      const order = [['verified', C.good], ['submitted', C.cyan], ['in_progress', C.blue], ['not_started', C.grey],
        ['blocked', C.warn], ['returned', C.orange], ['overdue', C.crit]];
      const traces = order.map(([k, col]) => ({
        type: 'bar', orientation: 'h', name: L[k], y: D.tasks.labels, x: D.tasks.series[k], customdata: D.tasks.urls,
        marker: {color: col, line: {color: '#fff', width: 1}}, hovertemplate: '%{y}<br>' + L[k] + ': %{x}<extra></extra>'
      }));
      Plotly.newPlot(el, traces, base(Object.assign(hbarAxes(D.tasks.labels.length, D.tasks.labels), {barmode: 'stack', showlegend: true,
        legend: {orientation: 'h', y: -0.12}})), cfg);
      link(el);
    }
    // 7. MOM compliance
    el = document.getElementById('ch-mom');
    if (el) {
      const v = D.mom.values;
      Plotly.newPlot(el, [{
        type: 'bar', orientation: 'h', y: D.mom.labels, x: v.map(x => x == null ? 0 : x), customdata: D.mom.urls,
        marker: {color: v.map(band)}, text: v.map(x => x == null ? '—' : x + '%'), textposition: 'outside', cliponaxis: false,
        hovertemplate: '%{y}<br>' + L.pct + ': %{text}<extra></extra>'
      }], base(Object.assign(hbarAxes(v.length, D.mom.labels), {xaxis: {range: RTL ? [110, 0] : [0, 110], gridcolor: C.grid, fixedrange: true}})), cfg);
      link(el);
    }
    // 8. heatmap
    el = document.getElementById('ch-heat');
    if (el) {
      const z = D.heat.z;
      Plotly.newPlot(el, [{
        type: 'heatmap', z: z, x: D.heat.x, y: D.heat.y, zmin: 0, zmax: 100, xgap: 3, ygap: 3,
        colorscale: [[0, '#f3d5d1'], [0.5, '#f6e7c1'], [0.8, '#cfe3f9'], [1, '#2a6fdb']],
        text: z.map(r => r.map(v => v == null ? '' : v + '%')), texttemplate: '%{text}', hoverongaps: false,
        customdata: D.heat.y.map((_, i) => D.heat.x.map(() => D.heat.urls[i])),
        hovertemplate: '%{y}<br>%{x}: %{text}<extra></extra>', colorbar: {ticksuffix: '%', thickness: 10, x: RTL ? -0.08 : 1.02, xanchor: RTL ? 'right' : 'left'}
      }], base({height: Math.max(300, 32 * D.heat.y.length + 90), margin: Object.assign(sideMargin(D.heat.y, 60), {t: 40, b: 10}),
        yaxis: {side: RTL ? 'right' : 'left', autorange: 'reversed', ticksuffix: ' '}, xaxis: {side: 'top', autorange: RTL ? 'reversed' : true}}), cfg);
      link(el);
    }
    // 9. trend
    el = document.getElementById('ch-trend');
    if (el) {
      Plotly.newPlot(el, [
        {type: 'scatter', mode: 'lines+markers', name: L.due, x: D.trend.labels, y: D.trend.due, line: {color: C.grey, width: 2}, marker: {size: 8}},
        {type: 'scatter', mode: 'lines+markers', name: L.done, x: D.trend.labels, y: D.trend.verified, line: {color: C.good, width: 2}, marker: {size: 8}}
      ], base({showlegend: true, legend: {orientation: 'h', y: -0.2}, height: 320,
        xaxis: {type: 'category', autorange: RTL ? 'reversed' : true, gridcolor: C.grid},
        yaxis: {gridcolor: C.grid, rangemode: 'tozero', side: RTL ? 'right' : 'left'}}), cfg);
    }
  };

  window.drawCalendar = function (id, feed, opts) {
    const el = document.getElementById(id);
    if (!el || !window.FullCalendar) return;
    const cal = new FullCalendar.Calendar(el, Object.assign({
      initialView: 'dayGridMonth', locale: RTL ? 'ar' : 'en', direction: RTL ? 'rtl' : 'ltr', firstDay: 6,
      headerToolbar: {start: 'prev,next today', center: 'title', end: 'dayGridMonth,listMonth'},
      events: feed, height: 'auto', eventDisplay: 'block', dayMaxEvents: 3
    }, opts || {}));
    cal.render();
  };

  window.filterRows = function (input, tableId) {
    const q = input.value.trim().toLowerCase();
    document.querySelectorAll('#' + tableId + ' tbody tr').forEach(tr => {
      tr.style.display = !q || tr.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  };
  window.sortTable = function (tableId, col, numeric) {
    const tb = document.querySelector('#' + tableId + ' tbody');
    const rows = Array.from(tb.rows);
    const dir = tb.dataset.sort === String(col) ? -1 : 1;
    tb.dataset.sort = dir === 1 ? String(col) : '';
    rows.sort((a, b) => {
      const x = a.cells[col].textContent.trim(), y = b.cells[col].textContent.trim();
      return dir * (numeric ? (parseFloat(x) || 0) - (parseFloat(y) || 0) : x.localeCompare(y));
    });
    rows.forEach(r => tb.appendChild(r));
  };
})();
