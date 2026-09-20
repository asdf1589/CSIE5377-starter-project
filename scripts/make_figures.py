"""
Render the figures used by docs/02-48h-run-results.md into docs/rsc/.

All series are restricted to the 48-hour observation window:
  - throughput-hourly.csv rows with in_window == true
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


def fig_throughput():
    rows = [r for r in csv.DictReader(open(ROOT / "results/throughput-hourly.csv"))
            if r["in_window"] == "true"]
    h = [int(r["hour"]) for r in rows]
    f = [int(r["fetches"]) for r in rows]
    peak = max(range(len(f)), key=lambda i: f[i])

    fig, ax = plt.subplots(figsize=(8.4, 4.0))
    ax.plot(h, f, color=BLUE, linewidth=2, solid_capstyle="round")
    ax.fill_between(h, f, color=BLUE, alpha=0.08, linewidth=0)

    # Direct-label the two points the text actually cites; the axis carries the rest.
    for i, va, dy in ((peak, "bottom", 2200), (len(f) - 1, "bottom", 2200)):
        ax.plot(h[i], f[i], "o", color=BLUE, markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        ax.annotate(f"第 {h[i]} 小時\n{f[i]:,}", (h[i], f[i] + dy),
                    ha="center", va=va, fontsize=9, color=INK2, linespacing=1.4)

    style(ax, "每小時抓取次數")
    ax.set_xlabel("運行時數（小時）")
    ax.set_title("48 小時視窗內每小時抓取次數：高峰後單調衰減約 9.2 倍",
                 color=INK, pad=14, loc="left")
    ax.set_xlim(0, 51.5)
    ax.set_ylim(0, 78000)
    ax.set_xticks([1, 6, 12, 18, 24, 30, 36, 42, 48])
    ax.yaxis.set_major_formatter(THOUSANDS)
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


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    fig_throughput()
    fig_concurrency()
    status, reason, latencies = scan_results()
    fig_latency_cdf(latencies)
    fig_outcomes(status, reason)
    print("wrote figures to", OUT)
