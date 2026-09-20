"""
Render the figures used by docs/02-48h-run-results.md into docs/rsc/.

All series are restricted to the 48-hour observation window:
  - throughput-hourly.csv rows with in_window == true
  - workers-hourly.csv / inflight-ceiling-30min.csv / inflight-hist-h48.csv /
    same-domain-gap-hist.csv, the committed aggregates that carry the series
    too expensive to recompute from the 360 MB results_clean.jsonl
  - metrics_log_clean.csv / results_clean.jsonl, which clean_results.py
    already clips to the window

Usage:
    python3 scripts/make_figures.py
"""

import csv
import json
import pathlib
import collections

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from matplotlib.patches import FancyBboxPatch

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "rsc"

# Palette: validated categorical slots 1-2 plus chart chrome / ink tokens.
BLUE, ORANGE = "#2a78d6", "#eb6834"
SURFACE, GRID, BASELINE = "#fcfcfb", "#e1e0d9", "#c3c2b7"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"

plt.rcParams.update({
    "font.family": "Noto Sans CJK JP",
    "axes.unicode_minus": False,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": INK2,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.titlesize": 13,
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
})

THOUSANDS = FuncFormatter(lambda v, _: f"{int(v):,}")


def style(ax, ylabel=None):
    """Hairline recessive grid, no top/right spines, solid rules only."""
    ax.grid(axis="y", color=GRID, linewidth=0.8, linestyle="-")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASELINE)
        ax.spines[s].set_linewidth(0.8)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2)


def read_workers():
    """results/workers-hourly.csv -> per-hour live-worker series.

    live_workers is the binomial MLE over the inflight gauge's value
    histogram, not a counter the crawler ever exported."""
    cols = collections.defaultdict(list)
    for r in csv.DictReader(open(ROOT / "results/workers-hourly.csv")):
        cols["hour"].append(int(r["hour"]))
        cols["n"].append(int(r["live_workers"]))
        cols["cycle"].append(float(r["cycle_s"]))
        cols["gauge_pct"].append(float(r["in_gauge_pct"]))
    return cols


def fig_throughput():
    rows = [r for r in csv.DictReader(open(ROOT / "results/throughput-hourly.csv"))
            if r["in_window"] == "true"]
    h = [int(r["hour"]) for r in rows]
    f = [int(r["fetches"]) for r in rows]
    peak = max(range(len(f)), key=lambda i: f[i])
    wk = read_workers()

    # Throughput and worker count share an x-axis but nothing else, so they get
    # stacked panels rather than a twin axis; the reader compares shape, not height.
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8.4, 5.2), sharex=True,
                                 gridspec_kw={"hspace": 0.22, "height_ratios": [3, 1.5]})

    a1.plot(h, f, color=BLUE, linewidth=2, solid_capstyle="round")
    a1.fill_between(h, f, color=BLUE, alpha=0.08, linewidth=0)

    # Direct-label the two points the text actually cites; the axis carries the rest.
    for i, va, dy in ((peak, "bottom", 2200), (len(f) - 1, "bottom", 2200)):
        a1.plot(h[i], f[i], "o", color=BLUE, markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        a1.annotate(f"第 {h[i]} 小時\n{f[i]:,}", (h[i], f[i] + dy),
                    ha="center", va=va, fontsize=9, color=INK2, linespacing=1.4)

    # Decay ratio, drawn in the right-hand margin so it crosses no data.
    for i in (peak, len(f) - 1):
        a1.plot([h[i], 50.4], [f[i], f[i]], color=BASELINE, linewidth=0.8, zorder=1)
    a1.annotate("", xy=(50.4, f[peak]), xytext=(50.4, f[-1]),
                arrowprops={"arrowstyle": "<->", "color": MUTED, "linewidth": 0.9,
                            "shrinkA": 0, "shrinkB": 0})
    a1.annotate(f"{f[peak] / f[-1]:.1f} 倍", (49.8, (f[peak] + f[-1]) / 2),
                rotation=90, ha="right", va="center", fontsize=9.5, color=INK2)

    style(a1, "每小時抓取次數")
    a1.set_title("每小時抓取次數與存活 worker 數是同一條曲線",
                 color=INK, pad=14, loc="left")
    a1.set_ylim(0, 78000)
    a1.yaxis.set_major_formatter(THOUSANDS)

    a2.step(wk["hour"], wk["n"], where="mid", color=ORANGE, linewidth=2,
            solid_capstyle="round")
    a2.fill_between(wk["hour"], wk["n"], step="mid", color=ORANGE, alpha=0.16,
                    linewidth=0)
    a2.axhline(50, color=MUTED, linewidth=0.8)
    for x in (1, 12, 24, 36, 48):
        a2.annotate(f"{wk['n'][x - 1]}", (x, wk["n"][x - 1] + 4), ha="center",
                    va="bottom", fontsize=9, color=INK2)
    style(a2, "存活 worker 數")
    a2.set_xlabel("運行時數（小時）")
    a2.set_ylim(0, 70)
    a2.set_yticks([0, 25, 50])
    a2.set_xlim(0, 51.5)
    a2.set_xticks([1, 6, 12, 18, 24, 30, 36, 42, 48])
    fig.tight_layout()
    fig.savefig(OUT / "throughput-48h.png", dpi=200)
    plt.close(fig)


def fig_concurrency():
    """Two measures on wildly different scales -> two stacked panels, never a
    second y-axis on one plot."""
    buckets = collections.defaultdict(lambda: [[], []])
    rdr = csv.DictReader(open(ROOT / "output/metrics_log_clean.csv"))
    rows = list(rdr)
    t0 = None
    import datetime as dt
    for r in rows:
        ts = dt.datetime.fromisoformat(r["ts"])
        t0 = t0 or ts
        hour = int((ts - t0).total_seconds() // 3600)
        if 0 <= hour < 48:
            buckets[hour][0].append(float(r["inflight"]))
            buckets[hour][1].append(float(r["queue_depth"]))
    hrs = sorted(buckets)
    infl = [sum(buckets[x][0]) / len(buckets[x][0]) for x in hrs]
    qd = [sum(buckets[x][1]) / len(buckets[x][1]) for x in hrs]
    hrs = [x + 1 for x in hrs]

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8.4, 5.4), sharex=True,
                                 gridspec_kw={"hspace": 0.28, "height_ratios": [3, 2]})

    a1.plot(hrs, infl, color=BLUE, linewidth=2, solid_capstyle="round")
    a1.axhline(50, color=MUTED, linewidth=0.8)
    a1.annotate("--concurrency 50（上限）", (1.2, 51.2), ha="left", va="bottom",
                fontsize=8.5, color=MUTED)
    pk = max(range(len(infl)), key=lambda i: infl[i])
    for i in (pk, len(infl) - 1):
        a1.plot(hrs[i], infl[i], "o", color=BLUE, markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        a1.annotate(f"{infl[i]:.0f}", (hrs[i], infl[i] + 3), ha="center",
                    fontsize=9, color=INK2)
    style(a1, "並發請求數（小時均值）")
    a1.set_ylim(0, 58)
    a1.set_title("有效並發持續下降，佇列始終滿載：瓶頸不在網路",
                 color=INK, pad=12, loc="left")

    a2.fill_between(hrs, qd, color=ORANGE, alpha=0.16, linewidth=0)
    a2.plot(hrs, qd, color=ORANGE, linewidth=2, solid_capstyle="round")
    a2.axhline(50000, color=MUTED, linewidth=0.8, zorder=1)
    a2.annotate("--queue-maxsize 50000（上限），運行約 14 分鐘後即觸頂",
                (1.2, 44000), ha="left", va="top", fontsize=8.5, color=INK2)
    style(a2, "佇列深度（小時均值）")
    a2.set_ylim(0, 56000)
    a2.set_yticks([0, 25000, 50000])   # label the cap value on the axis
    a2.set_xlabel("運行時數（小時）")
    a2.set_xlim(0, 49.5)
    a2.set_xticks([1, 6, 12, 18, 24, 30, 36, 42, 48])
    a2.yaxis.set_major_formatter(THOUSANDS)
    fig.tight_layout()
    fig.savefig(OUT / "concurrency-queue-48h.png", dpi=200)
    plt.close(fig)


def scan_results():
    """Single pass over results_clean.jsonl -> (status, reason, latencies)."""
    status = collections.Counter()
    reason = collections.Counter()
    latencies = []
    for line in open(ROOT / "output/results_clean.jsonl", encoding="utf-8"):
        d = json.loads(line)
        if d.get("robots_blocked"):
            continue
        s = d.get("status")
        if s is None:
            status["連線失敗"] += 1
        elif s in (200, 404, 403, 429):
            status[str(s)] += 1
        else:
            status["其他"] += 1          # remaining 2xx, all 3xx, remaining 4xx/5xx
        if d.get("ok"):
            latencies.append(d["elapsed_ms"])
            continue
        e = d.get("error") or ""
        if s is not None:
            reason["HTTP 錯誤狀態碼"] += 1
        elif not e:
            reason["錯誤字串為空"] += 1
        elif "Got more than 8190 bytes" in e:
            reason["header 過長"] += 1
        elif e.startswith("Cannot connect to host"):
            reason["DNS / 連線失敗"] += 1
        else:
            reason["其餘長尾"] += 1      # incl. 連線重置 143 + 伺服器斷線 93
    latencies.sort()
    return status, reason, latencies


def fig_latency_cdf(latencies):
    """Empirical CDF of successful-fetch latency, aggregated over the whole
    48-hour window. It shows the distribution, NOT its stability over time -
    the "p50 held flat throughout" claim comes from the time series in §2.3.2."""
    n = len(latencies)
    def q(prob):
        return latencies[min(n - 1, int(round(prob * (n - 1))))]
    probs = [i / 1500 for i in range(1501)]
    xs = [q(pr) for pr in probs]
    ys = [pr * 100 for pr in probs]

    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    ax.semilogx(xs, ys, color=BLUE, linewidth=2, solid_capstyle="round")

    for pr, lab in ((0.5, "p50"), (0.9, "p90"), (0.99, "p99")):
        x = q(pr)
        ax.plot([x, x], [0, pr * 100], color=MUTED, linewidth=0.8, zorder=1)
        ax.plot(x, pr * 100, "o", color=BLUE, markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        # Label sits up-and-left of the marker, where the curve never reaches.
        ax.annotate(f"{lab} = {x:,.0f} ms", (x * 0.88, pr * 100 + 2.5),
                    ha="right", va="bottom", fontsize=9, color=INK2)

    style(ax, "累積比例（%）")
    ax.set_xlabel("回應延遲（毫秒，對數刻度）")
    ax.set_title(f"成功抓取的延遲分布（n = {n:,}）：逾八成在 2 秒內完成",
                 color=INK, pad=14, loc="left")
    ax.set_xlim(2, 20000)
    ax.set_ylim(0, 108)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    fig.tight_layout()
    fig.savefig(OUT / "latency-cdf-48h.png", dpi=200)
    plt.close(fig)


def fig_outcomes(status, reason):
    """Two part-to-whole pies. Each pie sums to its own n, so the smallest
    classes are folded into one 其他 / 其餘長尾 wedge; the captions in
    docs/02-48h-run-results.md name exactly what went into them."""
    # Fixed categorical order - hues follow the category, never the rank.
    SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
    panels = [
        (status, ["200", "404", "403", "連線失敗", "429", "其他"],
         "狀態碼分布"),
        (reason, ["HTTP 錯誤狀態碼", "DNS / 連線失敗", "錯誤字串為空",
                  "header 過長", "其餘長尾"], "失敗原因"),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 6.4))
    for ax, (data, order, title) in zip(axes, panels):
        vals = [data[k] for k in order]
        total = sum(vals)
        wedges, _ = ax.pie(
            vals, startangle=90, counterclock=False,
            colors=SLOTS[:len(order)],
            # 2px surface gap between wedges, never a dark border
            wedgeprops={"linewidth": 2, "edgecolor": SURFACE},
        )
        # Label only the wedge with room; the legend carries every value.
        big = max(range(len(vals)), key=lambda i: vals[i])
        import math
        ang = (wedges[big].theta1 + wedges[big].theta2) / 2
        ax.text(0.58 * math.cos(math.radians(ang)), 0.58 * math.sin(math.radians(ang)),
                f"{vals[big] / total * 100:.2f}%", ha="center", va="center",
                fontsize=13, color="#ffffff")
        ax.set_title(f"{title}（n = {total:,}）", color=INK, pad=16,
                     loc="center", fontsize=12)
        ax.legend(wedges,
                  [f"{k}　{v:,}（{v / total * 100:.2f}%）" for k, v in zip(order, vals)],
                  loc="upper center", bbox_to_anchor=(0.5, -0.04), frameon=False,
                  fontsize=9.5, labelcolor=INK2, handlelength=1.1, handleheight=1.1)
    # A legend outside the axes is invisible to tight_layout - reserve the band.
    fig.subplots_adjust(top=0.90, bottom=0.30, left=0.02, right=0.98, wspace=0.05)
    fig.savefig(OUT / "outcomes-48h.png", dpi=200)
    plt.close(fig)


def fig_worker_survival():
    """The headline finding: the pool shrank, it did not slow down.

    Left: the ceiling of crawler_inflight_requests per 30-minute bucket. A
    ceiling that steps down and never recovers is a concurrency limit, not a
    statistical tail. Right: hour 48's gauge histogram against the two
    competing models, which is what makes the claim falsifiable."""
    import math

    buckets, ceil = [], []
    for r in csv.DictReader(open(ROOT / "results/inflight-ceiling-30min.csv")):
        buckets.append(int(r["bucket"]) * 0.5)   # bucket start, in hours
        ceil.append(int(r["max_inflight"]))
    wk = read_workers()

    obs = [int(r["samples"])
           for r in csv.DictReader(open(ROOT / "results/inflight-hist-h48.csv"))]
    n = sum(obs)
    mean = sum(i * c for i, c in enumerate(obs)) / n

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11.2, 4.5),
                                 gridspec_kw={"width_ratios": [1.85, 1],
                                              "wspace": 0.22})

    # --- left: the staircase -------------------------------------------------
    a1.step(buckets + [48.0], ceil + [ceil[-1]], where="post", color=BLUE,
            linewidth=1.8, solid_capstyle="round")
    a1.fill_between(buckets + [48.0], ceil + [ceil[-1]], step="post",
                    color=BLUE, alpha=0.08, linewidth=0)
    a1.step([x - 1 for x in wk["hour"]] + [48.0], wk["n"] + [wk["n"][-1]],
            where="post", color=ORANGE, linewidth=1.8, solid_capstyle="round")
    a1.axhline(50, color=MUTED, linewidth=0.8)
    a1.annotate("--concurrency 50（上限）", (0.6, 51.2), ha="left", va="bottom",
                fontsize=8.5, color=MUTED)

    top = max(range(len(ceil)), key=lambda i: ceil[i])
    a1.annotate(f"最高 {ceil[top]}", (buckets[top] + 0.6, ceil[top] + 1.4),
                ha="left", va="bottom", fontsize=9, color=INK2)
    a1.annotate(f"第 48 小時的 {n} 次取樣\n沒有一次超過 4", (47.5, 20),
                ha="right", va="bottom", fontsize=9, color=INK2, linespacing=1.5)
    a1.annotate("", xy=(47.8, 5.0), xytext=(47.8, 19.0),
                arrowprops={"arrowstyle": "-", "color": BASELINE, "linewidth": 0.9})

    # Colour-matched inline legend, parked in the dead space the decay leaves.
    a1.annotate("每 30 分鐘的 inflight 天花板", (24.5, 45.5), color=BLUE, fontsize=9.5)
    a1.annotate("存活 worker 數（二項式 MLE）", (24.5, 40.0), color=ORANGE, fontsize=9.5)

    style(a1, "並發請求數")
    a1.set_xlabel("運行時數（小時）")
    a1.set_title("有效併發從 50 塌到 4：天花板先升到 43，之後再也沒回到早期水準",
                 color=INK, pad=14, loc="left")
    a1.set_xlim(0, 48.5)
    a1.set_ylim(0, 58)
    a1.set_xticks([0, 6, 12, 18, 24, 30, 36, 42, 48])

    # --- right: hour-48 histogram vs. the two models -------------------------
    ks = list(range(0, 9))
    binom = lambda k, m, p: math.comb(m, k) * p ** k * (1 - p) ** (m - k)
    fit = [n * binom(k, 4, mean / 4) if k <= 4 else 0.0 for k in ks]
    naive = [n * binom(k, 50, mean / 50) for k in ks]
    over = n * (1 - sum(binom(k, 50, mean / 50) for k in range(5)))

    a2.bar(ks, obs + [0] * (len(ks) - len(obs)), width=0.72, color=BLUE,
           linewidth=0, label=f"實測（n = {n}）")
    a2.plot(ks, fit, "o-", color=ORANGE, linewidth=1.6, markersize=5,
            markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3,
            label=f"Binomial(4, {mean / 4:.2f})")
    a2.plot(ks, naive, "s--", color=MUTED, linewidth=1.2, markersize=4,
            markeredgecolor=SURFACE, markeredgewidth=1.0, zorder=3,
            label=f"Binomial(50, {mean / 50:.3f})")
    # Narrow enough to clear both the legend and the tallest bar.
    a2.annotate(f"若仍有 50 個 worker\n應有 {over:.0f} 次取樣 > 4\n實測 0 次",
                (8.6, 305), ha="right", va="top", fontsize=9, color=INK2,
                linespacing=1.5)
    a2.annotate("", xy=(6.2, 44), xytext=(6.9, 200),
                arrowprops={"arrowstyle": "-", "color": BASELINE, "linewidth": 0.9})

    style(a2, "取樣次數")
    a2.set_xlabel("crawler_inflight_requests 的值")
    a2.set_title("第 48 小時的 inflight 分布", color=INK, pad=14, loc="left")
    a2.set_xticks(ks)
    a2.set_xlim(-0.7, 8.7)
    a2.set_ylim(0, 430)
    a2.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK2,
              handlelength=1.6, borderpad=0.1)
    fig.tight_layout()
    fig.savefig(OUT / "worker-survival-48h.png", dpi=200)
    plt.close(fig)


def fig_worker_loop():
    """What the two instruments actually measure.

    The gauge starts after rate_limiter.acquire() and the elapsed_ms clock
    covers only one fetch attempt, so the politeness wait sits outside both.
    Costs are hour-48 measurements; the loop is crawler/worker.py:30-77."""
    wk = read_workers()
    cycle, gauge_pct = wk["cycle"][47], wk["gauge_pct"][47]
    gauge_s = cycle * gauge_pct / 100

    # (label, cost caption, inside the inflight gauge, unguarded ValueError site)
    stages = [
        ("frontier.get()", "取出 URL", False, True),
        ("robots.is_allowed()", "≤ 0.63 秒（上界）", False, False),
        ("rate_limiter.acquire()", "≈ 0.87 秒（條件）", False, False),
        ("fetch()", "1.23 秒（平均）", True, False),
        ("store.record()", "寫入結果", True, False),
        ("_follow_links()", "1.31 毫秒", False, True),
    ]
    W, GAP, X0, YB, YT = 14.0, 2.4, 3.2, 52.0, 72.0

    fig, ax = plt.subplots(figsize=(11.2, 4.6))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 89)
    ax.axis("off")

    spans = {}
    for i, (name, cost, in_gauge, deadly) in enumerate(stages):
        x = X0 + i * (W + GAP)
        spans[i] = (x, x + W)
        ax.add_patch(FancyBboxPatch(
            (x, YB), W, YT - YB, boxstyle="round,pad=0,rounding_size=1.2",
            facecolor=BLUE if in_gauge else SURFACE,
            alpha=0.10 if in_gauge else 1.0,
            edgecolor="none" if in_gauge else BASELINE, linewidth=1.0, zorder=1))
        if in_gauge:   # tinted fill cannot also carry a crisp edge
            ax.add_patch(FancyBboxPatch(
                (x, YB), W, YT - YB, boxstyle="round,pad=0,rounding_size=1.2",
                facecolor="none", edgecolor=BLUE, linewidth=1.2, zorder=2))
        ax.text(x + W / 2, YB + 11.2, name, ha="center", va="center",
                fontsize=10, color=INK)
        ax.text(x + W / 2, YB + 5.0, cost, ha="center", va="center",
                fontsize=9, color=INK2)
        if deadly:
            ax.plot(x + W - 1.9, YT - 2.0, marker="X", color=ORANGE,
                    markersize=7, zorder=4)
        if i:
            ax.annotate("", xy=(x - 0.5, (YB + YT) / 2),
                        xytext=(x - GAP + 0.5, (YB + YT) / 2),
                        arrowprops={"arrowstyle": "->", "color": MUTED,
                                    "linewidth": 1.0})
    def bracket(x0, x1, y, depth, color, label, sub, above):
        """Squared bracket; depth is signed away from the boxes."""
        d = depth if above else -depth
        ax.plot([x0, x0, x1, x1], [y, y + d, y + d, y], color=color,
                linewidth=1.4, solid_joinstyle="miter", zorder=3)
        ax.text((x0 + x1) / 2, y + d + (2.2 if above else -2.2), label,
                ha="center", va="bottom" if above else "top",
                fontsize=9.5, color=color)
        ax.text((x0 + x1) / 2, y + d + (7.6 if above else -7.6), sub,
                ha="center", va="bottom" if above else "top",
                fontsize=9, color=INK2)

    bracket(spans[3][0], spans[4][1], YT + 1.5, 4.0, BLUE, "crawler_inflight_requests",
            f"{gauge_s:.2f} 秒 = 迴圈的 {gauge_pct:.0f}%", True)
    bracket(spans[3][0], spans[3][1], YB - 1.5, 4.0, ORANGE, "elapsed_ms",
            "只計最後一次嘗試", False)
    bracket(spans[0][0], spans[5][1], 22.0, 4.0, MUTED, "一次完整迴圈",
            f"第 48 小時：{cycle:.2f} 秒／圈（存活的 {wk['n'][47]} 個 worker）", False)

    # The one thing the figure exists to show. Two lines, so the callout stays
    # clear of the elapsed_ms caption sitting one box to its right.
    ax.text(X0, YB - 5.0, "上界／條件／平均三種數字並列，不可相加",
            fontsize=9, color=MUTED, va="center", ha="left")
    cx = (spans[2][0] + spans[2][1]) / 2
    ax.annotate("acquire() 的等待\n落在兩個量測區間之外",
                xy=(cx, YB - 1.0), xytext=(cx, YB - 17.0), ha="center", va="top",
                fontsize=9.5, color=INK, linespacing=1.5,
                arrowprops={"arrowstyle": "->", "color": MUTED, "linewidth": 1.0})
    ax.plot(X0 + 0.6, 4.0, marker="X", color=ORANGE, markersize=7)
    ax.text(X0 + 3.2, 4.0, "未保護的 ValueError（worker.py:32,97,98；linkextract.py:39）",
            ha="left", va="center", fontsize=9, color=INK2)

    ax.set_title("worker 迴圈：兩個量測區間都看不到禮貌性等待",
                 color=INK, pad=10, loc="left")
    fig.tight_layout()
    fig.savefig(OUT / "worker-loop-anatomy.png", dpi=200)
    plt.close(fig)


def fig_same_domain_gap():
    """Direct evidence that --per-domain-delay 1.0 binds in production.

    Gaps between consecutive completions of the same domain. The spike sits
    on the gate, not at zero. Denominator is every same-domain consecutive
    pair in that hour, so the two hours are comparable despite the 9.2x
    throughput collapse. This says nothing about the decay - engagement
    actually falls."""
    rows = list(csv.DictReader(open(ROOT / "results/same-domain-gap-hist.csv")))
    edges = [int(r["gap_bin_ms"]) / 1000 for r in rows]
    series = [
        ("第 1 小時：{:.0f}% 落在閘門帶", BLUE, 20.9,
         [int(r["hour_1_pairs"]) for r in rows], int(rows[0]["hour_1_total_pairs"])),
        ("第 48 小時：{:.0f}%", ORANGE, 10.0,
         [int(r["hour_48_pairs"]) for r in rows], int(rows[0]["hour_48_total_pairs"])),
    ]

    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    ax.axvspan(0.9, 1.1, color=GRID, linewidth=0, zorder=0)
    for label, color, ly, counts, total in series:
        pct = [c / total * 100 for c in counts]
        ax.step(edges + [3.0], pct + [pct[-1]], where="post", color=color,
                linewidth=1.8, solid_capstyle="round", zorder=2)
        ax.fill_between(edges + [3.0], pct + [pct[-1]], step="post",
                        color=color, alpha=0.10, linewidth=0, zorder=1)
        band = sum(counts[9:11]) / total * 100   # the two bins straddling 1.0 s
        ax.annotate(label.format(band), (1.2, ly), ha="left", va="center",
                    fontsize=9.5, color=color)

    ax.annotate("--per-domain-delay 1.0 秒", (1.0, 25.4), ha="center", va="bottom",
                fontsize=9, color=MUTED)
    style(ax, "佔同網域連續完成對的比例（%）")
    ax.set_xlabel("同一網域連續兩次完成的間隔（秒）")
    ax.set_title("限速器確實在咬：尖峰落在 1 秒的間隔上，不是落在 0",
                 color=INK, pad=14, loc="left")
    ax.set_xlim(0, 3)
    ax.set_ylim(0, 27)
    ax.set_xticks([0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
    fig.tight_layout()
    fig.savefig(OUT / "same-domain-gap-48h.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    fig_throughput()
    fig_concurrency()
    fig_worker_survival()
    fig_worker_loop()
    fig_same_domain_gap()
    status, reason, latencies = scan_results()
    fig_latency_cdf(latencies)
    fig_outcomes(status, reason)
    print("wrote figures to", OUT)
