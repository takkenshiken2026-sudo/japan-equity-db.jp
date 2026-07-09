# 株チェック

EDINET API から取得できる開示情報を集約し、**企業検索・財務分析・スクリーニング**を行う Web アプリです。

## 取得できる情報（EDINET API）

| カテゴリ | 内容 | API |
|---------|------|-----|
| 企業マスタ | EDINETコード、証券コード、業種、上場区分、所在地 | コードリスト |
| 有価証券報告書 | 売上高、営業利益、純利益、総資産、ROE、営業CF、BPS、配当 等 | 書類取得 type=5 (CSV) |
| 四半期・半期報告書 | 四半期業績 | 書類一覧 + CSV |
| 株価・バリュエーション | 株価、時価総額、PER、PBR（Yahoo + EDINET EPS/BPS） | sync-prices |
| 臨時報告書 | 重要イベント | 書類一覧 |
| 大量保有報告書 | 株主動向 | 書類一覧 |
| PDF / XBRL | 原本ダウンロード | 書類取得 type=1,2 |

## 構成

- **Backend**: Python FastAPI + SQLite
- **UI**: `mock/index.html`（SPA、FastAPI `/` で配信）
- **SEO**: Jinja2 SSR（`/companies/`, `/industries/`）

## 毎日同期（自動化）

毎朝 7:00（launchd）または手動で以下が実行されます。

| 処理 | 内容 |
|------|------|
| 当日書類 | EDINET 新規開示の取得 |
| 財務バックフィル | 未解析有報 CSV（最大 100 件） |
| 株価更新 | 上場企業の株価・PER/PBR |
| 時価総額再計算 | 株価 × 発行株式数 |
| 四半期業績 | 未解析四半期 CSV（最大 200 件） |
| 不動産明細 | 未解析有報 XBRL（最大 50 件） |
| 企業プロフィール | SEO 用 XBRL（最大 50 件） |

```bash
# 手動実行
./scripts/daily-sync.sh
# または
cd backend && source .venv/bin/activate && python -m app.sync_cli daily-sync
```

**macOS 自動実行（毎朝7:00）:**

```bash
cp scripts/com.edinet-analyzer.daily-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.edinet-analyzer.daily-sync.plist
```

ログ: `data/daily-sync.log`

停止: `launchctl unload ~/Library/LaunchAgents/com.edinet-analyzer.daily-sync.plist`

## SEO（検索エンジン最適化）

クローラー向けに SSR ページ・サイトマップ・構造化データを提供しています。

| URL | 内容 |
|-----|------|
| `/companies/{EDINETコード}` | 企業ランディング（財務・事業概要・JSON-LD） |
| `/industries` | 業種一覧 |
| `/industries/{業種}` | 業種別企業一覧 |
| `/sitemap.xml` | サイトマップ（約3,800 URL） |
| `/robots.txt` | クロール制御 |

### 本番公開時の設定

`.env` に以下を設定してください。

```env
SITE_URL=https://your-domain.com
GOOGLE_SITE_VERIFICATION=（Search Consoleの確認コード）
```

Search Console で `https://your-domain.com/sitemap.xml` を登録します。

### プロフィール同期（事業内容・SEO用）

```bash
cd backend && source .venv/bin/activate
# 未同期を一括取得（pending=0まで）
python -m app.sync_cli sync-profiles-complete --batch-size 50 --workers 6
# XBRLのない企業を no_xbrl としてマーク
python -m app.sync_cli seed-no-xbrl-profiles
sqlite3 data/edinet.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

### SEO動作確認

```bash
./scripts/seo-verify.sh http://127.0.0.1:8000
```

## デプロイ（本番）

**本番は Render モノリス**（UI + API + SEO + SQLite）です。詳細は [docs/PRODUCTION.md](docs/PRODUCTION.md) を参照。

```bash
# 初回: DB バックアップを Release にアップロード → Render の DB_BACKUP_URL に設定
./scripts/publish-db-backup.sh

# 日常: main に push すると Render へ自動デプロイ
git push origin main
```

- 本番 URL: https://japan-equity-db.jp
- GitHub Pages は API 非対応のため **本番では使用しません**（プレビューのみ手動可）

### ローカル Docker（開発用）

```bash
docker compose up -d --build
```

- URL: http://localhost:8000/
- DB: `backend/data/edinet.db` をボリュームマウント
- 停止: `docker compose down`
- 本番 cron は無効（`ENABLE_DAILY_CRON=false`）

株価・財務の更新（ホスト側で実行）:

```bash
cd backend && source .venv/bin/activate
python -m app.sync_cli complete-data
python -m app.sync_cli sync-today --no-financials
```

## セットアップ

### 1. 環境変数

```bash
cp .env.example .env
# .env に EDINET_API_KEY を設定
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.sync_cli init-db
python -m app.sync_cli sync-companies
python -m app.sync_cli sync-filings --from 2025-06-01 --to 2025-06-30
python -m app.sync_cli backfill-financials --limit 200
python -m app.sync_cli reparse-financials --limit 500   # 拡張財務項目を既存データに反映
python -m app.sync_cli sync-prices --limit 500            # 株価・PER/PBR
python -m app.sync_cli complete-data                      # 上記3つを完了まで一括実行
uvicorn app.main:app --reload --port 8000
```

ブラウザで http://127.0.0.1:8000 を開きます。

## 画面

- `/` ダッシュボード・スクリーニング・企業検索・企業詳細（SPA、`#/company/{code}`）
- `/companies/{EDINETコード}` 企業ランディング（SSR、SEO用）
- `/industries` 業種一覧（SSR）

## 今後の拡張（株取引向け）

EDINET だけでは株価が取れないため、次を追加すると分析が実用的になります。

1. **株価 API 連携**（J-Quants / Yahoo Finance / Stooq）→ PER, PBR, 時価総額 ✅ 基本実装済み
2. **臨時報告書アラート** → 適時開示の通知
3. **セクター比較チャート** → 業種平均との比較
4. **バックテスト** → 財務スクリーニング + 株価リターン

## 注意

- EDINET API は 1 日 1 リクエストで書類一覧を取得する設計です。全期間の初回同期には時間がかかります。
- 財務 CSV のパースは提出様式によって差異があるため、必要に応じて項目マッピングを拡張してください。
