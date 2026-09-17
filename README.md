# The Virtual Biotech

<p align="center">
  <strong>
    To run demo queries directly on our web interface, visit
    <a href="https://virtualbiotech.ai">virtualbiotech.ai</a>.
  </strong>
</p>

The Virtual Biotech is a multi-agent AI framework for therapeutic discovery
and development. It models a cross-functional therapeutic research organization:
a virtual Chief Scientific Officer (CSO) coordinates scientist agents that
retrieve, analyze, and integrate multimodal biomedical evidence.

The system supports target prioritization, target validation and modality
selection, and analysis of clinical translation failure. It is designed for
human-guided research and decision support, with users setting the scientific
question and steering follow-up analyses.

The **conversational CLI is the recommended interface**. For a first
installation, follow [Setup](#setup) below, then continue to
[Running the CLI](#running-the-cli).

[![A CSO coordinates specialist agents for target prioritization, target validation and modality selection, and clinical translation failure analysis.](docs/figures/figure0.png)](docs/figures/figure0.pdf)

[Setup](#setup) · [CLI](#running-the-cli) · [Web interface (optional)](#running-gradio) · [Architecture](#architecture) · [Apptainer](#apptainer-linux-cluster-only)

## Setup

### System requirements

For the core installation with Open Targets data, plan for:

| Resource | Requirement or recommendation |
|---|---|
| CPU | At least 4 CPU cores |
| Storage | About 40 GB for the core installation, plus space for caches and research outputs |
| Memory | 16 GB RAM recommended; large or concurrent analyses can need more |
| GPU | Not required for the standard CLI workflow; language models run through an API |
| Network | Internet access for installation, model requests, and remote data sources |

Leave additional space for installation caches, research outputs, and large
single-cell downloads.
Optional Tahoe data requires about 83 GiB for the source download alone, plus
space for prepared files; see the [Tahoe setup guide](docs/TAHOE_SETUP.md).

### 1. Create the conda environment

Install Git and [Miniforge (Conda)](https://github.com/conda-forge/miniforge#install).
Use a terminal where `conda` is available, or the Miniforge Prompt on Windows.
If activation asks for shell initialization, run `conda init` and reopen the
terminal.

```bash
git clone https://github.com/harrisongzhang/TheVirtualBiotech.git
cd TheVirtualBiotech
conda env create -f environment.yml
conda activate vbt
```

Run the remaining commands from the repository root. Activate `vbt` in each new
terminal before running Python. The environment includes Python 3.11, the CLI
and MCP dependencies, scanpy, anndata, CELLxGENE Census, and R integration.

To update an existing `vbt` environment:

```bash
conda env update --name vbt --file environment.yml
```

<details>
<summary>Check R integration</summary>

Conda activation makes R available to `rpy2`. Check that R and its required
packages load:

```bash
python -c "from rpy2.robjects import r; print(r('R.version.string')[0]); r('library(lme4); library(lmerTest)')"
```

On Linux, the explicit `libopenblas` dependency supplies `libopenblas.so.0` for
`rpy2`, including when Conda selects MKL for other BLAS packages.

</details>

<details>
<summary>If startup reports CLINotFoundError</summary>

The standard environment includes the research runtime and uses its bundled
executable ahead of anything on `PATH`. A separate installation is normally
unnecessary. If the executable is missing, for example after installing
dependencies from source, install it with:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

This native installer does not require Node.js. See the
[runtime installation guide](https://code.claude.com/docs/en/setup) for other methods.
The audit tooling, tests, and `verify` do not need this runtime.

</details>

### 2. Obtain the data

#### Open Targets (required)

Use the archived [Open Targets Platform 25.09 release](https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/25.09/output/),
the version used to build and test this system. The downloader retrieves its
3,508 Parquet files across 38 datasets, preserving the directory layout:

```bash
python tools/download_open_targets.py /path/to/open_targets --workers 8
```

```text
/path/to/open_targets/
├── target/
├── credible_set/
├── evidence/
├── drug_molecule/
└── ...
```

No API key is needed. Rerun the same command to resume an interrupted download,
or add `--list-only` to inspect the inventory without downloading files.
The downloader announces discovery immediately and reports progress every
10 seconds during transfer and verification.

<details>
<summary>Download progress and integrity checks</summary>

Progress reports show validated files, bytes in active transfers (including
resumed bytes), bytes received during this invocation (including retries),
and elapsed time. Files count as validated after their transfer and checks
finish.

The downloader checks transfer completeness and Parquet header/footer markers.
It records sizes and SHA-256 hashes in a local manifest, then uses that manifest
to verify completed files on subsequent runs.

</details>

#### Tahoe-100M (optional)

Tahoe adds drug-perturbation tools using pseudobulk differential-expression
results from the [official Tahoe-100M dataset](https://huggingface.co/datasets/tahoebio/Tahoe-100M).
Follow the [download and preparation recipe](docs/TAHOE_SETUP.md), then set
`TAHOE_DATA_PATH` to the prepared directory.

The recipe pins the source revision and uses `tools/prepare_tahoe.py` to create
the filtered files the loader expects. It documents the adjusted-p-value and
log2-fold-change thresholds. DepMap essentiality tools work without Tahoe.

### 3. Set environment variables

Copy the configuration template, then edit `.env` in the project root:

```bash
cp .env.example .env
```

On Windows Command Prompt, use `copy .env.example .env`. Set your key and
absolute data path:

```dotenv
ANTHROPIC_API_KEY="your-anthropic-api-key"
OPEN_TARGETS_DATA_PATH="/absolute/path/to/open_targets"
```

The app loads `.env` automatically, and Git ignores the file. Quoted values
are supported. Exported shell variables take precedence, including explicitly
empty values.

| Variable | Purpose | Required or default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Model access | Required for live research |
| `OPEN_TARGETS_DATA_PATH` | Downloaded Open Targets directory; read-only | Required |
| `TAHOE_DATA_PATH` | Prepared Tahoe directory; read-only | Optional |
| `MCP_OUTPUT_DIR` | Output directory for standalone MCP tools | Defaults to `data/` in the project root; research sessions use their own output directory |

The data directories contain reference files you download. Output directories
start empty and are created as needed. During a research session, MCP query
results are saved under `work/_mcp/data/processed/` in that session's directory.
Optional `.env` entries:

```dotenv
TAHOE_DATA_PATH="/absolute/path/to/tahoe-prepared"
MCP_OUTPUT_DIR="/absolute/path/to/mcp_outputs"
```

Web-specific settings are covered under [Running Gradio](#running-gradio).

<details>
<summary>Using shell variables instead of .env</summary>

The examples above are file entries. In Bash, export variables with:

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export OPEN_TARGETS_DATA_PATH="/absolute/path/to/open_targets"
```

</details>

### 4. Configure the MCP servers and check setup

Model Context Protocol (MCP) servers connect the scientist agents to their data
and analysis tools. Configure them for your installation:

```bash
python setup_mcp.py
python tools/doctor.py --skip-api-key --smoke
```

`setup_mcp.py` configures 12 servers and writes `mcp_config.json` for the active
interpreter and checkout. Git ignores this machine-specific file. Regenerate
it after moving the checkout, changing environments, or switching to Apptainer.

The doctor checks dependencies, all 38 reference-data directories, file
readability and sizes, the download manifest when present, and MCP paths.
With `--smoke`, it also reads one disease record and initializes all 12 servers.
This local check does not load the large analysis tables or call a model or
external data API.

After adding your key, run `python tools/doctor.py` to check key presence too.
A pass does not validate authentication or billing. To recheck data hashes,
rerun the downloader; the doctor checks layout and sizes.

On systems with Bash, `./run.sh doctor --skip-api-key --smoke` also activates
the environment. To use a separate environment:

```bash
VBT_ENV=/absolute/path/to/env ./run.sh doctor --skip-api-key --smoke
```

For custom activation, see the `activate.local.sh` override described in
`activate.sh`. Optionally, run the regression tests without model requests.
The environment includes pytest:

```bash
python -m pytest -q
```

Once setup checks pass and your API key is configured, continue below to run
your first query.

## Running the CLI

Research queries require the configured API key and reference data and incur
model API charges. After [setup](#setup), start the recommended interactive CLI
from the repository root:

```bash
conda activate vbt
python run.py
```

At the `You:` prompt, enter:

```text
Evaluate PCSK9 as a target for lowering LDL cholesterol, including genetic evidence and existing therapies.
```

If the CSO asks clarifying questions, answer them at the same prompt to set
the scope of the analysis. Keep the CLI open to continue the conversation;
follow-up questions use the context from earlier turns. For example:

```text
How does its genetic evidence compare with LPA for the same indication?
```

The CLI checks the required Open Targets files before each research turn.
If a download is incomplete or files are missing, that turn is stopped before
a model request. Finish or repair the download, run
`python tools/doctor.py --skip-api-key`, then retry the prompt in the same session.
If a data source remains unavailable during analysis, a warning is shown with
the answer and recorded in the session's audit.

### Interactive sessions

Run `python run.py`, then type a question at the `You:` prompt. For multiline
input, enter `"""` on its own line, type or paste your prompt, then enter another
`"""` line to submit it as one message:

```text
"""
Evaluate PCSK9 as a target for lowering LDL cholesterol,
including genetic evidence and existing therapies.
"""
```

| Command | Action |
|---|---|
| `/summary` | Show per-turn cost and agent breakdown |
| `/done` | Save the session and exit |
| `/help` | Show available commands |
| `Ctrl+C` | Exit gracefully and save output files |

For a short launch reference after installation, see the
[interactive CLI quickstart](QUICKSTART.md).

### Session records and verification

The terminal prints the session directory at startup. Records are saved after
each turn, including follow-up questions, specialist activity, costs, and evidence:

```text
sessions/<SESSION_ID>/
├── MANIFEST.json                  # artifact hashes and run configuration
├── session_report.json            # interactive session's per-turn summary
├── README.md                      # run summary and artifact index
├── audit.html                     # evidence and execution report
├── inputs/                        # query.txt and plan.json
├── work/<agent>/                  # scripts, data, figures, tables and reports
├── work/_mcp/data/processed/       # MCP query outputs
├── logs/                          # transcript.md, cost_report.json, trace.jsonl
├── evidence/                      # claims.json and provenance.json
└── report/FINAL_REPORT.md          # CSO responses across conversation turns
```

The CLI also writes `transcript.md` and `trace.jsonl` at the session root for
tools that read the earlier session layout.

Open the session's `README.md` or `audit.html` to review it. To check the saved
record, replace `<SESSION_ID>` below with the directory name printed by the CLI:

```bash
python run_vbt.py verify "sessions/<SESSION_ID>"
```

Verification reports **artifact integrity** and **evidence coverage** separately.
Each research turn must link its findings to filed claims.
A research run with no filed claims, missing or stale evidence links, or
unresolved data errors is **INCOMPLETE**, even if every file hash matches.
Verification checks the recorded support for an answer; it does not establish
scientific correctness or independently verify external citations.

<details>
<summary>Audit tooling without a model key</summary>

Reading and verifying existing runs and running the audit-specific tests need
only Python 3.10+; they do not require the Conda environment, reference data,
or model access.

```bash
python3 tests/test_audit_spine.py
python3 tests/test_run_lifecycle.py
python3 tests/test_claim_ui.py
python3 tests/test_plan_and_verify.py
python3 tests/test_regressions.py
python3 tests/test_audit_coverage.py
python3 tests/test_live_provenance.py
python3 tools/audit_run.py /path/to/old-session -o ./audits
python3 run_vbt.py verify "<RUN_ID>"
```

The retrofit tool builds `audits/<id>/audit.html` from an old session's
`trace.jsonl`. Without a trace, it can inventory files but cannot recover
specialist attribution or the execution history. It cannot reconstruct claims
that were never filed. Some tests need a recorded session and skip when it is
absent; set `VBT_TEST_SESSION` to a session directory to enable them.
In the application environment, run the
complete suite with `python -m pytest -q`.

`verify` re-hashes artifacts and re-resolves claims. Adding `--rerun` also
executes the recorded analysis code and compares outputs by filename; it does
not guarantee that every original output is regenerated. Re-execution needs
the dependencies and data used by that analysis. `replay <RUN_ID>` submits
the same conversation to the recorded models and compares the result. It
makes new model requests, and stochastic sampling can change the trajectory.

See [the run manifest](src/utils/run_manifest.py) for the output layout and
[the run-organization instructions](.claude/skills/run-organization/SKILL.md)
for how the CSO records plans, evidence, and artifacts.

</details>

### Choose a model

Model selection is optional. Both CLIs accept model IDs and quoted display labels:

```bash
python run.py --model claude-opus-4-6
# --model "Opus 4.6" selects the same model.
```

The default is `claude-sonnet-4-5-20250929`. The chief-of-staff and
scientific-reviewer always use Haiku. To see the labels and options, use
`python run.py --help` or `python run_vbt.py run --help`.

Unrecognized labels fail immediately. Model IDs beginning with `claude-` are
forwarded unchanged; Anthropic returns an API error for unavailable models.
See the [model catalog](https://platform.claude.com/docs/en/about-claude/models/overview).

### Headless runs

Use the headless runner for scripted queries that exit when finished:

```bash
python run_vbt.py run "Evaluate PCSK9 as a target for lowering LDL cholesterol, including genetic evidence and existing therapies."
```

This uses the default model. Pass one quoted argument per conversation turn,
or use `-f questions.txt` for one turn per nonempty line. The runner prints the
run ID and paths to its `README.md` and `audit.html`.

| Interface | Command | Output directory |
|---|---|---|
| Conversational CLI (recommended) | `python run.py` | `sessions/<SESSION_ID>/` |
| Headless question or conversation | `python run_vbt.py run "<query>"` | `runs/<RUN_ID>/` |
| Headless with Bash activation | `./run.sh run "<query>"` | `runs/<RUN_ID>/` |

Use the Python entry points after activating the environment and configuring
MCP servers in [setup](#setup). `run.sh` handles activation and MCP configuration
automatically. For example, with an explicit model:

```bash
./run.sh run --model claude-opus-4-6 "Evaluate PCSK9 as a target for lowering LDL cholesterol, including genetic evidence and existing therapies."
```

Headless and web runs use the same internal directory layout as interactive
sessions, under `runs/<RUN_ID>/`. Check them with `./run.sh verify <RUN_ID>`.

## Running Gradio

The web interface is optional. To use it, set a shared access password in `.env`:

```dotenv
BIOTECH_APP_PASSWORD="choose-your-own"
```

This password is required for the web interface; the CLI does not use it.
Gradio verifies it before serving the interface or accepting research requests.
Start the web interface with:

```bash
conda activate vbt
python setup_mcp.py
python gradio_cso_app.py
```

Open `http://127.0.0.1:7860` and sign in with any username and your configured
password. The server binds to localhost. Research requests use the API key
and reference data configured in [setup](#setup).

If port 7860 is occupied, add this entry to `.env` and restart:

```dotenv
GRADIO_SERVER_PORT="17860"
```

Then open `http://127.0.0.1:17860`. From Bash, you can set the port for a single
launch with `GRADIO_SERVER_PORT=17860 python gradio_cso_app.py`. The wrapper
`./run.sh web` activates the environment, configures MCP servers, and starts
the same interface.

For a remote server, forward its port from your laptop. If the app uses port
17860 on the server:

```bash
ssh -L 17860:localhost:17860 <you>@<server>
```

Then open `http://localhost:17860` on your laptop.

## Architecture

The scientist agents span four research divisions: Target Identification and
Prioritization, Target Safety, Modality Selection, and Clinical Officers.
The chief of staff and scientific reviewer support the CSO's office.

The CSO clarifies the user's intent and uses the chief of staff's briefing on
the field and available data to plan the work. Scientist agents query data,
run analyses, and interpret results within their domains. The scientific
reviewer checks the methods, evidence, and alignment with the original question;
the CSO then requests further analysis where needed and synthesizes the findings.

The current data and tools focus on early-stage human therapeutic research.
The strength of a conclusion depends on coverage of the target, disease,
tissues, and populations in the available data. Generated hypotheses need
experimental validation.

### Specialist agents

The CSO delegates directly to these specialists:

| Agent | Role | Data sources |
|---|---|---|
| `genomics-analyst` | GWAS, L2G, QTL colocalization | Open Targets Genetics |
| `functional-genomics-analyst` | CRISPR essentiality, DepMap (cancer only) | DepMap, Tahoe |
| `single-cell-analyst` | Cell-type expression, scRNA-seq | CELLxGENE Census |
| `fda-safety-officer` | Drug warnings, adverse events, mouse phenotypes | Open Targets |
| `bio-pathways-ppi-analyst` | Reactome pathways, GO, protein interactions | Reactome, STRING |
| `clinical-trialist` | Clinical trials, cancer genomics | ClinicalTrials.gov, cBioPortal |
| `target-biologist` | Druggability, protein structure, localization | Open Targets, GTEx |
| `medchem-pharmacologist` | Drug development, modality ranking | ChEMBL, Open Targets |
| `chief-of-staff` | Field briefing, data landscape, recent developments | WebSearch, WebFetch |
| `scientific-reviewer` | Review of methods, evidence, and alignment with the question | Specialist outputs (read-only) |

### MCP servers

Twelve MCP servers provide standardized tools for querying biomedical data
and recording the run's evidence:

| Server | Data source |
|---|---|
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
| `provenance` | Run claims, artifacts, analysis plans, and evidence records |

## Apptainer (Linux cluster only)

`vbt.def` builds a self-contained image with the Conda environment. Apptainer
requires Linux; the [Conda setup](#setup) has also been tested on macOS and
Windows.

The source checkout stays on the host and is bind-mounted at runtime, so code
updates do not require rebuilding the image.

### Build

Run from the repository root:

```bash
apptainer build vbt.sif vbt.def
```

### Run

Use `apptainer exec` with `--pwd` set to the mounted project directory.
`apptainer run` is not the application entry point. Configure MCP servers inside
the container first so their absolute interpreter and script paths match:

```bash
apptainer exec \
  --bind /path/to/project:/workspace \
  --bind /path/to/open_targets:/data/open_targets \
  --pwd /workspace \
  vbt.sif python setup_mcp.py

apptainer exec \
  --bind /path/to/project:/workspace \
  --bind /path/to/open_targets:/data/open_targets \
  --pwd /workspace \
  vbt.sif python run.py
```

Regenerate `mcp_config.json` whenever you switch between Conda and the container.
Pass API keys and data paths with `--env`, or place a `.env` file in the mounted
project directory at `/workspace`. Use paths as seen inside the container.
The research runtime is bundled in the image's Python environment and does
not depend on executables on the host's `PATH` or in its `$HOME`.

### Test the image

```bash
apptainer test vbt.sif
```

## Clinical trial data

The [clinical trial data folder](datasets/clinical_trials/) contains two files:

- [`chembl_clinical_nct_data.parquet`](datasets/clinical_trials/chembl_clinical_nct_data.parquet):
  488,361 drug–target–disease mappings across 76,925 trials, derived from Open Targets.
- [`clinical_trial_labels_reconciled.csv`](datasets/clinical_trials/clinical_trial_labels_reconciled.csv):
  reconciled annotations for 56,707 completed, terminated, withdrawn, or suspended
  trials, including endpoint outcomes, phase progression, reasons for stopping,
  serious adverse event percentages, and source references.

Join the files on `nct_id`. The mapping file can contain multiple rows per trial;
the labels file has one row per trial.

Open Targets Platform data are released under
[CC0 1.0](https://platform-docs.opentargets.org/licence), which permits redistribution
and modification.

## License

The code is distributed under the MIT License. See [LICENSE](LICENSE) for details.
