# Interactive CLI quickstart

For a first installation, follow [Setup in the README](README.md#setup),
then continue to [Running the CLI](README.md#running-the-cli). The README
contains the complete installation and query instructions.

The conversational CLI is the recommended interface. This page is a short
launch reference after setup.
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

Required Open Targets files are checked before each research turn. If the data
is incomplete, finish the download and run `python tools/doctor.py --skip-api-key`
before retrying. The blocked turn is not sent to the model.

## Ask a question

At the `You:` prompt, enter:

```text
Evaluate PCSK9 as a target for lowering LDL cholesterol, including genetic evidence and existing therapies.
```

If the CSO asks clarifying questions, answer at the same prompt to set the
scope of the analysis. Keep the CLI open to ask follow-up questions using the
same conversation context, for example:

```text
How does its genetic evidence compare with LPA for the same indication?
```

For a multiline prompt, enter `"""` on its own line, paste the prompt, then
enter another `"""` line to submit it. See
[Interactive sessions](README.md#interactive-sessions) for an example and commands.

The CLI uses the default model; see [Choose a model](README.md#choose-a-model)
for alternatives.

## Save and exit

Records are saved after each turn. Type `/done` to finish and exit. The terminal
prints the directory under `sessions/<SESSION_ID>/`; open its `README.md` or
`audit.html` to review artifacts, evidence, and execution history. Logs are in
`logs/`, claims in `evidence/claims.json`, and specialist outputs in `work/<agent>/`.
Use `/summary` for the current cost and agent breakdown, or `/help` for commands.

See [Session records and verification](README.md#session-records-and-verification)
to check artifact integrity and evidence coverage. Scripted queries that exit
when finished are covered in [Headless runs](README.md#headless-runs).
