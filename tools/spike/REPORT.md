# データ取得スパイク レポート — 「APIで簡単に取れない」高moatデータ

対象: japan-equity-db.jp（EDINET 由来の日本株DB）
目的: サイトの優位性を高めるため、無料APIで簡単に取れない＝堀になるデータの取得可否を検証。
方針: デプロイ・本番DB変更なし。抽出コードと取得可否のみを用意。

## 0. 実行環境の制約（重要）
- この作業環境は組織の egress ポリシーにより **外部ホストへの接続が一律 403**
  （`api.edinet-fsa.go.jp` / `stooq.com` / `news.google.com` 等すべて）。
  `curl` / `httpx` / `WebFetch` はいずれも失敗。`WebSearch` のみ利用可。
- そのため **実 EDINET からのライブ取得は本環境では不可**。
- 代替として、EDINET type=5 CSV の**行フォーマットを模した合成フィクスチャ**で
  パーサの動作を実証済み（`selftest_fixture.py` → 全アサーション PASS）。
- 実データ取得は `run_extract.py` を **ネットワーク開放環境（ローカル/prod daily-sync）** で実行する。

## 1. 生成物
| ファイル | 役割 |
|---|---|
| `edinet_extractors.py` | type=5 CSV 行から各データを抽出する純関数群（prod未組込） |
| `selftest_fixture.py` | 合成CSVでパーサ動作を実証（外部接続不要・PASS確認済み） |
| `run_extract.py` | 実EDINETからDLして抽出するCLI（要ネットワーク＋APIキー） |

抽出は既存 `app/edinet/client.py` と同一流儀：UTF-16 TSV、
行=`[要素ID, 項目名, コンテキストID, 相対年度, 連結・個別, 期間・時点, ユニットID, 単位, 値]`、
要素IDのサフィックス一致（`_element_matches`）＋項目名フォールバックで頑健化。

## 2. データ別 取得可否

### ① 従業員の質（平均年間給与・平均年齢・平均勤続年数・従業員数）★取得可
- 出典: 有報「従業員の状況」／type=5 CSV
- 要素ID:
  - `jpcrp_cor:NumberOfEmployees`
  - `jpcrp_cor:AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees`
  - `jpcrp_cor:AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees`
  - `jpcrp_cor:AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees`
- 難易度: ★☆☆（単一値。項目名フォールバックも効く）
- 堀: 断片的な年収サイトは存在するが、**全上場約4,000社を財務と一体で横断**は希少。SEO集客力大。
- 注意: 平均年間給与は 2019/4 以降開示。提出会社（単体）の数値を採るため `連結・個別=提出会社` で絞る。

### ② 人的資本（女性管理職比率・男性育休取得率・男女賃金格差）★取得可
- 出典: 有報「従業員の状況」／2023年3月期以降で開示義務
- 要素ID（会社により表記揺れ、候補＋項目名で吸収）:
  - 女性管理職: `RatioOfFemaleManagersToTotalNumberOfManagers` 等（項目名「管理職に占める女性労働者の割合」）
  - 男性育休: `RatioOfMaleEmployeesWhoTookChildcareLeave` 等（項目名「男性労働者の育児休業取得率」）
  - 賃金格差: `DifferenceInWagesBetweenMenAndWomen...`（項目名「労働者の男女の賃金の差異」）
- 難易度: ★★☆（unit=pure は 0.024 形式 → ×100 で%化。全労働者/正規/非正規の次元があり要選別）
- 堀: 新設義務項目でまとめている無料サイトが少なく、トレンド性も高い。

### ③ 大株主の状況（上位株主・保有株数・保有比率）★取得可（次元解析）
- 出典: 有報「大株主の状況」／type=5 CSV
- 構造: コンテキストIDに次元メンバー `No{N}MajorShareholdersMember` が入り、
  同一メンバーで氏名・株数・比率が対応づく（`NameMajorShareholders` /
  `NumberOfSharesHeld` / `ShareholdingRatio`）。
- 難易度: ★★☆（値ではなく次元でのグルーピングが必要＝だから他がやらない）
- 堀: **大量保有報告書の時系列集約**まで進めれば「誰がいつ売買したか」が見え、
  アクティビスト検知・需給分析ができる。無料で提供する競合はほぼ皆無＝旗艦候補。

### ④ セグメント別業績（事業別・地域別 売上/利益）△取得可（要精緻化）
- 出典: 有報セグメント情報／type=5 CSV
- 構造: コンテキストの `...ReportableSegmentsMember` で束ねる。会社ごとにメンバー名が
  自由記述に近く、売上/利益の要素IDも複数（`NetSalesOfEachReportableSegment`,
  `OperatingIncomeLoss` 等）。
- 難易度: ★★★（メンバー名の正規化・調整額の除外が要る。試作は素朴実装）
- 堀: 無料APIは連結単体値のみ。事業ポートフォリオ分析はここにしかない。

### ⑤ 設備投資・研究開発費 ★取得可
- 要素ID: `TotalAmountOfCapitalInvestments...` / `ResearchAndDevelopmentExpenses`
- 難易度: ★☆☆。堀: R&D比率・投資積極度ランキング等に展開可。

### ⑥（トレンド系）有報テキストのキーワード言及トレンド ○プロトタイプ可
- 既存 `CompanyProfile.business_description` 等のテキストへキーワード辞書
  （生成AI/半導体/インバウンド/GX 等）でマッチ → 言及企業リスト＋言及企業数の経年推移。
- 難易度: ★★★（テキスト構造化）だが**最も新規性が高い**。既存 `themes.py`（現状は財務スクリーン）を発展。
- 注: 本スパイクでは抽出コード化まで未実施。次段の実装候補。

## 3. 結論と推奨
- ①②③⑤ は **既存の type=5 CSV 取得基盤に要素ID/次元マッチを足すだけ**で取得可能。
  外部依存ゼロ・ToSリスクなし・メンテ安定。
- **最速で価値**: ①従業員の質（特に平均年収）＝SEO集客の起爆剤。
- **最も深い堀**: ③大株主／大量保有の時系列（競合不在）。
- 実装時は本モジュールの抽出関数を `app/edinet/` へ移し、DBモデル（例:
  `EmployeeStats`, `MajorShareholder`, `BusinessSegment`）を追加、daily-sync に組込む。

## 4. 実データでの動作確認手順（ネットワーク開放環境）
```bash
cd backend && source .venv/bin/activate   # EDINET_API_KEY 設定済み・DB有り
python ../tools/spike/run_extract.py --sec-code 7203   # トヨタの最新有報で抽出
python ../tools/spike/run_extract.py --doc-id S100XXXX # 書類ID直接指定
```
本環境（egress 遮断）では合成フィクスチャで代替検証済み:
```bash
cd tools/spike && python3 selftest_fixture.py   # → ALL ASSERTIONS PASSED
```
