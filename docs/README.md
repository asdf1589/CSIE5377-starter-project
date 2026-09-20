# CSIE5377 Starter Project 文件

CSIE5377 Starter Project 是一支單機、單 process 的非同步網頁爬蟲，本目錄收錄
它的系統設計與一次 48 小時無人值守運行的實測結果。文件分為兩份編號正文與一份
同內容的簡報形式，編號對應檔名前綴，章節編號（如 §1.4、§2.3.1）在三份文件之
間可互相引用。

  - 設計決策與模組職責：並發模型、frontier 去重、禮貌性限速、robots.txt 合規
  - 種子清單如何產生：樞紐頁面加上 Tranco 排名的分層抽樣
  - 運行生命週期：到點自動停止、checkpoint 與 `--resume`
  - 48 小時視窗內的抓取統計、狀態碼與延遲分布、失敗原因分類
  - 吞吐量隨時間衰減的量測與成因分析

## 文件

1. [01-system-design.md](./01-system-design.md) —— 系統設計
2. [02-48h-run-results.md](./02-48h-run-results.md) —— 48 小時運行結果

同樣內容的 Marp 簡報形式見 [slides.md](./slides.md)。

## 快速開始

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 執行測試時需要

pip install tranco                    # generate_seeds.py 需要
python scripts/generate_seeds.py --count 984   # 產生 seeds.txt（1,000 個網址）
python -m crawler.main --seeds seeds.txt --concurrency 50
```

完整的 CLI 參數與 48 小時運行所用的啟動指令見
[01-system-design.md](./01-system-design.md) §1.1 與
[02-48h-run-results.md](./02-48h-run-results.md) §2.1。

## 引用數值的來源

文中引用的數值集中保存於 `results/`：`results/summary-48h.json` 為 48 小時
視窗彙總，`results/throughput-hourly.csv` 為每小時抓取次數。

[02-48h-run-results.md](./02-48h-run-results.md) 的三張圖存放於 `rsc/`，由
`scripts/make_figures.py` 從上述資料重新產生。

原始輸出位於 `output/`，該目錄被 `.gitignore` 排除、不進入版本控制。
`output/results_clean.jsonl`、`output/metrics_log_clean.csv` 與
`output/summary_clean.json` 由 `scripts/clean_results.py` 從原始檔案切出單一
運行的 48 小時視窗而來，原始檔案本身不被修改。
