"""
The Virtual Biotech — Interactive CLI

The primary conversational interface for The Virtual Biotech.
Run any user query through the CSO and specialist pool, with per-turn
usage tracking and auditable session reports.

Usage:
    python3 run.py
"""

import argparse
import asyncio
import json
import os
import shutil
import signal
import sys
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from src.config.models import DEFAULT_MODEL_ID, MODEL_HELP, model_argument

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.sdk_init_retry import RobustClaudeSDKClient
from src.utils.cost_tracker import CostTracker
from src.utils.trace_logger import TraceLogger, agents_in_events, tool_failures
from src.utils.run_manifest import RunManifest
from src.utils.session_audit import SessionAudit, scoped_mcp_servers, data_failure_notice
from src.utils.run_storage import write_json_atomic, write_text_atomic
from src.utils.runtime_paths import RuntimePaths
from src.utils.mcp_config import resolve_mcp_servers
from src.utils.agent_hooks import SecurityConfig, build_security_hooks, build_security_callback
from src.data.readiness import require_reference_data, DataReadinessError

# =============================================================================
# Configuration
# =============================================================================

SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

REPO_ROOT = Path(__file__).parent


def _resolve_mcp_config(mcp_servers: dict) -> dict:
    """Resolve portable child commands and environment values for this checkout."""
    return resolve_mcp_servers(mcp_servers, REPO_ROOT)

# =============================================================================
# Prompt Loading
# =============================================================================

def load_prompts():
    """Load all specialist system prompts"""
    base = Path(__file__).parent / 'src' / 'agents'
    prompts = {}

    # CSO prompt
    with open(base / 'cso' / 'system_prompt.md') as f:
        prompts['cso'] = f.read()

    # Target ID Division specialists (shortened versions)
    with open(base / 'target_id' / 'system_prompts' / 'genomics_analyst_short.md') as f:
        prompts['genomics'] = f.read()

    with open(base / 'target_id' / 'system_prompts' / 'functional_genomics_analyst_short.md') as f:
        prompts['functional_genomics'] = f.read()

    with open(base / 'target_id' / 'system_prompts' / 'single_cell_analyst_short.md') as f:
        prompts['single_cell'] = f.read()

    # Target Safety Division specialists (shortened versions)
    with open(base / 'safety' / 'system_prompts' / 'fda_safety_officer_short.md') as f:
        prompts['fda_safety'] = f.read()

    with open(base / 'safety' / 'system_prompts' / 'bio_pathways_ppi_analyst_short.md') as f:
        prompts['bio_pathways_ppi'] = f.read()

    # Clinical Officers Division specialists
    with open(base / 'safety' / 'system_prompts' / 'clinical_trialist_short.md') as f:
        prompts['clinical_trialist'] = f.read()

    # Modality Selection Division specialists (shortened versions)
    with open(base / 'modality_selection' / 'system_prompts' / 'target_biologist_short.md') as f:
        prompts['target_biologist'] = f.read()

    with open(base / 'modality_selection' / 'system_prompts' / 'medchem_pharmacologist_short.md') as f:
        prompts['medchem'] = f.read()

    # Chief of Staff (Haiku-powered intelligence brief)
    with open(base / 'chief_of_staff' / 'system_prompt.md') as f:
        prompts['chief_of_staff'] = f.read()

    # Scientific Reviewer (Haiku-powered quality assurance)
    with open(base / 'scientific_reviewer' / 'system_prompt.md') as f:
        prompts['scientific_reviewer'] = f.read()

    # Trial Matching Specialist
    with open(base / 'trial_matching' / 'system_prompt.md') as f:
        prompts['trial_matching'] = f.read()

    return prompts


# =============================================================================
# Specialist Agent Builder
# =============================================================================

from src.agents.registry import build_specialist_agents


# =============================================================================
# Session Management
# =============================================================================

class Session:
    """Interactive REPL session with per-turn usage tracking."""

    def __init__(self, model: str = "claude-sonnet-4-5-20250929"):
        self.model = model
        self.start_time = datetime.now()
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S") + '_' + uuid.uuid4().hex[:8]
        self.run = RunManifest.create(SESSIONS_DIR, run_id=timestamp, config={
            'model': model, 'interface': 'interactive', 'audit_required': False,
        })
        self.run_id = self.run.run_id
        self.session_dir = self.run.run_dir
        self.workspace_dir = self.run.run_dir

        # Copy .claude/skills to workspace
        skills_src = Path(__file__).parent / '.claude' / 'skills'
        skills_dst = self.workspace_dir / '.claude' / 'skills'
        if skills_src.exists() and not skills_dst.exists():
            skills_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skills_src, skills_dst)

        # Usage tracking state
        self.turns = []
        self.previous_cumulative_cost = 0.0
        self.cost_tracker = CostTracker(model=self.model.rsplit('-', 1)[0])
        self.client = None
        self._shutdown_requested = False
        self.trace_logger = TraceLogger()
        self.audit = SessionAudit(self.run, self.trace_logger)
        self.runtime_paths = None

    def _build_hooks(self):
        return self.audit.build_hooks()

    async def initialize(self):
        """Initialize the CSO client."""
        if self.client is not None:
            return
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Configure it in the project-root "
                ".env or export it before starting research."
            )
        require_reference_data()
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.types import ThinkingConfigAdaptive

        prompts = load_prompts()
        mcp_config_path = Path(__file__).parent / 'mcp_config.json'
        with open(mcp_config_path) as f:
            mcp_config = json.load(f)
        mcp_servers = scoped_mcp_servers(
            _resolve_mcp_config(mcp_config.get('mcpServers', {})), self.run.run_dir)

        specialist_agents = build_specialist_agents(prompts, workspace_dir=str(self.workspace_dir),
                                                    specialist_model=self.model)
        self.runtime_paths = RuntimePaths(self.workspace_dir)
        security = SecurityConfig(
            workspace_dir=str(self.workspace_dir), app_source_dir=str(REPO_ROOT),
            extra_read_dirs=[sys.prefix] + [os.environ[key] for key in
                ('OPEN_TARGETS_DATA_PATH', 'TAHOE_DATA_PATH') if os.environ.get(key)],
            blocked_read_dirs=[str(REPO_ROOT / name) for name in ('src', '.env', '.git')],
            runtime_paths=self.runtime_paths,
        )
        hooks = build_security_hooks(security)
        for event, matchers in self._build_hooks().items():
            hooks.setdefault(event, []).extend(matchers)

        cso_options = ClaudeAgentOptions(
            model=self.model,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": prompts['cso']
            },
            allowed_tools=[
                'Task', 'Agent', 'TodoWrite',
                'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash',
                'Skill', 'NotebookEdit',
                'WebFetch', 'WebSearch',
                'mcp__provenance__write_plan', 'mcp__provenance__record_claims',
                'mcp__provenance__list_artifacts',
            ],
            agents=specialist_agents,
            mcp_servers=mcp_servers,
            max_turns=100,
            cwd=str(self.workspace_dir),
            permission_mode='bypassPermissions',
            can_use_tool=build_security_callback(security),
            hooks=hooks,
            session_id=self.runtime_paths.session_id,
            env={**self.runtime_paths.sdk_env, 'VBT_RUN_DIR': str(self.run.run_dir)},
            thinking=ThinkingConfigAdaptive(type="adaptive"),
            effort="high",
        )

        print("[Initializing CSO client...]")
        self.client = await RobustClaudeSDKClient(
            options=cso_options,
            max_retries=5,
            initial_delay=3.0,
            backoff_factor=1.5,
            pre_warm=True,
            patch_timeout=True,
            verbose=True
        ).__aenter__()

        print(f"[CSO client ready]\n")

    async def run_turn(self, user_input: str) -> str:
        """Send a turn to the existing conversation and save its audit immediately."""
        from claude_agent_sdk import AssistantMessage, TextBlock, ThinkingBlock, ToolUseBlock, ResultMessage

        if self.client is None:
            await self.initialize()
        # Recheck before each query: a download or mount can change mid-session.
        require_reference_data()
        turn_number = len(self.turns) + 1
        turn_start = datetime.now()
        trace_start = len(self.trace_logger.events)
        self.audit.begin_turn(user_input, turn_number)
        response_text = ""
        visible_agents, visible_mcp = [], []
        cumulative_cost = self.previous_cumulative_cost
        completed = False
        failure = None
        try:
            await self.client.query(user_input)
            async for msg in self.client.receive_response():
                if isinstance(msg, AssistantMessage):
                    self.cost_tracker.process_message(msg)
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(block.text, end="", flush=True)
                            if response_text and not response_text.endswith('\n'):
                                response_text += "\n\n"
                            response_text += block.text
                        elif isinstance(block, ThinkingBlock):
                            self.trace_logger.thinking(block.thinking)
                        elif isinstance(block, ToolUseBlock):
                            if block.name in ('Task', 'Agent'):
                                agent = block.input.get('subagent_type', 'unknown')
                                visible_agents.append(agent)
                                print(f"\n\n[Delegating to {agent}: {block.input.get('description', '')}]\n")
                            elif block.name.startswith('mcp__'):
                                visible_mcp.append(block.name)
                                print(f"\n[MCP] {block.name}", end="")
                            elif block.name != 'TodoWrite':
                                print(f"\n[Tool: {block.name}]", end="")
                elif isinstance(msg, ResultMessage):
                    self.cost_tracker.process_result(msg)
                    new_cost = getattr(msg, 'total_cost_usd', None)
                    if new_cost is not None:
                        cumulative_cost = new_cost
                    if msg.is_error:
                        raise RuntimeError(f"Research turn failed: {msg.subtype}")
                    completed = True
            if not completed:
                raise RuntimeError("The response ended before the turn completed.")
        except BaseException as error:
            failure = str(error) or type(error).__name__
            raise
        finally:
            events = self.trace_logger.events_since(trace_start)
            notice = data_failure_notice(events)
            if failure:
                notice = (notice + '\n\n' if notice else '') + f"Turn incomplete: {failure}"
            if notice:
                print(f"\n\n{notice}")
                response_text += ('\n\n' if response_text else '') + notice
            agents = list(dict.fromkeys(agents_in_events(events) + visible_agents))
            mcp_tools = list(dict.fromkeys([
                event['tool_name'] for event in events
                if event['type'] == 'tool_start' and event.get('tool_name', '').startswith('mcp__')
            ] + visible_mcp))
            turn_cost = max(0.0, cumulative_cost - self.previous_cumulative_cost)
            self.turns.append({
                "turn": turn_number, "timestamp": turn_start.isoformat(),
                "prompt": user_input, "response": response_text,
                "status": "completed" if completed and not failure else "interrupted",
                "cost_usd": round(turn_cost, 6),
                "cumulative_cost_usd": round(cumulative_cost, 6),
                "agents_dispatched": agents, "mcp_tools_used": mcp_tools,
                "tool_failures": tool_failures(events),
                "response_length_chars": len(response_text),
                "thinking_traces": self.trace_logger.extract_thinking(events),
                "subagent_traces": self.trace_logger.extract_subagent_traces(events),
            })
            self.previous_cumulative_cost = cumulative_cost
            self.write_reports(quiet=True)
            print(f"\n--- Turn {turn_number} ---")
            print(f"  Turn cost:    ${turn_cost:.2f}")
            print(f"  Total so far: ${cumulative_cost:.2f}")
            print(f"  Agents used:  {', '.join(agents) or '(none)'}")
            print(f"  Audit:        {self.run.data['status']} ({self.session_dir / 'audit.html'})")
        return response_text

    def print_summary(self):
        """Print current session summary."""
        total = self.previous_cumulative_cost
        print(f"\n{'='*50}")
        print(f"SESSION SUMMARY")
        print(f"{'='*50}")
        print(f"  Turns:      {len(self.turns)}")
        print(f"  Total cost: ${total:.2f}")
        if self.turns:
            print(f"\n  Per-turn breakdown:")
            for t in self.turns:
                agents = ", ".join(t['agents_dispatched']) if t['agents_dispatched'] else "(none)"
                print(f"    Turn {t['turn']}: ${t['cost_usd']:.2f}  [{agents}]")
        print(f"{'='*50}\n")

    def write_reports(self, *, quiet=False):
        """Save the live audit and compatible session reports after each turn."""
        end_time = datetime.now()

        # session_report.json
        report = {
            "session_dir": str(self.session_dir.name),
            "start_time": self.start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "total_cost_usd": round(self.previous_cumulative_cost, 6),
            "num_turns": len(self.turns),
            "trace_events": len(self.trace_logger.events),
            "turns": self.turns,
        }
        report_path = self.session_dir / "session_report.json"
        write_json_atomic(report_path, report)
        if not quiet:
            print(f"[Written] {report_path}")

        # trace.jsonl — full event log (tool I/O, agent transcripts, reasoning)
        trace_path = self.session_dir / "trace.jsonl"
        self.trace_logger.write_jsonl(trace_path)
        if not quiet:
            print(f"[Written] {trace_path} ({len(self.trace_logger.events)} events)")

        # transcript.md
        lines = [
            f"# Virtual Biotech Session: {self.session_dir.name}",
            f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Ended: {end_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total cost: ${self.previous_cumulative_cost:.2f}",
            "",
        ]
        for t in self.turns:
            agents = ", ".join(t['agents_dispatched']) if t['agents_dispatched'] else "(none)"
            lines.append(f"## Turn {t['turn']}")
            lines.append(f"**User:** {t['prompt']}")
            lines.append("")
            lines.append(f"**CSO:** {t.get('response', '')}")
            lines.append("")
            lines.append(f"**Cost:** ${t['cost_usd']:.2f} (cumulative: ${t['cumulative_cost_usd']:.2f})")
            lines.append(f"**Agents:** {agents}")
            lines.append("")

            # CSO reasoning traces
            thinking = t.get('thinking_traces', [])
            if thinking:
                lines.append("### CSO Reasoning")
                for th in thinking:
                    preview = th.replace('\n', '\n> ')[:2000]
                    lines.append(f"> {preview}")
                    if len(th) > 2000:
                        lines.append(f"> *... [{len(th):,} chars total]*")
                    lines.append("")

            # Sub-agent trace summaries
            subagents = t.get('subagent_traces', [])
            if subagents:
                lines.append("### Sub-agent Traces")
                for sa in subagents:
                    dur = f" — {sa['duration_s']}s" if sa.get('duration_s') else ""
                    n_msgs = len(sa.get('conversation', []))
                    lines.append(f"- **{sa['agent_type']}**{dur} ({n_msgs} messages)")
                lines.append("")
                lines.append("*Full sub-agent conversations in session_report.json and trace.jsonl*")
                lines.append("")

        transcript_path = self.session_dir / "transcript.md"
        write_text_atomic(transcript_path, "\n".join(lines))
        write_text_atomic(self.session_dir / "logs" / "transcript.md", "\n".join(lines))
        if not quiet:
            print(f"[Written] {transcript_path}")
        self.audit.finish_turn(self.turns, self.previous_cumulative_cost,
            interrupted=any(t.get("status") == "interrupted" for t in self.turns))

    async def run_repl(self):
        """Run the interactive REPL loop."""
        print("=" * 60)
        print(f"THE VIRTUAL BIOTECH")
        print("=" * 60)
        print(f"Session dir: {self.session_dir}")
        print(f"Workspace:   {self.workspace_dir}")
        print()
        print("Commands:")
        print("  /done or quit  — end session (reports save after every turn)")
        print("  /summary       — print current session summary")
        print("  /help          — show this help")
        print("  Ctrl+C         — graceful shutdown, write reports")
        print("=" * 60)
        print()

        await self.initialize()

        # Set up Ctrl+C handler
        loop = asyncio.get_event_loop()
        original_handler = signal.getsignal(signal.SIGINT)

        def sigint_handler(sig, frame):
            self._shutdown_requested = True
            print("\n\n[Ctrl+C received — finishing up and writing reports...]")
            # Restore original handler so a second Ctrl+C will force-exit
            signal.signal(signal.SIGINT, original_handler)

        signal.signal(signal.SIGINT, sigint_handler)

        try:
            while not self._shutdown_requested:
                try:
                    user_input = input("\nYou: ").strip()
                except EOFError:
                    break

                if not user_input:
                    continue

                # Multi-line mode: start with """ and end with """ on its own line
                if user_input == '"""' or user_input.startswith('"""'):
                    first_line = user_input[3:].strip()
                    lines = [first_line] if first_line else []
                    print('... (enter """ on its own line to finish)')
                    while True:
                        try:
                            line = input("... ")
                        except EOFError:
                            break
                        if line.strip() == '"""':
                            break
                        lines.append(line)
                    user_input = "\n".join(lines).strip()
                    if not user_input:
                        continue

                if user_input.lower() in ('/done', 'quit'):
                    break

                if user_input.lower() == '/summary':
                    self.print_summary()
                    continue

                if user_input.lower() == '/help':
                    print("Commands:")
                    print('  """            — start multi-line input (end with """ on its own line)')
                    print("  /done or quit  — end session, write reports")
                    print("  /summary       — print current session summary")
                    print("  /help          — show this help")
                    continue

                # Run the turn
                print("\nCSO: ", end="", flush=True)
                try:
                    await self.run_turn(user_input)
                except DataReadinessError as error:
                    print(f"\n[Data not ready] {error}")
                except Exception as e:
                    print(f"\n[ERROR] Turn failed: {e}")
                    import traceback
                    traceback.print_exc()
                    print("[You can try again or type /done to finish.]")

        finally:
            try:
                print()
                self.print_summary()
                self.write_reports()
            finally:
                try:
                    if self.client:
                        await self.client.disconnect()
                finally:
                    signal.signal(signal.SIGINT, original_handler)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="The Virtual Biotech — interactive CSO command-line interface.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_ID,
        type=model_argument,
        metavar="MODEL",
        help=MODEL_HELP,
    )
    args = parser.parse_args()
    from run_vbt import _require_api_key
    _require_api_key()
    session = Session(model=args.model)
    try:
        asyncio.run(session.run_repl())
    except (DataReadinessError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
