# 完全無料の本番運用（GitHub Pages 静的サイト）

**月額 ¥0** · Mac 常時起動不要 · Render 不要

## 仕組み

```
GitHub Actions（毎朝 7:00 JST + main push）
  ├─ Release から DB 復元
  ├─ daily-sync（EDINET / 株価 / RSS / Trends → SQLite）
  ├─ DB → JSON 書き出し（tools/export_static_data.py）
  └─ public_site/ → GitHub Pages デプロイ

ユーザー
  └─ 静的 JSON を読む（/data/companies/E12345.json 等）
```

| データ | 取得 | 表示 |
|--------|------|------|
| 財務・スクリーニング | EDINET | ビルド時 JSON 化 |
| ニュース | Google News **RSS** | ビルド時取得済み → JSON |
| 検索トレンド | Google Trends | ビルド時取得済み → JSON |
| 更新頻度 | **1日1回** | ビルド時点のスナップショット |

## セットアップ

### 1. GitHub Secret

| Secret | 必須 |
|--------|------|
| `EDINET_API_KEY` | ✅（日次 sync 用） |
| `GOOGLE_SITE_VERIFICATION` | 任意 |

### 2. DNS

`japan-equity-db.jp` → **GitHub Pages**（Render は不要）

GitHub → Settings → Pages → Custom domain

### 3. 初回ビルド

Actions → **Static Site** → Run workflow  
（`skip_sync: false` で DB 同期 + 全 JSON 生成）

### 4. 確認

```bash
curl -s https://japan-equity-db.jp/data/manifest.json
# {"built_at":"...","screening_count":...,"company_bundles":...}
```

## ファイル構成（生成物）

```
public_site/
  index.html          ← STATIC_MODE=true
  assets/static-api.js ← /api/* を JSON にルーティング
  data/
    manifest.json
    screening/index.json
    search/catalog.json
    trending/home.json
    themes/weekly.json
    calendar/*.json
    companies/E12345.json  ← 銘柄詳細バンドル
```

## 制限

| 項目 | 内容 |
|------|------|
| データ更新 | 1日1回 |
| 外部メディア | 1日120社ずつ巡回（全社揃うまで数日） |
| スクリーニング | クライアント側フィルタ（概ね API 同等） |
| ビルド時間 | 全社 JSON 化で **30〜90分**（Actions 上） |

## 日常運用

| 操作 | 方法 |
|------|------|
| UI 変更 | `main` に push → 自動ビルド・デプロイ |
| データ更新 | 毎朝自動（schedule） |
| 手動更新 | Actions → Static Site → Run workflow |

## ローカル開発

```bash
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
# STATIC_MODE=false → 従来どおり /api/* を使用
```

静的ビルドのローカル確認:

```bash
pip install -r backend/requirements.txt
python3 tools/build_public_site.py
python3 -m http.server -d public_site 8080
```

## 有料 Render について

`render.yaml` は残していますが、本番は **GitHub Pages 静的構成** を推奨します。
