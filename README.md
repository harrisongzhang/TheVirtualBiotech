# The Virtual Biotech

A multi-agent AI system for pharmaceutical target identification and due diligence, built on the Claude Agent SDK. A Chief Scientific Officer (CSO) agent orchestrates a pool of specialist agents — each with access to curated biomedical databases via Model Context Protocol (MCP) servers — to answer arbitrary drug discovery and target biology queries.

---

## Setup

### 1. Create the conda environment

```bash
conda env create -f environment.yml
conda activate vbt
```

The environment includes all dependencies for the CLI, MCP servers, and analysis scripts (Python 3.11, scanpy, anndata, CELLxGENE Census, R integration, etc.).

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

**Tahoe-100M drug perturbation data (optional — enables the Tahoe functional-genomics tools)**

The functional genomics MCP server's drug-perturbation tools use Tahoe-100M pseudobulk differential expression results. These are **not distributed with this repository** — obtain your own copy of the DE results from the [Tahoe-100M dataset](https://www.biorxiv.org/content/10.1101/2024.04.09.588750) and point `TAHOE_DATA_PATH` at them. Without `TAHOE_DATA_PATH` set, the DepMap essentiality tools still work; only the Tahoe drug-perturbation tools are inactive. The loader expects the following layout under `TAHOE_DATA_PATH`:

```
tahoe/data/
├── tahoe_permissive_padj010.parquet       # permissive pseudobulk DE (padj < 0.10)
├── pseudobulk_de_significant/             # significant (padj < 0.05)
├── pseudobulk_de_high_quality/            # high quality (padj < 0.05, |FC| > 0.5)
└── metadata/
    ├── gene_metadata.parquet
    ├── drug_metadata.parquet
    ├── cell_line_metadata.parquet
    └── sample_metadata.parquet
```

### 3. Set environment variables

Environment variables can be set in a `.env` file in the project root (loaded automatically via `python-dotenv`) or exported in your shell.

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

**Optional — web app access password** (only used by the Gradio web UI):
```bash
BIOTECH_APP_PASSWORD="choose-your-own"   # overrides the built-in default login password
```
The Gradio app (`gradio_cso_app.py`) is gated by a simple access password. It ships
with a weak built-in default and is intended to bind to localhost; set
`BIOTECH_APP_PASSWORD` to your own value before exposing it beyond your machine.

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

```bash
conda activate vbt
python3 setup_mcp.py     # once per environment — writes mcp_config.json
python3 run.py
```

`setup_mcp.py` records this environment's absolute interpreter and MCP server
paths in `mcp_config.json`; `run.py` reads that file at startup and cannot run
without it. Re-run it after moving the checkout or rebuilding the environment.
`source activate.sh` and `./run.sh` do this step for you.

By default the CSO and its specialist agents run on `claude-sonnet-4-5-20250929`.
Pass `--model` with any current [Anthropic API model ID](https://platform.claude.com/docs/en/about-claude/models/overview)
to change it — this sets the CSO, and the specialists inherit it (the `chief-of-staff`
and `scientific-reviewer` agents always run on Haiku):

```bash
python3 run.py --model claude-opus-4-6
```

Each run creates a new timestamped session directory under `sessions/`.

### Interactive commands

Once the session starts and the CSO is ready:

```
You: Evaluate CD276 (B7-H3) as an immunotherapy target in NSCLC

# Multi-line input
You: """
Analyze OSMR as a target for ulcerative colitis:
1. Expression in disease-relevant cell types
2. Genetic evidence from GWAS
3. Existing drug modalities
"""

/summary   — print per-turn cost and agent breakdown
/done      — end session and write output files
/help      — show available commands
Ctrl+C     — graceful exit, writes output files
```

### Session outputs

Each session creates a timestamped directory under `sessions/`:

```
sessions/
└── 20260524_103000/
    ├── session_report.json   # Full structured data: turns, costs, agent traces
    ├── transcript.md         # Human-readable conversation with reasoning traces
    ├── trace.jsonl           # Fine-grained event log (tool calls, sub-agent I/O)
    └── workspace/            # Files written by agents during the session
```

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
