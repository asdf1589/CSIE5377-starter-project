# 01. 系統設計

本文件說明 `edu_crawler` 的系統設計。這是一支單機、單一 process 的非同步網頁
爬蟲，設計目標是在給定的 wall-clock 時間上限內無人值守運行至自動停止。內容涵
蓋並發模型、frontier（待爬佇列）、禮貌性與韌性控制、結果紀錄格式、運行生命週
期，以及可觀測性介面。

## 1.1 範圍與定位

`edu_crawler` 是單一 Python process、單一 asyncio event loop 的網頁爬蟲，具
備下列能力：全域並發上限控制、兩層彼此獨立的禮貌性限速、robots.txt 合規、含
指數退避與隨機抖動的重試、可在當機後續跑的週期性 checkpoint、受防爬蟲陷阱上
限約束的連結追蹤、三層可觀測性介面，以及跑完後的網域彙整報表。它處理的是
HTTP 回應本身與頁面上的 `<a href>` 連結；內容解析、排名與索引都不在範圍內。

運行邊界由兩個機制界定：`--max-runtime-hours` 給定時間上限，
`--max-pages-per-domain` 給定單一網域的頁數上限。兩者都是為了讓一次性、不可
重跑的長時間運行能在無人看管的情況下自行收斂。

```bash
# 完整的 CLI 介面；未列出的參數一律採用預設值
python -m crawler.main \
  --seeds seeds.txt \
  --output-dir output \
  --concurrency 50 \
  --per-domain-concurrency 2 \
  --per-domain-delay 1.0 \
  --timeout 10.0 \
  --max-retries 3 \
  --queue-maxsize 2000 \
  --follow-links \
  --max-pages-per-domain 20 \
  --max-runtime-hours 48 \
  --checkpoint-interval 300 \
  --checkpoint-path output/checkpoint.json \
  --metrics-port 9090 \
  --status-interval 5.0

# 從既有 checkpoint 續跑；此路徑不再載入種子清單
python -m crawler.main --resume output/checkpoint.json --follow-links
```

另有兩個預設關閉的開關：`--no-robots` 關閉 robots.txt 檢查，`--save-body` 將
回應內文另存至 `output/pages/`。

## 1.2 Process 拓樸與模組職責

系統是單一 Python process、單一 asyncio event loop，沒有跨 process 的 IPC，
也沒有外部佇列或資料庫；所有狀態都在記憶體中，僅由 checkpoint 週期性落地。模
組職責切分如下：

| 模組 | 職責 |
|---|---|
| `frontier.py` | 待爬佇列（有界佇列）、網址正規化去重、每網域頁數統計、referrer 追蹤、checkpoint 快照 |
| `ratelimiter.py` | 每網域併發數上限與每網域最小請求間隔 |
| `robots.py` | robots.txt 快取（依 origin 快取，fail-open） |
| `fetcher.py` | 實際發出 HTTP 請求，指數退避加隨機抖動的重試 |
| `linkextract.py` | 以標準庫 `html.parser` 抽取頁面上的 `<a href>` |
| `checkpoint.py` | 週期性、原子化地把爬蟲狀態寫入磁碟，供中斷後續跑 |
| `storage.py` | append-only JSON Lines 結果紀錄 |
| `worker.py` | 串接上述元件：抓取 → 記錄 → （選擇性）連結追蹤 |
| `main.py` | 程式進入點，負責啟動與關閉流程、訊號處理、CLI 參數 |
| `metrics.py` / `logging_config.py` / `reporter.py` | Prometheus 指標、JSON 結構化 log、終端機狀態列 |

## 1.3 並發模型：為什麼是 asyncio，而非多執行緒或多程序

爬蟲是典型的 I/O bound 工作：絕大部分時間花在等待網路回應，CPU 幾乎不吃負載。
asyncio 讓單一 process、單一執行緒就能同時維持數十到數千條正在等待的連線，既
不必承受多執行緒的鎖競爭與 context switch 成本，也不必付出多程序的序列化與
IPC 開銷。

全域並發上限由 `aiohttp.TCPConnector(limit=...)` 施加，對應 CLI 參數
`--concurrency`，預設值為 50——亦即任何時刻最多 50 個請求在飛。worker 數量與
這個上限綁在同一個旋鈕上：`main.py` 啟動 `--concurrency` 個 worker coroutine，
每個 worker 跑同一個迴圈——從 frontier 取出一個網址、檢查 robots.txt、取得限
速名額、抓取、寫入紀錄、（選擇性）追蹤連結。因為兩者同值，不會出現 worker 數
多於連線上限而在 connector 層互相排隊的情形。

## 1.4 Frontier：有界佇列與正規化去重

待爬網址存放在 `asyncio.Queue(maxsize=...)`，容量由 `--queue-maxsize` 指定，
預設 2000。刻意選擇有界而非無限佇列：有界佇列讓佇列本身成為一種天然的反壓
（backpressure）機制——抓取速度跟不上連結發現速度時，佇列會塞滿並擋住新網址加
入，而不是讓記憶體無限膨脹。這個預設值適用於固定種子清單；開啟連結追蹤後連結
發現速度遠高於抓取速度，因此 §2.1 的正式運行把它提高到 50,000。

每個網址在加入前先經過 `normalize_url()` 正規化：統一 scheme 與 host 的大小
寫、移除 fragment、空路徑補成 `/`。query string 刻意不排序也不移除，因為
`?page=2` 這類參數帶有語意，擅自丟棄等於靜默遺失資料。正規化後的網址記入一個
`set()`（`_seen`）去重，確保同一頁面不會被排進佇列兩次。去重發生在入列時而非
抓取時，因此「在 `_seen` 裡」等價於「至多被入列過一次」，這個不變式讓
`seen_count` 成為單調遞增的進度指標。

除了 `_seen` 與佇列本身，Frontier 另外維護每網域頁數統計（供 §1.5 的防爬蟲陷
阱上限使用）與 referrer 對應表（供 §1.7 的輸出欄位使用）。這兩者連同佇列內容
與 `_seen`，構成 `snapshot_state()` 落地成 checkpoint 的全部狀態（§1.8）。

### 1.4.1 兩種入列語意：`add()` 與 `try_add()`

Frontier 提供兩個入列方法。`add()` 在佇列已滿時會 `await queue.put()` 等待，
直到其他 coroutine 取走項目騰出空間為止；`try_add()` 改用
`queue.put_nowait()`，佇列已滿時立刻回傳 `False` 並放棄該網址。除此之外兩者
行為完全相同：都做正規化、都檢查並更新 `_seen`、都累加每網域頁數、都記錄
referrer。被放棄的網址不會寫入 `_seen`，因此佇列之後有空間時仍有機會被重新發
現——放棄的語意是「這次不排程」，而不是「永久排除」。

種子清單載入（`add_many()`）使用會阻塞的 `add()`：此時尚無任何 worker 在跑，
等待騰出空間本來就是正確行為。連結追蹤（`worker._follow_links()`）一律使用
`try_add()`，原因是結構性的：呼叫端本身就是那個數量有限的 worker pool 裡的一
員，而佇列唯一的排空途徑是 worker 呼叫 `get()`。在一個已滿的有界佇列上做阻塞
式入列，等於生產者在等一個就是自己的消費者；只要所有 worker 同時停在入列點，
就沒有任何一個還留在迴圈上層呼叫 `get()`，整個 pool 便不再前進。非阻塞入列讓
這個循環在結構上不可能成立，代價是高負載下會丟棄連結，丟棄數量由
`crawler_links_dropped_queue_full_total` 計數。

## 1.5 連結追蹤與防爬蟲陷阱上限

`--follow-links` 啟用連結追蹤；不帶這個參數時，爬蟲只處理種子清單上的網址，
抓完即結束。啟用後，每當成功抓到一個 `text/html` 回應，就用標準庫的
`html.parser` 掃出頁面上所有 `<a href>`，以原網址為 base 轉成絕對網址後加進
待爬佇列。刻意不引入 BeautifulSoup 或 lxml：這是一支爬蟲而非內容解析器，需要
的只是把每個 href 抽出來，`html.parser` 作為標準庫的 SAX 式 tokenizer 剛好足
夠，而且對格式不良的 HTML 有容錯——解析錯誤只讓該頁少抽到幾條連結，不會讓
worker 崩潰。

連結追蹤天生帶有一個危險：爬蟲陷阱（crawl trap）。某些網站的「下一頁」連結會
無限生成（例如行事曆頁面），若不設限，爬蟲可能把整段運行時間都耗在同一個網域
上。`--max-pages-per-domain`（預設 20）為此設限：每個網域最多只能佔用固定數
量的佇列名額，達到上限後，再發現指向該網域的連結就直接略過。

這個上限刻意用被發現連結「自己的網域」判斷，而不是用「發現它的那個來源頁面的
網域」。要防的是某一個網域自己無限生成同網域連結，因此需要設上限的預算是
「frontier 已經知道多少該網域的頁面」。若改用來源頁面的網域判斷，效果會反過
來：一個連向大量外部網站的熱門樞紐頁會在達到自身上限後被整個切斷，擋掉的是合
法的跨網域發現，與「盡量觸及多個不同網域」的廣度優先取向背道而馳。

連結追蹤透過 `try_add()` 入列（§1.4.1），因此佇列已滿時是丟棄該連結，而不是
讓 worker 卡住。入列動作也刻意安排在釋放該網域限速名額之後：否則停在入列點的
worker 會一直握著該網域的併發名額，餓死其他想抓同網域的 worker，而
`crawler_inflight_requests` 也會把 HTML 解析與入列誤計為網路併發。

## 1.6 禮貌性與韌性

本節涵蓋三組彼此獨立的控制：對目標站台的限速、robots.txt 合規，以及失敗請求
的重試策略。

### 1.6.1 兩層獨立的禮貌性控制

只有一個全域並發上限是不夠的：假設 1,000 個種子網址分散在 40 個網域，排程順
序不巧的話，仍然可能同時對某個小網站發出 20 個並發請求，導致被目標站台限流甚
至封鎖 IP。因此另外設置兩個與全域上限彼此獨立的機制：

- 每網域併發數上限（`--per-domain-concurrency`，預設 2）——以「每個網域各自一
  個」的 `asyncio.Semaphore` 實作，限制同一網域同時最多幾個請求在飛。
- 每網域最小請求間隔（`--per-domain-delay`，預設 1.0 秒）——即使併發數是 1，
  若沒有這個限制，同一網域仍可能每隔幾毫秒就被打一次（前一個回應一回來就立刻
  發下一個）。這個機制強制同網域「請求發起時間」之間至少間隔一段時間。

兩者由 `DomainRateLimiter` 一併提供：`acquire()` 先取得該網域的 semaphore，
再在該網域專屬的 lock 內檢查距離上次發起是否已滿足間隔，不足則 sleep 補齊後
才更新時間戳。這是真實禮貌性層（例如 Scrapy 的 AutoThrottle）的簡化版本，差
別在於它不會依觀測到的伺服器延遲動態調整。

### 1.6.2 robots.txt 合規

每個 origin（scheme 加 host）的 robots.txt 只在第一次遇到時抓取一次並快取於
`RobotsCache`，內部以標準庫的 `urllib.robotparser.RobotFileParser` 解析。首
次抓取由該 origin 專屬的 `asyncio.Lock` 保護，避免多個 worker 同時去拉同一份
robots.txt——那會讓請求數翻倍，反而違背這個機制本身要保護的禮貌性。

採用 fail-open：robots.txt 不存在、回應非 200、或抓取本身失敗時，預設允許抓
取。fail-open 是正式爬蟲的慣例做法；在規模更大或更敏感的場景，fail-closed 才
是保守的選擇。

### 1.6.3 重試：指數退避與隨機抖動

單次請求失敗（逾時、連線錯誤、DNS 失敗等）會重試，次數上限由 `--max-retries`
指定，預設 3。第 n 次重試前的等待時間是 `retry_backoff_base * 2 ** (n - 1)`，
再加上一段 `random.uniform(0, retry_backoff_base)` 的隨機抖動。

抖動是為了避免 thundering herd：若目標站台短暫異常導致大量請求在同一瞬間失敗，
而所有請求又採用完全相同的確定性退避，它們會在同一時刻一起重試，可能把剛恢復
的服務再打掛一次；加入隨機量可以把重試散開在時間軸上。
`asyncio.CancelledError` 永遠不被吞掉，因此 Ctrl-C 能立即中斷正在退避等待中
的請求。

## 1.7 結果紀錄與儲存格式

結果以 append-only JSON Lines 寫入 `output/results.jsonl`。選擇 JSONL 而非
SQLite 的理由是：它 crash-safe——唯一可能的損毀形式是最後一行只寫了一半，而這
種情況既容易偵測也容易捨棄；它不需要 schema migration；而且可以直接
`tail -f` 觀察，事後也能直接餵進 pandas 或 duckdb。多個 worker coroutine 併
發呼叫寫入，因此寫入由一個 `asyncio.Lock` 序列化，維持單一寫入者、只追加的語
意。

每筆紀錄包含 `url`、`domain`、`status`、`ok`、`elapsed_ms`、`attempts`、
`error`、`robots_blocked`、`ts`；開啟 `--follow-links` 時另加 `referrer`。其
中三個欄位值得說明：

- `domain` 是該網址所屬網域，事後彙整時不必重新解析每個網址。
- `robots_blocked` 讓被 robots.txt 擋下的網址同樣留下一筆紀錄。若不記錄，
  「有遵守 robots.txt」這件事在輸出裡完全不會留下任何證據；§2.5 中「觸及網域」
  與「實際發出請求的網域」之間的差距，正是由這個欄位算出來的。這類紀錄沒有發
  出請求，因此 `status` 為 `null`、`elapsed_ms` 與 `attempts` 為 0。
- `referrer` 只在開啟連結追蹤時寫入，而且是整個 key 不存在，而非值為 `null`。
  固定清單模式下沒有 referrer 這個概念，輸出結構就不該多一個永遠沒人填的欄位。

## 1.8 運行生命週期：到點自動停止與 checkpoint / resume

`--max-runtime-hours` 為整次運行設定 wall-clock 上限。實作上是額外開一個背景
coroutine，睡滿指定時數後去 `set()` 那個 SIGINT 與 SIGTERM 同樣會設定的
`asyncio.Event`（`stop_event`）。這個設計刻意重用既有的關閉路徑，而不是另建
一套獨立的停止機制：如此「時間到自動停止」與「使用者按 Ctrl-C」在收尾行為上
完全一致，都會正常寫出最後一次 checkpoint 與 `summary.json`，不會有兩套邏輯
各自維護、其中一邊漏掉步驟的風險。關閉時 `main.py` 讓 `frontier.join()`（佇
列排空）與 `stop_event` 互相競速，任一方先完成就取消所有 worker task，兩條路
徑共用同一段 teardown。

運行是一次性、不可重跑的，因此狀態必須週期性落地。`--checkpoint-interval`
（預設 300 秒）決定寫入頻率。checkpoint 的內容是 `Frontier.snapshot_state()`
回傳的三個 key——`seen`（所有曾入列的網址）、`pending`（仍在等待 worker 的網
址）、`domain_page_counts`（每網域頁數統計），也就是 §1.4 描述的那三組結構——
再加上累計統計數字。`pending` 除了佇列當前內容，還包含 `add()` 已經寫入
`_seen`、但 `await queue.put()` 尚未完成的網址；少了這一塊，卡在該空窗期的網
址會既不在佇列裡也不在快照裡，續跑時又被 `_seen` 擋下而永遠遺失。

寫入特別處理過原子性：先寫到同目錄下的暫存檔（`path + ".tmp"`），完全寫完後
才用 `os.replace()` 覆蓋原檔。`os.replace()` 在 POSIX 與 Windows 上都是原子
操作，因此即使 process 在 write 與 replace 之間被 `kill -9`，也不會留下一份
半截的 checkpoint；上一次成功完成的版本永遠完整可用。寫入失敗（磁碟滿、權限
錯誤、路徑不存在）只記錄 log 並讓迴圈繼續，不向外拋出——否則該 coroutine 會靜
默死亡，讓一次長時間的無人值守運行在毫無徵兆的情況下失去後續所有 checkpoint。
`--resume <path>` 從這份存檔直接重建 frontier 與統計計數器，跳過載入種子清單，
避免整批已抓過的網址被重抓。

## 1.9 可觀測性：三層、三種受眾

指標設計明確對應 SRE 的四個黃金訊號：延遲對應
`crawler_fetch_latency_seconds`，流量對應 `crawler_requests_total`，錯誤對應
`crawler_errors_total` 與 `crawler_requests_total` 的 `status` label，飽和度
對應 `crawler_inflight_requests` 相對於 `--concurrency`、以及
`crawler_queue_depth` 相對於 `--queue-maxsize`。另有
`crawler_urls_seen_total`、`crawler_robots_skipped_total`、
`crawler_retries_total`，以及兩個與連結追蹤直接相關的指標：
`crawler_distinct_domains_total` 記錄曾加入 frontier 的相異網域數——開啟連結
追蹤後，相異網址數與相異網域數是兩件不同的事，必須分開追蹤——而
`crawler_links_dropped_queue_full_total` 記錄因佇列已滿而被丟棄的連結數（§
1.4.1）。

三種觀察方式對應三種受眾：

- 結構化 log（`logging_config.py`）——JSON Lines 輸出到 stdout，便於用 `jq`
  過濾，或匯入 Loki、Elasticsearch 查詢分析。
- 終端機狀態列（`reporter.py`）——每 `--status-interval` 秒印一行人類可讀的摘
  要，開發時盯著終端機用。
- Prometheus 指標（`metrics.py`，透過 `prometheus_client` 曝露在
  `:9090/metrics`）。

`:9090/metrics` 有兩種取用方式。`monitoring/docker-compose.yml` 提供
Prometheus 加 Grafana 的標準組合。不便安裝 Docker 時，
`scripts/watch_metrics.py` 是零相依的替代方案：它只用標準庫輪詢 `/metrics`，
印出精簡的即時儀表板，並把每一次取樣追加寫入 `output/metrics_log.csv`——§2.3
的時間序列就來自這個檔案。

## 1.10 跑完後的網域彙整

`scripts/summarize.py` 讀取整份 `results.jsonl`，依網域彙整出 `domain`、
`first_seen_ts`、`success_count`、`fail_count`、`robots_blocked` 五個欄位，
同時輸出 `output/domain_summary.csv` 與 `output/domain_summary.json` 兩種格
式。

每筆紀錄只會落入 success、fail、robots_blocked 其中一個桶，因此三者之和恆等
於該網域的紀錄總數。`robots_blocked` 與 `fail_count` 分開計數是刻意的：選擇
不抓（禮貌性）與抓了但失敗（可靠性）是兩件不同的事，混在一起會讓遵守
robots.txt 的紀錄在報表上看起來像錯誤。

這支腳本設計成跑完之後手動執行一次，而不是邊爬邊即時彙整，目的是讓即時寫入的
熱路徑（`storage.py` 的 append-only 寫檔）維持越簡單越好；一次性的批次運算完
全可以等資料到齊之後再做。

## 1.11 測試策略

測試套件完全不使用 mock：整個 `tests/` 目錄沒有任何 `unittest.mock`、
`monkeypatch` 或 `patch()`。會實際發出網路請求的邏輯（fetcher、robots.txt 檢
查、連結追蹤）一律以 `aiohttp.test_utils.TestServer` 啟動真正監聽本機連接埠
的 HTTP 伺服器來驗證，真的走一次 TCP 與 HTTP；需要檔案系統的測試則用 pytest
的 `tmp_path` 寫真實檔案。

最關鍵的一個測試針對當機後續跑：用 `subprocess.Popen` 啟動真正的 crawler
process，跑一段時間後呼叫 `.kill()`（在 POSIX 上等同 SIGKILL，完全不給程式優
雅關閉的機會），確認 checkpoint 檔案存在且有效，接著以 `--resume` 啟動第二個
process，驗證前後兩次抓取的網址完全不重疊、且聯集涵蓋全部網址。這比在程式內
部用 `task.cancel()` 模擬中斷更貼近真實情境，也才真正測到作業系統層級強制終
止這個最嚴苛的情況。

另有一個回歸測試直接驅動多個 worker，讓每個 worker 各自處理一個連結數超過佇
列容量的頁面，斷言整場爬取仍然跑完——鎖住 §1.4.1 的非阻塞入列語意。全套共 55
個測試，分布於 12 個檔案。

## 1.12 技術棧

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
| 版本控制 | `git` | 原始碼版本管理 |

## 1.13 已知限制

- **記憶體中的狀態沒有上限，也沒有淘汰機制** —— `_seen`、referrer 對應表、
  `RobotsCache` 中的 `RobotFileParser` 物件，以及 `ratelimiter.py` 裡三個以
  網域為 key 的 dict（semaphore、lock、last-start），全部只增不減。長時間運
  行下這是吞吐量衰減的主要待證假設，見 §2.3.4。
- **`write_checkpoint()` 是同步的，會阻塞 event loop** —— 它在 event loop 執
  行緒上把整個 `_seen` 序列化成 JSON，成本隨已看過的網址數成長，寫入期間所有
  worker 都無法推進。
- **`bytes_received` 無法歸屬到個別紀錄** —— 未開啟 `--save-body` 時，單筆紀
  錄不帶回應大小，該數值只以 process 生命期的累計計數器存在，無法切分到任意
  時間視窗，見 §2.5。
- **全域計數器狀態會跨測試殘留** —— `metrics.py` 與 `stats.py` 的計數器是模
  組層級的單例，測試套件沒有 autouse fixture 重置它們，因此測試之間理論上可
  能透過執行順序互相影響。
- **`summary.json` 的 `throughput_per_s` 不可直接引用** —— 該欄位以跨
  `--resume` 還原的累計 `fetched` 為分子、只計算最後一個 process 存活時間的
  `elapsed_s` 為分母，兩者不同源，相除的結果不對應任何一段真實區間；視窗內的
  正確吞吐量請以 `results/summary-48h.json` 為準。
- **沒有 CI** —— 測試只在本機以 `pytest` 執行，沒有任何自動化流程在提交時把
  關。
