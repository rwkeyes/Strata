# Glossary

Every word Strata's guides use, in plain language. Sorted into groups rather than one alphabet, because the
words come in families: the **model**, the **engine**, the **server**, and **measuring** it.

If a term you met is not here, it is a bug — say so on the [documentation index](README.md).

## The model

**Model / model family**
A set of weights. This repository's guides cover Qwen3.8-Flash-Next (with the Coder variant), Qwen3.6-35B-A3B
and DeepSeek V4.1-Flash. Each family has its own packs, memory needs and support.

**GGUF**
The file format the weights arrive in: a set of tensors plus a header describing them, split into parts when
large. The engine reads them directly.

**Shard**
One part of a split GGUF. A model can be one file or several (`…-00001-of-00002.gguf`). All parts are needed;
a missing shard stops the pack build.

**Pack**
A folder the engine loads instead of the GGUF: an `index.txt` describing every tensor, the dense weights
(`dense.bin`), optionally the expert blobs (`experts.bin`), the expert layout (`native_experts.txt`), and the
`tokenizer/` folder. Packs are built once per model with `tools/iq_pack.py`. A pack without
`native_experts.txt` is unfinished and will not load.

**Quantization**
How much the weights were compressed. Smaller numbers are smaller and faster and lose a little quality:

| Name | Roughly | Typical use |
| --- | --- | --- |
| `Q2_0` | 2 bits, uniform | fastest, least memory, "good" quality |
| `IQ2_XS` | ~2 bits, importance-aware | a little better at a little more size |
| `IQ3_XXS` | ~3 bits | the usual balance |
| `IQ3_S` | ~3 bits, larger | the most faithful of the small packs |
| `IQ1_M` | ~1 bit, importance-aware, coding-only | the Coder pack for a small machine |

The i-quants (`IQ…`) keep the experts in their GGUF form; `Q2_0` cannot express them and has its own pack
form. Sizes and what fits: [Models](MODELS.md#pick-by-memory).

**Expert / mixture of experts (MoE)**
The model is a team of small specialist networks — 24,576 of them in Qwen3.8-Flash-Next — and each word needs
only about ten. Strata can therefore keep most of them out of VRAM without losing quality.

**Router**
The small network that decides which experts a given word needs. It runs on the card for every token.

**Shared expert**
An expert used for every token, so it always lives on the card.

**Draft layer (MTP)**
A small built-in model that guesses the next few words so the big model can check several at once. It only
guesses; the big model still decides every word, so answers do not change. See also **spec**.

**PLE table**
The large lookup table (28.8 GB for Qwen3.8-Flash-Next) that Strata reads a few rows of per token from the SSD
instead of holding in memory. `--ple-gguf` points at it; `--ple-io` chooses how it is read.

## The engine

**Engine**
The program that runs the model (`strata`). Separate from the app and from the terminal command, and versioned
separately — an app version and an engine version are different numbers.

**Backend**
Which graphics API the engine was compiled for: **CUDA** (NVIDIA), **HIP** (AMD), **SYCL** (Intel Arc),
**Metal** (Apple Silicon). A binary built for one backend does not run on another card.

**VRAM / RAM / unified memory**
Memory on the graphics card / the system's memory / one pool shared between them (Apple Silicon, Strix Halo).
Strata needs both VRAM and RAM; unified memory counts once.

**Expert cache**
The part of VRAM that holds the most-used experts. It fills the card and adapts while you chat. Each extra GB
of VRAM holds roughly 700 more experts.

**Expert profile**
A small file (`data/expert-profile.bin`) telling the engine which experts to start with. Without it the engine
exits on start — a source build needs it beside the working directory.

**`--stream-experts`**
Stream the next layer's experts to the card while the current layer computes, instead of waiting for a whole
layer to arrive.

**KV cache**
What the model remembers about the conversation. It costs memory per token of context, which is why context
length and `--kv` precision show up in every memory table. `--kv int8` stores it at 8 bits; `--kv-resident`
keeps N tokens of it on the card.

**Context / `--max-context`**
How many tokens the model can see at once, prompt plus answer. Larger is more useful and needs more memory.

**Slot**
One request being served. The engine serves one at a time by design, so extra concurrent requests queue.

**Prefill / decode**
Reading your prompt (fast, batched, thousands of tokens per second) / writing the answer (one token at a
time). Different speeds; a single "tokens per second" number usually means decode.

**Spec / speculation / `--spec`**
How many tokens the draft layer guesses ahead. Guessing more can help, guessing badly costs — `--spec-min-p`
is the confidence floor for accepting a guess.

**Prompt cache**
Keeping the processed prompt so a follow-up on the same conversation does not re-read it. `--prompt-cache`
controls how many, `--prompt-cache-every` how often it is refreshed.

**Thinking / reasoning effort**
Some models write a scratchpad before answering. It can help on hard problems and costs time and tokens; the
app, the terminal and the server can all turn it down or off.

**AOT**
Ahead-of-time compilation of the GPU code. For Intel Arc, `-DSTRATA_SYCL_AOT=bmg-g31` compiles for the Arc
Pro B70, `bmg-g21` for B580/B570/Pro B60; leave it empty on A-series cards and the code is compiled at first
start instead.

**ISA floor (AVX, AVX2)**
The CPU instruction set the engine requires for the experts the card does not hold. Ready-made x86 engines
need AVX2; source builds can be made for older CPUs, which is experimental.

## The server and this fork

**Server / `serve/`**
The HTTP layer in front of the engine: an OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`), an
Anthropic-compatible one (`/v1/messages`), and the app-facing endpoints. `--serve` alone is the engine's own
internal protocol, not HTTP.

**Terminal / `strata`**
The installed command-line product: `strata launch`, `strata connect`, `strata doctor`, `strata update`,
`strata models`. It picks the right engine, prepares models, and manages the server.

**Run config**
The JSON file the terminal or setup writes (`strata-<model>.json`). It holds the engine `args`, the model paths
and the server's own settings. Setup can rewrite the fields it owns — back it up before editing.

**TUNE / retune (this fork)**
A channel for changing engine settings on a running engine, between requests, without a restart or reload.
`GET /v1/tuning` reports the current settings; `POST /v1/tune` changes them. Stock upstream Strata has no such
channel.

**conv route / conversation-cache routing (this fork)**
A server-side rule that turns the prompt cache off during a run of one-shot prompts (faster prefill) and back
on for a conversation (prefix reuse). Modes: `off`, `shadow` (classify and log only), `on`.

**Carry**
This fork's delta on top of an upstream tag: 35 files, +5,024/−264 for v0.2.12. Kept as one patch in
[`dirigo-results/notes/carry-0212.patch`](../dirigo-results/notes/carry-0212.patch) so the difference from the
tag is auditable in one file.

**Verified tree (this fork)**
The branch `dirigo/0.2.12-verified`: upstream v0.2.12 plus the carry, reproduced in a clean room to the same
git tree hash, built from scratch, tested and gated. [DIRIGO.md](../DIRIGO.md) has the evidence.

## Measuring it

**TTFT**
Time to first token — how long from sending a prompt until the answer starts. Dominated by prefill.

**ITL**
Inter-token latency — the gap between tokens while the answer streams. Its inverse, with overhead, is the
decode rate.

**tok/s**
Tokens per second. Say whether it is prompt (prefill) or answer (decode): they differ by an order of magnitude.

**Sweep / A-B**
Changing one setting and measuring both values under the same conditions. A single number from one run is not
an A-B; interleaved runs are what make a difference believable.

**Standard benchmark protocol**
Upstream's fixed method (`strata bench run`, protocol `strata-qwen38-thinking-v1-draft.1`): frozen prompts,
model-card sampling, a validated `results.json`. Different from a **community report**, which is anyone's own
measurement in a documented format. [Benchmark protocol](BENCHMARK_PROTOCOL.md) ·
[community benchmarks](COMMUNITY_BENCHMARKS.md).
