# Getting started with Strata

This page assumes you have never run Strata and takes you to your first answer. It covers both routes:
the **released app / terminal** (recommended for a first install) and **this fork's source snapshot** (if you
want the verified tree itself). Every word Strata uses is explained in the [glossary](GLOSSARY.md).

Budget about an hour, most of it downloading a model over a normal connection. You will need roughly 40 GB of
free SSD space and 40 GB of memory for the smallest recommended model.

## 0. The thirty-second reality check

Strata runs a very large model by keeping the parts that do not fit in VRAM in RAM and on the SSD. That is why
it needs more memory than a normal local-AI setup:

| Model pack | Memory (RAM + VRAM) | Speed | Quality |
| --- | ---: | --- | --- |
| Qwen3.8-Flash-Next Q2_0 | 37.6 GB | fastest | good |
| Qwen3.8-Flash-Next IQ2_XS | 39.2 GB | fast | better |
| Coder IQ1_M (coding only) | ~32 GB | fast | good at code |
| Qwen3.8-Flash-Next IQ3_S | 54.8 GB | slowest | best |

> **Measured on:** the sizes above are upstream's estimates for model weights in RAM + VRAM, per
> [Models](MODELS.md#pick-by-memory); leave headroom for the OS and your other programs. Unified memory
> (Apple Silicon, Strix Halo) counts once.

If you have less memory than the first row, Strata can still stream the model from an SSD — it works and is much
slower. If you want a smaller model instead, see [Qwen3.6-35B-A3B](QWEN36.md) (32–52 GB) or the
[comparison page](MODELS.md).

Check your card is supported before downloading anything: [what you need](INSTALL.md#what-you-need) →
[AMD](AMD_HIP.md) · [Intel Arc](INTEL_ARC.md) · [Mac](MAC.md) · [older GPUs](OLDER_GPUS.md).

## 1. Pick a route

| | Released app / terminal | This fork's source snapshot |
| --- | --- | --- |
| Best for | running Strata, using it daily | reproducing Dirigo's verified build, testing a change |
| Needs a compiler | no | yes (oneAPI, CUDA, ROCm or Xcode) |
| Install time | ~10 min plus model download | 30–60 min of compiling plus model download |
| Gets you | `strata launch`, the desktop app, `strata connect` | `build-sycl-aot/strata` and the bundled server |
| Follow | [Route A](#route-a-released-strata-recommended) | [Route B](#route-b-this-snapshot-from-source) |

Either way the model files are the same, and both listen on `localhost` with an OpenAI-compatible API.

## Route A: released Strata (recommended)

### A1. Install the terminal app

Linux or Mac:

```sh
curl -fsSL https://github.com/Niko1221/Strata/releases/latest/download/install.sh -o install.sh && sh install.sh
```

Windows PowerShell (then open a **new** terminal):

```powershell
irm https://github.com/Niko1221/Strata/releases/latest/download/install.ps1 | iex
```

The installer prints where `strata` went (normally `~/.local/bin/strata`).

**Check:** `strata --version` prints a version. If the command is not found, use the full path the installer
printed and add that folder to your `PATH`.

If you would rather have a window than a terminal, install the desktop app instead — the
[install guide](INSTALL.md#the-app) has the one file per system. The app downloads the engine for your card on
first start.

### A2. Look before you download

```sh
strata launch --plan
```

`--plan` prints what would be downloaded, where, and how much memory the chosen model needs. It changes nothing.
To choose a model and provider explicitly:

```sh
strata launch --plan --family qwen --model iq2_xs
```

The recommended provider order is ISTA-DASLab, then Unsloth, then Gyro. The packs in the table above are the
`--model` values: `q2_0`, `iq2_xs`, `iq3_xxs`, `iq3_s` for Qwen3.8-Flash-Next, and the coding pack is its own
family — `strata launch --family coder --model IQ1_M`.

### A3. Start it

```sh
strata launch
```

Follow the screens: model, context length, storage folder, access settings. The monitor opens when the engine
is up. A model can take several minutes to load the first time; later loads reuse what the system has cached.

**Check:** the monitor shows a model name, a memory figure and a request counter that moves when you send a
prompt. `strata launch --status` in another terminal should say the model is running.

Prefer commands? `strata launch --yes --no-tui` accepts the shown choices and stays in the background.

### A4. Use it

- Chat in the terminal's own interface, or open the address it printed (default `http://127.0.0.1:8080`).
- Point a coding tool at it: `strata connect` prints the setup line for Claude Code, Codex, OpenCode, Hermes
  and others. [Connection guide](TERMINAL_LAUNCH.md#connect-your-apps-strata-connect).
- Stop it with `strata launch --stop`. Update with `strata update --check` and then `strata update`.

## Route B: this snapshot, from source

Use this route to reproduce the build in this repository. It is the Intel Arc SYCL build (oneAPI 2026.2, AOT
for `bmg-g31`, engine 0.1.58); for AMD, NVIDIA or Mac, use that backend's build commands and the same source.

### B1. Get the source and the Python side

```sh
git clone --branch dirigo/0.2.12-verified https://github.com/rwkeyes/Strata.git
cd Strata
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
```

### B2. Build

You need CMake 3.24+, Ninja, a C++20 compiler and Intel oneAPI (DPC++ `icpx` and oneMKL) on `PATH`:

```sh
source /opt/intel/oneapi/setvars.sh      # or wherever oneAPI is installed
cmake -S sycl -B build-sycl-aot -DSTRATA_SYCL_AOT=bmg-g31
cmake --build build-sycl-aot --target strata -j"$(nproc)"
```

`bmg-g31` is the Arc Pro B70. For a B580, B570 or Pro B60 use `bmg-g21`; on an A-series (Alchemist) card leave
`-DSTRATA_SYCL_AOT` out and the GPU code is compiled at first start instead. A build for the wrong card is a
build that stops at startup, not a slow run.

**Check:** the last line says it linked `strata`, and

```sh
./build-sycl-aot/strata --version        # strata 0.1.58
```

The version string is the point of this step. A successful build of some other tree is not evidence that you
have this one; the number is. Budget ~17 minutes of compiling on a 10-core machine for the SYCL AOT build.

Other backends: [AMD / HIP](AMD_HIP.md#build) · [NVIDIA / CUDA](SOURCE_INSTALL.md#build-from-git-on-linux-to-try-a-pull-request-or-change-the-code) ·
[Mac / Metal](MAC.md). The guided `./setup.sh` route builds, prepares the model and writes the run settings for
you — [source install](SOURCE_INSTALL.md).

### B3. Prepare the model

The engine reads a **pack**: a prepared folder with an index, the dense weights and the expert layout. Build it
once per model from the GGUF files from Hugging Face:

```sh
python tools/iq_pack.py \
  --gguf /path/to/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf \
  --out packs/iq3xxs
```

The pack is finished only when it contains `native_experts.txt`; a pack without it will not load. Keep the pack,
the tokenizer folder and the GGUF shards together.

### B4. Serve the engine

```sh
./build-sycl-aot/strata --serve \
  --pack packs/iq3xxs --native <shard-1.gguf> --ple-gguf <shard-2.gguf> \
  --expert-profile data/expert-profile.bin --expert-cache auto --stream-experts --prefill auto \
  --prompt-cache 6 --prompt-cache-every 4096 --batch 0 --spec 6 --spec-min-p 0.5 \
  --mtp <draft> --mtp-draft-vocab data/draft_vocab_en.bin --mtp-window 8192 \
  --max-context 131072 --kv int8 --kv-resident 32768 --vram-reserve-mib 700
```

These are the settings the verified service ran with; adapt the paths and the context to your card.
[DIRIGO.md](../DIRIGO.md) records what they produced. Start smaller if unsure — a 4K–8K `--max-context` with a
matching `--kv-resident` is a smoke test, not a model limit.

**Check:** the engine prints its load progress and then waits. If it exits immediately, read the message: a
missing `data/expert-profile.bin`, a pack without `native_experts.txt` and a card the binary was not compiled
for are the three usual causes.

### B5. Put the HTTP API in front of it

`--serve` is the engine's own token protocol. For a browser, a coding tool or `curl`, run the bundled server
with those arguments in its config (`args`) — a working example is in
[the local server example](ORCA.md#local-server):

```sh
.venv/bin/python -m serve.server --engine strata --config strata.json --port 8080
```

**Check:**

```sh
curl -s http://127.0.0.1:8080/v1/models
```

It returns the model name, and then a first answer:

```sh
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"Say hello in five words."}]}'
```

## Choose a model

- **Download sizes and what fits:** [Models](MODELS.md#pick-by-memory).
- **Providers:** ISTA-DASLab (original packs), Unsloth (4–8 bit), Gyro (least memory, slower), Swift 1.5,
  NVFP4, OrcaRouter.
- **Quantization names** (`Q2_0`, `IQ2_XS`, `IQ3_XXS`, `IQ1_M`): [explained in the glossary](GLOSSARY.md#the-model).
  Roughly: Q2_0 is the fastest, IQ3_S the most faithful, and `IQ1_M` is the coding-only pack for a small machine.
- **Adding or switching models:** [Models § adding](MODELS.md#adding-or-switching-models).

## Connect a coding tool

The server speaks OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages`). With the released terminal,
`strata connect` prints ready-made settings for Claude Code, Codex CLI, OpenCode, pi.dev, Hermes Agent,
OpenClaw and others — [connection guide](TERMINAL_LAUNCH.md#connect-your-apps-strata-connect). Otherwise point
the tool at `http://127.0.0.1:8080/v1` as an OpenAI-compatible base URL.

## If it is slow

| What you see | Where to look |
| --- | --- |
| First answer takes minutes, later ones are fast | Expected: the first start warms the file cache |
| Answers are slow from the first token | Smaller pack, lower `--max-context`, fewer resident KV tokens |
| Very fast card, still slow | RAM is the bottleneck — check `strata doctor --live`, and that the experts fit |
| Everything slow and the disk is busy | The model is streaming from the SSD; free RAM or use a smaller pack |
| Fine in a terminal, slow in an app | The app and the terminal may be using different engines: check `strata launch --status` |

Measurement guidance — what a number means, and how to compare two configurations honestly — is in
[details](DETAILS.md) and [performance notes](AMD_HIP_PERFORMANCE.md) for AMD.

## What "working" looks like

- [ ] `strata --version` (or `./build-sycl-aot/strata --version`) prints the version you meant to install.
- [ ] The model is running: `strata launch --status` says so, or the engine process is alive.
- [ ] `/v1/models` answers on the port you expect.
- [ ] One real request returns an answer, not an error.
- [ ] You can stop it cleanly: `strata launch --stop` ends the model, and the port stops answering.

If a box is ticked falsely, that is a bug report worth filing: use
[upstream's issue templates](https://github.com/Niko1221/Strata/issues/new/choose) for engine or install bugs.
This fork's issue tracker is off — changes and corrections come in as pull requests.

## Where to read next

| Topic | Page |
| --- | --- |
| Every Strata word, in plain language | [Glossary](GLOSSARY.md) |
| Install, update, where files go | [Install](INSTALL.md) |
| Build from a source checkout | [Source install](SOURCE_INSTALL.md) |
| Terminal commands and options | [Terminal launch](TERMINAL_LAUNCH.md) · [Terminal reference](TERMINAL.md) |
| How the engine works | [How it works](HOW_IT_WORKS.md) · [Details](DETAILS.md) |
| This fork's verified build and evidence | [DIRIGO.md](../DIRIGO.md) |
| All guides | [Documentation index](README.md) |
