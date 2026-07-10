from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from sqlalchemy import func, select

from app.config import settings
from app.collection_log import record_sync_snapshot
from app.data_quality import build_data_quality_stats
from app.external_media.collector import collect_external_media_batch
from app.external_media.store import purge_all_irrelevant_news
from app.db import Company, CompanyProfile, Filing, Financial, QuarterlyFinancial, RealEstateProperty, RealEstateSync, SessionLocal, StockQuote, init_db
from app.db_maintenance import checkpoint_after_write
from app.edinet.client import CURRENT_PARSE_VERSION, EdinetClient
from app.edinet.sync import sync_companies, sync_filings_for_range, upsert_financial_from_doc
from app.edinet.backfill_fast import backfill_financials_fast
from app.edinet.real_estate_sync import sync_real_estate
from app.edinet.profile_sync import count_pending_profiles, seed_no_xbrl_profiles, sync_profiles
from app.edinet.quarterly_sync import recompute_all_qoq, sync_quarterly_financials
from app.edinet.sync_plan import estimate_api_calls, iter_peak_windows, iter_quarterly_windows
from app.market.sync import backfill_quote_valuations, sync_stock_prices

YUBO_DOC_TYPES = ("120", "130", "140", "150")


def _count_pending_backfill(db) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Filing)
            .outerjoin(Financial, Filing.doc_id == Financial.doc_id)
            .where(
                Filing.doc_type_code.in_(YUBO_DOC_TYPES),
                Filing.has_csv.is_(True),
                Financial.id.is_(None),
            )
        )
        or 0
    )


def _count_reparse_needed(db) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Financial)
            .where(Financial.parse_version < CURRENT_PARSE_VERSION)
        )
        or 0
    )


def _count_missing_quotes(db) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Company)
            .outerjoin(StockQuote, Company.edinet_code == StockQuote.edinet_code)
            .where(
                Company.listing_status == "上場",
                Company.sec_code.is_not(None),
                StockQuote.edinet_code.is_(None),
            )
        )
        or 0
    )


def _print_gap_status(db, label: str = "進捗") -> None:
    print(
        {
            "phase": label,
            "pending_backfill": _count_pending_backfill(db),
            "reparse_needed": _count_reparse_needed(db),
            "missing_quotes": _count_missing_quotes(db),
        },
        flush=True,
    )


def _run_backfill_rounds(db, client: EdinetClient, *, rounds: int, limit: int) -> dict[str, int]:
    total = {"processed": 0, "financials": 0}
    for round_no in range(1, rounds + 1):
        pending = db.scalars(
            select(Filing)
            .outerjoin(Financial, Filing.doc_id == Financial.doc_id)
            .where(
                Filing.doc_type_code.in_(YUBO_DOC_TYPES),
                Filing.has_csv.is_(True),
                Financial.id.is_(None),
            )
            .limit(limit)
        ).all()
        if not pending:
            break
        ok = 0
        for filing in pending:
            try:
                if upsert_financial_from_doc(db, client, filing):
                    ok += 1
                db.commit()
            except Exception as exc:
                db.rollback()
                print("  backfill error", filing.doc_id, exc, flush=True)
        total["processed"] += len(pending)
        total["financials"] += ok
        print(
            f"  backfill round {round_no}: {{'processed': {len(pending)}, 'financials': {ok}}}",
            flush=True,
        )
    return total


def _run_reparse_batch(db, client: EdinetClient, *, limit: int) -> dict[str, int]:
    financials = db.scalars(
        select(Financial)
        .where(Financial.parse_version < CURRENT_PARSE_VERSION)
        .limit(limit)
    ).all()
    ok = 0
    for financial in financials:
        filing = db.get(Filing, financial.doc_id)
        if not filing:
            continue
        try:
            if upsert_financial_from_doc(db, client, filing):
                ok += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            print("  reparse error", financial.doc_id, exc, flush=True)
    return {"processed": len(financials), "updated": ok}


def main() -> None:
    parser = argparse.ArgumentParser(description="EDINET データ同期")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="DB初期化")
    sub.add_parser("plan", help="同期プランのAPI呼び出し見積もり")

    companies = sub.add_parser("sync-companies", help="EDINETコードリスト同期")
    companies.add_argument("--api-key", default=settings.edinet_api_key)

    filings = sub.add_parser("sync-filings", help="指定期間の書類・財務データ同期")
    filings.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    filings.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD")
    filings.add_argument("--api-key", default=settings.edinet_api_key)
    filings.add_argument("--no-financials", action="store_true")
    filings.add_argument("--yuho-only", action="store_true")
    filings.add_argument("--sleep", type=float, default=1.0)

    today = sub.add_parser("sync-today", help="当日分の書類一覧を同期")
    today.add_argument("--api-key", default=settings.edinet_api_key)
    today.add_argument("--no-financials", action="store_true")
    today.add_argument("--sleep", type=float, default=1.0)

    peaks = sub.add_parser("sync-peaks", help="有報提出ピーク期間を一括同期")
    peaks.add_argument("--years", default="2023,2024,2025", help="例: 2023,2024,2025")
    peaks.add_argument("--api-key", default=settings.edinet_api_key)
    peaks.add_argument("--no-financials", action="store_true", help="一覧だけ先に取る（推奨）")
    peaks.add_argument("--all-types", action="store_true", help="四半期・半期も含めて同期")
    peaks.add_argument("--sleep", type=float, default=1.0)

    backfill = sub.add_parser("backfill-financials", help="未解析の有報CSVから財務データを取得")
    backfill.add_argument("--api-key", default=settings.edinet_api_key)
    backfill.add_argument("--limit", type=int, default=100)
    backfill.add_argument("--sleep", type=float, default=1.5)

    backfill_fast = sub.add_parser(
        "backfill-fast",
        help="財務CSVを並列ダウンロードして高速バックフィル",
    )
    backfill_fast.add_argument("--api-key", default=settings.edinet_api_key)
    backfill_fast.add_argument("--limit", type=int, default=500)
    backfill_fast.add_argument("--workers", type=int, default=12)
    backfill_fast.add_argument("--sleep", type=float, default=0.25)

    collect = sub.add_parser("collect-more", help="追加同期+財務バックフィルをまとめて実行")
    collect.add_argument("--api-key", default=settings.edinet_api_key)
    collect.add_argument("--years", default="2020,2021,2022", help="追加で同期する年（空ならスキップ）")
    collect.add_argument("--skip-peaks", action="store_true", help="ピーク同期をスキップ")
    collect.add_argument("--backfill-rounds", type=int, default=5)
    collect.add_argument("--backfill-limit", type=int, default=500)
    collect.add_argument("--sleep", type=float, default=0.8)

    loop = sub.add_parser("backfill-loop", help="財務バックフィルを繰り返し実行")
    loop.add_argument("--api-key", default=settings.edinet_api_key)
    loop.add_argument("--rounds", type=int, default=20)
    loop.add_argument("--limit", type=int, default=500)
    loop.add_argument("--sleep", type=float, default=0.6)

    prices = sub.add_parser("sync-prices", help="上場企業の株価・PER/PBRをYahoo Financeから取得")
    prices.add_argument("--limit", type=int, default=500)
    prices.add_argument("--sleep", type=float, default=1.0)
    prices.add_argument("--only-missing", action="store_true")
    prices.add_argument("--workers", type=int, default=1, help="並列数（12以上推奨）")
    prices.add_argument("--fast", action="store_true", help="Yahoo Chart API のみで高速取得")

    backfill_quotes = sub.add_parser(
        "backfill-quotes",
        help="株価×発行株式数から時価総額・PER/PBRを再計算（Yahoo API不要）",
    )

    reparse = sub.add_parser("reparse-financials", help="既存財務データを拡張項目付きで再解析")
    reparse.add_argument("--api-key", default=settings.edinet_api_key)
    reparse.add_argument("--limit", type=int, default=200)
    reparse.add_argument("--sleep", type=float, default=0.6)

    complete = sub.add_parser(
        "complete-data",
        help="Step1: バックフィル + reparse + 株価同期を完了まで実行",
    )
    complete.add_argument("--api-key", default=settings.edinet_api_key)
    complete.add_argument("--batch-rounds", type=int, default=25, help="バックフィル1サイクルのラウンド数")
    complete.add_argument("--batch-limit", type=int, default=500, help="1ラウンドあたり件数")
    complete.add_argument("--reparse-limit", type=int, default=500, help="reparse 1サイクルの件数")
    complete.add_argument("--price-limit", type=int, default=2000, help="株価同期 1サイクルの件数")
    complete.add_argument("--sleep", type=float, default=0.55)
    complete.add_argument("--price-workers", type=int, default=16)
    complete.add_argument("--max-cycles", type=int, default=100, help="全体の繰り返し上限")

    daily = sub.add_parser("daily-sync", help="毎日実行: 書類・財務・株価・四半期・不動産を更新")
    daily.add_argument("--api-key", default=settings.edinet_api_key)
    daily.add_argument("--backfill-limit", type=int, default=100)
    daily.add_argument("--price-limit", type=int, default=4000)
    daily.add_argument("--price-workers", type=int, default=16)
    daily.add_argument("--reparse-limit", type=int, default=300, help="年次reparse 1日あたり上限")
    daily.add_argument("--quarterly-limit", type=int, default=200, help="四半期CSV解析 1日あたり上限")
    daily.add_argument("--quarterly-workers", type=int, default=8)
    daily.add_argument("--real-estate-limit", type=int, default=150, help="不動産XBRL解析 1日あたり上限")
    daily.add_argument("--real-estate-workers", type=int, default=4)
    daily.add_argument("--sleep", type=float, default=0.6)

    real_estate = sub.add_parser(
        "sync-real-estate",
        help="有報XBRLから主要設備（保有不動産明細）を高速取得",
    )
    real_estate.add_argument("--api-key", default=settings.edinet_api_key)
    real_estate.add_argument("--limit", type=int, default=100)
    real_estate.add_argument("--workers", type=int, default=8, help="並列ダウンロード数")
    real_estate.add_argument("--sleep", type=float, default=0.35)
    real_estate.add_argument("--only-missing", action="store_true", default=True)
    real_estate.add_argument("--all", dest="only_missing", action="store_false")
    real_estate.add_argument("--listing", default="上場", help="対象上場区分（空=全社）")

    quarterly = sub.add_parser(
        "sync-quarterly",
        help="四半期報告書CSVから YoY/QoQ を高速取得",
    )
    quarterly.add_argument("--api-key", default=settings.edinet_api_key)
    quarterly.add_argument("--limit", type=int, default=500)
    quarterly.add_argument("--workers", type=int, default=12)
    quarterly.add_argument("--sleep", type=float, default=0.25)
    quarterly.add_argument("--only-missing", action="store_true", default=True)
    quarterly.add_argument("--all", dest="only_missing", action="store_false")
    quarterly.add_argument("--reparse-stale", action="store_true", help="空行・旧バージョンの四半期を再解析")
    quarterly.add_argument("--listing", default="上場", help="空=全社")

    quarterly_collect = sub.add_parser(
        "collect-quarterly",
        help="四半期書類メタ同期 + YoY/QoQ 一括収集",
    )
    quarterly_collect.add_argument("--api-key", default=settings.edinet_api_key)
    quarterly_collect.add_argument("--years", default="2023,2024,2025")
    quarterly_collect.add_argument("--sleep", type=float, default=0.6)
    quarterly_collect.add_argument("--parse-limit", type=int, default=500)
    quarterly_collect.add_argument("--parse-workers", type=int, default=12)
    quarterly_collect.add_argument("--parse-sleep", type=float, default=0.25)
    quarterly_collect.add_argument("--max-parse-rounds", type=int, default=50)

    profiles = sub.add_parser(
        "sync-profiles",
        help="有報XBRLから企業概要・事業内容をバッチ取得（SEO用）",
    )
    profiles.add_argument("--api-key", default=settings.edinet_api_key)
    profiles.add_argument("--limit", type=int, default=100)
    profiles.add_argument("--workers", type=int, default=4)
    profiles.add_argument("--sleep", type=float, default=0.35)
    profiles.add_argument("--only-missing", action="store_true", default=True)
    profiles.add_argument("--all", dest="only_missing", action="store_false")
    profiles.add_argument("--listing", default="上場")
    profiles.add_argument("--no-priority", dest="priority_revenue", action="store_false", default=True)

    seed_profiles = sub.add_parser(
        "seed-no-xbrl-profiles",
        help="XBRLのない上場企業に no_xbrl 行を付与",
    )
    seed_profiles.add_argument("--listing", default="上場")

    complete_profiles = sub.add_parser(
        "sync-profiles-complete",
        help="未同期プロフィールを一括取得（pending=0まで）",
    )
    complete_profiles.add_argument("--api-key", default=settings.edinet_api_key)
    complete_profiles.add_argument("--batch-size", type=int, default=50)
    complete_profiles.add_argument("--workers", type=int, default=6)
    complete_profiles.add_argument("--sleep", type=float, default=0.35)
    complete_profiles.add_argument("--max-rounds", type=int, default=200)
    complete_profiles.add_argument("--listing", default="上場")

    external_media = sub.add_parser(
        "collect-external-media",
        help="Google News / Trends をローテーション取得（重複排除してDB保存）",
    )
    external_media.add_argument("--limit", type=int, default=settings.external_media_batch_limit)
    external_media.add_argument("--sleep-news", type=float, default=settings.external_media_sleep_news)
    external_media.add_argument("--sleep-trends", type=float, default=settings.external_media_sleep_trends)
    external_media.add_argument("--trend-days", type=int, default=90)
    external_media.add_argument("--no-trends", action="store_true", help="トレンド取得をスキップ")

    purge_news = sub.add_parser(
        "purge-irrelevant-news",
        help="保存済みニュースから無関係記事を一括削除",
    )
    purge_news.add_argument("--listing", default="上場")
    purge_news.add_argument("--limit", type=int, default=None)

    sub.add_parser(
        "cleanup-local-data",
        help="GitHub Release / 本番公開後にローカル DB・収集ログを削除",
    )

    args = parser.parse_args()
    init_db()

    if args.command == "init-db":
        print("DB initialized")
        return

    if args.command == "cleanup-local-data":
        from pathlib import Path

        tools_root = Path(__file__).resolve().parents[2] / "tools"
        sys.path.insert(0, str(tools_root))
        from cleanup_local_data import cleanup_local_data

        removed = cleanup_local_data()
        if removed:
            print("Removed local API data:")
            for item in removed:
                print(f"  - {item}")
        else:
            print("No local API data to remove.")
        return

    if args.command == "plan":
        years = [2023, 2024, 2025]
        print("=== フェーズ1: ピーク期間のみ（一覧） ===")
        print(estimate_api_calls(years, include_financial_download=False))
        print("=== フェーズ2: 有報CSV取得を追加 ===")
        print(estimate_api_calls(years, include_financial_download=True))
        print("=== フェーズ3: 毎日同期 ===")
        print({"daily_list_api_calls": 1, "note": "当日分メタデータ+書類一覧を1回"})
        return

    api_key = getattr(args, "api_key", None)
    if args.command not in ("init-db", "plan", "cleanup-local-data", "sync-prices", "backfill-quotes", "sync-real-estate", "sync-quarterly", "collect-quarterly", "sync-profiles", "seed-no-xbrl-profiles", "collect-external-media", "purge-irrelevant-news") and not api_key:
        raise SystemExit("EDINET_API_KEY を .env に設定してください")

    db = SessionLocal()
    try:
        if args.command == "sync-companies":
            client = EdinetClient(api_key=api_key)
            count = sync_companies(db, client)
            print(f"同期完了: {count} 社")

        elif args.command == "sync-filings":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            stats = sync_filings_for_range(
                db,
                client,
                start=date.fromisoformat(args.from_date),
                end=date.fromisoformat(args.to_date),
                fetch_financials=not args.no_financials,
                yuho_only=args.yuho_only,
            )
            print(stats)

        elif args.command == "sync-today":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            today_date = date.today()
            stats = sync_filings_for_range(
                db,
                client,
                start=today_date,
                end=today_date,
                fetch_financials=not args.no_financials,
            )
            print({"date": today_date.isoformat(), **stats})

        elif args.command == "sync-peaks":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
            total = {"windows": 0, "dates": 0, "filings": 0, "financials": 0, "errors": 0}
            for start, end, label in iter_peak_windows(years):
                print(f"[{datetime.now().isoformat(timespec='seconds')}] {label} {start}..{end}")
                stats = sync_filings_for_range(
                    db,
                    client,
                    start=start,
                    end=end,
                    fetch_financials=not args.no_financials,
                    yuho_only=not args.all_types,
                )
                total["windows"] += 1
                for key in ("dates", "filings", "financials", "errors"):
                    total[key] += stats[key]
                print(stats)
            print(total)

        elif args.command == "backfill-financials":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            filings = db.scalars(
                select(Filing)
                .outerjoin(Financial, Filing.doc_id == Financial.doc_id)
                .where(
                    Filing.doc_type_code.in_(YUBO_DOC_TYPES),
                    Filing.has_csv.is_(True),
                    Financial.id.is_(None),
                )
                .limit(args.limit)
            ).all()
            ok = 0
            for filing in filings:
                try:
                    if upsert_financial_from_doc(db, client, filing):
                        ok += 1
                        db.commit()
                except Exception as exc:
                    db.rollback()
                    print("error", filing.doc_id, exc)
            print({"processed": len(filings), "financials": ok})

        elif args.command == "backfill-fast":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            stats = backfill_financials_fast(
                db, client, limit=args.limit, workers=args.workers
            )
            print(stats, flush=True)

        elif args.command == "collect-more":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            years = [int(y.strip()) for y in args.years.split(",") if y.strip()]

            print("=== 企業マスタ更新 ===", flush=True)
            print({"companies": sync_companies(db, client)}, flush=True)

            print("=== 当日分同期 ===", flush=True)
            today_date = date.today()
            print(
                sync_filings_for_range(
                    db, client, start=today_date, end=today_date, fetch_financials=False
                ),
                flush=True,
            )

            if years and not args.skip_peaks:
                print(f"=== ピーク同期 {years} ===", flush=True)
                peak_total = {"windows": 0, "dates": 0, "filings": 0, "errors": 0}
                for start, end, label in iter_peak_windows(years):
                    print(f"  {label} {start}..{end}", flush=True)
                    stats = sync_filings_for_range(
                        db,
                        client,
                        start=start,
                        end=end,
                        fetch_financials=False,
                        yuho_only=True,
                    )
                    peak_total["windows"] += 1
                    for key in ("dates", "filings", "errors"):
                        peak_total[key] += stats[key]
                print(peak_total, flush=True)

            print(f"=== 財務バックフィル x{args.backfill_rounds} ===", flush=True)
            backfill_total = {"processed": 0, "financials": 0}
            for round_no in range(1, args.backfill_rounds + 1):
                pending = db.scalars(
                    select(Filing)
                    .outerjoin(Financial, Filing.doc_id == Financial.doc_id)
                    .where(
                        Filing.doc_type_code.in_(YUBO_DOC_TYPES),
                        Filing.has_csv.is_(True),
                        Financial.id.is_(None),
                    )
                    .limit(args.backfill_limit)
                ).all()
                if not pending:
                    print(f"  round {round_no}: 未処理なし", flush=True)
                    break
                ok = 0
                for filing in pending:
                    try:
                        if upsert_financial_from_doc(db, client, filing):
                            ok += 1
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        print("  error", filing.doc_id, exc, flush=True)
                backfill_total["processed"] += len(pending)
                backfill_total["financials"] += ok
                print(
                    f"  round {round_no}: {{'processed': {len(pending)}, 'financials': {ok}}}",
                    flush=True,
                )
            print(backfill_total, flush=True)

        elif args.command == "backfill-loop":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            total = {"processed": 0, "financials": 0}
            for round_no in range(1, args.rounds + 1):
                pending = db.scalars(
                    select(Filing)
                    .outerjoin(Financial, Filing.doc_id == Financial.doc_id)
                    .where(
                        Filing.doc_type_code.in_(YUBO_DOC_TYPES),
                        Filing.has_csv.is_(True),
                        Financial.id.is_(None),
                    )
                    .limit(args.limit)
                ).all()
                if not pending:
                    print(f"round {round_no}: 未処理なし", flush=True)
                    break
                ok = 0
                for filing in pending:
                    try:
                        if upsert_financial_from_doc(db, client, filing):
                            ok += 1
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        print("error", filing.doc_id, exc, flush=True)
                total["processed"] += len(pending)
                total["financials"] += ok
                print(
                    f"round {round_no}: {{'processed': {len(pending)}, 'financials': {ok}}}",
                    flush=True,
                )
            print(total, flush=True)

        elif args.command == "sync-prices":
            stats = sync_stock_prices(
                db,
                limit=args.limit,
                sleep_seconds=args.sleep,
                only_missing=args.only_missing,
                workers=args.workers,
                fast=args.fast,
            )
            print(stats, flush=True)

        elif args.command == "backfill-quotes":
            stats = backfill_quote_valuations(db)
            print(stats, flush=True)

        elif args.command == "reparse-financials":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            financials = db.scalars(
                select(Financial)
                .where(Financial.parse_version < CURRENT_PARSE_VERSION)
                .limit(args.limit)
            ).all()
            ok = 0
            for financial in financials:
                filing = db.get(Filing, financial.doc_id)
                if not filing:
                    continue
                try:
                    if upsert_financial_from_doc(db, client, filing):
                        ok += 1
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    print("error", financial.doc_id, exc, flush=True)
            print({"processed": len(financials), "updated": ok}, flush=True)

        elif args.command == "complete-data":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            _print_gap_status(db, "開始")
            summary = {
                "backfill_processed": 0,
                "backfill_financials": 0,
                "reparse_processed": 0,
                "reparse_updated": 0,
                "prices_updated": 0,
                "cycles": 0,
            }

            for cycle in range(1, args.max_cycles + 1):
                summary["cycles"] = cycle
                pending = _count_pending_backfill(db)
                reparse_needed = _count_reparse_needed(db)
                missing_quotes = _count_missing_quotes(db)

                if pending == 0 and reparse_needed == 0 and missing_quotes == 0:
                    print(f"=== 完了 (cycle {cycle}) ===", flush=True)
                    break

                print(f"\n=== cycle {cycle} ===", flush=True)
                _print_gap_status(db, f"cycle {cycle} 開始")

                if pending > 0:
                    print("--- 財務バックフィル ---", flush=True)
                    batch = _run_backfill_rounds(
                        db,
                        client,
                        rounds=args.batch_rounds,
                        limit=args.batch_limit,
                    )
                    summary["backfill_processed"] += batch["processed"]
                    summary["backfill_financials"] += batch["financials"]

                reparse_needed = _count_reparse_needed(db)
                if reparse_needed > 0:
                    print("--- 拡張項目 reparse ---", flush=True)
                    while reparse_needed > 0:
                        batch = _run_reparse_batch(
                            db, client, limit=args.reparse_limit
                        )
                        summary["reparse_processed"] += batch["processed"]
                        summary["reparse_updated"] += batch["updated"]
                        print(f"  reparse batch: {batch}", flush=True)
                        if batch["processed"] == 0:
                            break
                        reparse_needed = _count_reparse_needed(db)
                        if batch["processed"] < args.reparse_limit:
                            break

                missing_quotes = _count_missing_quotes(db)
                if missing_quotes > 0:
                    print("--- 株価同期 ---", flush=True)
                    price_stats = sync_stock_prices(
                        db,
                        limit=args.price_limit,
                        only_missing=True,
                        workers=args.price_workers,
                        fast=True,
                    )
                    summary["prices_updated"] += price_stats["updated"]
                    print(f"  prices: {price_stats}", flush=True)

                _print_gap_status(db, f"cycle {cycle} 終了")

            _print_gap_status(db, "最終")
            print(summary, flush=True)

        elif args.command == "daily-sync":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            today = date.today()
            print("=== 当日書類一覧 ===", flush=True)
            print(
                sync_filings_for_range(
                    db,
                    client,
                    start=today,
                    end=today,
                    fetch_financials=False,
                ),
                flush=True,
            )
            print("=== 新規財務バックフィル ===", flush=True)
            pending = db.scalars(
                select(Filing)
                .outerjoin(Financial, Filing.doc_id == Financial.doc_id)
                .where(
                    Filing.doc_type_code.in_(YUBO_DOC_TYPES),
                    Filing.has_csv.is_(True),
                    Financial.id.is_(None),
                )
                .limit(args.backfill_limit)
            ).all()
            ok = 0
            for filing in pending:
                try:
                    if upsert_financial_from_doc(db, client, filing):
                        ok += 1
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    print("  error", filing.doc_id, exc, flush=True)
            print({"processed": len(pending), "financials": ok}, flush=True)

            print("=== 年次reparse ===", flush=True)
            reparse_rows = db.scalars(
                select(Financial)
                .where(Financial.parse_version < CURRENT_PARSE_VERSION)
                .limit(args.reparse_limit)
            ).all()
            reparse_ok = 0
            for financial in reparse_rows:
                filing = db.get(Filing, financial.doc_id)
                if not filing:
                    continue
                try:
                    if upsert_financial_from_doc(db, client, filing):
                        reparse_ok += 1
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    print("  reparse error", financial.doc_id, exc, flush=True)
            print({"processed": len(reparse_rows), "updated": reparse_ok}, flush=True)

            print("=== 株価更新 ===", flush=True)
            price_stats = sync_stock_prices(
                db,
                limit=args.price_limit,
                workers=args.price_workers,
                fast=True,
            )
            print(price_stats, flush=True)

            print("=== 時価総額・PER/PBR 再計算 ===", flush=True)
            print(backfill_quote_valuations(db), flush=True)

            print("=== 四半期業績 ===", flush=True)
            quarterly_stats = sync_quarterly_financials(
                db,
                client,
                limit=args.quarterly_limit,
                only_missing=True,
                listing="上場",
                workers=args.quarterly_workers,
                reparse_stale=False,
            )
            print(quarterly_stats, flush=True)

            print("=== 不動産明細 ===", flush=True)
            re_stats = sync_real_estate(
                db,
                client,
                limit=args.real_estate_limit,
                only_missing=True,
                listing="上場",
                workers=args.real_estate_workers,
            )
            print(re_stats, flush=True)
            _print_gap_status(db, "daily-sync 完了")

        elif args.command == "sync-real-estate":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            stats = sync_real_estate(
                db,
                client,
                limit=args.limit,
                only_missing=args.only_missing,
                listing=args.listing or None,
                workers=args.workers,
            )
            pending = (
                db.scalar(
                    select(func.count())
                    .select_from(Filing)
                    .outerjoin(RealEstateSync, Filing.doc_id == RealEstateSync.doc_id)
                    .join(Company, Filing.edinet_code == Company.edinet_code)
                    .where(
                        Filing.doc_type_code.in_(YUBO_DOC_TYPES),
                        Filing.has_xbrl.is_(True),
                        RealEstateSync.doc_id.is_(None),
                        Company.listing_status == (args.listing or Company.listing_status),
                    )
                )
                if args.listing
                else db.scalar(
                    select(func.count())
                    .select_from(Filing)
                    .outerjoin(RealEstateSync, Filing.doc_id == RealEstateSync.doc_id)
                    .where(
                        Filing.doc_type_code.in_(YUHO_DOC_TYPES),
                        Filing.has_xbrl.is_(True),
                        RealEstateSync.doc_id.is_(None),
                    )
                )
            ) or 0
            total_props = db.scalar(select(func.count()).select_from(RealEstateProperty)) or 0
            print({**stats, "pending_filings": pending, "total_properties": total_props}, flush=True)

        elif args.command == "sync-quarterly":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            stats = sync_quarterly_financials(
                db,
                client,
                limit=args.limit,
                only_missing=args.only_missing and not args.reparse_stale,
                listing=args.listing or None,
                workers=args.workers,
                reparse_stale=args.reparse_stale,
            )
            pending = db.scalar(
                select(func.count())
                .select_from(Filing)
                .outerjoin(QuarterlyFinancial, Filing.doc_id == QuarterlyFinancial.doc_id)
                .join(Company, Filing.edinet_code == Company.edinet_code)
                .where(
                    Filing.doc_type_code.in_(("140", "150")),
                    Filing.has_csv.is_(True),
                    QuarterlyFinancial.id.is_(None),
                    Company.listing_status == (args.listing or Company.listing_status),
                )
            ) if args.listing else db.scalar(
                select(func.count())
                .select_from(Filing)
                .outerjoin(QuarterlyFinancial, Filing.doc_id == QuarterlyFinancial.doc_id)
                .where(
                    Filing.doc_type_code.in_(("140", "150")),
                    Filing.has_csv.is_(True),
                    QuarterlyFinancial.id.is_(None),
                )
            ) or 0
            total_q = db.scalar(select(func.count()).select_from(QuarterlyFinancial)) or 0
            qoq_all = 0
            if pending == 0 and total_q > 0:
                qoq_all = recompute_all_qoq(db)
            print({**stats, "pending_quarterly": pending, "total_quarterly_rows": total_q, "qoq_recomputed": qoq_all}, flush=True)

        elif args.command == "collect-quarterly":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
            meta_total = {"windows": 0, "dates": 0, "filings": 0, "errors": 0}
            print(f"=== 四半期書類メタ同期 {years} ===", flush=True)
            for start, end, label in iter_quarterly_windows(years):
                print(f"  {label} {start}..{end}", flush=True)
                stats = sync_filings_for_range(
                    db,
                    client,
                    start=start,
                    end=end,
                    fetch_financials=False,
                    yuho_only=False,
                )
                meta_total["windows"] += 1
                for key in ("dates", "filings", "errors"):
                    meta_total[key] += stats[key]
            print(meta_total, flush=True)

            parse_total = {"rounds": 0, "processed": 0, "parsed": 0, "errors": 0}
            print("=== 四半期 YoY/QoQ パース ===", flush=True)
            for round_no in range(1, args.max_parse_rounds + 1):
                pending = db.scalar(
                    select(func.count())
                    .select_from(Filing)
                    .outerjoin(QuarterlyFinancial, Filing.doc_id == QuarterlyFinancial.doc_id)
                    .join(Company, Filing.edinet_code == Company.edinet_code)
                    .where(
                        Filing.doc_type_code.in_(("140", "150")),
                        Filing.has_csv.is_(True),
                        QuarterlyFinancial.id.is_(None),
                        Company.listing_status == "上場",
                    )
                ) or 0
                if pending == 0:
                    print(f"  round {round_no}: 未処理なし", flush=True)
                    break
                parse_client = EdinetClient(api_key=api_key, sleep_seconds=args.parse_sleep)
                batch = sync_quarterly_financials(
                    db,
                    parse_client,
                    limit=args.parse_limit,
                    only_missing=True,
                    listing="上場",
                    workers=args.parse_workers,
                )
                parse_total["rounds"] = round_no
                parse_total["processed"] += batch["processed"]
                parse_total["parsed"] += batch["parsed"]
                parse_total["errors"] += batch["errors"]
                print(f"  round {round_no}: {batch} pending={pending}", flush=True)
                if batch["processed"] == 0:
                    break

            qoq_all = recompute_all_qoq(db)
            total_q = db.scalar(select(func.count()).select_from(QuarterlyFinancial)) or 0
            companies = db.scalar(
                select(func.count(func.distinct(QuarterlyFinancial.edinet_code)))
                .select_from(QuarterlyFinancial)
                .join(Company, QuarterlyFinancial.edinet_code == Company.edinet_code)
                .where(Company.listing_status == "上場")
            ) or 0
            print(
                {
                    "meta": meta_total,
                    "parse": parse_total,
                    "total_quarterly_rows": total_q,
                    "listed_companies_with_quarterly": companies,
                    "qoq_recomputed": qoq_all,
                },
                flush=True,
            )

        elif args.command == "seed-no-xbrl-profiles":
            print(seed_no_xbrl_profiles(db, args.listing or "上場"), flush=True)

        elif args.command == "sync-profiles-complete":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            for round_no in range(1, args.max_rounds + 1):
                pending = count_pending_profiles(db, args.listing or "上場")
                if pending == 0:
                    print(f"round {round_no}: pending=0, done", flush=True)
                    break
                stats = sync_profiles(
                    db,
                    client,
                    limit=args.batch_size,
                    only_missing=True,
                    listing=args.listing or None,
                    workers=args.workers,
                )
                pending = count_pending_profiles(db, args.listing or "上場")
                cached = db.scalar(
                    select(func.count())
                    .select_from(CompanyProfile)
                    .where(CompanyProfile.parse_status == "ok")
                ) or 0
                print(
                    f"round {round_no}: {stats} pending={pending} cached={cached}",
                    flush=True,
                )
            seed_stats = seed_no_xbrl_profiles(db, args.listing or "上場")
            print(seed_stats, flush=True)
            checkpoint_after_write(db)

        elif args.command == "sync-profiles":
            client = EdinetClient(api_key=api_key, sleep_seconds=args.sleep)
            stats = sync_profiles(
                db,
                client,
                limit=args.limit,
                only_missing=args.only_missing,
                listing=args.listing or None,
                workers=args.workers,
                priority_revenue=args.priority_revenue,
            )
            pending = count_pending_profiles(db, args.listing or "上場")
            cached = db.scalar(
                select(func.count())
                .select_from(CompanyProfile)
                .where(CompanyProfile.parse_status == "ok")
            ) or 0
            print({**stats, "pending_profiles": pending, "cached_profiles": cached}, flush=True)
            checkpoint_after_write(db)

        elif args.command == "collect-external-media":
            summary = collect_external_media_batch(
                db,
                limit=args.limit,
                sleep_news=args.sleep_news,
                sleep_trends=args.sleep_trends,
                trend_days=args.trend_days,
                trends_enabled=not args.no_trends,
            )
            print(summary, flush=True)

        elif args.command == "purge-irrelevant-news":
            print(
                purge_all_irrelevant_news(db, listing=args.listing or None, limit=args.limit),
                flush=True,
            )

    finally:
        db.close()
        if args.command not in ("init-db", "plan", "cleanup-local-data"):
            try:
                db_log = SessionLocal()
                record_sync_snapshot(args.command, build_data_quality_stats(db_log))
                db_log.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
