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
| All guides, page by page | [Documentation index](docs/README.md) | — |

## What you need

Strata is not a small-model tool. The smallest recommended Qwen3.8-Flash-Next pack needs about **37.6 GB of
memory (RAM plus VRAM)**; the coding-only pack runs in about 32 GB. Unified memory (Apple Silicon, Strix Halo)
counts once. Check [which model fits](docs/MODELS.md#pick-by-memory) before you download tens of gigabytes.

| | Requirement |
| --- | --- |
| Graphics card | NVIDIA RTX 20/30/40/50 · AMD Radeon RX 7000/9000 and AI PRO R9700 (RX 6000 experimental) · Intel Arc on Linux · Apple Silicon |
| Memory | ~32–55 GB for Qwen3.8-Flash-Next; 46–64 GB RAM plus a 12 GB card for DeepSeek V4.1-Flash |
| Disk | SSD with room for the model — roughly 40 GB for Qwen3.8-Flash-Next, 239–274 GB for DeepSeek V4.1-Flash |
| To build from source | Python 3, CMake 3.24+, Ninja, a C++20 compiler, and your GPU's toolkit (CUDA, ROCm, oneAPI or Xcode) |

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

Measured effect, limits and the full list: [`DIRIGO.md`](DIRIGO.md). The parts that belong upstream are open
as separate branches listed there.

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
| `dirigo-results/` | this fork's measurements, benchmark results and the carry patch |

## Licence

MIT — upstream Strata plus the Dirigo changes; see [`LICENSE`](LICENSE). Model files are not part of this
repository and carry their own licences.

The translated READMEs (`README.de.md`, `README.es.md`, `README.fr.md`, `README.ja.md`, `README.pl.md`,
`README.pt-BR.md`, `README.zh-CN.md`) are upstream's and describe **stock** Strata, not this fork.
