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
  let dashboardHome = null;
  let globalsCache = {};
  const companyCache = new Map();

  const DASH_VIEW_SIGNATURES = {
    revenue: 'sort_by=revenue&order=desc',
    roe: 'sort_by=roe&order=desc&min_roe=0.1',
    margin: 'sort_by=operating_margin&order=desc',
    growth: 'sort_by=revenue_growth&order=desc&min_revenue_growth=0.1',
    net_income: 'sort_by=net_income&order=desc',
    market_cap: 'sort_by=market_cap&order=desc',
    low_per: 'sort_by=per&order=asc&max_per=20',
    realestate: 'sort_by=real_estate_book&order=desc&has_real_estate=true',
    net_cash: 'sort_by=net_cash&order=desc&has_net_cash=true',
    dividend: 'sort_by=dividend_yield&order=desc&min_dividend_yield=0.03',
  };

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

  async function loadDashboardHome() {
    if (!dashboardHome) {
      try {
        dashboardHome = await fetchJson('/data/dashboard/home.json');
      } catch {
        dashboardHome = { views: {} };
      }
    }
    return dashboardHome;
  }

  function matchDashboardView(params) {
    if (params.get('industry')) return null;
    if (params.get('listing') && params.get('listing') !== '上場') return null;
    for (const [view, expected] of Object.entries(DASH_VIEW_SIGNATURES)) {
      const exp = new URLSearchParams(expected);
      let ok = true;
      for (const [k, v] of exp.entries()) {
        if ((params.get(k) || '') !== v) { ok = false; break; }
      }
      if (!ok) continue;
      const allowed = new Set(['listing', 'limit', 'offset', ...exp.keys()]);
      for (const [k, v] of params.entries()) {
        if (!v) continue;
        if (!allowed.has(k)) { ok = false; break; }
      }
      if (ok) return view;
    }
    return null;
  }

  async function loadCompanyBundle(code) {
    if (!companyCache.has(code)) {
      companyCache.set(code, await fetchJson(`/data/companies/${code}.json`));
    }
    return companyCache.get(code);
  }

  async function loadGlobal(rel) {
    if (!globalsCache[rel]) {
      try {
        globalsCache[rel] = await fetchJson(`/data/${rel}`);
      } catch (err) {
        if (String(rel).startsWith('explore/')) {
          globalsCache[rel] = { items: [], count: 0, total: 0 };
        } else {
          throw err;
        }
      }
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
    const hasNetCash = params.get('has_net_cash') === 'true';
    const minEquity = num(params.get('min_equity_ratio'));
    const maxDe = num(params.get('max_debt_equity_ratio'));
    const minDivYield = num(params.get('min_dividend_yield'));
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
    if (hasNetCash) {
      rows = rows.filter(r => {
        const cash = r.cash_and_deposits;
        if (cash == null) return false;
        const net = r.net_cash != null ? r.net_cash : cash - (r.interest_bearing_debt || 0);
        return net > 0;
      });
    }
    if (minEquity != null) rows = rows.filter(r => (r.equity_ratio ?? -Infinity) >= minEquity);
    if (maxDe != null) rows = rows.filter(r => r.debt_equity_ratio != null && r.debt_equity_ratio >= 0 && r.debt_equity_ratio <= maxDe);
    if (minDivYield != null) rows = rows.filter(r => (r.dividend_yield ?? -Infinity) >= minDivYield);
    if (sortBy === 'market_cap') {
      rows = rows.filter(r => (r.market_cap ?? 0) > 0);
    }
    if (sortBy === 'net_cash') rows = rows.filter(r => r.cash_and_deposits != null);
    if (sortBy === 'equity_ratio') rows = rows.filter(r => r.equity_ratio != null);
    if (sortBy === 'debt_equity') rows = rows.filter(r => r.debt_equity_ratio != null);
    if (sortBy === 'dividend_yield') rows = rows.filter(r => (r.dividend_yield ?? 0) > 0);
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
      equity_ratio: r => r.equity_ratio,
      debt_equity: r => r.debt_equity_ratio,
      net_cash: r => r.net_cash != null ? r.net_cash : (r.cash_and_deposits != null ? r.cash_and_deposits - (r.interest_bearing_debt || 0) : null),
      dividend_yield: r => r.dividend_yield,
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
      let rows = (all.items || []).slice();
      const codes = (params.get('codes') || '').split(',').map(c => c.trim()).filter(Boolean);
      if (codes.length) {
        const set = new Set(codes);
        rows = rows.filter(r => set.has(r.edinet_code));
      }
      const q = (params.get('q') || '').trim().toLowerCase();
      if (q) {
        rows = rows.filter(r => {
          const hay = [r.company_name, r.doc_description, r.edinet_code, r.sec_code]
            .filter(Boolean).join(' ').toLowerCase();
          return hay.includes(q);
        });
      }
      const limit = Math.min(num(params.get('limit')) || 50, 500);
      const offset = num(params.get('offset')) || 0;
      const items = rows.slice(offset, offset + limit);
      return { total: rows.length, count: items.length, offset, items };
    }
    if (pathname === '/api/explore/quarterly-momentum') {
      const all = await loadGlobal('explore/quarterly-momentum.json');
      let rows = (all.items || []).slice();
      const industry = params.get('industry');
      const minYoy = num(params.get('min_revenue_yoy'));
      if (industry) rows = rows.filter(r => (r.industry || '').includes(industry));
      if (minYoy != null) rows = rows.filter(r => (r.revenue_yoy ?? -Infinity) >= minYoy);
      const sortBy = params.get('sort_by') || 'revenue_yoy';
      const order = params.get('order') || 'desc';
      rows.sort((a, b) => {
        const av = a[sortBy];
        const bv = b[sortBy];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        return order === 'asc' ? av - bv : bv - av;
      });
      return paginate(rows, params);
    }
    if (pathname === '/api/explore/prefectures') {
      return loadGlobal('explore/prefectures.json');
    }
    if (pathname === '/api/screening') {
      const viewKey = matchDashboardView(params);
      if (viewKey) {
        const home = await loadDashboardHome();
        const cached = home.views && home.views[viewKey];
        if (cached && Array.isArray(cached.items) && cached.items.length) {
          const limit = Math.min(num(params.get('limit')) || 100, 500);
          const offset = num(params.get('offset')) || 0;
          const slice = cached.items.slice(offset, offset + limit);
          return {
            total: cached.total ?? cached.items.length,
            items: slice,
            count: slice.length,
            offset,
          };
        }
      }
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
      if (sub === 'short-selling') return bundle.short_selling || { holders: [], count: 0, total_ratio: null };
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
  // トップ表示に必要な軽量JSONのみ先行ロード（screening/catalog は遅延）
  global.staticApiReady = Promise.all([
    fetchJson('/data/manifest.json').catch(() => null),
    loadDashboardHome(),
    loadGlobal('themes/weekly.json').catch(() => null),
    loadGlobal('trending/home.json').catch(() => null),
    loadGlobal('industries.json').catch(() => null),
  ]);
})(typeof window !== 'undefined' ? window : globalThis);
