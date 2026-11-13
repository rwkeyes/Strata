# Strata guides

**New here? Read [Getting started](GETTING-STARTED.md) first.** It takes you from "what is this" to a first
answer, on either the released install or this repository's source snapshot. Every word Strata uses is in the
[glossary](GLOSSARY.md).

These guides describe the tree in this repository: upstream Strata **v0.2.12** (engine **0.1.58**) plus this
fork's changes. For the fork's verified build, what was measured, and the evidence, read
[DIRIGO.md](../DIRIGO.md).

## Start here

| You are… | Start with |
| --- | --- |
| Completely new to Strata | [Getting started](GETTING-STARTED.md) |
| Installing, updating, or wondering where files went | [Install](INSTALL.md) |
| Building from a source checkout | [Source install](SOURCE_INSTALL.md) |
| Wondering whether your PC can run it | [Which model fits](MODELS.md#pick-by-memory) |
| Stuck on an error | [Troubleshooting](TROUBLESHOOTING.md) |
| Reading this fork's numbers | [DIRIGO.md](../DIRIGO.md) · [`dirigo-results/`](../dirigo-results/) |

## Use Strata

| I want to… | Guide |
| --- | --- |
| Install or update | [Desktop app and Terminal](INSTALL.md) |
| Choose a model and check memory | [Models and quantizations](MODELS.md) |
| Run DeepSeek V4.1-Flash (app and Terminal) | [DeepSeek V4.1-Flash](MODELS.md#deepseek-v41-flash) |
| Run Qwen3.6-35B-A3B | [Qwen3.6-35B-A3B](QWEN36.md) |
| Start from the command line | [Terminal launch](TERMINAL_LAUNCH.md) |
| Connect a coding tool | [Tool connections](TERMINAL_LAUNCH.md#connect-your-apps-strata-connect) |
| Fix an install or startup problem | [Troubleshooting](TROUBLESHOOTING.md) |
| Check privacy, downloads and network access | [Safety and privacy](SAFETY.md) |

## Hardware

Use the guide for your GPU and operating system. Model and feature support can differ between backends.

| Hardware | Guide |
| --- | --- |
| NVIDIA RTX 20 / 30 / 40 / 50 | [Install](INSTALL.md#what-you-need) |
| AMD Radeon (RX and AI PRO R9700) | [AMD / HIP](AMD_HIP.md) |
| AMD Ryzen AI Max / Strix Halo | [Strix Halo](STRIX_HALO.md), [on Windows](STRIX_HALO_WINDOWS.md) |
| Intel Arc on Linux | [Intel Arc](INTEL_ARC.md) |
| Apple Silicon | [Mac / Metal](MAC.md) |
| More than one GPU | [Multiple GPUs](MULTI_GPU.md) |
| Two cards: one reads the prompt, one makes the answer (opt-in) | [Split serving](SPLIT_SERVE.md) |
| Older GPUs | [Experimental hardware](OLDER_GPUS.md) |

## Developers and contributors

- [Build or run from a source checkout](SOURCE_INSTALL.md)
- [Docker](DOCKER.md)
- [Contribution rules](../CONTRIBUTING.md)
- [Community benchmarks](COMMUNITY_BENCHMARKS.md) and the [standard benchmark protocol](BENCHMARK_PROTOCOL.md)
- [How the engine works](HOW_IT_WORKS.md), [technical reference](DETAILS.md) and [Terminal reference](TERMINAL.md)
- [KVA: approximate prompt processing for the late layers (experimental, opt-in)](KVA.md)
- [App development](../apps/host/README.md) and [packaging](../packaging/README.md)

## This fork

- [Verified build, measurements and evidence](../DIRIGO.md)
- [Measurements, suite logs and the carry patch](../dirigo-results/)
- [Glossary of every term](GLOSSARY.md)
- The parts that belong upstream are open as separate branches, listed in [DIRIGO.md](../DIRIGO.md#upstream)

## AI tools and media

- [Install for a user](AI_SETUP.md)
- [Verified facts, model availability and API reference](AI_REFERENCE.md)
- [Articles and videos](MEDIA_KIT.md)
- [Repository instructions for agents](../AGENTS.md)

## Which instructions apply?

`strata launch` belongs to the installed Terminal. `START-HERE.bat`, `SETUP.bat`, `UPDATE.bat` and `setup.sh`
belong to a **source checkout**. Raw engine commands are a third interface. Their flags are not interchangeable:
Terminal launch uses `--gpu` and `--vision on|off`; source setup uses `--gpus` and `--vision yes|no`.

Release notes, design proposals and benchmark reports describe their named versions and machines. They remain
available as historical evidence; use the guides above for a new installation. A published result on one GPU
does not establish support or speed on every GPU.

## Writing a guide page

- Installation steps live in [INSTALL.md](INSTALL.md), source steps in [SOURCE_INSTALL.md](SOURCE_INSTALL.md). The public READMEs stay short.
- Check command examples against the parser and filenames against the release assets. Do not hard-code a version
  number in an instruction: the release page names the file, and this repository is a snapshot that will age.
- Write for a reader who has never seen the term before; link the [glossary](GLOSSARY.md) instead of re-explaining.
- A guide describes the current release. Version history, fixed bugs and superseded settings go in a closing
  **History / older measurements** section, in `bench/results`, or in the release notes. Do not mix them into instructions.

Every guide page has this shape:

| Part | Content |
| --- | --- |
| Title | `# Name of the task or topic` |
| Purpose | One to three lines: what the page is for and who it is for |
| Quick start or Steps | The shortest working path, commands in code blocks |
| Details | `##` sections; option and flag lists as tables; short paragraphs |
| History / older measurements | Optional, last. Dated numbers only |

Label every measurement the same way, directly above the number or table:

> **Measured on:** hardware (GPU, CPU, RAM), model or set, Strata version, workload.
