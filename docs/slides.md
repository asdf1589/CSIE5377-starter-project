---
marp: true
paginate: true
header: 'edu_crawler — 48 小時無人值守爬取'
---

# edu_crawler

## 單機非同步爬蟲，無人值守連續運行 48 小時

NTU CSIE5376 · 索引見 `docs/README.md` · 完整文件為 `docs/01-system-design.md` 與 `docs/02-48h-run-results.md`

---

## 系統是一支會自己收斂的單機爬蟲

- 單一 Python process、單一 asyncio event loop，沒有 IPC、沒有外部佇列或資料庫
- 全域並發上限，加上兩層彼此獨立的每網域禮貌性限速
- robots.txt 合規：依 origin 快取，抓取失敗時 fail-open
- 失敗請求以指數退避加隨機抖動重試
- 週期性 checkpoint，當機後可從存檔續跑
- 選擇性的連結追蹤，受防爬蟲陷阱上限約束

<!--
範圍只到 HTTP 回應本身與頁面上的 <a href> 連結；內容解析、排名與索引都不在範圍內。
--max-runtime-hours 與 --max-pages-per-domain 兩個上限，讓一次性、不可重跑的長時間
運行能在無人看管的情況下自行收斂。
-->

---

## 九個模組各自只負責一件事

| 模組 | 職責 |
|---|---|
| `frontier.py` | 有界待爬佇列、網址正規化去重、每網域頁數統計、referrer 追蹤、checkpoint 快照 |
| `ratelimiter.py` | 每網域併發數上限與每網域最小請求間隔 |
| `robots.py` | robots.txt 快取（依 origin 快取，fail-open） |
| `fetcher.py` | 實際發出 HTTP 請求，指數退避加隨機抖動的重試 |
| `linkextract.py` | 以標準庫 `html.parser` 抽取頁面上的 `<a href>` |
| `checkpoint.py` | 週期性、原子化地把爬蟲狀態寫入磁碟 |
| `storage.py` | append-only JSON Lines 結果紀錄 |
| `worker.py` | 串接上述元件：抓取 → 記錄 → （選擇性）連結追蹤 |
| `main.py` | 進入點：啟動與關閉流程、訊號處理、CLI 參數 |

---

## I/O bound 的工作不該用執行緒或程序去換並發

- 絕大部分時間花在等待網路回應，CPU 幾乎不吃負載；同時維持數千條連線幾乎沒有成本，數千條執行緒則不然
- 單一 event loop 代表共享的可變狀態不需要任何鎖，也沒有 context switch 與 IPC 序列化開銷
- 全域上限只在一個地方施加——`aiohttp.TCPConnector(limit=...)`，對應 `--concurrency 50`

<!--
worker 數量與這個上限綁在同一個旋鈕上：main.py 啟動 --concurrency 個 worker
coroutine，因此不會出現 worker 數多於連線上限、在 connector 層互相排隊的情形。
-->

---

## Frontier 是一個有界佇列，不是無限佇列

- `asyncio.Queue(maxsize=--queue-maxsize)`，預設 2000；48 小時正式運行提高到 50,000
- 刻意有界：佇列塞滿本身就是反壓機制，記憶體不會無限膨脹
- 入列前先 `normalize_url()`，再以正規化後的網址檢查 `_seen` 去重
- 每網域頁數統計與 referrer 對應表存在同一個物件裡，連同佇列內容與 `_seen` 構成 checkpoint 落地的全部狀態

<!--
去重發生在入列時而非抓取時，因此「在 _seen 裡」等價於「至多被入列過一次」，
這個不變式讓 seen_count 成為單調遞增的進度指標。
-->

---

## 兩種入列語意，是結構決定的，不是風格選擇

| 呼叫者 | 方法 | 佇列滿時 |
|---|---|---|
| 種子清單載入（`add_many()`） | `add()` | `await queue.put()` 等待空位 |
| 連結追蹤（`worker._follow_links()`） | `try_add()` | 立刻放棄該連結並計數 |

**連結追蹤的呼叫端本身就是那個數量有限的 worker pool 裡的一員，而佇列唯一的排空途徑是 worker 呼叫 `get()`；在已滿的有界佇列上做阻塞式入列，等於生產者在等一個就是自己的消費者。**

<!--
被放棄的網址不會寫入 _seen，因此佇列之後有空間時仍有機會被重新發現——放棄的語意是
「這次不排程」，而不是「永久排除」。丟棄數量由 crawler_links_dropped_queue_full_total 計數。
-->

---

## 連結追蹤被防爬蟲陷阱上限框住

- `--follow-links` 啟用；不帶這個參數時只處理種子清單，抓完即結束
- 用標準庫 `html.parser` 掃出 `<a href>`，刻意不引入 BeautifulSoup 或 lxml——需要的只是把 href 抽出來，且對格式不良的 HTML 有容錯
- `--max-pages-per-domain` 預設 20：達到上限後，再發現指向該網域的連結就直接略過
- 上限用被發現連結**自己的網域**判斷，而不是來源頁面的網域

<!--
若改用來源頁面的網域判斷，效果會反過來：一個連向大量外部網站的熱門樞紐頁會在達到
自身上限後被整個切斷，擋掉的是合法的跨網域發現。
-->

---

## 禮貌性由兩個彼此獨立的控制構成

| 控制 | 參數 | 預設 |
|---|---|---|
| 每網域併發數上限 | `--per-domain-concurrency` | 2 |
| 對同一網域兩次請求起始的最小間隔 | `--per-domain-delay` | 1.0 秒 |

只有一個全域上限 50 是不夠的：排程順序不巧時，仍可能同時對某個小網站發出 20 個並發請求；這兩個控制則不會。

<!--
第二層的存在理由是：即使併發數是 1，連續的請求仍可能快到讓小型站台吃不消。
這是 Scrapy AutoThrottle 的簡化版本，差別在於它不會依觀測到的伺服器延遲動態調整。
-->

---

## robots.txt、重試與失敗處理各有明確政策

- 每個 origin 的 robots.txt 只抓一次並快取整場運行，首次抓取由該 origin 專屬的 `asyncio.Lock` 保護
- 採 fail-open：robots.txt 不存在、回應非 200、或抓取本身失敗時，預設允許抓取
- 重試等待為 `retry_backoff_base * 2 ** (n - 1)`，再加一段 `random.uniform(0, retry_backoff_base)` 的隨機抖動
- 抖動是為了避免 thundering herd：目標站台短暫異常時，大量請求不會在同一瞬間一起重試
- `--max-retries` 預設 3；取消（cancellation）一律在任何寬泛的 `except` 之前重新拋出

---

## 每一筆紀錄都自帶來源證據

```json
{"url": "https://www.example.edu/", "domain": "www.example.edu",
 "status": 200, "ok": true, "elapsed_ms": 868, "attempts": 1,
 "referrer": "https://www.example.edu/index.html",
 "robots_blocked": false, "ts": "2026-09-17T00:49:32Z"}
```

被 `robots.txt` 擋下的網址同樣留下一筆紀錄（`status` 為 `null`、`elapsed_ms` 與 `attempts` 為 0）——那是整份輸出裡唯一能證明 robots.txt 確實被遵守的證據。

<!--
referrer 只在開啟連結追蹤時寫入，而且是整個 key 不存在，而非值為 null。
固定清單模式下沒有 referrer 這個概念，輸出結構就不該多一個永遠沒人填的欄位。
-->

---

## 到點停止與使用者中斷走同一條收尾路徑

- `--max-runtime-hours 48` 由背景 coroutine 去 `set()` 那個 SIGINT 與 SIGTERM 同樣會設定的 `stop_event`，因此只有一套 teardown
- `--checkpoint-interval 300` 決定落地頻率；內容是 `seen`、`pending`、`domain_page_counts` 加上累計統計
- 寫入先進同目錄暫存檔，再以 `os.replace()` 覆蓋——即使在 write 與 replace 之間被 `kill -9`，也不會留下半截檔案
- `--resume` 直接重建 frontier 與統計計數器，跳過載入種子清單

<!--
pending 除了佇列當前內容，還包含 add() 已經寫入 _seen、但 await queue.put() 尚未完成
的網址；少了這一塊，卡在該空窗期的網址會既不在佇列裡也不在快照裡。
-->

---

## 三層可觀測性對應三種受眾

| 層 | 受眾 | 介面 |
|---|---|---|
| 結構化 log | log pipeline、`grep` 與 `jq` | JSON Lines 輸出到 stdout，一個事件一個物件 |
| 終端機狀態列 | 盯著 tmux pane 的人 | `--status-interval` 秒印一行人類可讀摘要 |
| Prometheus 指標 | 儀表板與腳本輪詢 | `:9090/metrics`，10 個 collector，由 `scripts/watch_metrics.py` 即時取樣 |

<!--
指標明確對應 SRE 四個黃金訊號：延遲 crawler_fetch_latency_seconds、流量
crawler_requests_total、錯誤 crawler_errors_total、飽和度 crawler_inflight_requests
相對於 --concurrency 與 crawler_queue_depth 相對於 --queue-maxsize。
watch_metrics.py 每次取樣追加寫入 output/metrics_log.csv，後面的時間序列就來自這個檔案。
-->

---

## 測試完全不使用 mock

- 全套 55 個測試、分布於 12 個檔案，整個 `tests/` 目錄沒有任何 `unittest.mock`、`monkeypatch` 或 `patch()`
- 會發網路請求的邏輯一律以 `aiohttp.test_utils.TestServer` 啟動真正監聽本機連接埠的伺服器驗證，真的走一次 TCP 與 HTTP
- 續跑測試用 `subprocess.Popen` 啟動真正的 process 再 `.kill()`（等同 SIGKILL），然後斷言兩次抓取的網址完全不重疊、且聯集涵蓋全部網址
- 一個回歸測試讓每個 worker 各自處理連結數超過佇列容量的頁面，斷言整場爬取仍然跑完——鎖住非阻塞入列語意

---

## 48 小時完成 1,264,115 次抓取

2026-09-17 00:49:32 至 2026-09-19 00:49:32，整整 172,800 秒，種子清單 1,000 個網址。

| 項目 | 數值 |
|---|---|
| 抓取次數（fetch attempts） | 1,264,115 |
| 成功（`ok`） | 1,199,676（94.90%） |
| 失敗 | 64,439（5.10%） |
| `robots.txt` 略過 | 23,179 |
| 需要重試的請求 | 18,615（1.47%） |
| 不重複網址 | 1,287,294 |
| 觸及網域 | 165,730 |
| 實際發出請求的網域 | 158,885 |
| 平均吞吐量 | 7.32 次/秒 |

---

## 請求大多成功，延遲全程持平

| 狀態碼 | 佔比 |
|---|---|
| 200 | 94.81% |
| 404 | 1.83% |
| 403 | 1.57% |
| 連線失敗（無狀態碼） | 1.10% |
| 429 | 0.25% |

| 延遲百分位（n = 1,199,676） | p50 | p90 | p99 | 最大 |
|---|---|---|---|---|
| 毫秒 | 868 | 2,495 | 6,019 | 14,381 |

p50 全程持平，正是下一頁用來排除「網路變慢」這個解釋的依據。

---

## 吞吐量在 48 小時視窗內衰減 9.2 倍

| 時點 | 每小時抓取次數 |
|---|---|
| 第 5 小時（高峰） | 67,614 |
| 第 12 小時 | 50,095 |
| 第 24 小時 | 19,968 |
| 第 36 小時 | 8,066 |
| 第 48 小時（視窗結束） | 7,354 |
| 第 60 小時（**視窗之外**，process 持續至 60.2 小時） | 3,553 |

- 高峰 18.8 次/秒 降到視窗結束的 2.0 次/秒，即 48 小時視窗內 **9.2 倍**
- 延伸到 process 結束的 1.0 次/秒 則為 **19 倍**；這個比值只在視窗之外的第 59 至 60 小時成立
- 因此前一頁的 7.32 次/秒 是健康階段與瀕死階段的混合平均

---

## 衰減的是有效並發，不是網路

- p50 延遲從頭到尾穩定在約 870 ms，網路端並未變慢
- `crawler_inflight_requests` 小時均值由第 6 小時的約 26 降到視窗結束的約 3，到 process 結束為 1——上限是 `--concurrency 50`
- `crawler_queue_depth` 在開始約 14 分鐘後便觸及 `--queue-maxsize 50000`，視窗內 90.4% 的取樣維持在 49,999 以上，累計丟棄 65,355,760 條連結
- Little's law 在各時點都成立，所以問題收斂成一句話：為什麼 worker 花在 `fetch()` 之外的時間愈來愈長（視窗結束時每次循環約 24 秒，其中僅約 0.87 秒在 `fetch()` 之內）

| 假設 | 判定 |
|---|---|
| 佇列被少數網域佔據 | 排除——process 結束（60.2 小時）時，佇列中 49,998 個待爬網址分散在 28,242 個網域，佔比最高者僅 0.03% |
| checkpoint 寫入阻塞 event loop | 部分成立，非主因——67 段停頓共 841 秒，約佔視窗 0.5% |
| 記憶體中無上限累積的狀態造成 GC 停頓 | 主要假設，尚未證實——process 結束時 1,439,284 筆 `_seen`、同量級 referrer、181,888 個 `RobotFileParser`、三個從不淘汰的 dict |

證實這個假設需要另行以記憶體剖析進行。防爬蟲陷阱上限本身確實有效：process 結束（60.2 小時）時記錄的 181,888 個網域中有 43,256 個（23.8%）達到 20 頁上限，且最大值恰為 20；48 小時視窗結束時 `crawler_distinct_domains_total` 為 174,973。

<!--
表格中三個 60.2 小時的結束狀態值取自 results/endstate-60h.json，與 02-48h-run-results.md
§2.3.3、§2.4 同源；視窗內的對照值 174,973 取自 output/metrics_log_clean.csv 的最後一列。
-->

---

## 技術棧

| 類別 | 技術／套件 | 用途 |
|---|---|---|
| 語言／執行環境 | Python 3.13 | 主要開發語言 |
| 並發模型 | `asyncio`（標準庫） | 單執行緒非同步併發 |
| HTTP client | `aiohttp` | 非同步 HTTP 請求；測試用本地伺服器（`aiohttp.test_utils`） |
| 連結解析 | `html.parser`（標準庫） | 抽取 `<a href>`，刻意不引入第三方 HTML 解析套件 |
| robots.txt 解析 | `urllib.robotparser`（標準庫） | robots.txt 規則解析 |
| 可觀測性 | `prometheus_client` | 曝露 Counter、Gauge 與 Histogram 指標 |
| 資料持久化 | JSON / JSON Lines（標準庫） | 結果紀錄、checkpoint 與 log 格式 |
| 測試框架 | `pytest` + `pytest-asyncio` | 單元測試與整合測試，含真實子行程加 SIGKILL 測試 |

---

## 已知限制

- **記憶體中的狀態沒有上限，也沒有淘汰機制**——`_seen`、referrer 對應表、`RobotsCache` 中的 `RobotFileParser` 物件，以及 `ratelimiter.py` 裡三個以網域為 key 的 dict，全部只增不減；長時間運行下這是吞吐量衰減的主要待證假設
- **`write_checkpoint()` 是同步的，會阻塞 event loop**——在 event loop 執行緒上把整個 `_seen` 序列化成 JSON，成本隨已看過的網址數成長，寫入期間所有 worker 都無法推進
- **`bytes_received` 無法歸屬到個別紀錄**——未開啟 `--save-body` 時單筆紀錄不帶回應大小，該值只以 process 生命期的累計計數器存在，無法切分到任意時間視窗
- **全域計數器狀態會跨測試殘留**——`metrics.py` 與 `stats.py` 的計數器是模組層級的單例，測試套件沒有 autouse fixture 重置它們
- **沒有 CI**——測試只在本機以 `pytest` 執行，沒有任何自動化流程在提交時把關

---

## 資料限制

- **`bytes_received` 未列入視窗統計**——該值只以 process 生命週期內的累計計數器形式存在，無法歸屬到 48 小時視窗，因此 `results/summary-48h.json` 中該欄位為 `null` 而非 0
- **「觸及網域」與「實際發出請求的網域」不是同一件事**——兩者相差 6,845 個，那是只被 `robots.txt` 擋下、從未真正發出請求的網域；兩個數字分開列出，以免把「觸及」誤讀為「抓取」
