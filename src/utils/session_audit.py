"""Live audit lifecycle shared by conversational, batch, and web sessions."""

import json
from pathlib import Path
import re
import sys

from src.utils.claims import ClaimSet, validate_claims
from src.utils.provenance import build_provenance
from src.utils.run_manifest import CSO_DIR, RunManifest, snapshot_dir, diff_snapshots
from src.utils.run_storage import run_lock, write_json_atomic, write_text_atomic
from src.utils.trace_logger import (
    agents_in_events, unresolved_tool_failures, parse_agent_transcript, compute_agent_cost,
)


SUPPORT_AGENTS = {'chief-of-staff', 'scientific-reviewer', CSO_DIR, 'cso', 'unknown'}


def refresh_claim_status(claims, manifest, provenance):
    """Refresh display flags without replacing filed claims or evidence hashes.

    Later turns may revise or delete artifacts. Keep the original evidence
    pointers for auditing, while ensuring their stored/displayed status reflects
    current validation. Only explicit claim refiling can update its evidence.
    """
    validated = validate_claims(claims.claims, manifest, provenance, strict=False)

    def pointer(evidence):
        return tuple(evidence.get(key) for key in
                     ('kind', 'path', 'tool_use_id', 'pmid', 'doi', 'url'))

    current = {
        (claim['id'], pointer(evidence)): evidence.get('verified', False)
        for claim in validated.claims for evidence in claim['evidence']
    }
    for claim in claims.claims:
        for evidence in claim['evidence']:
            evidence['verified'] = bool(current.get((claim['id'], pointer(evidence)), False))
        claim['n_verified'] = sum(evidence['verified'] for evidence in claim['evidence'])
    return claims


def attribute_script_outputs(manifest, provenance):
    """Recover unowned outputs from a unique, observed script writer.

    A filename in source can also be an input or a comment. Limit attribution
    to an identifiable write statement, one matching output and one matching
    script, while preserving ownership already assigned to a specialist.
    """
    artifacts = manifest.data['artifacts']
    scripts = [manifest.run_dir / key for key, entry in artifacts.items()
               if entry.get('kind') == 'code']
    provenance.index_script_outputs(scripts)
    texts = {}
    for script in scripts:
        try:
            texts[str(script)] = script.read_text(errors='replace')
        except OSError:
            continue
    written = provenance.files_written(include_returned=False)
    writer = re.compile(
        r'\.(?:to_csv|to_parquet|to_excel|savefig|write_text|write_bytes|write_h5ad|'
        r'write_csv|write_parquet)\s*\(|\b(?:saveRDS|write\.csv|write\.table)\s*\('
        r'|\bopen\s*\([^)]*,\s*[\'\"](?:w|a|x)')
    for key, entry in artifacts.items():
        if entry.get('produced_by') not in (None, '', CSO_DIR, '_mcp') or entry.get('created_by'):
            continue
        name = Path(key).name
        if sum(Path(path).name == name for path in artifacts) != 1:
            continue
        hit = provenance.attribute_by_script(key)
        if not hit or not writer.search(hit['statement']):
            continue
        mentions = [path for path, text in texts.items()
                    if re.search(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])', text)]
        if len(mentions) != 1 or mentions[0] != hit['script']:
            continue
        script = Path(hit['script'])
        if sum(path.name == script.name for path in scripts) != 1:
            continue
        creators = {provenance.agent_for(tool_id) for path, tool_id in written.items()
                    if Path(path).name == script.name}
        if len(creators) != 1:
            continue
        agent = next(iter(creators))
        if agent in SUPPORT_AGENTS or agent == '_mcp':
            continue
        entry['produced_by'] = agent
        entry['created_by'] = f"{script.name}:{hit['line']}"
        if agent not in manifest.data['agents']:
            manifest.data['agents'].append(agent)


def scoped_mcp_servers(servers, run_dir):
    """Pass run-specific paths to children without changing process globals."""
    root = Path(run_dir).resolve()
    output = root / 'work' / '_mcp' / 'data' / 'processed'
    output.mkdir(parents=True, exist_ok=True)
    return {
        name: {**config, 'env': {
            **config.get('env', {}),
            'VBT_RUN_DIR': str(root), 'MCP_OUTPUT_DIR': str(output),
        }} for name, config in servers.items()
    }


def data_failure_notice(events):
    failed = [f for f in unresolved_tool_failures(events)
              if f['tool_name'].startswith(('mcp__', 'Web'))
              and not f['tool_name'].startswith('mcp__provenance__')]
    if not failed:
        return ''
    names = ', '.join(dict.fromkeys(f['tool_name'] for f in failed))
    return ('Data/evidence warning: these tools failed during this turn: ' + names +
            '. Their results cannot support this answer. Any alternative sources '
            'must be identified separately; evidence coverage is incomplete.')


class SessionAudit:
    def __init__(self, run, trace):
        self.run = run
        self.trace = trace
        self.trace.bind(run.run_dir / 'logs' / 'trace.jsonl')
        self._snapshot = snapshot_dir(run.run_dir)
        self._agents = {}
        self._active = {}
        self.turn = 0

    def _update(self, action):
        with run_lock(self.run.run_dir):
            current = RunManifest.load(self.run.run_dir)
            result = action(current)
            current.write()
            self.run.data = current.data
            return result

    def begin_turn(self, prompt, number):
        self.turn = number
        def update(current):
            if not current.data.get('query'):
                current.data['query'] = prompt
            current.data['status'] = 'in_progress'
            current.data['completed'] = None
            current.data['config'].setdefault('research_turns', [])
        self._update(update)

    def _require_claims(self, current):
        """Record research activity for this turn as well as for the session."""
        config = current.data['config']
        config['audit_required'] = True
        if self.turn > 0:
            numbers = config.setdefault('research_turns', [])
            if self.turn not in numbers:
                numbers.append(self.turn)

    def note_error(self, error):
        message = str(error)[:2000]
        print(f'[AUDIT] {message}', file=sys.stderr, flush=True)
        def update(current):
            errors = current.data['config'].setdefault('audit_errors', [])
            if message not in errors:
                errors.append(message)
        self._update(update)

    def capture(self, agent=CSO_DIR, tool_use_id=None):
        def update(current):
            after = snapshot_dir(current.run_dir)
            changed = diff_snapshots(self._snapshot, after)
            for relative in changed:
                parts = Path(relative).parts
                owner = parts[1] if len(parts) > 1 and parts[0] == 'work' else agent
                # Shared MCP output can be written concurrently. A later hook
                # observes those files but does not establish which call wrote
                # them; keep the shared owner until provenance can resolve it.
                writer = tool_use_id if owner == agent and owner != '_mcp' else None
                current.add_artifact(current.run_dir / relative,
                                     produced_by=owner or CSO_DIR,
                                     tool_use_id=writer)
            current.scan(refresh_changed=True)
            if current.data['artifacts']:
                current.data['config']['audit_required'] = True
            if changed:
                self._require_claims(current)
            self._snapshot = after
            return changed
        return self._update(update)

    def _agent_of(self, event):
        agent = event.get('agent_type') or self._agents.get(event.get('agent_id'))
        # The runtime omits agent_id on the main thread. An active specialist
        # does not own the parent's concurrent calls merely by being active.
        return agent or CSO_DIR

    def _mark_research_tool(self, name):
        scientific = (name.startswith('mcp__') and not name.startswith('mcp__provenance__')
                      or name in ('WebSearch', 'WebFetch'))
        config = self.run.data.get('config', {})
        if scientific and (config.get('audit_required') is not True
                           or self.turn not in config.get('research_turns', [])):
            self._update(self._require_claims)

    def _import_conversation(self, agent, conversation):
        """Recover child tool calls when parent hooks do not receive them."""
        started = {e.get('tool_use_id'): e for e in self.trace.events
                   if e['type'] == 'tool_start'}
        ended = {e.get('tool_use_id') for e in self.trace.events
                 if e['type'] in ('tool_end', 'tool_error')}
        for message in conversation:
            for call in message.get('tool_calls', []):
                key = call.get('id')
                self._mark_research_tool(call.get('name') or 'unknown')
                if key and key not in started:
                    self.trace.tool_start(key, call.get('name', 'unknown'),
                                          call.get('input') or {}, agent=agent)
                    started[key] = {'tool_name': call.get('name', 'unknown'),
                                    'tool_input': call.get('input') or {}}
            for result in message.get('tool_results', []):
                key = result.get('tool_use_id')
                if key and key in started and key not in ended:
                    call = started[key]
                    self.trace.tool_end(key, call['tool_name'], call['tool_input'],
                                        result.get('content', ''),
                                        is_error=result.get('is_error', False), agent=agent)
                    ended.add(key)

    def build_hooks(self, on_agent_start=None, on_agent_stop=None):
        from claude_agent_sdk import HookMatcher

        def guarded(function):
            async def hook(event, matcher, context):
                try:
                    function(event)
                except Exception as error:
                    self.note_error(f'{function.__name__}: {error}')
                    return {'systemMessage': 'Audit recording failed. Report incomplete evidence and do not invent citations.'}
                return {}
            return hook

        def start(event):
            agent = event.get('agent_type') or 'unknown'
            key = event.get('agent_id', agent)
            self._agents[key] = agent
            self._active[key] = agent
            def update(current):
                if agent and Path(agent).name == agent:
                    current.agent_dir(agent)
                if agent not in SUPPORT_AGENTS:
                    self._require_claims(current)
            self._update(update)
            self.trace.agent_start(key, agent)
            if on_agent_start:
                on_agent_start(event)

        def stop(event):
            key = event.get('agent_id', 'unknown')
            agent = event.get('agent_type') or self._agents.get(key, 'unknown')
            transcript = event.get('agent_transcript_path')
            conversation = parse_agent_transcript(transcript) if transcript else []
            self._import_conversation(agent, conversation)
            self.trace.agent_stop(key, agent, transcript_path=transcript,
                                  conversation=conversation,
                                  cost=compute_agent_cost(transcript) if transcript else None)
            self._active.pop(key, None)
            self.capture(agent)
            if on_agent_stop:
                on_agent_stop(event)

        def before(event):
            name = event['tool_name']
            self._mark_research_tool(name)
            if name.startswith('mcp__provenance__'):
                self.capture(self._agent_of(event))
            self.trace.tool_start(event['tool_use_id'], name,
                                  event.get('tool_input', {}), agent=self._agent_of(event))

        def after(event):
            self.trace.tool_end(event['tool_use_id'], event['tool_name'],
                                event.get('tool_input', {}), event.get('tool_response', ''),
                                agent=self._agent_of(event))
            name = event['tool_name']
            if name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Bash') or name.startswith('mcp__'):
                self.capture(self._agent_of(event), event['tool_use_id'])

        def failure(event):
            self.trace.tool_error(event['tool_use_id'], event['tool_name'],
                                  event.get('tool_input', {}), event.get('error', 'Tool failed'),
                                  agent=self._agent_of(event))

        return {name: [HookMatcher(hooks=[guarded(function)])] for name, function in (
            ('SubagentStart', start), ('SubagentStop', stop), ('PreToolUse', before),
            ('PostToolUse', after), ('PostToolUseFailure', failure),
        )}

    def finish_turn(self, turns, total_cost, *, interrupted=False):
        """Save audit state after every turn without resetting conversation state."""
        from src.utils.run_report import render_readme, render_audit_html
        from src.utils.verify import assess_evidence_coverage

        self.capture()
        research_turns = set(self.run.data['config'].get('research_turns', []))
        for turn in turns:
            turn['audit_required'] = bool(turn.get('audit_required') or turn['turn'] in research_turns)
        final = '\n\n---\n\n'.join(t.get('response', '') for t in turns)
        write_text_atomic(self.run.run_dir / 'report' / 'FINAL_REPORT.md', final)
        write_text_atomic(self.run.run_dir / 'inputs' / 'query.txt', '\n\n'.join(
            f"--- turn {t['turn']} ---\n{t['prompt']}" for t in turns))
        write_json_atomic(self.run.run_dir / 'logs' / 'cost_report.json', {
            'run_id': self.run.run_id, 'total_cost_usd': round(total_cost, 6),
            'num_turns': len(turns), 'trace_events': len(self.trace.events), 'turns': turns,
        })
        provenance = build_provenance(self.run.run_dir / 'logs' / 'trace.jsonl')
        def update(current):
            plan = current.run_dir / 'inputs' / 'plan.json'
            if plan.exists():
                current.data['plan'] = json.loads(plan.read_text())
            current.record_execution([
                {'agent': a['agent_type'], 'start': a.get('start'), 'end': a.get('end'),
                 'duration_s': a.get('duration_s')} for a in provenance.agents.values()])
            config = current.data['config']
            config.update(total_cost_usd=round(total_cost, 6), num_turns=len(turns))
            interrupted_turns = list(config.get('interrupted_turns') or [])
            for turn in turns:
                if turn.get('status') == 'interrupted' and turn['turn'] not in interrupted_turns:
                    interrupted_turns.append(turn['turn'])
            if interrupted and not interrupted_turns:
                interrupted_turns.append(turns[-1]['turn'] if turns else self.turn)
            config['interrupted_turns'] = interrupted_turns
            config['data_source_errors'] = [f for f in unresolved_tool_failures(self.trace.events)
                                             if f['tool_name'].startswith(('mcp__', 'Web'))
                                             and not f['tool_name'].startswith('mcp__provenance__')]
            attribute_script_outputs(current, provenance)
            claims = ClaimSet.load(current.run_dir / 'evidence' / 'claims.json')
            refresh_claim_status(claims, current, provenance)
            claims.link_into_manifest(current)
            claims.write(current.run_dir / 'evidence' / 'claims.json')
            coverage = assess_evidence_coverage(current, claims, provenance, final, finalizing=True)
            current.finalize(status='interrupted' if interrupted_turns else
                             ('completed' if coverage['ok'] else 'incomplete'))
            return claims, coverage
        claims, coverage = self._update(update)
        provenance.write(self.run.run_dir / 'evidence' / 'provenance.json')
        write_text_atomic(self.run.run_dir / 'README.md', render_readme(self.run, provenance, claims))
        write_text_atomic(self.run.run_dir / 'audit.html', render_audit_html(self.run, provenance, claims))
        return claims, coverage
