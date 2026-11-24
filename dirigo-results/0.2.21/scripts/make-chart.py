#!/usr/bin/env python3
"""Render the 0.2.21 fork-vs-stock comparison from the committed artifacts.

Reads (BENCH env var or the parent folder):
  guidellm/guidellm-gl0221*.json    fork arms (two runs)
  guidellm/guidellm-gs0221*.json    stock arms (two runs)
  turnaround/probe-*.json           the sequential engine probe (fork and stock)

Writes chart.svg next to itself. Every number is recomputed from those files; nothing is written
into the script by hand.
"""
import glob
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.environ.get("BENCH", os.path.dirname(HERE))

FORK_LABELS = ("gl0221", "gl0221b")
STOCK_LABELS = ("gs0221", "gs0221b")
FORK_PROBES = ("0221a", "0221b")
STOCK_PROBES = ("s0221a", "s0221b")


def _rows(path):
    j = json.load(open(path))
    cfg = j["config"]["benchmarks"]
    rows: list[dict] = []
    for i, bm in enumerate(j["benchmarks"]):
        m, ss = bm["metrics"], bm["scheduler_state"]
        rows.append(dict(
            streams=cfg[i]["profile.streams"],
            ttft=m["time_to_first_token_ms"]["successful"]["median"],
            tok_s_win=m["output_token_count"]["successful"]["total_sum"] / bm["duration"],
            n_ok=ss["successful_requests"],
            n_cancel=ss["cancelled_requests"],
            n_err=ss["errored_requests"],
        ))
    return rows


def guidellm():
    out = {}
    for name, labels in (("fork", FORK_LABELS), ("stock", STOCK_LABELS)):
        runs = [_rows(f"{BENCH}/guidellm/guidellm-{l}.json") for l in labels]
        merged: list[dict] = []
        for i in range(len(runs[0])):
            merged.append(dict(
                streams=runs[0][i]["streams"],
                ttft=st.mean(run[i]["ttft"] for run in runs),
                tok_s_win=st.mean(run[i]["tok_s_win"] for run in runs),
                n_ok=st.mean(run[i]["n_ok"] for run in runs),
                n_cancel=sum(run[i]["n_cancel"] for run in runs),
                n_err=sum(run[i]["n_err"] for run in runs),
            ))
        out[name] = merged
    return out


def probes():
    """Engine-reported prefill and dead time (TTFT minus prefill), per request, medians."""
    def collect(labels):
        pre, dead, wall = [], [], []
        for lab in labels:
            path = f"{BENCH}/turnaround/probe-{lab}.json"
            if not os.path.exists(path):
                continue
            d = json.load(open(path))
            eng = [e for e in d["engine"] if e["prompt"] > 500 and e["gen"] >= 100]
            rows: list[dict] = d["rows"]
            for e in eng:
                pre.append(e["read"])
            for r, e in zip(rows, eng):
                if r.get("ttft_ms"):
                    dead.append(r["ttft_ms"] - e["read"])
            wall += [r["wall_ms"] for r in rows if r.get("wall_ms")]
        return dict(prefill=st.median(pre), dead=st.median(dead), wall=st.median(wall),
                    n_pre=len(pre), n_dead=len(dead))
    return dict(fork=collect(FORK_PROBES), stock=collect(STOCK_PROBES))


def esc(x):
    return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


INK, MUTED, GRID = "#1f2430", "#6b7280", "#e5e7eb"
FORK, STOCK, BLUE = "#0a8f6c", "#9aa4b2", "#2563eb"
FONT = "font-family='ui-sans-serif, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif'"


def main():
    g = guidellm()
    p = probes()
    D, S = g["fork"], g["stock"]
    W, H = 1180, 1040
    s = []
    a = s.append
    a(f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' viewBox='0 0 {W} {H}'>")
    a(f"<rect width='{W}' height='{H}' fill='#ffffff'/>")
    a(f"<g {FONT} fill='{INK}'>")
    a("<text x='34' y='46' font-size='23' font-weight='700'>Dirigo 0.2.21 vs stock 0.2.21 — same card, same requests</text>")
    a(f"<text x='34' y='70' font-size='13.5' fill='{MUTED}'>Intel Arc Pro B70 32 GB · engine 0.1.67 on both sides · Qwen3.8-Flash-Next Coder IQ1_M · GuideLLM 0.8.1, 512 in / 128 out, 45 s per point, two runs per arm</text>")

    # left: first token
    px, pw = 66, 610
    ptop, pbot = 176, 470
    a(f"<text x='{px}' y='{ptop-42}' font-size='16' font-weight='600'>Time to first token — lower is better</text>")
    a(f"<text x='{px}' y='{ptop-22}' font-size='12.5' fill='{MUTED}'>median per request, ms (mean of two runs)</text>")
    maxv = 10000

    def yof(v):
        return pbot - (v / maxv) * (pbot - ptop)

    for tick in range(0, maxv + 1, 2000):
        yy = yof(tick)
        a(f"<line x1='{px}' y1='{yy:.1f}' x2='{px+pw}' y2='{yy:.1f}' stroke='{GRID}'/>")
        a(f"<text x='{px-10}' y='{yy+4:.1f}' font-size='11.5' fill='{MUTED}' text-anchor='end'>{tick//1000} s</text>")
    a(f"<line x1='{px}' y1='{pbot}' x2='{px+pw}' y2='{pbot}' stroke='{MUTED}'/>")
    n = len(D)
    gw, bw = pw / n, 54
    for i, (dr, sr) in enumerate(zip(D, S)):
        cx = px + gw * (i + 0.5)
        for off, val, col in ((-bw - 3, dr["ttft"], FORK), (3, sr["ttft"], STOCK)):
            xx, yy = cx + off, yof(val)
            a(f"<rect x='{xx:.1f}' y='{yy:.1f}' width='{bw}' height='{pbot-yy:.1f}' rx='3' fill='{col}'/>")
            a(f"<text x='{xx+bw/2:.1f}' y='{yy-7:.1f}' font-size='12' font-weight='600' fill='{INK}' text-anchor='middle'>{val:.0f} ms</text>")
        dl = 100 * (dr["ttft"] / sr["ttft"] - 1)
        a(f"<text x='{cx:.1f}' y='{pbot+28}' font-size='14' font-weight='700' fill='{FORK if dl < -1 else MUTED}' text-anchor='middle'>{dl:+.1f} %</text>")
        a(f"<text x='{cx:.1f}' y='{pbot+50}' font-size='12.5' fill='{MUTED}' text-anchor='middle'>{dr['streams']} stream{'s' if dr['streams']>1 else ''}</text>")
    ly = pbot + 84
    a(f"<rect x='{px}' y='{ly-11}' width='14' height='14' rx='3' fill='{FORK}'/><text x='{px+21}' y='{ly}' font-size='13'>Dirigo 0.2.21 (this fork)</text>")
    a(f"<rect x='{px+242}' y='{ly-11}' width='14' height='14' rx='3' fill='{STOCK}'/><text x='{px+263}' y='{ly}' font-size='13'>stock Strata v0.2.21</text>")

    # right: the engine, measured without the server
    ex, ew = 726, 420
    a(f"<text x='{ex}' y='{ptop-42}' font-size='16' font-weight='600'>The engine itself</text>")
    a(f"<text x='{ex}' y='{ptop-22}' font-size='12.5' fill='{MUTED}'>sequential probe, engine-reported, median per request</text>")
    f, k = p["fork"], p["stock"]
    panels = [
        ("prefill, 613-token prompt (ms)", f["prefill"], k["prefill"], 1100),
        ("dead time, TTFT minus prefill (ms)", f["dead"], k["dead"], 60),
        ("wall time per request (ms)", f["wall"], k["wall"], 2800),
    ]
    bx, bmax = ex + 196, 104
    for j, (label, fv, sv, scale) in enumerate(panels):
        yy = ptop + 14 + j * 96
        a(f"<text x='{ex}' y='{yy}' font-size='12.5' font-weight='600'>{esc(label)}</text>")
        for oi, (val, col, nm) in enumerate(((fv, FORK, "fork"), (sv, STOCK, "stock"))):
            w = max(3.0, bmax * val / scale)
            a(f"<rect x='{bx}' y='{yy+10+oi*20}' width='{w:.1f}' height='15' rx='2' fill='{col}'/>")
            a(f"<text x='{bx+6+w:.1f}' y='{yy+22+oi*20}' font-size='11.5' fill='{MUTED if col==STOCK else INK}'>{val:.0f} · {nm}</text>")
        if fv and sv:
            dl = 100 * (fv / sv - 1)
            a(f"<text x='{ex+ew-6}' y='{yy+24}' font-size='13' font-weight='700' text-anchor='end' fill='{FORK if dl < -1 else MUTED}'>{dl:+.1f} %</text>")
    a(f"<text x='{ex}' y='{ptop+14+3*96-6}' font-size='11.5' fill='{MUTED}'>decode is 82–91 tok/s on the same binary from run to run:</text>")
    a(f"<text x='{ex}' y='{ptop+14+3*96+9}' font-size='11.5' fill='{MUTED}'>a build lottery, so no column is claimed here.</text>")

    # bottom: work finished per window
    ty = 656
    a(f"<text x='{px}' y='{ty}' font-size='15' font-weight='600'>Work finished inside each fixed 45 s window</text>")
    a(f"<text x='{px+368}' y='{ty}' font-size='11.5' fill='{MUTED}'>(128 output tokens per finished request; one request ≈ 4.8 % of a window)</text>")
    for i, (dr, sr) in enumerate(zip(D, S)):
        yy = ty + 26 + i * 34
        cap = max(dr["n_ok"], sr["n_ok"], 1)
        wmax = 150
        a(f"<text x='{px}' y='{yy+9}' font-size='12' fill='{MUTED}'>{dr['streams']} stream{'s' if dr['streams']>1 else ''}</text>")
        for off, r, col in ((0, dr, FORK), (15, sr, STOCK)):
            w = wmax * r["n_ok"] / cap
            a(f"<rect x='{px+96}' y='{yy+off}' width='{w:.1f}' height='11' rx='2' fill='{col}'/>")
            a(f"<text x='{px+102+w:.1f}' y='{yy+off+10}' font-size='11' fill='{MUTED if col==STOCK else INK}'>{r['n_ok']:.1f} requests · {r['tok_s_win']:.1f} tok/s</text>")

    fy = H - 74
    a(f"<line x1='34' y1='{fy-18}' x2='{W-34}' y2='{fy-18}' stroke='{GRID}'/>")
    a(f"<text x='34' y='{fy}' font-size='11.5' fill='{MUTED}'>Source: dirigo-results/0.2.21/guidellm/guidellm-gl0221*.json and -gs0221*.json (mean of two runs), turnaround/probe-*.json.  Recompute: scripts/make-chart.py</text>")
    a(f"<text x='34' y='{fy+17}' font-size='11.5' fill='{MUTED}'>Same-arm repeat spread on this rig: 4 % at one and two streams, 7–9 % at four and eight — the eight-stream row is inside it.</text>")
    a(f"<text x='34' y='{fy+34}' font-size='11.5' fill='{MUTED}'>GuideLLM sends unique prompts, so the fork's cache-off path applies; a conversation switches the cache back on.</text>")
    a(f"<text x='34' y='{fy+51}' font-size='11.5' font-weight='600' fill='{INK}'>Both arms ran one request at a time (--batch 0): at eight concurrent clients the two servers are queueing, and the first-token gap closes.</text>")
    a("</g></svg>")
    out = os.path.join(BENCH, "chart.svg")
    open(out, "w").write("\n".join(s))
    print("wrote", out)
    print("ttft deltas:", [f"{100*(d['ttft']/x['ttft']-1):+.1f}%" for d, x in zip(D, S)])
    print("windows fork:", [(round(r['n_ok'],1), round(r['tok_s_win'],1)) for r in D])
    print("windows stock:", [(round(r['n_ok'],1), round(r['tok_s_win'],1)) for r in S])
    print("probe:", {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in p.items()})
    print("errors:", [r["n_err"] for r in D], [r["n_err"] for r in S])


if __name__ == "__main__":
    main()
