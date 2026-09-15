# Quickstart

The **interactive CLI is the recommended starting point**. This guide covers
installation and two ways to run queries from a terminal.

Live research needs Git, [Miniforge (Conda)](https://github.com/conda-forge/miniforge#install),
an Anthropic API key, and the Open Targets reference data. Start in a terminal
where `conda` is available. If activation asks for shell initialization, run
`conda init` and reopen the terminal.

Allow about 29 GiB for the reference data, plus the environment, package caches
and outputs. Our separate Linux installation used about 5.4 GiB for the
environment; one target/DepMap query process reached about 9.8 GiB of RAM.
Concurrent analyses can need more. See [measured resources](README.md#2-obtain-the-data).

## Install and check

Run these commands from the repository root after cloning:

```bash
git clone https://github.com/harrisongzhang/TheVirtualBiotech.git
cd TheVirtualBiotech
conda env create -f environment.yml
conda activate vbt
```

The SDK bundles the Claude Code CLI; a separate CLI installation is normally
unnecessary. For troubleshooting or Apptainer, see [full setup](README.md#setup).

Choose an absolute destination for the data and use it in both the download
command and `.env` below. Replace `/absolute/path/to/open_targets` with that path:

```bash
python tools/download_open_targets.py /absolute/path/to/open_targets --workers 8
cp .env.example .env
```

On Windows Command Prompt, use `copy .env.example .env`. The download retrieves
3,508 Parquet files in 38 datasets. It announces discovery immediately, then
reports transfer/verification progress every 10 seconds. Our server took about
28 minutes; connection speed and storage affect timing. Rerun the same command
to resume an interruption.

Edit `.env` in the project root with your data path and key:

```dotenv
OPEN_TARGETS_DATA_PATH="/absolute/path/to/open_targets"
ANTHROPIC_API_KEY="your-anthropic-api-key"
```

These are **file entries**. The app loads `.env` automatically; exported shell
variables take precedence, including empty ones. You can leave the API key blank
while testing the local setup.
Tahoe is optional; its separate [download/preparation recipe](docs/TAHOE_SETUP.md)
enables drug-perturbation tools. DepMap works without Tahoe.

```bash
python setup_mcp.py
python tools/doctor.py --skip-api-key --smoke
```

Expect 38 data directories, 12 MCP servers, and `PASS`. The smoke check reads
one disease record and initializes the servers without a model request or
external data query. It checks local file layout/sizes, not all table contents;
rerunning the downloader verifies SHA-256 hashes. After setting your key, run
`python tools/doctor.py` to check key presence too. Neither command verifies
model authentication or billing.

Activate `vbt` in each new terminal. Regenerate `mcp_config.json` with
`python setup_mcp.py` after moving the checkout or changing environments.
On systems with Bash, `./run.sh doctor --skip-api-key --smoke` handles both
activation and MCP configuration. For another environment, set
`VBT_ENV=/absolute/path/to/env` before using `run.sh`.

## Two CLI examples

Both examples make billable model requests and need the key and data above.

**1. Interactive conversation (recommended):**

```bash
python run.py
```

At the `You:` prompt, enter:

```text
Evaluate PCSK9 as a target for lowering LDL cholesterol, including genetic evidence and existing therapies.
```

Ask follow-up questions in the same session, then type `/done` to save and exit.
The transcript, trace and report are saved in `sessions/<timestamp>/`.

**2. Single query:**

```bash
python run_vbt.py run "Evaluate PCSK9 as a target for lowering LDL cholesterol, including genetic evidence and existing therapies."
```

The command prints a run ID and report paths under `runs/<RUN_ID>/`.
Use `python run_vbt.py verify <RUN_ID>` with that printed ID to check the recorded
artifacts. With Bash, `./run.sh run ...` is the same headless interface and
activates the environment for you.

Both examples use the default model. To change it, add an optional model ID
such as `--model claude-opus-4-6` or a quoted label such as `--model "Opus 4.6"`.
The default is `claude-sonnet-4-5-20250929`; the chief-of-staff and
scientific-reviewer use Haiku. Unknown labels fail immediately, and model IDs
are forwarded unchanged for Anthropic to validate. See `python run.py --help`
or `python run_vbt.py run --help` for labels and options.

## Web interface

The web interface is optional. To use it, add a shared access password to `.env`:

```dotenv
BIOTECH_APP_PASSWORD="choose-your-own-web-password"
```

The CLI does not use this password. Start the web interface with:

```bash
python gradio_cso_app.py
```

Open `http://127.0.0.1:7860` and log in with any username and the configured
`BIOTECH_APP_PASSWORD`. If the port is occupied, add `GRADIO_SERVER_PORT="17860"`
to `.env`, restart, and open `http://127.0.0.1:17860`. The default bind address
remains localhost. The Bash equivalent is `./run.sh web`.

For a remote server, forward its port from your laptop:

```bash
ssh -L 17860:localhost:17860 <you>@<server>
# Then open http://localhost:17860, if the app uses 17860 on the server.
```

## Audit tooling without a model key

Reading/verifying existing runs and the audit-specific tests need only Python
3.10+; they do not require the Conda environment, reference data or model access.

```bash
python3 tests/test_audit_spine.py
python3 tests/test_run_lifecycle.py
python3 tests/test_claim_ui.py
python3 tests/test_plan_and_verify.py
python3 tests/test_regressions.py
python3 tools/audit_run.py /path/to/old-session -o ./audits
```

The retrofit tool builds `audits/<id>/audit.html` from an old session's
`trace.jsonl`. Sessions without that trace cannot be audited. Some tests need
a recorded session and skip when it is absent; set `VBT_TEST_SESSION` to a
session directory to enable them. In the application environment, run the
complete suite with `python -m unittest discover -s tests`.

`verify` re-hashes artifacts and re-resolves claims. Adding `--rerun` also
executes the recorded analysis code and compares outputs by filename; it does
not guarantee that every original output is regenerated. `replay <RUN_ID>`
submits the same conversation to the recorded models and compares the result.
It makes new model requests, and stochastic sampling can change the trajectory.

See [README.md](README.md) for architecture and configuration,
`src/utils/run_manifest.py` for output layout, and `.claude/skills/run-organization/`
for the run-organization instructions given to the CSO.
