# Strata — Dirigo Agents' verified build

**Strata is a local AI engine.** It runs a very large language model on one ordinary PC by sharing the work
between the graphics card, the CPU, the RAM and the SSD — no account, no cloud. It speaks the OpenAI and
Anthropic APIs on `localhost`, so the coding tools you already use can talk to it.

This repository is **Dirigo Agents' fork** of [Niko1221/Strata](https://github.com/Niko1221/Strata) (MIT).
It is **source only**: no installers, no engine binaries, no model files. Upstream's own README is preserved at
[`docs/UPSTREAM-README.md`](docs/UPSTREAM-README.md).

<p align="center">
  <img src="docs/media/app-chat.png" width="820" alt="The Strata desktop app: a chat, the files it is editing, and a live preview in one window">
  <br><sub>Strata running a 24,576-expert model locally, on one graphics card.</sub>
</p>

The default branch, `dirigo/0.2.12-verified`, is one snapshot: upstream tag **v0.2.12** (commit `146df3c4`,
engine **0.1.58**) plus this fork's changes, in the state that served a live machine on 2026-11-08 — with the
measurements behind it kept in [`dirigo-results/`](dirigo-results/). What was verified, and how:
[`DIRIGO.md`](DIRIGO.md).

## Why it is fast

Stock Strata v0.2.13 and this fork run the **same engine version (0.1.59)**, so the engine's speed is upstream's —
Niko1221's — and identical on both sides. Three decisions make that engine fast:

- **Only the experts a word needs are computed.** Qwen3.8-Flash-Next is 24,576 small specialists and a word
  needs about ten. The busiest live on the card (each free GB holds roughly 700 more), every expert stays in RAM, and the
  CPU computes the ones the card does not hold *at the same time* as the card works — neither waits.
- **Long prompts are read in big pieces** (up to 8,192 tokens), which is why a long document is read at over
  1,000 tokens a second, and a follow-up reuses the context already read instead of starting over.
- **Guess, then check.** The model's built-in draft layer guesses up to three tokens and the model verifies all
  of them in one pass (2.4–3.2 tokens per pass), so an answer arrives 1.6–1.8× sooner with the same text.

**All three are stock behaviour.** The expert split, the prompt-chunk reads and guess-then-check (the MTP draft
layer) run the same on both sides. The comparison below starts both arms with `--spec 6 --spec-min-p 0.5` and neither
sends a per-request override, so drafting is not part of the gain.

What the fork adds is **control over the drafter, not a different drafter**:

| setting | stock v0.2.13 | this fork |
| --- | --- | --- |
| draft depth | `--spec` at startup, for every request | also `spec=N` per request, clamped by the engine to 2…the startup value |
| prompt lookup | `--suffix-draft` at startup | also `lookup=0\|1` per request |
| draft confidence | `spec_min_p` per request via `strata_tune` | the same, and changeable globally on a live server |
| window policy | — | `spec_window`: serve some traffic at a lower depth (0 in the measurement) |

None of it was switched on for the numbers below: `spec_window` was 0, and GuideLLM sends no `strata_tune`. The
fork's measured difference is on the **server** side, which is the next section.

On the card this branch was verified on (Arc Pro B70, Qwen3.8-Flash-Next Coder IQ1_M) that comes out at **1,404 tok/s**
reading a 4K prompt and 1,773 at 16K, **80.8 and 68.1 tok/s** writing, first token after 2.94 s and 9.27 s —
[those measurements](DIRIGO.md#speed-against-stock-measured-with-guidellm-the-standard-tool). How each step of the
engine's speed was won is the [history table in `docs/DETAILS.md`](docs/DETAILS.md); it is not repeated here.

### Against stock v0.2.13, on the same card

Same card, same engine version (0.1.59), same model and pack, same server config, same request stream. GuideLLM
0.8.1, 512 tokens in / 128 out, one pass per arm.

**The short version.** The first token arrives **11–22 % sooner** on a stream of fresh prompts — 739 ms against
stock's 949 ms at one stream — and at four streams the same 45-second window finishes **three more answers**. Nothing
is traded for it: prefill and decode are within 1.1 % of stock on all eight engine-level legs, and the gain is a
server-side one — the engine measures the same on both sides.

<p align="center">
  <img src="dirigo-results/0.2.13-port/chart.svg" width="1000" alt="Time to first token, Dirigo 0.2.13 vs stock 0.2.13 on an Intel Arc Pro B70: 739 vs 949 ms at one stream, 2622 vs 3163 at two, 6688 vs 7500 at four, 9641 vs 9659 at eight. Engine-level prefill and decode are within 1.1 percent on all eight legs.">
</p>

| streams | first token, this fork | stock v0.2.13 | arrives sooner by | read the other way: stock needs |
| --- | ---: | ---: | ---: | ---: |
| 1 | **739 ms** | 949 ms | **210 ms (22.1 %)** | 28.4 % longer |
| 2 | **2,622 ms** | 3,163 ms | **541 ms (17.1 %)** | 20.6 % longer |
| 4 | **6,688 ms** | 7,500 ms | **812 ms (10.8 %)** | 12.1 % longer |
| 8 | 9,641 ms | 9,659 ms | 18 ms — equal | equal |

The last two columns are one measurement read two ways: 739 ms is 22.1 % below 949 ms, and 949 ms is 28.4 % above
739 ms. "Arrives sooner" is the column to quote. In the same 45-second window the fork finishes 22 / 22 / 22 / 23
requests against 21 / 20 / 19 / 21 — 62.6 / 62.6 / 62.6 / 65.4 output tokens a second against 59.7 / 56.9 / 54.0 / 59.7.

**Why the gap exists.** A fresh prompt is the one case the two servers handle differently. Stock opens the prompt
cache and pays the pool work before it can emit a token. The fork's router recognises a run of unique prompts as
one-shot traffic and serves it with the cache **off** — no pool work between the request and the first token — then
switches the cache back on the moment the traffic looks like a conversation. That is why the advantage is largest
with requests arriving one at a time, and gone at eight streams where both servers are queue-bound.

**The same config also changes answers, not just timing.** Asked for a date inside a 24-token budget ("Invoice
INV-7 dated 9 June 2024 → ISO date"), stock spends all 24 tokens on reasoning and returns an empty string; the fork
returns `2024-06-09` with zero reasoning characters. The port's suite is green — 1,537 tests, 17 skipped — where the
stock tree has one failure (`test_hip_ordinal`, a test that reads the host's AMD topology; fixed upstream in
[#5602](https://github.com/Niko1221/Strata/pull/5602)).

**What this does not claim.** A faster engine — it is the same speed, and this table would look identical for any
server-side change. A win on conversational traffic, where the fork turns the cache back on and behaves like stock.
A throughput difference at one stream: one extra request in a 45-second window is the measurement's own granularity
(4.8 %), and it is reported rather than counted as a win.

> **Measured on:** Intel Arc Pro B70 32 GB, Ryzen 7 7700X, Qwen3.8-Flash-Next Coder IQ1_M, engine 0.1.59 on both
> sides, 2026-11-09. One pass per arm in one session; positional drift in this rig is 2–4 %, so the 10.8–22.1 %
> first-token gaps sit outside it. Errors: 0 in both arms. Full report, raw JSON and the script that recomputes
> every figure: [`dirigo-results/0.2.13-port/`](dirigo-results/0.2.13-port/).

> **Never used Strata before?** Start with [Getting started](docs/GETTING-STARTED.md) — it walks from "what is
> this" to your first answer, and explains every word Strata uses in [plain language](docs/GLOSSARY.md).
>
> **Just want to run Strata, not build it?** Use the stock release instead — one line in the terminal and no compiler:
> [Download the release](https://github.com/Niko1221/Strata/releases/latest) · [install guide](docs/INSTALL.md).

## Start here

| I want to… | Go to | Time |
| --- | --- | --- |
| Run Strata on my own PC | [Install (stock release)](docs/INSTALL.md) — installer, or one line in the terminal | 10 min + model download |
| Reproduce the build this fork verified | [Quick start](#quick-start-build-this-snapshot) below · [evidence](DIRIGO.md) | 30–60 min of compiling |
| Connect Claude Code, Codex, OpenCode, Hermes… | `strata connect`, or [tool connections](docs/TERMINAL_LAUNCH.md#connect-your-apps-strata-connect) | 5 min |
| Check my hardware before downloading anything | [What you need](#what-you-need) · [which model fits](docs/MODELS.md#pick-by-memory) | 5 min |
| Understand the engine | [How it works](docs/HOW_IT_WORKS.md) · [technical reference](docs/DETAILS.md) | — |
| Read the evidence for the numbers | [`DIRIGO.md`](DIRIGO.md) · [`dirigo-results/`](dirigo-results/) | — |
| See how it compares to stock 0.2.13 | [Measured side by side](#against-stock-v0213-on-the-same-card) | — |
| All guides, page by page | [Documentation index](docs/README.md) | — |

## What you need

Strata is not a small-model tool. The smallest recommended Qwen3.8-Flash-Next pack needs about **37.6 GB of
memory (RAM plus VRAM)**; the coding-only pack runs in about 32 GB. Unified memory (Apple Silicon, Strix Halo)
counts once. Check [which model fits](docs/MODELS.md#pick-by-memory) before you download tens of gigabytes.

| | Requirement |
| --- | --- |
| Graphics card | NVIDIA RTX 20/30/40/50 · AMD Radeon RX 7000/9000 and AI PRO R9700 (RX 6000 experimental) · Intel Arc on Linux · Apple Silicon |
| Memory | 38–55 GB of RAM + VRAM for the Qwen3.8-Flash-Next packs (37.6 GB for the smallest), with room left for the OS; 46–64 GB of system RAM plus a 12 GB card for DeepSeek V4.1-Flash |
| Disk | SSD: **66–84 GB** for Qwen3.8-Flash-Next — the pack plus the model's 28.8 GB lookup table — and 239–274 GB for DeepSeek V4.1-Flash |
| To build from source | Python 3, CMake 3.24+, Ninja, a C++20 compiler, and your GPU's toolkit (CUDA, ROCm, oneAPI or Xcode) |

The 28.8 GB in that disk figure is the model's lookup table, not the engine's: it is the publisher's second file,
read from the SSD a few rows at a time instead of being held in memory. It belongs to every pack of this model, stock
or fork alike — [what it is and where the size comes from](docs/GLOSSARY.md#the-model).

The card-specific details are in [NVIDIA](docs/INSTALL.md#what-you-need), [AMD](docs/AMD_HIP.md),
[Intel Arc](docs/INTEL_ARC.md), [Mac](docs/MAC.md), [multiple GPUs](docs/MULTI_GPU.md).

## Quick start: build this snapshot

This is the build the branch was verified with: Intel Arc, SYCL AOT `bmg-g31`, Intel oneAPI 2026.2. AMD and
NVIDIA build from the same sources with their own guides below.

```sh
# 1. source (the default branch is the verified snapshot)
git clone --branch dirigo/0.2.12-verified https://github.com/rwkeyes/Strata.git
cd Strata

# 2. Python side
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# 3. build — needs oneAPI's icpx and oneMKL on PATH: source /opt/intel/oneapi/setvars.sh
cmake -S sycl -B build-sycl-aot -DSTRATA_SYCL_AOT=bmg-g31
cmake --build build-sycl-aot --target strata -j"$(nproc)"

# 4. prove what you built before going further
./build-sycl-aot/strata --version          # must print: strata 0.1.58
```

The engine reads a **pack** (a prepared model folder) plus the model's GGUF shards. Build the pack from the
GGUF files you downloaded, once per model:

```sh
python tools/iq_pack.py \
  --gguf /path/to/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf \
  --out packs/iq3xxs
```

Then serve. These are the settings the measured service ran with — replace the paths, and keep
`data/expert-profile*.bin` where the binary can find it:

```sh
./build-sycl-aot/strata --serve \
  --pack packs/iq3xxs --native <shard-1.gguf> --ple-gguf <shard-2.gguf> \
  --expert-profile data/expert-profile.bin --expert-cache auto --stream-experts --prefill auto \
  --prompt-cache 6 --prompt-cache-every 4096 --batch 0 --spec 6 --spec-min-p 0.5 \
  --mtp <draft> --mtp-draft-vocab data/draft_vocab_en.bin --mtp-window 8192 \
  --max-context 131072 --kv int8 --kv-resident 32768 --vram-reserve-mib 700
```

`--serve` is the engine's own token protocol. For a browser or an OpenAI-compatible API, run the bundled
server with those arguments in its config (`args`), and check it answers:

```sh
.venv/bin/python -m serve.server --engine strata --config strata.json --port 8080
curl -s http://127.0.0.1:8080/v1/models
```

Other builds: [AMD / HIP](docs/AMD_HIP.md#build) · [NVIDIA / CUDA](docs/SOURCE_INSTALL.md#build-from-git-on-linux-to-try-a-pull-request-or-change-the-code) ·
[Mac / Metal](docs/MAC.md) · [the guided `./setup.sh` route](docs/SOURCE_INSTALL.md).

## Quick start: stock release

If you want Strata running rather than built, the released app and terminal do the engine choice, download
and model preparation for you:

```sh
# Linux / Mac
curl -fsSL https://github.com/Niko1221/Strata/releases/latest/download/install.sh -o install.sh && sh install.sh
strata launch
```

```powershell
# Windows PowerShell, then open a new terminal
irm https://github.com/Niko1221/Strata/releases/latest/download/install.ps1 | iex
```

`strata launch --plan` prints what would be downloaded and how much memory it needs, and changes nothing.
[Full install guide](docs/INSTALL.md) · [Terminal launch](docs/TERMINAL_LAUNCH.md).

## Words you will meet

| Word | Plain meaning |
| --- | --- |
| pack | a prepared model folder the engine reads |
| shard | one part of a model file split across several downloads |
| quantization (Q2_0, IQ3_XXS, IQ1_M) | how much the model was compressed — smaller is faster and lighter, larger is better |
| expert | one small specialist network inside the model; only a few are needed per word |
| PLE table | the big lookup table Strata reads from your SSD instead of holding in memory |
| KV cache | what the model remembers about the conversation; costs memory per token of context |
| slot | one conversation being served at a time (the engine serves one at a time by design) |
| prefill / decode | reading your prompt / writing the answer |
| TTFT | time to first token — how long before the answer starts |
| spec / MTP | the helper that guesses the next words so the big model can check several at once |
| TUNE | the fork's channel for changing engine settings between requests, without a restart |

Full list, including the fork-specific terms: [Glossary](docs/GLOSSARY.md).

## If your first run fails

| Symptom | What to do |
| --- | --- |
| `strata` is not found | Open a new terminal; on Linux/Mac add `~/.local/bin` to `PATH` |
| "Couldn't find an engine for this computer" | `strata update`, then `strata doctor --live` ([Troubleshooting](docs/TROUBLESHOOTING.md)) |
| Build stops at `icpx: not found` | `source /opt/intel/oneapi/setvars.sh` before `cmake` |
| Engine exits before it is ready, after a source build | The expert profile is missing — `data/expert-profile.bin` must be beside the binary's working directory |
| It used the wrong graphics card | `strata-device --list-devices`; pick one with `--device` or `ROCR_VISIBLE_DEVICES` / `CUDA_VISIBLE_DEVICES` |
| Loads, then runs out of memory | Smaller pack, lower `--max-context`, or lower `--kv-resident` |
| Very slow first answer | First start warms the file cache; measure the second request |

More: [Troubleshooting](docs/TROUBLESHOOTING.md) · [engine errors](docs/DETAILS.md#troubleshooting).

## What this fork changes

The engine, kernels and model format are upstream's. This fork adds server-level behaviour and one engine
change; with none of them enabled the server behaves as stock.

| Change | What it does |
| --- | --- |
| conversation-cache routing | turns the prompt cache off for runs of single-shot prompts, back on for a conversation |
| thinking policy | skips the scratchpad for short, factual asks; keeps it when the answer needs work |
| retune between requests | change engine settings on a live server — no restart, no reload |
| per-request draft depth | a client can set its own speculation depth for one request |

### What it is worth, measured

The numbers are above, in [Against stock v0.2.13](#against-stock-v0213-on-the-same-card): first token 11–22 % sooner
at one to four streams, engine at parity, and short requests answered that stock truncates. Full report, raw JSON,
the chart and the script that recomputes every figure in it:
[`dirigo-results/0.2.13-port/`](dirigo-results/0.2.13-port/).

The parts that belong upstream are open as separate branches listed in [`DIRIGO.md`](DIRIGO.md).

## Repository map

| Path | What lives there |
| --- | --- |
| `sycl/` | the Intel Arc build of the engine |
| `src/`, `include/` | the engine core (CUDA, and the shared C++ sources) |
| `metal/` | the Apple Silicon backend |
| `serve/` | the OpenAI / Anthropic compatible server, model preparation, monitoring |
| `strata_cli/`, `strata_control/` | the `strata` terminal command |
| `apps/` | the desktop app |
| `tools/` | pack building, calibration and measurement tools |
| `bench/` | benchmark protocol and community results |
| `docs/` | all guides — [index](docs/README.md) |
| `dirigo-results/` | this fork's measurements: the 0.2.12 evidence, the 0.2.13 fork-vs-stock report and chart, the carry patch |

## Licence

MIT — upstream Strata plus the Dirigo changes; see [`LICENSE`](LICENSE). Model files are not part of this
repository and carry their own licences.

The translated READMEs (`README.de.md`, `README.es.md`, `README.fr.md`, `README.ja.md`, `README.pl.md`,
`README.pt-BR.md`, `README.zh-CN.md`) are upstream's and describe **stock** Strata, not this fork.
