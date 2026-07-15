/**
 * Chart.js helpers for 株チェック (canvas-based charts).
 * Requires Chart.js 4.x loaded globally.
 */
(function (global) {
  if (!global.Chart) return;

  const FONT = '"Noto Sans JP", sans-serif';
  const BRAND = '#0284c7';
  const BRAND_LIGHT = '#0ea5e9';

  const COLORS = {
    green: '#059669',
    blue: BRAND,
    blueLight: BRAND_LIGHT,
    purple: '#7c3aed',
    cyan: '#0891b2',
    amber: '#d97706',
    red: '#dc2626',
    grid: '#e2e8f0',
    text: '#64748b',
    ink: '#334155',
  };

  Chart.defaults.font.family = FONT;
  Chart.defaults.color = COLORS.text;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.padding = 14;
  Chart.defaults.plugins.legend.labels.font = { size: 11, family: FONT };

  const yenOku = (v) => {
    if (v == null || Number.isNaN(v)) return '-';
    const abs = Math.abs(v) / 1e8;
    if (abs >= 10000) return `${(v / 1e12).toFixed(1)}兆`;
    if (abs >= 100) return `${Math.round(abs).toLocaleString()}億`;
    if (abs >= 10) return `${Math.round(abs)}億`;
    return `${abs.toFixed(1)}億`;
  };

  const pct = (v, signed = false) => {
    if (v == null || Number.isNaN(v)) return '-';
    const n = v * 100;
    const sign = signed && n > 0 ? '+' : '';
    return `${sign}${n.toFixed(1)}%`;
  };

  const instances = new Map();

  const valueLabelsPlugin = {
    id: 'valueLabels',
    afterDatasetsDraw(chart, _args, pluginOpts) {
      const formatter = pluginOpts?.formatter;
      if (!formatter) return;
      const { ctx } = chart;
      const isHorizontal = chart.config.type === 'bar' && chart.options.indexAxis === 'y';
      chart.data.datasets.forEach((dataset, datasetIndex) => {
        const meta = chart.getDatasetMeta(datasetIndex);
        if (meta.hidden) return;
        meta.data.forEach((element, index) => {
          const raw = dataset.data[index];
          if (raw == null || Number.isNaN(raw)) return;
          const text = formatter(raw, chart, datasetIndex, index);
          if (!text || text === '-') return;
          const { x, y } = element.getProps(['x', 'y'], true);
          ctx.save();
          ctx.font = '600 11px "Noto Sans JP", sans-serif';
          ctx.fillStyle = COLORS.ink;
          if (isHorizontal) {
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, x + 6, y);
          } else if (chart.config.type === 'bar') {
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText(text, x, y - 4);
          } else {
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText(text, x, y - 8);
          }
          ctx.restore();
        });
      });
    },
  };

  Chart.register(valueLabelsPlugin);

  function destroy(id) {
    const prev = instances.get(id);
    if (prev) {
      prev.destroy();
      instances.delete(id);
    }
  }

  function resizeWrap(canvasId, heightPx) {
    const el = document.getElementById(canvasId);
    if (!el) return;
    const wrap = el.closest('.chart-canvas-wrap');
    if (wrap && heightPx) wrap.style.height = `${heightPx}px`;
  }

  function showEmptyChart(canvasId, message = 'データがありません') {
    const el = document.getElementById(canvasId);
    if (!el) return;
    destroy(canvasId);
    const wrap = el.closest('.chart-canvas-wrap');
    if (!wrap) return;
    let note = wrap.querySelector('.chart-empty-note');
    if (!note) {
      note = document.createElement('div');
      note.className = 'chart-empty-note';
      wrap.appendChild(note);
    }
    note.textContent = message;
    el.style.display = 'none';
  }

  function clearEmptyChart(canvasId) {
    const el = document.getElementById(canvasId);
    if (!el) return;
    const wrap = el.closest('.chart-canvas-wrap');
    if (!wrap) return;
    wrap.querySelector('.chart-empty-note')?.remove();
    el.style.display = 'block';
  }

  function hasNumericData(data) {
    return (data || []).some((v) => v != null && !Number.isNaN(v));
  }

  function scheduleResize() {
    requestAnimationFrame(() => {
      instances.forEach((ch) => {
        try { ch.resize(); } catch (_) { /* ignore */ }
      });
    });
  }

  function scaleX() {
    return {
      grid: { color: COLORS.grid, drawBorder: false },
      border: { display: false },
      ticks: {
        color: COLORS.text,
        font: { size: 11, family: FONT },
        autoSkip: true,
        maxRotation: 45,
        minRotation: 0,
        maxTicksLimit: 8,
        padding: 6,
      },
    };
  }

  function scaleY(yFmt, { pctAxis = false } = {}) {
    return {
      beginAtZero: true,
      grace: pctAxis ? '12%' : '10%',
      grid: { color: COLORS.grid, drawBorder: false },
      border: { display: false },
      ticks: {
        color: COLORS.text,
        font: { size: 11, family: FONT },
        maxTicksLimit: 6,
        padding: 6,
        callback: (v) => (pctAxis ? pct(v / 100) : yFmt(v)),
      },
    };
  }

  function baseOptions(yFmt, { legend = false, pctAxis = false, labelFmt = null } = {}) {
    const plugins = {
      legend: legend
        ? {
            display: true,
            position: 'top',
            align: 'end',
            labels: { boxWidth: 8, padding: 14, font: { size: 11, family: FONT }, usePointStyle: true },
          }
        : { display: false },
      tooltip: {
        backgroundColor: '#fff',
        titleColor: '#0f172a',
        bodyColor: COLORS.ink,
        borderColor: COLORS.grid,
        borderWidth: 1,
        padding: 10,
        titleFont: { family: FONT, size: 12, weight: '600' },
        bodyFont: { family: FONT, size: 11 },
        callbacks: {},
      },
    };
    if (labelFmt) {
      plugins.valueLabels = { formatter: labelFmt };
    }
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      layout: {
        padding: legend
          ? { top: 24, right: 20, bottom: 20, left: 12 }
          : { top: 24, right: 20, bottom: 12, left: 12 },
      },
      plugins,
      scales: {
        x: scaleX(),
        y: scaleY(yFmt, { pctAxis }),
      },
      elements: {
        line: { borderWidth: 2 },
        point: { radius: 3, hoverRadius: 5, hitRadius: 8 },
      },
      datasets: {
        line: { clip: false },
        bar: { clip: false },
      },
    };
  }

  function lineDataset(label, data, color) {
    const c = color || BRAND;
    return {
      label,
      data,
      borderColor: c,
      backgroundColor: c === BRAND ? 'rgba(2, 132, 199, 0.1)' : c + '18',
      fill: true,
      tension: 0.3,
      spanGaps: true,
      pointRadius: 3,
      pointHoverRadius: 5,
      pointBackgroundColor: '#fff',
      pointBorderColor: c,
      pointBorderWidth: 2,
      borderWidth: 2,
      clip: false,
    };
  }

  function mountChart(canvasId, config) {
    const el = document.getElementById(canvasId);
    if (!el) return;
    clearEmptyChart(canvasId);
    destroy(canvasId);
    const chart = new Chart(el, config);
    instances.set(canvasId, chart);
    scheduleResize();
    return chart;
  }

  function renderLineChart(canvasId, labels, data, { label = '', color = COLORS.green, yFmt = yenOku } = {}) {
    if (!document.getElementById(canvasId)) return;
    if (!hasNumericData(data)) {
      showEmptyChart(canvasId);
      return;
    }
    const opts = baseOptions(yFmt, { labelFmt: (v) => yFmt(v) });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${yFmt(ctx.parsed.y)}`;
    mountChart(canvasId, {
      type: 'line',
      data: { labels, datasets: [lineDataset(label, data, color)] },
      options: opts,
    });
  }

  function renderDualLineChart(canvasId, labels, series, { yFmt = (v) => pct(v) } = {}) {
    if (!document.getElementById(canvasId)) return;
    const hasData = series.some((s) => hasNumericData(s.data));
    if (!hasData) {
      showEmptyChart(canvasId);
      return;
    }
    resizeWrap(canvasId, 300);
    const opts = baseOptions(yFmt, { legend: true, labelFmt: (v) => yFmt(v) });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${yFmt(ctx.parsed.y)}`;
    mountChart(canvasId, {
      type: 'line',
      data: {
        labels,
        datasets: series.map((s) => lineDataset(s.label, s.data, s.color)),
      },
      options: opts,
    });
  }

  function renderBarChart(canvasId, labels, data, { label = '', signed = true } = {}) {
    if (!document.getElementById(canvasId)) return;
    if (!hasNumericData(data)) {
      showEmptyChart(canvasId);
      return;
    }
    const opts = baseOptions((v) => pct(v / 100, signed), {
      pctAxis: true,
      labelFmt: (v) => pct(v / 100, signed),
    });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${pct(ctx.parsed.y / 100, signed)}`;
    mountChart(canvasId, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label,
          data: data.map((v) => (v == null ? null : v * 100)),
          backgroundColor: data.map((v) => (v != null && v < 0 ? 'rgba(220, 38, 38, 0.65)' : 'rgba(5, 150, 105, 0.65)')),
          borderColor: data.map((v) => (v != null && v < 0 ? COLORS.red : COLORS.green)),
          borderWidth: 1,
          borderRadius: 4,
          maxBarThickness: 40,
          clip: false,
        }],
      },
      options: opts,
    });
  }

  function renderVerticalBarChart(canvasId, labels, data, options = {}) {
    const el = document.getElementById(canvasId);
    if (!el) return;
    const {
      datasetLabel = '売上高',
      formatValue = yenOku,
      signedPct = false,
    } = options;
    if (!hasNumericData(data)) {
      showEmptyChart(canvasId);
      return;
    }
    resizeWrap(canvasId, 300);
    destroy(canvasId);
    clearEmptyChart(canvasId);
    const chart = new Chart(el, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: datasetLabel,
          data,
          backgroundColor: signedPct
            ? data.map((v) => (v != null && v < 0 ? 'rgba(220, 38, 38, 0.65)' : 'rgba(2, 132, 199, 0.65)'))
            : 'rgba(2, 132, 199, 0.65)',
          borderColor: signedPct
            ? data.map((v) => (v != null && v < 0 ? COLORS.red : BRAND))
            : BRAND,
          borderWidth: 1,
          borderRadius: 4,
          maxBarThickness: 28,
          clip: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 22, right: 12, bottom: 6, left: 8 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: (ctx) => `${ctx.dataset.label}: ${formatValue(ctx.parsed.y)}` },
          },
          valueLabels: {
            formatter: (v) => formatValue(v),
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: COLORS.text,
              font: { size: 9 },
              maxRotation: 55,
              minRotation: 45,
              autoSkip: false,
              padding: 2,
            },
          },
          y: {
            beginAtZero: true,
            grace: '12%',
            grid: { color: COLORS.grid },
            border: { display: false },
            ticks: {
              color: COLORS.text,
              font: { size: 10 },
              maxTicksLimit: 6,
              padding: 6,
              callback: (v) => formatValue(v),
            },
          },
        },
        datasets: { bar: { clip: false } },
      },
    });
    instances.set(canvasId, chart);
    scheduleResize();
  }

  function renderHorizontalBarChart(canvasId, labels, data, options = {}) {
    const el = document.getElementById(canvasId);
    if (!el) return;
    const {
      datasetLabel = '売上高',
      formatValue = yenOku,
    } = options;
    if (!hasNumericData(data)) {
      showEmptyChart(canvasId);
      return;
    }
    resizeWrap(canvasId, Math.max(280, labels.length * 36 + 56));
    destroy(canvasId);
    clearEmptyChart(canvasId);
    const chart = new Chart(el, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: datasetLabel,
          data,
          backgroundColor: 'rgba(2, 132, 199, 0.65)',
          borderColor: BRAND,
          borderWidth: 1,
          borderRadius: 4,
          barThickness: 16,
          clip: false,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 8, right: 24, bottom: 8, left: 8 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: (ctx) => formatValue(ctx.parsed.x) },
          },
          valueLabels: {
            formatter: (v) => formatValue(v),
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            grace: '8%',
            grid: { color: COLORS.grid },
            border: { display: false },
            ticks: { color: COLORS.text, callback: (v) => formatValue(v), maxTicksLimit: 5 },
          },
          y: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: COLORS.text,
              font: { size: 10 },
              autoSkip: false,
              padding: 4,
            },
          },
        },
        datasets: { bar: { clip: false } },
      },
    });
    instances.set(canvasId, chart);
    scheduleResize();
  }

  function hasAnnualMetrics(row) {
    return row.revenue != null
      || row.operating_income != null
      || row.net_income != null
      || row.total_assets != null;
  }

  function growthRate(current, prior) {
    if (current == null || prior == null || prior === 0) return null;
    return (current - prior) / prior;
  }

  function sanitizeAnnualRows(fin) {
    const buckets = new Map();
    for (const row of fin || []) {
      const fiscalYearEnd = (row.fiscal_year_end || '').trim();
      if (!fiscalYearEnd || !hasAnnualMetrics(row)) continue;
      const year = fiscalYearEnd.slice(0, 4);
      if (!year) continue;
      if (!buckets.has(year)) buckets.set(year, []);
      buckets.get(year).push(row);
    }

    const annual = [];
    for (const year of [...buckets.keys()].sort()) {
      const rows = buckets.get(year);
      const marchRows = rows.filter((row) => (row.fiscal_year_end || '').slice(5, 7) === '03');
      const pool = marchRows.length ? marchRows : rows;
      pool.sort((a, b) => (b.revenue || 0) - (a.revenue || 0));
      const pick = pool[0];
      annual.push({
        label: year,
        revenue: pick.revenue,
        operating_income: pick.operating_income,
        net_income: pick.net_income,
        operating_cf: pick.operating_cf,
        total_assets: pick.total_assets,
        roe: pick.roe,
        operating_margin: pick.operating_margin,
      });
    }
    return annual;
  }

  function quarterLabel(row) {
    const periodEnd = (row.period_end || '').trim();
    if (periodEnd && row.quarter_number) return `${periodEnd.slice(2, 4)}Q${row.quarter_number}`;
    if (row.quarter_number) return `Q${row.quarter_number}`;
    return periodEnd.slice(0, 7) || '';
  }

  function resolveRevenueYoy(row) {
    if (row.revenue_yoy != null) return row.revenue_yoy;
    return growthRate(row.revenue_cumulative, row.revenue_prior_year_cum);
  }

  function resolveOperatingIncomeYoy(row) {
    if (row.operating_income_yoy != null) return row.operating_income_yoy;
    return growthRate(row.operating_income_cumulative, row.operating_income_prior_year_cum);
  }

  function sanitizeQuarterlyRows(items) {
    const sorted = [...(items || [])].sort((a, b) => (a.period_end || '').localeCompare(b.period_end || ''));
    const seen = new Set();
    const quarterly = [];
    for (const row of sorted) {
      const label = quarterLabel(row);
      if (!label || seen.has(label)) continue;
      const revenueYoy = resolveRevenueYoy(row);
      const operatingIncomeYoy = resolveOperatingIncomeYoy(row);
      if (revenueYoy == null && operatingIncomeYoy == null) continue;
      seen.add(label);
      quarterly.push({
        label,
        revenue_yoy: revenueYoy,
        operating_income_yoy: operatingIncomeYoy,
      });
    }
    return quarterly;
  }

  function renderRevenueProfitCombo(canvasId, annualRows) {
    const el = document.getElementById(canvasId);
    if (!el) return;
    if (!annualRows?.length) {
      showEmptyChart(canvasId);
      return;
    }
    resizeWrap(canvasId, 360);
    const labels = annualRows.map((r) => r.label);
    const opts = baseOptions(yenOku, { legend: true, labelFmt: (v) => yenOku(v) });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${yenOku(ctx.parsed.y)}`;
    mountChart(canvasId, {
      type: 'line',
      data: {
        labels,
        datasets: [
          lineDataset('売上高', annualRows.map((r) => r.revenue), COLORS.green),
          lineDataset('営業利益', annualRows.map((r) => r.operating_income), COLORS.blue),
        ],
      },
      options: opts,
    });
  }

  function renderCompanyCharts(data, ids = {}) {
    const annual = data?.annual || [];
    const quarterly = data?.quarterly || [];
    const labels = annual.map((r) => r.label);
    const id = (k, fallback) => ids[k] || fallback;

    renderRevenueProfitCombo(id('combo', 'chart-revenue-profit'), annual);
    renderLineChart(id('revenue', 'chart-revenue'), labels, annual.map((r) => r.revenue), { label: '売上高', color: COLORS.green });
    renderLineChart(id('operating_income', 'chart-opincome'), labels, annual.map((r) => r.operating_income), { label: '営業利益', color: COLORS.blue });
    renderLineChart(id('net_income', 'chart-netincome'), labels, annual.map((r) => r.net_income), { label: '純利益', color: COLORS.purple });
    renderLineChart(id('operating_cf', 'chart-opcf'), labels, annual.map((r) => r.operating_cf), { label: '営業CF', color: COLORS.cyan });
    renderDualLineChart(id('margins', 'chart-margins'), labels, [
      { label: 'ROE', data: annual.map((r) => r.roe), color: COLORS.green },
      { label: '営業利益率', data: annual.map((r) => r.operating_margin), color: COLORS.blue },
    ]);
    renderLineChart(id('assets', 'fin-chart-assets'), labels, annual.map((r) => r.total_assets), { label: '総資産', color: COLORS.amber });

    const qLabels = quarterly.map((r) => r.label);
    renderBarChart(id('revenue_yoy', 'chart-q-yoy'), qLabels, quarterly.map((r) => r.revenue_yoy), { label: '売上YoY' });
    renderBarChart(id('qtab_yoy', 'qtab-chart-yoy'), qLabels, quarterly.map((r) => r.revenue_yoy), { label: '売上YoY' });
    renderBarChart(id('qtab_op_yoy', 'qtab-chart-op-yoy'), qLabels, quarterly.map((r) => r.operating_income_yoy), { label: '営業利益YoY' });
    scheduleResize();
  }

  function renderFromFinancials(fin, qtrItems, ids) {
    const annual = sanitizeAnnualRows(fin);
    const quarterly = sanitizeQuarterlyRows(qtrItems);
    renderCompanyCharts({ annual, quarterly }, ids);
  }

  function resizeAll() {
    scheduleResize();
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('resize', () => scheduleResize());
  }

  function halfYearLabelIndices(points) {
    if (!points?.length) return new Set();
    const indices = new Set();
    const dates = points.map((p) => new Date(p.date));
    const start = dates[0];
    const end = dates[dates.length - 1];
    const target = new Date(start.getFullYear(), start.getMonth(), 1);

    while (target <= end) {
      let bestIdx = 0;
      let bestDiff = Infinity;
      for (let i = 0; i < dates.length; i++) {
        const diff = Math.abs(dates[i].getTime() - target.getTime());
        if (diff < bestDiff) {
          bestDiff = diff;
          bestIdx = i;
        }
      }
      indices.add(bestIdx);
      target.setMonth(target.getMonth() + 6);
    }
    return indices;
  }

  function renderSearchTrendChart(canvasId, points, opts = {}) {
    const el = document.getElementById(canvasId);
    if (!el || !points?.length) {
      showEmptyChart(canvasId, '検索トレンドデータがありません');
      return;
    }
    clearEmptyChart(canvasId);
    resizeWrap(canvasId, opts.height ?? 260);
    const tickLimit = opts.tickLimit ?? 8;
    const labels = points.map((p) => p.date.slice(5));
    const data = points.map((p) => p.value);
    const labelIndices = sparseLabelIndices(points.length, tickLimit);
    const trendLabel = (v) => `${Math.round(v)}`;
    const chartOpts = baseOptions(trendLabel);
    chartOpts.plugins.valueLabels = {
      formatter: (raw, _chart, _datasetIndex, index) => (
        labelIndices.has(index) ? trendLabel(raw) : null
      ),
    };
    chartOpts.plugins.tooltip.callbacks.label = (ctx) => `関心度: ${trendLabel(ctx.parsed.y)}`;
    chartOpts.scales.x.ticks.maxTicksLimit = tickLimit;
    chartOpts.scales.y.max = 100;
    chartOpts.scales.y.ticks.maxTicksLimit = 5;
    mountChart(canvasId, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: opts.datasetLabel || '検索関心度',
          data,
          borderColor: COLORS.green,
          backgroundColor: COLORS.green + '18',
          fill: true,
          tension: 0.25,
          pointRadius: points.map((_, i) => (labelIndices.has(i) ? 2.5 : 0)),
          pointHoverRadius: 4,
          borderWidth: 2,
        }],
      },
      options: chartOpts,
    });
  }

  function sparseLabelIndices(length, maxLabels = 8) {
    if (!length) return new Set();
    if (length <= maxLabels) return new Set([...Array(length).keys()]);
    const indices = new Set([0, length - 1]);
    const step = Math.max(1, Math.floor((length - 1) / (maxLabels - 1)));
    for (let i = step; i < length - 1; i += step) indices.add(i);
    return indices;
  }

  function renderPriceLineChart(canvasId, points, opts = {}) {
    const el = document.getElementById(canvasId);
    if (!el || !points?.length) {
      showEmptyChart(canvasId, '株価データがありません');
      return;
    }
    clearEmptyChart(canvasId);
    const tickLimit = opts.tickLimit ?? 8;
    const labels = points.map((p) => (opts.fullDateLabels ? p.date : p.date.slice(5)));
    const data = points.map((p) => p.close);
    const labelIndices = halfYearLabelIndices(points);
    const priceLabel = (v) => `${Math.round(v).toLocaleString()}円`;
    const chartOpts = baseOptions(priceLabel);
    chartOpts.plugins.valueLabels = {
      formatter: (raw, _chart, _datasetIndex, index) => (
        labelIndices.has(index) ? priceLabel(raw) : null
      ),
    };
    chartOpts.plugins.tooltip.callbacks.label = (ctx) => `終値: ${priceLabel(ctx.parsed.y)}`;
    chartOpts.scales.x.ticks.maxTicksLimit = tickLimit;
    mountChart(canvasId, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: '株価',
          data,
          borderColor: COLORS.blue,
          backgroundColor: COLORS.blue + '18',
          fill: true,
          tension: 0.2,
          pointRadius: points.map((_, i) => (labelIndices.has(i) ? 3 : 0)),
          pointHoverRadius: 4,
          borderWidth: 2,
        }],
      },
      options: chartOpts,
    });
  }

  function renderValuationChart(canvasId, items) {
    const el = document.getElementById(canvasId);
    if (!el) return;
    const rows = (items || []).filter((r) => r.per != null || r.pbr != null);
    if (!rows.length) {
      showEmptyChart(canvasId, 'PER/PBR推移を算出できるデータがありません');
      return;
    }
    clearEmptyChart(canvasId);
    resizeWrap(canvasId, 280);
    const labels = rows.map((r) => (r.fiscal_year_end || '').slice(0, 7));
    const opts = baseOptions((v) => `${v.toFixed(1)}倍`, { legend: true, labelFmt: (v) => `${v.toFixed(1)}倍` });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed?.(1) ?? ctx.parsed.y}倍`;
    mountChart(canvasId, {
      type: 'line',
      data: {
        labels,
        datasets: [
          lineDataset('PER', rows.map((r) => r.per), COLORS.green),
          lineDataset('PBR', rows.map((r) => r.pbr), COLORS.blue),
        ],
      },
      options: opts,
    });
  }

  function sanitizeAnnualRowsFull(fin) {
    const buckets = new Map();
    for (const row of fin || []) {
      const fiscalYearEnd = (row.fiscal_year_end || '').trim();
      if (!fiscalYearEnd || !hasAnnualMetrics(row)) continue;
      const year = fiscalYearEnd.slice(0, 4);
      if (!year) continue;
      if (!buckets.has(year)) buckets.set(year, []);
      buckets.get(year).push(row);
    }
    const annual = [];
    for (const year of [...buckets.keys()].sort()) {
      const rows = buckets.get(year);
      const marchRows = rows.filter((row) => (row.fiscal_year_end || '').slice(5, 7) === '03');
      const pool = marchRows.length ? marchRows : rows;
      pool.sort((a, b) => (b.revenue || 0) - (a.revenue || 0));
      const pick = pool[0];
      annual.push({
        label: year,
        fiscal_year_end: pick.fiscal_year_end,
        revenue: pick.revenue,
        operating_income: pick.operating_income,
        ordinary_income: pick.ordinary_income,
        net_income: pick.net_income,
        operating_cf: pick.operating_cf,
        investing_cf: pick.investing_cf,
        financing_cf: pick.financing_cf,
        cash_and_deposits: pick.cash_and_deposits,
        interest_bearing_debt: pick.interest_bearing_debt,
        total_liabilities: pick.total_liabilities,
        net_assets: pick.net_assets,
        eps: pick.eps,
        dividend_per_share: pick.dividend_per_share,
        equity_ratio: pick.equity_ratio,
        debt_equity_ratio: pick.debt_equity_ratio,
        roa: pick.roa,
        roe: pick.roe,
        operating_margin: pick.operating_margin,
        total_assets: pick.total_assets,
      });
    }
    return annual;
  }

  function renderCfTrioChart(canvasId, annualRows) {
    if (!document.getElementById(canvasId)) return;
    const rows = annualRows.filter((r) =>
      r.operating_cf != null || r.investing_cf != null || r.financing_cf != null
    );
    if (!rows.length) { showEmptyChart(canvasId, 'CFデータがありません'); return; }
    clearEmptyChart(canvasId);
    resizeWrap(canvasId, 300);
    const labels = rows.map((r) => r.label);
    const toOku = (v) => (v == null ? null : v / 1e8);
    const opts = baseOptions(yenOku, { legend: true });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${yenOku(ctx.parsed.y * 1e8)}`;
    mountChart(canvasId, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { label: '営業CF', data: rows.map((r) => toOku(r.operating_cf)), backgroundColor: COLORS.green + 'cc', borderRadius: 3 },
          { label: '投資CF', data: rows.map((r) => toOku(r.investing_cf)), backgroundColor: COLORS.blue + 'cc', borderRadius: 3 },
          { label: '財務CF', data: rows.map((r) => toOku(r.financing_cf)), backgroundColor: COLORS.amber + 'cc', borderRadius: 3 },
        ],
      },
      options: opts,
    });
  }

  function renderFcfChart(canvasId, annualRows) {
    if (!document.getElementById(canvasId)) return;
    const rows = annualRows.filter((r) => r.operating_cf != null);
    if (!rows.length) { showEmptyChart(canvasId, 'FCF算出データがありません'); return; }
    clearEmptyChart(canvasId);
    const labels = rows.map((r) => r.label);
    const fcf = rows.map((r) => {
      const inv = r.investing_cf ?? 0;
      return (r.operating_cf + inv) / 1e8;
    });
    const opts = baseOptions(yenOku, { labelFmt: (v) => yenOku(v * 1e8) });
    opts.plugins.tooltip.callbacks.label = (ctx) => `FCF: ${yenOku(ctx.parsed.y * 1e8)}`;
    mountChart(canvasId, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'フリーキャッシュフロー',
          data: fcf,
          backgroundColor: fcf.map((v) => (v < 0 ? COLORS.red : COLORS.cyan) + 'cc'),
          borderRadius: 4,
        }],
      },
      options: opts,
    });
  }

  function renderEquityDebtChart(canvasId, annualRows) {
    if (!document.getElementById(canvasId)) return;
    const rows = annualRows.filter((r) => r.equity_ratio != null || r.debt_equity_ratio != null);
    if (!rows.length) { showEmptyChart(canvasId, '財務比率データがありません'); return; }
    clearEmptyChart(canvasId);
    const labels = rows.map((r) => r.label);
    renderDualLineChart(canvasId, labels, [
      { label: '自己資本比率(%)', data: rows.map((r) => (r.equity_ratio != null ? r.equity_ratio * 100 : null)), color: COLORS.green },
      { label: 'D/Eレシオ', data: rows.map((r) => r.debt_equity_ratio), color: COLORS.amber },
    ], { yFmt: (v) => (v == null ? '-' : `${v.toFixed(1)}`) });
  }

  function renderDividendEpsChart(canvasId, annualRows) {
    if (!document.getElementById(canvasId)) return;
    const rows = annualRows.filter((r) => r.eps != null || r.dividend_per_share != null);
    if (!rows.length) { showEmptyChart(canvasId, '配当・EPSデータがありません'); return; }
    clearEmptyChart(canvasId);
    resizeWrap(canvasId, 280);
    const labels = rows.map((r) => r.label);
    const opts = baseOptions((v) => `${v.toFixed(0)}円`, { legend: true });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed?.(0) ?? ctx.parsed.y}円`;
    mountChart(canvasId, {
      type: 'line',
      data: {
        labels,
        datasets: [
          lineDataset('EPS', rows.map((r) => r.eps), COLORS.blue),
          lineDataset('1株配当', rows.map((r) => r.dividend_per_share), COLORS.green),
        ],
      },
      options: opts,
    });
  }

  function renderDividendYieldChart(canvasId, valuationItems) {
    if (!document.getElementById(canvasId)) return;
    const rows = (valuationItems || []).filter((r) => r.dividend_yield != null);
    if (!rows.length) { showEmptyChart(canvasId, '配当利回り推移がありません'); return; }
    clearEmptyChart(canvasId);
    const labels = rows.map((r) => (r.fiscal_year_end || '').slice(0, 7));
    renderLineChart(canvasId, labels, rows.map((r) => r.dividend_yield * 100), {
      label: '配当利回り',
      color: COLORS.green,
      yFmt: (v) => `${v.toFixed(2)}%`,
    });
    const chart = instances.get(canvasId);
    if (chart) {
      chart.options.plugins.valueLabels = { formatter: (v) => `${v.toFixed(2)}%` };
      chart.options.scales.y.ticks.callback = (v) => `${v}%`;
    }
  }

  function renderQuarterlySingleChart(canvasId, qtrItems) {
    if (!document.getElementById(canvasId)) return;
    const sorted = [...(qtrItems || [])].sort((a, b) => (a.period_end || '').localeCompare(b.period_end || ''));
    const rows = sorted.filter((r) => r.operating_income_single != null || r.net_income_single != null);
    if (!rows.length) { showEmptyChart(canvasId, '四半期単期データがありません'); return; }
    clearEmptyChart(canvasId);
    const labels = rows.map(quarterLabel);
    const toOku = (v) => (v == null ? null : v / 1e8);
    const opts = baseOptions(yenOku, { legend: true });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${yenOku(ctx.parsed.y * 1e8)}`;
    mountChart(canvasId, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { label: '営業利益（単期）', data: rows.map((r) => toOku(r.operating_income_single)), backgroundColor: COLORS.blue + 'cc', borderRadius: 3 },
          { label: '純利益（単期）', data: rows.map((r) => toOku(r.net_income_single)), backgroundColor: COLORS.purple + 'cc', borderRadius: 3 },
        ],
      },
      options: opts,
    });
  }

  function renderQuarterlyQoqChart(canvasId, qtrItems) {
    if (!document.getElementById(canvasId)) return;
    const sorted = [...(qtrItems || [])].sort((a, b) => (a.period_end || '').localeCompare(b.period_end || ''));
    const seen = new Set();
    const rows = [];
    for (const row of sorted) {
      const label = quarterLabel(row);
      if (!label || seen.has(label)) continue;
      if (row.revenue_qoq == null && row.operating_income_qoq == null) continue;
      seen.add(label);
      rows.push({ label, revenue_qoq: row.revenue_qoq, operating_income_qoq: row.operating_income_qoq });
    }
    if (!rows.length) { showEmptyChart(canvasId, 'QoQデータがありません'); return; }
    clearEmptyChart(canvasId);
    const labels = rows.map((r) => r.label);
    renderDualLineChart(canvasId, labels, [
      { label: '売上QoQ', data: rows.map((r) => r.revenue_qoq), color: COLORS.green },
      { label: '営業利益QoQ', data: rows.map((r) => r.operating_income_qoq), color: COLORS.blue },
    ], { yFmt: (v) => pct(v) });
  }

  function renderDeepAnalysis(fin, qtrItems, valuationItems) {
    const annual = sanitizeAnnualRowsFull(fin);
    renderCfTrioChart('chart-cf-trio', annual);
    renderFcfChart('chart-fcf', annual);
    renderEquityDebtChart('chart-equity-debt', annual);
    renderDividendEpsChart('chart-dividend-eps', annual);
    renderDividendYieldChart('chart-dividend-yield', valuationItems);
    renderQuarterlySingleChart('chart-q-single', qtrItems);
    renderQuarterlyQoqChart('chart-q-qoq', qtrItems);
  }

  function renderCompareCharts(items, priceByCode = {}, opts = {}) {
    const COMPARE_COLORS = ['#0284c7', '#059669', '#d97706', '#dc2626'];
    const prefix = opts.prefix || 'compare-chart';
    const fiscalLabel = (fye) => String(fye || '').slice(0, 7);
    const shortName = (item) => (item.name || '').replace(/株式会社/g, '').trim() || item.edinet_code;

    function buildSeries(field) {
      const labelSet = new Set();
      items.forEach((item) => {
        (item.financials || []).forEach((f) => {
          if (f.fiscal_year_end) labelSet.add(fiscalLabel(f.fiscal_year_end));
        });
      });
      const labels = [...labelSet].sort();
      const series = items.map((item, idx) => {
        const map = Object.fromEntries(
          (item.financials || []).map((f) => [fiscalLabel(f.fiscal_year_end), f[field]])
        );
        return {
          label: shortName(item),
          data: labels.map((l) => (map[l] != null ? map[l] : null)),
          color: COMPARE_COLORS[idx % COMPARE_COLORS.length],
        };
      });
      return { labels, series };
    }

    const revenue = buildSeries('revenue');
    renderMultiLineChart(`${prefix}-revenue`, revenue.labels, revenue.series, { yFmt: yenOku });

    const roe = buildSeries('roe');
    renderMultiLineChart(`${prefix}-roe`, roe.labels, roe.series, { yFmt: (v) => pct(v) });

    const margin = buildSeries('operating_margin');
    renderMultiLineChart(`${prefix}-margin`, margin.labels, margin.series, { yFmt: (v) => pct(v) });

    const priceSeries = items.map((item, idx) => {
      const points = priceByCode[item.edinet_code] || [];
      if (points.length < 2 || !points[0].close) return null;
      const base = points[0].close;
      return {
        label: shortName(item),
        labels: points.map((p) => p.date),
        data: points.map((p) => (p.close / base) * 100),
        color: COMPARE_COLORS[idx % COMPARE_COLORS.length],
      };
    }).filter(Boolean);

    if (!priceSeries.length) {
      showEmptyChart(`${prefix}-price`, '株価データがありません');
      return;
    }
    const maxLen = Math.max(...priceSeries.map((s) => s.data.length));
    const labels = priceSeries[0].labels.slice(-maxLen).map((d) => String(d).slice(5));
    renderMultiLineChart(
      `${prefix}-price`,
      labels,
      priceSeries.map((s) => ({
        label: s.label,
        data: s.data.slice(-maxLen),
        color: s.color,
      })),
      // 日次で点数が多いため、数値ラベル・塗りつぶし・マーカーを消して細い線のみに
      // （数値やマーカーが重なって真っ黒になるのを防ぎ、各社の推移が見えるように）。
      { yFmt: (v) => `${Number(v).toFixed(1)}`, fill: false, pointRadius: 0, borderWidth: 1.5, valueLabels: false }
    );
  }

  function renderMultiLineChart(canvasId, labels, series, { yFmt = yenOku, fill = true, pointRadius = 2, borderWidth, valueLabels = true } = {}) {
    if (!document.getElementById(canvasId)) return;
    const hasData = series.some((s) => hasNumericData(s.data));
    if (!hasData) {
      showEmptyChart(canvasId);
      return;
    }
    resizeWrap(canvasId, 280);
    // valueLabels=false のとき各点の数値ラベルを描かない（点数が多いと数値が
    // 重なって真っ黒になり読めなくなるため）。
    const opts = baseOptions(yFmt, { legend: true, labelFmt: valueLabels ? (v) => yFmt(v) : null });
    opts.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${yFmt(ctx.parsed.y)}`;
    mountChart(canvasId, {
      type: 'line',
      data: {
        labels,
        datasets: series.map((s) => {
          const ds = {
            ...lineDataset(s.label, s.data, s.color),
            spanGaps: true,
            pointRadius,
            // 点数が多いチャートは塗り重なりで真っ黒になるため、fill を切って
            // 細い線のみで描画する（fill=false のとき）。
            fill,
          };
          if (borderWidth != null) ds.borderWidth = borderWidth;
          return ds;
        }),
      },
      options: opts,
    });
  }

  global.EdinetCharts = {
    renderCompanyCharts,
    renderFromFinancials,
    renderVerticalBarChart,
    renderHorizontalBarChart,
    renderSearchTrendChart,
    renderPriceLineChart,
    renderValuationChart,
    renderDeepAnalysis,
    renderCompareCharts,
    renderMultiLineChart,
    resizeAll,
    yenOku,
    pct,
  };
})(typeof window !== 'undefined' ? window : globalThis);
