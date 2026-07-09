/**
 * GitHub Pages 静的モード: /api/* をローカル JSON + クライアント処理へルーティング
 */
(function (global) {
  'use strict';

  const MIN_SANE_EPS = 0.01;
  const MAX_SANE_EPS = 100000;
  const MIN_SANE_PER = 0.5;
  const MAX_SANE_PER = 500;
  const MIN_SANE_MARKET_CAP = 1_000_000_000;

  let screeningIndex = null;
  let searchCatalog = null;
  let globalsCache = {};
  const companyCache = new Map();

  function parsePath(path) {
    const q = path.indexOf('?');
    if (q === -1) return { pathname: path, params: new URLSearchParams() };
    return { pathname: path.slice(0, q), params: new URLSearchParams(path.slice(q + 1)) };
  }

  function num(v) {
    if (v == null || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function perVal(item) {
    return item.per_edinet ?? item.per;
  }

  function pbrVal(item) {
    return item.pbr_edinet ?? item.pbr;
  }

  async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  async function loadScreeningIndex() {
    if (!screeningIndex) {
      screeningIndex = await fetchJson('/data/screening/index.json');
    }
    return screeningIndex;
  }

  async function loadSearchCatalog() {
    if (!searchCatalog) {
      searchCatalog = await fetchJson('/data/search/catalog.json');
    }
    return searchCatalog;
  }

  async function loadCompanyBundle(code) {
    if (!companyCache.has(code)) {
      companyCache.set(code, await fetchJson(`/data/companies/${code}.json`));
    }
    return companyCache.get(code);
  }

  async function loadGlobal(rel) {
    if (!globalsCache[rel]) {
      globalsCache[rel] = await fetchJson(`/data/${rel}`);
    }
    return globalsCache[rel];
  }

  function passesPerSanity(item) {
    const eps = item.eps;
    const p = perVal(item);
    if (eps == null || eps <= MIN_SANE_EPS || eps > MAX_SANE_EPS) return false;
    if (p == null || p < MIN_SANE_PER || p > MAX_SANE_PER) return false;
    return true;
  }

  function filterScreening(items, params) {
    let rows = items.slice();
    const industry = params.get('industry');
    const minRevenue = num(params.get('min_revenue'));
    const maxRevenue = num(params.get('max_revenue'));
    const minOperatingMargin = num(params.get('min_operating_margin'));
    const minRoe = num(params.get('min_roe'));
    const minRoa = num(params.get('min_roa'));
    const minGrowth = num(params.get('min_revenue_growth'));
    const minPer = num(params.get('min_per'));
    const maxPer = num(params.get('max_per'));
    const minPbr = num(params.get('min_pbr'));
    const maxPbr = num(params.get('max_pbr'));
    const minNav = num(params.get('min_real_estate_nav_ratio'));
    const hasRe = params.get('has_real_estate') === 'true';
    const hasCf = params.get('has_operating_cf') === 'true';
    const sortBy = params.get('sort_by') || 'revenue';
    const order = params.get('order') || 'desc';

    const usesPer = minPer != null || maxPer != null || sortBy === 'per' || sortBy === 'pbr';
    if (usesPer) rows = rows.filter(passesPerSanity);

    if (industry) rows = rows.filter(r => (r.industry || '').includes(industry));
    if (minRevenue != null) rows = rows.filter(r => (r.revenue ?? -Infinity) >= minRevenue);
    if (maxRevenue != null) rows = rows.filter(r => (r.revenue ?? Infinity) <= maxRevenue);
    if (minOperatingMargin != null) rows = rows.filter(r => (r.operating_margin ?? -Infinity) >= minOperatingMargin);
    if (minRoe != null) rows = rows.filter(r => (r.roe ?? -Infinity) >= minRoe);
    if (minRoa != null) rows = rows.filter(r => (r.roa ?? -Infinity) >= minRoa);
    if (minGrowth != null) rows = rows.filter(r => (r.revenue_growth ?? -Infinity) >= minGrowth);
    if (minPer != null) rows = rows.filter(r => (perVal(r) ?? -Infinity) >= minPer);
    if (maxPer != null) rows = rows.filter(r => (perVal(r) ?? Infinity) <= maxPer);
    if (minPbr != null) rows = rows.filter(r => (pbrVal(r) ?? -Infinity) >= minPbr);
    if (maxPbr != null) rows = rows.filter(r => (pbrVal(r) ?? Infinity) <= maxPbr);
    if (hasRe) rows = rows.filter(r => r.real_estate);
    if (hasCf) rows = rows.filter(r => (r.operating_cf ?? 0) > 0);
    if (sortBy === 'market_cap') {
      rows = rows.filter(r => (r.market_cap ?? 0) > 0);
    }
    if (minNav != null || sortBy === 'real_estate_nav') {
      rows = rows.filter(r => (r.market_cap ?? 0) >= MIN_SANE_MARKET_CAP && r.real_estate_nav_ratio != null);
    }
    if (minNav != null) {
      rows = rows.filter(r => (r.real_estate_nav_ratio ?? 0) >= minNav);
    }

    const sortKey = {
      revenue: r => r.revenue,
      operating_margin: r => r.operating_margin,
      roe: r => r.roe,
      roa: r => r.roa,
      revenue_growth: r => r.revenue_growth,
      net_income: r => r.net_income,
      per: r => perVal(r),
      pbr: r => pbrVal(r),
      market_cap: r => r.market_cap,
      operating_cf: r => r.operating_cf,
      real_estate_nav: r => r.real_estate_nav_ratio,
      real_estate_book: r => r.real_estate?.total_book_value_m,
    }[sortBy] || (r => r.revenue);

    rows.sort((a, b) => {
      const av = sortKey(a);
      const bv = sortKey(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return order === 'asc' ? av - bv : bv - av;
    });

    return rows;
  }

  function paginate(items, params) {
    const limit = Math.min(num(params.get('limit')) || 100, 500);
    const offset = num(params.get('offset')) || 0;
    const slice = items.slice(offset, offset + limit);
    return { total: items.length, items: slice, count: slice.length, offset };
  }

  function searchCompanies(params) {
    const q = (params.get('q') || '').trim().toLowerCase();
    const listing = params.get('listing');
    const industry = params.get('industry');
    const hasRe = params.get('has_real_estate') === 'true';
    let rows = searchCatalog.items.slice();
    if (listing) rows = rows.filter(r => r.listing_status === listing);
    if (industry) rows = rows.filter(r => (r.industry || '').includes(industry));
    if (hasRe) rows = rows.filter(r => r.real_estate);
    if (q) {
      rows = rows.filter(r => {
        const hay = [r.name, r.name_en, r.sec_code, r.edinet_code].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(q);
      });
    }
    rows.sort((a, b) => (a.name || '').localeCompare(b.name || '', 'ja'));
    const limit = Math.min(num(params.get('limit')) || 50, 200);
    const offset = num(params.get('offset')) || 0;
    const slice = rows.slice(offset, offset + limit);
    return { total: rows.length, items: slice };
  }

  function compareBatch(codes) {
    return Promise.all(
      codes.map(async (code) => {
        try {
          const bundle = await loadCompanyBundle(code);
          const s = bundle.summary;
          const c = s.company;
          const fin = (s.financials || [])[0];
          const st = s.stock;
          return {
            edinet_code: c.edinet_code,
            name: c.name,
            sec_code: c.sec_code,
            industry: c.industry,
            fiscal_year_end: fin?.fiscal_year_end,
            revenue: fin?.revenue,
            operating_margin: fin?.operating_margin,
            roe: fin?.roe,
            revenue_growth: fin?.revenue_growth,
            price: st?.price,
            market_cap: st?.market_cap,
            per: st?.per_edinet ?? st?.per,
            pbr: st?.pbr_edinet ?? st?.pbr,
            real_estate: s.real_estate,
          };
        } catch {
          return null;
        }
      })
    ).then(items => ({ count: items.filter(Boolean).length, items: items.filter(Boolean) }));
  }

  async function compareDetail(codes, financialLimit) {
    const items = [];
    for (const code of codes) {
      try {
        const bundle = await loadCompanyBundle(code);
        const s = bundle.summary;
        const fin = (s.financials || []).slice(0, financialLimit);
        const snap = (await compareBatch([code])).items[0];
        if (!snap) continue;
        items.push({
          ...snap,
          financials: fin.map(f => ({
            fiscal_year_end: f.fiscal_year_end,
            revenue: f.revenue,
            operating_income: f.operating_income,
            net_income: f.net_income,
            operating_margin: f.operating_margin,
            roe: f.roe,
            roa: f.roa,
            revenue_growth: f.revenue_growth,
            operating_cf: f.operating_cf,
            equity_ratio: f.equity_ratio,
            eps: f.eps,
            bps: f.bps,
            submit_date_time: f.submit_date_time,
          })),
          fiscal_year_end: fin[0]?.fiscal_year_end,
          real_estate: s.real_estate,
          stock: s.stock,
        });
      } catch { /* skip */ }
    }
    return { count: items.length, items };
  }

  async function staticApiFetch(path) {
    const { pathname, params } = parsePath(path);

    if (pathname === '/api/screening/industries') {
      return loadGlobal('industries.json');
    }
    if (pathname === '/api/trending/home') {
      return loadGlobal('trending/home.json');
    }
    if (pathname === '/api/themes/weekly') {
      return loadGlobal('themes/weekly.json');
    }
    if (pathname === '/api/calendar/earnings') {
      return loadGlobal('calendar/earnings.json');
    }
    if (pathname === '/api/calendar/disclosures') {
      const all = await loadGlobal('calendar/disclosures.json');
      const limit = Math.min(num(params.get('limit')) || 50, 500);
      const offset = num(params.get('offset')) || 0;
      const items = (all.items || []).slice(offset, offset + limit);
      return { ...all, total: all.total ?? all.items?.length ?? 0, count: items.length, offset, items };
    }
    if (pathname === '/api/screening') {
      const idx = await loadScreeningIndex();
      const filtered = filterScreening(idx.items || [], params);
      return paginate(filtered, params);
    }
    if (pathname === '/api/companies') {
      await loadSearchCatalog();
      return searchCompanies(params);
    }
    if (pathname === '/api/companies/compare/batch') {
      const codes = (params.get('codes') || '').split(',').map(c => c.trim()).filter(Boolean).slice(0, 4);
      return compareBatch(codes);
    }
    if (pathname === '/api/companies/compare/detail') {
      const codes = (params.get('codes') || '').split(',').map(c => c.trim()).filter(Boolean).slice(0, 4);
      const limit = num(params.get('financial_limit')) || 8;
      return compareDetail(codes, limit);
    }

    const companyMatch = pathname.match(/^\/api\/companies\/([^/]+)(?:\/(.+))?$/);
    if (companyMatch) {
      const code = companyMatch[1];
      const sub = companyMatch[2];
      const bundle = await loadCompanyBundle(code);
      if (!sub) {
        const limit = num(params.get('financial_limit')) || 12;
        const summary = bundle.summary;
        if (summary?.financials) {
          summary.financials = summary.financials.slice(0, limit);
        }
        return summary;
      }
      if (sub === 'news') return bundle.news || { items: [], count: 0 };
      if (sub === 'search-trend') {
        const days = num(params.get('days')) || 90;
        const key = days <= 7 ? 'trend_7' : days <= 30 ? 'trend_30' : 'trend_90';
        return bundle[key] || { points: [], count: 0 };
      }
      if (sub === 'profile') return bundle.profile;
      if (sub === 'real-estate') return bundle.real_estate;
      if (sub === 'quarterly') return bundle.quarterly;
      if (sub === 'valuation-history') return bundle.valuation_history;
      if (sub === 'price-history') {
        const range = params.get('range') || '1y';
        return bundle.price_history?.[range] || { points: [] };
      }
    }

    throw new Error('404');
  }

  global.staticApiFetch = staticApiFetch;
  global.staticApiReady = Promise.all([
    loadScreeningIndex().catch(() => { screeningIndex = { items: [] }; }),
    loadSearchCatalog().catch(() => { searchCatalog = { items: [] }; }),
    fetchJson('/data/manifest.json').catch(() => null),
  ]);
})(typeof window !== 'undefined' ? window : globalThis);
