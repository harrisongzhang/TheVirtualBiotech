# Quickstart

```bash
git clone https://github.com/harrisongzhang/TheVirtualBiotech.git
cd TheVirtualBiotech
```

## What runs where

Most of the repo cannot run on a laptop.

| | Laptop | HPC cluster |
|---|---|---|
| Audit tooling, tests, reading a past run | **yes** — Python 3.10+, nothing else | yes |
| The web interface and live agent runs | **no** | yes |

The agents are not the obstacle; the **data** is. The MCP servers read Open
Targets / GTEx / DepMap tables — hundreds of GB that are not in this repo and
cannot be. Without them the specialists have nothing to query.

---

## On a laptop — the audit tooling

Everything here is stdlib-only. No install, no API key, no configuration.

### Run the tests

```bash
python3 tests/test_audit_spine.py       # manifest, provenance, claim validation
python3 tests/test_run_lifecycle.py     # a full run, end to end
python3 tests/test_claim_ui.py          # claim markers and the evidence panel
python3 tests/test_plan_and_verify.py   # plan DAG, tamper detection, re-execution
python3 tests/test_regressions.py       # regressions caught on live runs
```

Some tests assert against a real recorded session and skip automatically when it
is not reachable (set `VBT_TEST_SESSION` to a recorded session directory to run
them). The rest run anywhere.

### Audit an old flat session

The retrofit tool rebuilds an audit trail for sessions that predate the run
layout, from their `trace.jsonl` alone:

```bash
python3 tools/audit_run.py /path/to/old-session -o ./audits
# then open audits/<id>/audit.html in a browser
```

Sessions with no `trace.jsonl` cannot be audited — the report says so rather than
rendering a plausible-looking empty one.

### Where to read

- `README.md` — architecture, setup, and the data sources
- `src/utils/run_manifest.py` — the run layout and artifact registry
- `src/utils/provenance.py` — how "who produced what" is reconstructed
- `src/utils/claims.py` — the claim-evidence object and its validator
- `.claude/skills/run-organization/` — the standard the CSO is held to

---

## On an HPC cluster — live runs

### Setup

Create the pinned environment once (see `README.md` for full detail):

```bash
conda env create -f environment.yml     # creates the `vbt` conda env
# or build the container:                apptainer build vbt.sif vbt.def
```

For the container, invoke it with `apptainer exec --pwd /workspace` (not
`apptainer run`) — see `README.md`.

Live runs need the **Claude Code CLI**, which the pinned `claude-agent-sdk` wheel bundles — normally there is nothing extra to install. If the SDK reports `CLINotFoundError`, install it yourself with `curl -fsSL https://claude.ai/install.sh | bash` (no Node.js required), or see the [setup guide](https://code.claude.com/docs/en/setup). The audit tooling / tests / `verify` need no CLI at all.

Put your key and data paths in a `.env` file (see `README.md` for the full list):
`ANTHROPIC_API_KEY`, `OPEN_TARGETS_DATA_PATH`, and the optional data locations.
Then:

```bash
source activate.sh      # activates the `vbt` env, configures the MCP servers
./run.sh doctor         # checks interpreter, packages, CLAUDE_CONFIG_DIR, API key, MCP config
```

`activate.sh` and `run.sh` both run `setup_mcp.py` for you, which writes the
`mcp_config.json` this environment needs. Activating conda by hand instead? Run
`python setup_mcp.py` once yourself — `run.py` will not start without it.

`doctor` says PASS or names what is missing. The audit commands
(`verify` / `audit` / `index` / `test`) work even when the conda env does not.

### Running

```bash
./run.sh web                          # web interface on 127.0.0.1:7860
./run.sh run "<query>"                # one headless run
./run.sh verify <RUN_ID>              # re-hash artifacts, re-resolve claims
./run.sh verify <RUN_ID> --rerun      # also re-execute the analysis code
./run.sh replay <RUN_ID>              # re-run the same turns, then diff
./run.sh audit <session_dir>          # retrofit an old flat session
./run.sh index                        # rebuild runs/INDEX.md
```

For the web interface from your laptop, tunnel to whichever node it is on:

```bash
ssh -J <you>@<login-node> -L 7860:localhost:7860 <you>@<compute-node>
# then open http://localhost:7860
```

### Two guarantees, kept apart

`verify` re-hashes every artifact; with `--rerun` it also re-executes the
agent-written analysis code and compares the regenerated outputs (matched by
filename) against the originals — ordinary deterministic Python. It checks the
outputs a rerun *does* produce; it is not a guarantee that every original output
is regenerated.

`replay` is **trajectory-level**: the same turns against the same pinned models,
prompt hashes and MCP servers, then a diff. LLM sampling is stochastic, so this
is a comparison, not a reproduction — the tool says so in its own output.
