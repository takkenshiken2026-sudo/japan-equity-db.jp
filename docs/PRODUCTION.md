# 本番環境セットアップ

本番は **Render 上の Docker モノリス**（FastAPI + SQLite + SPA + SEO）で動かします。  
GitHub Pages は API がなく機能しないため、**`japan-equity-db.jp` は Render に向けます**。

## アーキテクチャ

```
japan-equity-db.jp (DNS)
        │
        ▼
Render Web Service (Docker)
├── /              → mock/index.html（SPA）
├── /api/*         → FastAPI JSON
├── /companies/*   → SEO SSR
├── /assets/*      → charts.js 等
└── /app/data/edinet.db（永続ディスク 2GB）
        │
        └── cron 07:00 JST → daily-sync
```

## 初回セットアップ（チェックリスト）

### 1. Render で Blueprint を適用

1. [Render Blueprint](https://dashboard.render.com/select-repo?type=blueprint) を開く
2. リポジトリ `takkenshiken2026-sudo/japan-equity-db.jp` を選択
3. `render.yaml` を適用して Web サービス `japan-equity-db` を作成

### 2. Render 環境変数

| 変数 | 必須 | 説明 |
|------|------|------|
| `EDINET_API_KEY` | ✅ | EDINET API キー（日次同期に必要） |
| `SITE_URL` | ✅ | `https://japan-equity-db.jp`（Blueprint 既定） |
| `GOOGLE_SITE_VERIFICATION` | 任意 | Search Console HTML タグ |
| `DB_BACKUP_URL` | 初回 | DB シード用 `.gz` の URL（下記参照） |

### 3. DB の初回投入

ローカル DB を GitHub Release にアップロード:

```bash
chmod +x scripts/publish-db-backup.sh
./scripts/publish-db-backup.sh
```

表示された URL を Render の `DB_BACKUP_URL` に設定し、再デプロイ。  
空の DB ファイルの場合、起動時に自動ダウンロードしてシードします。

> **注意:** Release はリポジトリの公開設定に依存します。公開リポジトリの場合、DB バックアップも公開されます。

### 4. GitHub Secrets

| Secret | 用途 |
|--------|------|
| `RENDER_DEPLOY_HOOK` | `main` push 時に Render デプロイをトリガー（Render → Settings → Deploy Hook） |
| `GOOGLE_SITE_VERIFICATION` | Search Console（任意） |
| `EDINET_API_KEY` | CI から使う場合のみ（通常は Render 側で設定） |

### 5. DNS を Render に切り替え

**GitHub Pages から Render へ移行:**

1. GitHub → リポジトリ **Settings → Pages** で Custom domain `japan-equity-db.jp` を **削除**
2. Render → サービス **Settings → Custom Domains** で `japan-equity-db.jp` / `www.japan-equity-db.jp` を追加
3. DNS（お名前.com 等）を Render の指示に従って更新  
   - 通常: `CNAME japan-equity-db.jp → <render-hostname>.onrender.com`  
   - または Render が提示する A レコード

### 6. 動作確認

```bash
curl -s https://japan-equity-db.jp/api/health
# {"status":"ok","db_ready":true,"companies":..., "listed":...}

curl -s https://japan-equity-db.jp/api/stats
```

ブラウザで https://japan-equity-db.jp を開き、ダッシュボードに銘柄が表示されることを確認。

## デプロイフロー（日常）

```
コード変更 → git push main
    ├── GitHub Actions: Deploy Production (Render) → Deploy Hook
    └── Render Git 連携: 自動ビルド・デプロイ
```

GitHub Pages の自動デプロイは **無効化** 済み（手動プレビューのみ）。

## 日次同期

本番コンテナ内 cron が **毎日 07:00 JST**（22:00 UTC）に `production-daily-sync.sh` を実行:

- 書類・財務・株価・四半期・不動産
- 企業プロフィール・外部メディア
- 日曜: 四半期メタ追加収集

ログ: Render ディスク上 `/app/data/collection-logs/daily-sync.log`  
（`COLLECTION_LOG_DIR` で変更可）

手動実行（Render Shell）:

```bash
/app/scripts/production-daily-sync.sh
```

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| ダッシュボードが空 | `/api/health` で `db_ready: false` → `DB_BACKUP_URL` 設定後に再デプロイ |
| API 404 | DNS が GitHub Pages のまま → Render に切り替え |
| データが古い | `EDINET_API_KEY` 確認、cron ログ確認 |
| charts が表示されない | モノリスでは `/assets/charts.js` を FastAPI が配信（問題なし） |

## Fly.io（代替）

支払い情報登録済みの場合、`fly.toml` + `.github/workflows/fly-deploy.yml`（手動）でもデプロイ可能。  
DNS は Render か Fly の **どちらか一方** に向けてください。
