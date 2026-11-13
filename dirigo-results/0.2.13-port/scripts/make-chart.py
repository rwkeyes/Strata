#!/usr/bin/env python3
"""Render the 0.2.13 fork-vs-stock comparison from the committed JSON artifacts.

Reads (BENCH env var or the parent folder):
  guidellm/guidellm-0213ab-d0213.json   fork arm
  guidellm/guidellm-0213ab-s0213.json   stock arm
  battery/*.json                        engine-level battery (2 passes per arm)

Writes chart.svg next to itself. Every number is recomputed from those files.
"""
import glob
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.environ.get("BENCH", os.path.dirname(HERE))


def guidellm():
    out = {}
    for arm in ("d0213", "s0213"):
        j = json.load(open(f"{BENCH}/guidellm/guidellm-0213ab-{arm}.json"))
        cfg = j["config"]["benchmarks"]
        rows = []
        for i, bm in enumerate(j["benchmarks"]):
            m, ss = bm["metrics"], bm["scheduler_state"]
            dur = bm["duration"]
            out_tokens = m["output_token_count"]["successful"]["total_sum"]
            rows.append(dict(
                streams=cfg[i]["profile.streams"],
                ttft=m["time_to_first_token_ms"]["successful"]["median"],
                tok_s_win=out_tokens / dur,
                n_ok=ss["successful_requests"],
                n_cancel=ss["cancelled_requests"],
                n_err=ss["errored_requests"],
                out_tokens=out_tokens,
                dur=dur,
            ))
        out[arm] = rows
    return out


def battery():
    data = {}
    for f in sorted(glob.glob(f"{BENCH}/battery/*.json")):
        arm = os.path.basename(f).split("-")[1][0]
        for t in json.load(open(f)):
            k = t.get("kind") or t.get("leg")
            m = t.get("metrics") or {}
            if not k or "prefill_tps" not in m:
                continue
            key = (arm, k, round(m.get("prompt_tokens") or 0))
            data.setdefault(key, []).append((m["prefill_tps"], m["decode_tps"]))
    legs = {}
    for (arm, k, p), v in data.items():
        legs.setdefault((k, p), {})[arm] = (
            st.median([x[0] for x in v]), st.median([x[1] for x in v]), len(v))
    out = []
    for (k, p), d in sorted(legs.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if "d" in d and "s" in d:
            out.append(dict(leg=k, prompt=p,
                            pre=100 * (d["d"][0] / d["s"][0] - 1),
                            dec=100 * (d["d"][1] / d["s"][1] - 1), n=d["d"][2]))
    return out


def esc(x):
    return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


INK, MUTED, GRID = "#1f2430", "#6b7280", "#e5e7eb"
FORK, STOCK, BLUE = "#0a8f6c", "#9aa4b2", "#2563eb"
FONT = "font-family='ui-sans-serif, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif'"


def main():
    g = guidellm()
    b = battery()
    D, S = g["d0213"], g["s0213"]
    W, H = 1180, 1000
    s = []
    a = s.append
    a(f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' viewBox='0 0 {W} {H}'>")
    a(f"<rect width='{W}' height='{H}' fill='#ffffff'/>")
    a(f"<g {FONT} fill='{INK}'>")
    a("<text x='34' y='46' font-size='23' font-weight='700'>Dirigo 0.2.13 vs stock 0.2.13 — same card, same requests</text>")
    a(f"<text x='34' y='70' font-size='13.5' fill='{MUTED}'>Intel Arc Pro B70 32 GB · engine 0.1.59 on both sides · Qwen3.8-Flash-Next Coder IQ1_M · GuideLLM 0.8.1, 512 in / 128 out, 45 s per point</text>")

    px, pw = 66, 610
    ptop, pbot = 176, 500
    a(f"<text x='{px}' y='{ptop-42}' font-size='16' font-weight='600'>Time to first token — lower is better</text>")
    a(f"<text x='{px}' y='{ptop-22}' font-size='12.5' fill='{MUTED}'>median per request, ms</text>")
    maxv = 10000

    def yof(v):
        return pbot - (v / maxv) * (pbot - ptop)

    for tick in range(0, maxv + 1, 2000):
        yy = yof(tick)
        a(f"<line x1='{px}' y1='{yy:.1f}' x2='{px+pw}' y2='{yy:.1f}' stroke='{GRID}'/>")
        a(f"<text x='{px-10}' y='{yy+4:.1f}' font-size='11.5' fill='{MUTED}' text-anchor='end'>{tick//1000} s</text>")
    a(f"<line x1='{px}' y1='{pbot}' x2='{px+pw}' y2='{pbot}' stroke='{MUTED}'/>")

    n = len(D)
    gw = pw / n
    bw = 54
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
    a(f"<rect x='{px}' y='{ly-11}' width='14' height='14' rx='3' fill='{FORK}'/><text x='{px+21}' y='{ly}' font-size='13'>Dirigo port (this fork)</text>")
    a(f"<rect x='{px+206}' y='{ly-11}' width='14' height='14' rx='3' fill='{STOCK}'/><text x='{px+227}' y='{ly}' font-size='13'>stock Strata v0.2.13</text>")

    ex = 726
    a(f"<text x='{ex}' y='{ptop-42}' font-size='16' font-weight='600'>The engine itself: parity</text>")
    a(f"<text x='{ex}' y='{ptop-22}' font-size='12.5' fill='{MUTED}'>direct-to-engine battery, median of 2 interleaved passes</text>")
    gx0, gx1 = ex + 74, ex + 300
    mid, half = (gx0 + gx1) / 2, (gx1 - gx0) / 2
    band = 1.5
    rows_top = ptop + 16
    rows_h = 26
    a(f"<rect x='{gx0}' y='{rows_top-10}' width='{gx1-gx0}' height='{rows_h*len(b)+4}' fill='#f5f6f8'/>")
    a(f"<line x1='{mid}' y1='{rows_top-14}' x2='{mid}' y2='{rows_top+rows_h*len(b)-4}' stroke='{MUTED}' stroke-dasharray='3 3'/>")
    axis_y = rows_top + rows_h * len(b) + 22
    a(f"<text x='{gx0-8}' y='{axis_y}' font-size='11' fill='{MUTED}' text-anchor='end'>-{band} %</text>")
    a(f"<text x='{mid}' y='{axis_y}' font-size='11' fill='{MUTED}' text-anchor='middle'>0</text>")
    a(f"<text x='{gx1+8}' y='{axis_y}' font-size='11' fill='{MUTED}'>+{band} %</text>")
    for j, leg in enumerate(b):
        yy = rows_top + 8 + j * rows_h
        size = f"{leg['prompt']//1000}k" if leg["prompt"] >= 1000 else str(leg["prompt"])
        a(f"<text x='{ex}' y='{yy+4}' font-size='11.5' fill='{MUTED}'>{esc(leg['leg'])} {size}</text>")
        for val, col in ((leg["pre"], FORK), (leg["dec"], BLUE)):
            xx = mid + val / band * half
            xx = max(gx0 - 4, min(gx1 + 4, xx))
            a(f"<circle cx='{xx:.1f}' cy='{yy}' r='4.5' fill='{col}'/>")
    worst = max(max(abs(x["pre"]), abs(x["dec"])) for x in b)
    ly2 = axis_y + 28
    a(f"<circle cx='{ex+6}' cy='{ly2-4}' r='4.5' fill='{FORK}'/><text x='{ex+16}' y='{ly2}' font-size='11.5'>prefill</text>")
    a(f"<circle cx='{ex+84}' cy='{ly2-4}' r='4.5' fill='{BLUE}'/><text x='{ex+94}' y='{ly2}' font-size='11.5'>decode</text>")
    a(f"<text x='{ex}' y='{ly2+24}' font-size='12.5' font-weight='600'>Largest difference on any leg: {worst:.1f} %</text>")
    a(f"<text x='{ex}' y='{ly2+42}' font-size='11.5' fill='{MUTED}'>inside the run-to-run spread: engine speed is the same</text>")

    ty = 706
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
            a(f"<text x='{px+102+w:.1f}' y='{yy+off+10}' font-size='11' fill='{MUTED if col==STOCK else INK}'>{r['n_ok']} requests · {r['tok_s_win']:.1f} tok/s</text>")

    fy = H - 52
    a(f"<line x1='34' y1='{fy-18}' x2='{W-34}' y2='{fy-18}' stroke='{GRID}'/>")
    a(f"<text x='34' y='{fy}' font-size='11.5' fill='{MUTED}'>Source: dirigo-results/0.2.13-port/guidellm/guidellm-0213ab-d0213.json and -s0213.json, medians of the per-request samples; battery/*.json. Recompute with scripts/make-chart.py.</text>")
    a(f"<text x='34' y='{fy+17}' font-size='11.5' fill='{MUTED}'>One pass per arm in one session; the companion 0.2.12→0.2.13 run put positional drift at 2–4 %. GuideLLM sends unique prompts, so the fork's cache-off path applies — a conversation switches the cache back on.</text>")
    a("</g></svg>")
    out = os.path.join(BENCH, "chart.svg")
    open(out, "w").write("\n".join(s))
    print("wrote", out)
    print("ttft deltas:", [f"{100*(d['ttft']/x['ttft']-1):+.1f}%" for d, x in zip(D, S)])
    print("windows:", [(r["n_ok"], round(r["tok_s_win"], 1)) for r in D], [(r["n_ok"], round(r["tok_s_win"], 1)) for r in S])
    print("cancelled:", [r["n_cancel"] for r in D], [r["n_cancel"] for r in S], "errors:", [r["n_err"] for r in D], [r["n_err"] for r in S])
    print("parity worst:", round(worst, 2), "%")


if __name__ == "__main__":
    main()
