# Interactive CLI quickstart

For a first installation, follow [Setup in the README](README.md#setup),
then continue to [Running the CLI](README.md#running-the-cli). The README
contains the complete installation and query instructions.

This page is a short reference for starting an interactive session after setup.
Research queries use your configured API key and reference data and incur
model API charges.

## Start a session

From the repository root, activate the environment and launch the CLI:

```bash
conda activate vbt
python run.py
```

If you moved the checkout or changed environments, run `python setup_mcp.py`
before launching to update the MCP configuration.

## Ask a question

At the `You:` prompt, enter:

```text
Evaluate PCSK9 as a target for lowering LDL cholesterol, including genetic evidence and existing therapies.
```

If the CSO asks clarifying questions, answer at the same prompt to set the
scope of the analysis. You can then ask follow-up questions in the same session.
The CLI uses the default model; see [Choose a model](README.md#choose-a-model)
for alternatives.

## Save and exit

Type `/done` to save the session and exit. The terminal prints the session
directory under `sessions/<timestamp>/`, containing `transcript.md`,
`session_report.json`, `trace.jsonl`, and agent-generated files in `workspace/`.
Use `/summary` for the current cost and agent breakdown, or `/help` for commands.

For single queries that exit when finished, see the
[README's CLI examples](README.md#running-the-cli) and
[headless run options](README.md#headless-runs).
