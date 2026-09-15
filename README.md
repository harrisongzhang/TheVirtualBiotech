# The Virtual Biotech

A multi-agent AI system for pharmaceutical target identification and due diligence, built on the Claude Agent SDK. A Chief Scientific Officer (CSO) agent orchestrates a pool of specialist agents — each with access to curated biomedical databases via Model Context Protocol (MCP) servers — to answer arbitrary drug discovery and target biology queries.

Start with the [Quickstart](QUICKSTART.md) for a complete installation and two CLI examples.

---

## Setup

### 1. Create the conda environment

Install Git and [Miniforge (Conda)](https://github.com/conda-forge/miniforge#install).
Open a terminal with Conda available (the Miniforge Prompt on Windows). If
`conda activate` asks you to initialize your shell, run `conda init` and reopen
that terminal. Then clone the repository:

```bash
git clone https://github.com/harrisongzhang/TheVirtualBiotech.git
cd TheVirtualBiotech
```

Run all subsequent commands from this repository root:

```bash
conda env create -f environment.yml
conda activate vbt
```

The environment includes all dependencies for the CLI, MCP servers, and analysis scripts (Python 3.11, scanpy, anndata, CELLxGENE Census, R integration, etc.).

Activate `vbt` in each new shell before running Python. Activation also makes
the environment's R installation available to `rpy2`. For an existing `vbt`
environment, apply dependency changes with
`conda env update --name vbt --file environment.yml`.

The explicit `libopenblas` dependency supplies `libopenblas.so.0`, which the
Linux `rpy2` extension needs even when Conda selects MKL for other BLAS packages.
Check the R integration after installation:

```bash
python -c "from rpy2.robjects import r; print(r('R.version.string')[0]); r('library(lme4); library(lmerTest)')"
```

> **Live runs need the Claude Code CLI, which the pinned SDK provides.** The
> `claude-agent-sdk` wheels bundle the CLI binary the SDK drives, and it is used
> ahead of anything on your `PATH` — so the environment above needs no separate
> install. If the SDK ever reports `CLINotFoundError` (pip having fallen back to
> the source distribution, say), install the CLI yourself — the native installer
> needs no Node.js:
> ```bash
> curl -fsSL https://claude.ai/install.sh | bash
> ```
> See Anthropic's [setup guide](https://code.claude.com/docs/en/setup) for other install methods. (The audit tooling, tests, and `verify` need no CLI at all.)

### 2. Obtain the data

**Open Targets Platform data (required)**

Download the [Open Targets Platform 25.09 data release](https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/25.09/output/) — the exact archived version this system was built and tested on — in Parquet format. The loader expects a directory with one subdirectory per dataset, e.g.:

```
/path/to/open_targets/
├── target/
├── credible_set/
├── evidence/
├── drug_molecule/
└── ...
```

The included downloader retrieves the archived layout automatically:

```bash
python tools/download_open_targets.py /path/to/open_targets --workers 8
```

The complete release contains 3,508 Parquet files in 38 datasets and occupies
approximately 29 GiB. Allow additional disk space for the Conda environment
and research outputs. Rerun the same command after an interrupted download;
it resumes partial transfers and verifies completed files against the local
size/SHA-256 manifest. Downloads are checked for complete transfers and Parquet
header/footer markers. Use `--list-only` to inspect the archive inventory
without downloading it. No API key is needed for downloading or local data
checks. Loading large datasets into memory can require substantially more RAM
than their compressed file size.

The downloader announces inventory discovery immediately and prints progress
at least every 10 seconds while downloading/verifying. It shows validated
files, bytes in active transfers (including resumed bytes), bytes received
this invocation (including retries), and elapsed time. A file becomes validated
only after its transfer and checks finish.

A separate Linux x86_64 installation on 15 September 2026 measured:

| Resource | Observed usage |
|---|---|
| Open Targets 25.09 archive | 28.99 GiB |
| Installed Conda environment | About 5.4 GiB, plus package caches and outputs |
| Largest process in target/DepMap smoke queries | About 9.8 GiB resident RAM |
| Data download on the test server | About 28 minutes, including an interruption/resume test, with 8 then 16 workers |

These are measurements, not minimum requirements or promised timings. Concurrent
specialist queries can consume more RAM than a single query. Allow disk space
for caches and research outputs beyond the archive and environment.

**Tahoe-100M drug perturbation data (optional — enables the Tahoe functional-genomics tools)**

The [official Tahoe-100M dataset](https://huggingface.co/datasets/tahoebio/Tahoe-100M)
provides pseudobulk differential-expression results. Follow the
[Tahoe download and preparation recipe](docs/TAHOE_SETUP.md) to create the
filtered files expected by this loader with `tools/prepare_tahoe.py`, then set
`TAHOE_DATA_PATH` to that prepared directory. The recipe pins a source revision
and defines the exact adjusted-p-value and log2-fold-change thresholds.
Without Tahoe, the DepMap essentiality tools still work.

### 3. Set environment variables

Create your local configuration from the supplied template:

```bash
cp .env.example .env
```

Edit `.env` in the project root. Use absolute data paths and enter your own
`ANTHROPIC_API_KEY`; the web UI also needs `BIOTECH_APP_PASSWORD`. The app loads
this file automatically. It is excluded from Git. Quoted values are supported.
The examples below are **file entries**, not shell exports. To configure the
shell instead, use `export ANTHROPIC_API_KEY="..."` and
`export OPEN_TARGETS_DATA_PATH="/absolute/path/to/open_targets"`.
Exported variables take precedence over `.env`, including explicitly empty values.

**Two kinds of paths — don't confuse them:**

- **Reference data (read-only; you download and provide it):** `OPEN_TARGETS_DATA_PATH` — plus the optional `TAHOE_DATA_PATH` — point at large external datasets (see step 2 above). The app only ever *reads* these; it never writes to them.
- **Working directory (writable; the app creates it):** the optional `MCP_OUTPUT_DIR` is a directory the app makes and writes into. It starts **empty**; you only choose *where* it lives.

**Required:**
```bash
ANTHROPIC_API_KEY="sk-ant-..."
OPEN_TARGETS_DATA_PATH="/path/to/open_targets"   # READ-ONLY Open Targets dump you download (see step 2)
```

**Optional — data:**
```bash
TAHOE_DATA_PATH="/path/to/tahoe/data"            # optional; enables the Tahoe functional-genomics tools (see step 2)
```

**Optional — output directory** (defaults to `data/` under the project root):
```bash
MCP_OUTPUT_DIR="/path/to/data"       # MCP server file outputs (parquet query results)
```

**Required for the Gradio web UI** (not needed by the CLI):
```bash
BIOTECH_APP_PASSWORD="choose-your-own"
```
The Gradio app requires an explicitly configured access password. Gradio
verifies it before serving the interface or accepting research submissions.
Enter any username and the configured password on the login form. This is a
single shared password for the local app.

### 4. Configure the MCP servers and check setup

```bash
python setup_mcp.py
```

This generates `mcp_config.json` for the active interpreter and checkout. It is
machine-specific and excluded from Git; regenerate it after moving the checkout,
changing environments or switching to Apptainer. The setup reports 12 servers.

Check the local installation before making a model request:

```bash
python tools/doctor.py --skip-api-key --smoke
```

This checks dependencies, all 38 reference-data directories, file readability
and sizes, the download manifest when present, and the generated MCP paths.
`--smoke` also reads one local disease record and initializes all 12 MCP servers;
it does not load the large analysis tables or call a model/external data API.
The doctor checks layout and sizes; rerunning the downloader rechecks SHA-256
hashes. After adding your key, run `python tools/doctor.py` to include key-presence
validation. A passing result does not validate model authentication or billing.

On systems with Bash, `./run.sh doctor --skip-api-key --smoke` runs the same checks
and activates the environment for you. For a separate environment, use
`VBT_ENV=/absolute/path/to/env ./run.sh doctor --skip-api-key --smoke`, or the
`activate.local.sh` override described in `activate.sh`.

The application regression tests can be run without model requests:

```bash
python -m unittest discover -s tests
```

---

## Apptainer (Linux cluster only)

`vbt.def` builds a self-contained Apptainer image with the conda environment
baked in. Apptainer is Linux-only; for macOS or Windows, use the conda environment directly (see [Setup](#setup) — tested on macOS and Windows).

The source code is **not** embedded — bind-mount the project
directory at runtime so you can update code without rebuilding.

### Build

Run from the project root:

```bash
apptainer build vbt.sif vbt.def
```

### Run

Use `apptainer exec` — not `apptainer run` — and set the working directory to
the bind-mounted project with `--pwd`. Configure the MCP servers from *inside*
the container first: `setup_mcp.py` records absolute interpreter and server
paths, which differ between the container and a host conda environment.

```bash
apptainer exec \
  --bind /path/to/project:/workspace \
  --bind /path/to/open_targets:/data/open_targets \
  --pwd /workspace \
  vbt.sif python setup_mcp.py          # once per image

apptainer exec \
  --bind /path/to/project:/workspace \
  --bind /path/to/open_targets:/data/open_targets \
  --pwd /workspace \
  vbt.sif python run.py
```

`mcp_config.json` is environment-specific, so regenerate it whenever you switch
between the conda environment and the container.

Environment variables (API keys, data paths, etc.) can be passed with `--env`
or by bind-mounting a `.env` file into `/workspace`. Nothing else is needed for
live runs: the CLI the SDK drives is bundled inside the image's Python
environment, so it depends on neither `PATH` nor your host `$HOME`.

### Test the image

```bash
apptainer test vbt.sif
```

---

## Running the CLI

Live queries require `ANTHROPIC_API_KEY` and the reference data configured above.
They make billable model requests. See the [two quickstart examples](QUICKSTART.md#two-cli-examples).

| Interface | Start command | Output |
|---|---|---|
| Interactive terminal | `python run.py` | `sessions/<timestamp>/` |
| Headless question/conversation | `./run.sh run "<query>"` | `runs/<RUN_ID>/` |
| Headless without Bash | `python run_vbt.py run "<query>"` | `runs/<RUN_ID>/` |

Activate the environment and run `python setup_mcp.py` before using the Python
entry points. `run.sh` handles activation and MCP configuration automatically.
Regenerate `mcp_config.json` after moving the checkout or changing environments.

Both CLIs accept the same model IDs and quoted display labels:

```bash
python run.py --model claude-opus-4-6
./run.sh run --model claude-opus-4-6 "Summarize the genetic evidence for OSMR in ulcerative colitis."
# The label --model "Opus 4.6" selects the same model.
```

The default is `claude-sonnet-4-5-20250929`. The chief-of-staff and scientific-reviewer
always use Haiku. Run either CLI with `--help` to see the available labels.
Unrecognized labels fail immediately. Model IDs starting with `claude-` are
forwarded unchanged for Anthropic to validate; unavailable IDs produce an API
error instead of silently selecting another model. See the
[Anthropic model catalog](https://platform.claude.com/docs/en/about-claude/models/overview).

### Interactive commands and outputs

After `python run.py` starts, type a question at the `You:` prompt. For multi-line
input, enter a line containing `"""`, then your question, then another `"""` line.

```text
/summary   — print per-turn cost and agent breakdown
/done      — end the session and write output files
/help      — show available commands
Ctrl+C     — graceful exit, writes output files
```

Each interactive session writes `session_report.json`, `transcript.md`,
`trace.jsonl`, and agent files under `workspace/` in `sessions/<timestamp>/`.

### Headless commands and outputs

Pass one quoted argument per conversation turn, or use `-f questions.txt` for
one turn per nonempty line. The runner prints the run ID and paths to its
`README.md` and `audit.html`. Research artifacts are organized under
`runs/<RUN_ID>/work/`; logs and evidence have their own subdirectories.
Use that printed ID with `./run.sh verify <RUN_ID>` to check the recorded artifacts.
The interactive `sessions/` layout is separate from the headless/web `runs/` layout.

---

## Running Gradio

Set `BIOTECH_APP_PASSWORD` in the project-root `.env`, then run:

```bash
conda activate vbt
python setup_mcp.py
python gradio_cso_app.py
```

Open `http://127.0.0.1:7860` and sign in with any username and your configured
password. If 7860 is occupied, set `GRADIO_SERVER_PORT="17860"` in `.env`, or
launch with `GRADIO_SERVER_PORT=17860 python gradio_cso_app.py` and open
`http://127.0.0.1:17860`. The app continues to bind to localhost by default.
Research requests require `ANTHROPIC_API_KEY` and the reference-data
path from setup step 3. On systems with Bash, `bash run.sh web` activates the
environment, regenerates the MCP configuration and starts the same interface.

---

## Architecture

### Specialist agents

The CSO delegates to a flat pool of specialists, each with access to specific MCP servers:

| Agent | Role | Data sources |
|-------|------|--------------|
| `genomics-analyst` | GWAS, L2G, QTL colocalization | Open Targets Genetics |
| `functional-genomics-analyst` | CRISPR essentiality, DepMap (cancer only) | DepMap, Tahoe |
| `single-cell-analyst` | Cell-type expression, scRNA-seq | CELLxGENE Census |
| `fda-safety-officer` | Drug warnings, adverse events, mouse phenotypes | Open Targets |
| `bio-pathways-ppi-analyst` | Reactome pathways, GO, protein interactions | Reactome, STRING |
| `clinical-trialist` | Clinical trials, cancer genomics | ClinicalTrials.gov, cBioPortal |
| `target-biologist` | Druggability, protein structure, localization | Open Targets, GTEx |
| `medchem-pharmacologist` | Drug development, modality ranking | ChEMBL, Open Targets |
| `chief-of-staff` | Web intelligence, field overview | WebSearch, WebFetch |
| `scientific-reviewer` | Quality assurance, rigor review | (read-only) |

### MCP servers

Local MCP servers provide structured access to biomedical databases:

| Server | Data source |
|--------|-------------|
| `expression` | GTEx bulk RNA-seq expression |
| `genetics` | Open Targets Genetics (GWAS, L2G, QTL) |
| `target` | Open Targets target annotations and druggability |
| `drug` | Open Targets drug mechanisms and safety |
| `disease` | Open Targets disease ontology |
| `association` | Open Targets target-disease associations |
| `single_cell` | CELLxGENE Census |
| `functional_genomics` | DepMap CRISPR essentiality + Tahoe drug perturbations |
| `pathway` | Reactome pathways + Gene Ontology |
| `interaction` | Protein-protein interaction networks |
| `clinicaltrials` | ClinicalTrials.gov + cBioPortal cancer genomics |

---

## License

MIT License. See [LICENSE](LICENSE) for details.
