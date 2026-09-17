"""
The Virtual Biotech - Web Interface

A Gradio-based web interface for the CSO 2-level orchestrator.
Provides a chat interface with agent activity tracking sidebar.

Usage:
    source activate.sh
    python gradio_cso_app.py

For external access via Cloudflare Tunnel:
    cloudflared tunnel run biotech-cso &
    python gradio_cso_app.py
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta
import uuid
from dataclasses import dataclass
import threading
import shutil
import traceback
import zipfile

import gradio as gr
from dotenv import load_dotenv
from src.config.models import MODEL_CHOICES, resolve_model

# Use this checkout's configuration even when launched from another directory.
# Exported environment variables take precedence over .env values.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.sdk_init_retry import RobustClaudeSDKClient
from src.utils.agent_hooks import SecurityConfig, build_security_hooks, build_security_callback
from src.utils.cost_tracker import CostTracker
from src.utils.trace_logger import TraceLogger, agents_in_events, tool_failures
from src.utils.claims import ClaimSet
from src.utils.claim_ui import (
    render_claim_refs, render_evidence_panel, render_evidence_static,
    _strip_claim_refs,
)
from src.utils.run_manifest import CSO_DIR, RunManifest
from src.utils.run_index import load_index, render_index_html, update_index
from src.utils.session_audit import SessionAudit, scoped_mcp_servers, data_failure_notice
from src.utils.runtime_paths import RuntimePaths
from src.utils.mcp_config import resolve_mcp_servers
from src.utils.run_storage import write_json_atomic, write_text_atomic
from src.data.readiness import require_reference_data, DataReadinessError

# =============================================================================
# Configuration
# =============================================================================

# Explicitly configured password for Gradio's server-side authentication.
APP_PASSWORD = os.environ.get("BIOTECH_APP_PASSWORD", "")

# Server settings
SERVER_HOST = "127.0.0.1"  # Use 0.0.0.0 if not using Cloudflare Tunnel
SERVER_PORT = 7860

# Workspace for agent file operations (isolated from main codebase).
# One directory per run, each self-describing — see src/utils/run_manifest.py.
RUNS_DIR = Path(os.environ.get("VBT_RUNS_DIR", Path(__file__).parent / "runs"))
RUNS_DIR.mkdir(parents=True, exist_ok=True)

# Retained so the legacy flat sessions stay reachable from the Past Runs tab.
WORKSPACE_DIR = Path(__file__).parent / "web_workspace"

# =============================================================================
# Security Configuration (hooks-based)
# =============================================================================

# Resolved paths for sandbox enforcement (computed once at startup)
_APP_SOURCE_DIR = str(Path(__file__).parent.resolve())

_BLOCKED_READ_DIRS = [
    str(Path(__file__).parent / "src"),   # Source code — agents should not read their own prompts
    str(Path(__file__).parent / ".env"),   # API keys
    str(Path(__file__).parent / ".git"),   # Git internals
]


def _build_session_security(workspace_dir: str, runtime_paths: RuntimePaths) -> tuple[dict, callable]:
    """Build security hooks and can_use_tool callback for a session.

    Returns (hooks_dict, can_use_tool_callback).

    SDK hooks apply to the parent (CSO) agent but do NOT propagate to
    subagents. The can_use_tool callback covers subagent tool calls,
    ensuring MCP tools remain accessible to specialist subagents while
    still enforcing the same sandbox and safety guardrails.
    """
    config = SecurityConfig(
        workspace_dir=workspace_dir,
        app_source_dir=_APP_SOURCE_DIR,
        extra_read_dirs=[sys.prefix] + [os.environ[key] for key in
            ('OPEN_TARGETS_DATA_PATH', 'TAHOE_DATA_PATH') if os.environ.get(key)],
        blocked_read_dirs=_BLOCKED_READ_DIRS,
        runtime_paths=runtime_paths,
    )
    return build_security_hooks(config), build_security_callback(config)

# =============================================================================
# Activity Tracking
# =============================================================================

@dataclass
class ActivityEvent:
    """Represents an event in the agent activity log"""
    timestamp: str
    event_type: str  # 'agent', 'tool', 'mcp', 'system'
    title: str
    detail: str = ""
    status: str = "running"  # 'running', 'complete', 'error'


class ActivityTracker:
    """Thread-safe activity tracker for the sidebar"""

    def __init__(self):
        self.events: list[ActivityEvent] = []
        self.lock = threading.Lock()

    def add_event(self, event_type: str, title: str, detail: str = "", status: str = "running") -> int:
        """Add an event and return its index"""
        with self.lock:
            event = ActivityEvent(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                event_type=event_type,
                title=title,
                detail=detail,
                status=status
            )
            self.events.append(event)
            return len(self.events) - 1

    def update_status(self, index: int, status: str):
        """Update the status of an event"""
        with self.lock:
            if 0 <= index < len(self.events):
                self.events[index].status = status

    def clear(self):
        """Clear all events"""
        with self.lock:
            self.events.clear()

    def format_markdown(self) -> str:
        """Format events as markdown for display"""
        with self.lock:
            if not self.events:
                return "*No activity yet. Send a message to start.*"

            lines = []
            for event in reversed(self.events):
                # Status indicator
                if event.status == "running":
                    icon = "🔄"
                elif event.status == "complete":
                    icon = "✅"
                else:
                    icon = "❌"

                # Event type styling
                if event.event_type == "agent":
                    type_badge = "🤖"
                elif event.event_type == "mcp":
                    type_badge = "🔌"
                elif event.event_type == "tool":
                    type_badge = "🔧"
                else:
                    type_badge = "ℹ️"

                line = f"{icon} `{event.timestamp}` {type_badge} **{event.title}**"
                if event.detail:
                    line += f"\n   _{event.detail}_"
                lines.append(line)

            return "\n\n".join(lines)


# =============================================================================
# Prompt Loading (from prototype_cso_2level.py)
# =============================================================================

def load_prompts():
    """Load all specialist system prompts"""
    base = Path(__file__).parent / 'src' / 'agents'
    prompts = {}

    # CSO prompt
    with open(base / 'cso' / 'system_prompt.md') as f:
        prompts['cso'] = f.read()

    # Target ID Division specialists (SHORTENED versions)
    with open(base / 'target_id' / 'system_prompts' / 'genomics_analyst_short.md') as f:
        prompts['genomics'] = f.read()

    with open(base / 'target_id' / 'system_prompts' / 'functional_genomics_analyst_short.md') as f:
        prompts['functional_genomics'] = f.read()

    with open(base / 'target_id' / 'system_prompts' / 'single_cell_analyst_short.md') as f:
        prompts['single_cell'] = f.read()

    # Target Safety Division specialists (SHORTENED versions)
    with open(base / 'safety' / 'system_prompts' / 'fda_safety_officer_short.md') as f:
        prompts['fda_safety'] = f.read()

    with open(base / 'safety' / 'system_prompts' / 'bio_pathways_ppi_analyst_short.md') as f:
        prompts['bio_pathways_ppi'] = f.read()

    # Clinical Officers Division specialists
    with open(base / 'safety' / 'system_prompts' / 'clinical_trialist_short.md') as f:
        prompts['clinical_trialist'] = f.read()

    # Modality Selection Division specialists (SHORTENED versions)
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


from src.agents.registry import build_specialist_agents


# =============================================================================
# CSO Session Manager
# =============================================================================

class CSOSession:
    """Manages a single CSO orchestrator session for one user"""

    # Model choices available to users
    MODEL_CHOICES = MODEL_CHOICES

    def __init__(self, session_id: str, model_key: str = "Sonnet 4.5 (default)"):
        self.session_id = session_id
        self.model_key = model_key
        self.model_id = resolve_model(model_key)
        self.client = None
        self.activity_tracker = ActivityTracker()
        self.is_initialized = False
        self.init_lock = asyncio.Lock()
        self.query_lock = asyncio.Lock()
        self.last_activity = datetime.now()
        self.conversation_history = []
        self.current_agent = None  # Track active specialist for error handling
        self.chief_of_staff_ran = False  # Track if Chief of Staff has run for this session
        self._query_cancelled = False  # Track if current query was cancelled
        self._needs_client_reset = False  # Track if client needs reinitialization
        self.agent_statuses: dict[str, str] = {}  # {agent_name: "running"/"complete"/"error"}
        self._agent_by_id: dict[str, str] = {}    # SDK agent_id -> agent_type

        # Cost tracking and execution tracing
        self.cost_tracker = CostTracker(model=self.model_id.rsplit('-', 1)[0])
        self.trace_logger = TraceLogger()
        self.turns = []
        self.previous_cumulative_cost = 0.0
        self._client_cost_base = 0.0

        # The run directory is created on the first query, not here, so its
        # RUN_ID can carry a slug of the actual question — a run is far easier
        # to find later as `20260730-142233-il-33-safety-in-asthma-a1b2c3d4`
        # than as a bare UUID.
        self.run = None
        self.run_id = None
        self.workspace_dir = None
        self.audit = None
        self.runtime_paths = None

        # Claims filed by the CSO this session (reviewer comment R2.3).
        self.claim_set = ClaimSet()
        self.claim_errors: list[str] = []

    def ensure_run(self, query: str = "") -> RunManifest:
        """Create this session's run directory. Idempotent — first call wins.

        Creates runs/<RUN_ID>/ with a manifest, per-agent work trees, and
        separate inputs/evidence/logs/report areas. Replaces the flat
        web_workspace/<uuid>/ that reviewer comment R2.5 found unauditable.
        """
        if self.run is not None:
            return self.run

        self.run = RunManifest.create(
            RUNS_DIR, query=query, config=self._pinned_config()
        )
        self.run_id = self.run.run_id
        self.workspace_dir = self.run.run_dir
        self.run.agent_dir(CSO_DIR)

        # Copy .claude/skills directory to the run workspace for SDK access
        skills_src = Path(__file__).parent / '.claude' / 'skills'
        skills_dst = self.workspace_dir / '.claude' / 'skills'
        if skills_src.exists() and not skills_dst.exists():
            skills_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skills_src, skills_dst)

        # Copy environment spec so agents can check available packages
        env_src = Path(__file__).parent / 'environment.yml'
        env_dst = self.workspace_dir / 'environment.yml'
        if env_src.exists() and not env_dst.exists():
            shutil.copy2(env_src, env_dst)

        self.run.write()
        self.audit = SessionAudit(self.run, self.trace_logger)
        print(f"[Session {self.session_id}] run: {self.workspace_dir}")
        return self.run

    def _pinned_config(self) -> dict:
        """Everything needed to interpret — and attempt to reproduce — this run.

        Prompt files are recorded by content hash rather than by path: a run is
        only reproducible if you know which version of the system prompts it
        actually used, and those files change between runs.
        """
        import platform

        cfg = {
            "session_id": self.session_id,
            "cso_model": self.model_id,
            "specialist_model": self.model_id,
            "specialist_model_label": self.model_key,
            "effort": "xhigh",
            "max_turns": 100,
            "python": platform.python_version(),
            "host": platform.node(),
            "started": datetime.now().isoformat(),
            "interface": "web_or_batch",
            "audit_required": False,
        }
        try:
            from claude_agent_sdk import __version__ as _sdk_v
            cfg["claude_agent_sdk"] = _sdk_v
        except Exception:
            pass
        try:
            cfg["git_commit"] = subprocess.run(
                ["git", "-C", str(Path(__file__).parent), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() or None
        except Exception:
            cfg["git_commit"] = None

        prompt_dir = Path(__file__).parent / "src" / "agents"
        hashes = {}
        for f in sorted(prompt_dir.rglob("*.md")):
            try:
                hashes[str(f.relative_to(prompt_dir))] = hashlib.sha256(
                    f.read_bytes()
                ).hexdigest()[:16]
            except OSError:
                continue
        cfg["prompt_sha256"] = hashes

        mcp_path = Path(__file__).parent / "mcp_config.json"
        if mcp_path.exists():
            try:
                cfg["mcp_servers"] = sorted(
                    json.loads(mcp_path.read_text()).get("mcpServers", {})
                )
            except (json.JSONDecodeError, OSError):
                pass
        return cfg

    # Display names for agents (slug → readable)
    AGENT_DISPLAY_NAMES = {
        'genomics-analyst': 'Genomics Analyst',
        'functional-genomics-analyst': 'Functional Genomics Analyst',
        'single-cell-analyst': 'Single Cell Analyst',
        'bio-pathways-ppi-analyst': 'Pathways & PPI Analyst',
        'fda-safety-officer': 'FDA Safety Officer',
        'clinical-trialist': 'Clinical Trialist',
        'trial-matching-specialist': 'Trial Matching Specialist',
        'target-biologist': 'Target Biologist',
        'medchem-pharmacologist': 'MedChem Pharmacologist',
        'chief-of-staff': 'Chief of Staff',
        'scientific-reviewer': 'Scientific Reviewer',
    }

    def render_agent_status_bar(self) -> str:
        """Render the agent status bar HTML from tracked agent statuses."""
        if not self.agent_statuses:
            return '<div class="agent-status"></div>'
        badges = []
        for agent, status in self.agent_statuses.items():
            label = self.AGENT_DISPLAY_NAMES.get(agent, agent.replace('-', ' ').title())
            if status == "running":
                badges.append(f'<span class="agent-badge running">⏳ {label}</span>')
            elif status == "complete":
                badges.append(f'<span class="agent-badge complete">● {label}</span>')
            else:
                badges.append(f'<span class="agent-badge error">● {label}</span>')
        header = '<span class="agent-status-header">Active R&amp;D</span>'
        return f'<div class="agent-status active">{header}{" · ".join(badges)}</div>'

    def _capture_artifacts(self, agent: str, tool_use_id: str = None) -> list[str]:
        return self.audit.capture(agent, tool_use_id) if self.audit else []

    def _build_trace_hooks(self):
        """Use the shared audit lifecycle while updating web activity badges."""
        def on_subagent_start(event):
            agent = event.get('agent_type') or 'unknown'
            self._agent_by_id[event.get('agent_id', '')] = agent
            self.agent_statuses[agent] = "running"
            self.current_agent = agent
            division = AGENT_DIVISIONS.get(agent, 'Unknown')
            self.activity_tracker.add_event("agent", f"[{division}] {agent}", "", "running")

        def on_subagent_stop(event):
            agent = event.get('agent_type') or self._agent_by_id.get(event.get('agent_id'), 'unknown')
            self.agent_statuses[agent] = "complete"
            for index, activity in enumerate(self.activity_tracker.events):
                if activity.status == "running" and agent in activity.title:
                    self.activity_tracker.update_status(index, "complete")
                    break

        return self.audit.build_hooks(on_subagent_start, on_subagent_stop)

    async def initialize(self):
        """Initialize the CSO client (lazy initialization)"""
        async with self.init_lock:
            if self.is_initialized:
                return

            if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
                raise ValueError(
                    "ANTHROPIC_API_KEY is not set. Configure it in the project-root "
                    ".env or export it before starting research."
                )
            require_reference_data()
            self.ensure_run()

            from claude_agent_sdk import ClaudeAgentOptions
            from claude_agent_sdk.types import ThinkingConfigAdaptive

            # Load configuration
            prompts = load_prompts()
            mcp_config_path = Path(__file__).parent / 'mcp_config.json'
            with open(mcp_config_path) as f:
                mcp_config = json.load(f)
            mcp_servers = scoped_mcp_servers(
                resolve_mcp_servers(mcp_config.get('mcpServers', {}), Path(__file__).parent),
                self.run.run_dir,
            )

            # Build specialist agents with workspace directory
            specialist_agents = build_specialist_agents(
                prompts, workspace_dir=str(self.workspace_dir), specialist_model=self.model_id
            )

            # Build security hooks + callback for this session's workspace.
            # Hooks apply to the parent CSO agent; can_use_tool callback
            # applies to subagent tool calls (hooks do NOT propagate to
            # subagents, so the callback ensures MCP tools remain usable).
            self.runtime_paths = RuntimePaths(self.workspace_dir)
            self._client_cost_base = self.previous_cumulative_cost
            security_hooks, session_callback = _build_session_security(
                str(self.workspace_dir), self.runtime_paths,
            )

            # Build trace hooks for cost tracking and execution tracing
            trace_hooks = self._build_trace_hooks()

            # Merge security hooks and trace hooks
            merged_hooks = dict(security_hooks)
            for hook_event, matchers in trace_hooks.items():
                if hook_event in merged_hooks:
                    merged_hooks[hook_event] = merged_hooks[hook_event] + matchers
                else:
                    merged_hooks[hook_event] = matchers

            # CSO options with session-specific workspace
            # Note: SDK automatically finds skills in .claude/skills/ at project root
            cso_options = ClaudeAgentOptions(
                model=self.model_id,
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
                    # Audit record: the analysis plan and claim-evidence objects
                    'mcp__provenance__write_plan', 'mcp__provenance__record_claims',
                    'mcp__provenance__list_artifacts',
                ],
                agents=specialist_agents,
                mcp_servers=mcp_servers,
                max_turns=100,
                cwd=str(self.workspace_dir),
                permission_mode='bypassPermissions',
                hooks=merged_hooks,
                can_use_tool=session_callback,
                session_id=self.runtime_paths.session_id,
                env={**self.runtime_paths.sdk_env, 'VBT_RUN_DIR': str(self.run.run_dir)},
                thinking=ThinkingConfigAdaptive(type="adaptive", display="summarized"),
                effort="xhigh",
            )

            # Initialize with retry logic
            self.activity_tracker.add_event(
                "system", "Initializing CSO",
                "Connecting to MCP servers...", "running"
            )

            self.client = await RobustClaudeSDKClient(
                options=cso_options,
                max_retries=5,          # More retries for robustness
                initial_delay=3.0,      # Longer initial delay
                backoff_factor=1.5,
                pre_warm=True,          # True MCP initialization verification
                patch_timeout=True,     # Patch SDK timeout from 60s to 180s
                verbose=True            # Show initialization progress
            ).__aenter__()

            self.activity_tracker.update_status(0, "complete")
            self.is_initialized = True

    async def cleanup(self):
        """Cleanup the CSO client"""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                print(f"[WARNING] Error during client disconnect: {e}")
            self.client = None
            self.is_initialized = False

    def abort_query(self):
        """
        Abort the current query. Called when user clicks stop.
        The active generator saves its interrupted turn and releases the lock.
        """
        self._query_cancelled = True
        self._needs_client_reset = True
        self.current_agent = None

    async def reset_client(self, conversation_history: list = None):
        """
        Reset the client connection after cancellation.

        Args:
            conversation_history: Optional list of conversation messages to restore context.
                                  Format: [{"role": "user"|"assistant", "content": "..."}]
        """
        print(f"[Session {self.session_id}] Resetting client after cancellation...")
        self._needs_client_reset = False
        self._query_cancelled = False

        # Store conversation history for context restoration
        if conversation_history:
            self.conversation_history = conversation_history

        # Cleanup existing client
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                print(f"[WARNING] Error during client disconnect: {e}")
            self.client = None

        # Mark as not initialized to force reinitialization on next query
        self.is_initialized = False

    def build_context_summary(self, history: list) -> str:
        """
        Build a context summary from conversation history for restoration after reset.

        Args:
            history: List of conversation messages [{"role": "user"|"assistant", "content": "..."}]

        Returns:
            A formatted context summary string
        """
        if not history:
            return ""

        # Filter out system messages, typing indicators, and stop messages
        relevant_messages = []
        for msg in history:
            content = msg.get("content", "")
            role = msg.get("role", "")

            # Skip typing indicators and stop messages
            if "typing-indicator" in content or "Query stopped" in content:
                continue
            # Skip empty content
            if not content.strip():
                continue

            relevant_messages.append(msg)

        if not relevant_messages:
            return ""

        # Build summary - include last few exchanges for context
        # Limit to last 6 messages (3 exchanges) to avoid token bloat
        recent = relevant_messages[-6:] if len(relevant_messages) > 6 else relevant_messages

        summary_parts = ["[CONVERSATION CONTEXT - Previous discussion before interruption:]"]
        for msg in recent:
            role = "User" if msg.get("role") == "user" else "Assistant"
            content = msg.get("content", "")
            # Truncate very long messages
            if len(content) > 500:
                content = content[:500] + "... [truncated]"
            summary_parts.append(f"{role}: {content}")

        summary_parts.append("[END CONTEXT - Continue the conversation naturally.]")

        return "\n\n".join(summary_parts)

    def touch(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()


class SessionManager:
    """Manages multiple user sessions with automatic cleanup"""

    def __init__(self, max_sessions: int = 5, session_timeout_minutes: int = 30):
        self.sessions: dict[str, CSOSession] = {}
        self.max_sessions = max_sessions
        self.session_timeout = timedelta(minutes=session_timeout_minutes)
        self.lock = threading.Lock()

    def get_or_create_session(self, session_id: str, model_key: str = "Sonnet 4.5 (default)") -> CSOSession:
        """Get existing session or create new one"""
        with self.lock:
            # Clean up expired sessions first
            self._cleanup_expired_sessions()

            if session_id in self.sessions:
                session = self.sessions[session_id]
                session.touch()
                return session

            # Check if we can create a new session
            if len(self.sessions) >= self.max_sessions:
                # Remove oldest session (with proper cleanup)
                oldest_id = min(self.sessions.keys(),
                               key=lambda k: self.sessions[k].last_activity)
                self._schedule_cleanup(self.sessions[oldest_id])
                del self.sessions[oldest_id]

            # Create new session with selected model
            session = CSOSession(session_id, model_key=model_key)
            self.sessions[session_id] = session
            return session

    def _cleanup_expired_sessions(self):
        """Remove sessions that have been inactive too long"""
        now = datetime.now()
        expired = [
            sid for sid, session in self.sessions.items()
            if now - session.last_activity > self.session_timeout
        ]
        for sid in expired:
            self._schedule_cleanup(self.sessions[sid])
            del self.sessions[sid]

    def _schedule_cleanup(self, session: CSOSession):
        """Schedule async cleanup of a session in a background thread"""
        def cleanup_in_thread():
            try:
                # Create event loop for this thread if needed
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(session.cleanup())
                finally:
                    loop.close()
            except Exception as e:
                print(f"[WARNING] Session cleanup failed: {e}")

        # Run cleanup in background to avoid blocking
        cleanup_thread = threading.Thread(target=cleanup_in_thread, daemon=True)
        cleanup_thread.start()

    def get_active_session_count(self) -> int:
        """Return number of active sessions"""
        with self.lock:
            self._cleanup_expired_sessions()
            return len(self.sessions)


# Global session manager
session_manager = SessionManager(max_sessions=20, session_timeout_minutes=480)  # 8 hours


# =============================================================================
# Chief of Staff Helper
# =============================================================================

# =============================================================================
# Chat Handler
# =============================================================================

# Division mapping for display
AGENT_DIVISIONS = {
    'genomics-analyst': 'Target ID & Prioritization',
    'functional-genomics-analyst': 'Target ID & Prioritization',
    'single-cell-analyst': 'Target ID & Prioritization',
    'bio-pathways-ppi-analyst': 'Target Safety',
    'fda-safety-officer': 'Target Safety & Clinical Officers',
    'clinical-trialist': 'Clinical Officers',
    'trial-matching-specialist': 'Clinical Officers',
    'target-biologist': 'Modality Selection',
    'medchem-pharmacologist': 'Modality Selection',
    'chief-of-staff': 'Intelligence & Strategy',
    'scientific-reviewer': 'Quality Assurance',
}


def _finalize_run(session: CSOSession, *, interrupted: bool = False):
    """Persist the shared audit record after every turn and refresh the run list."""
    if session.audit is None:
        return
    try:
        session.claim_set, coverage = session.audit.finish_turn(
            session.turns, session.previous_cumulative_cost, interrupted=interrupted,
        )
        # Claims can be revised without changing their count.
        session._evidence_cache_key = None
        if not coverage['ok']:
            details = '; '.join(problem['detail'] for problem in coverage['problems'])
            session.activity_tracker.add_event("system", "Evidence audit incomplete", details, "error")
        update_index(RUNS_DIR)
    except Exception as error:
        session.audit.note_error(f'Failed to finalize run: {error}')
        session.activity_tracker.add_event("system", "Audit recording failed", str(error), "error")
        raise


def _write_session_cost_report(session: CSOSession):
    """Write cost_report.json, trace.jsonl, and transcript.md to the run's logs/."""
    if session.run is None:
        return
    now = datetime.now()
    try:
        # cost_report.json
        report = {
            "session_id": session.session_id,
            "run_id": session.run_id,
            "start_time": session.turns[0]["timestamp"] if session.turns else now.isoformat(),
            "end_time": now.isoformat(),
            "total_cost_usd": round(session.previous_cumulative_cost, 6),
            "num_turns": len(session.turns),
            "trace_events": len(session.trace_logger.events),
            "turns": session.turns,
        }
        log_dir = session.workspace_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        report_path = log_dir / "cost_report.json"
        write_json_atomic(report_path, report)

        # trace.jsonl
        trace_path = log_dir / "trace.jsonl"
        session.trace_logger.write_jsonl(trace_path)

        # transcript.md
        lines = [
            f"# Virtual Biotech Session",
            f"**Run:** {session.run_id}",
            f"**Session:** {session.session_id}",
            f"**Started:** {report['start_time']}",
            f"**Last updated:** {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Total cost:** ${session.previous_cumulative_cost:.2f}",
            "",
        ]
        for t in session.turns:
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

        transcript_path = log_dir / "transcript.md"
        write_text_atomic(transcript_path, "\n".join(lines))
    except Exception as e:
        session.audit.note_error(f'Failed to write cost report: {e}')
        raise


async def process_message(message: str, history: list, session_id: str, model_key: str = "Sonnet 4.5 (default)"):
    """Stream one web or batch turn using the shared conversational audit lifecycle."""
    from claude_agent_sdk import (
        AssistantMessage, TextBlock, ToolUseBlock, ResultMessage, ThinkingBlock,
        TaskStartedMessage, TaskProgressMessage, TaskNotificationMessage,
    )

    session = session_manager.get_or_create_session(session_id, model_key=model_key)
    if session.query_lock.locked():
        history = history + [{"role": "user", "content": message}, {
            "role": "assistant", "content": "⚠️ **Your previous query is still processing.** Please wait for it to complete.",
        }]
        yield history, session.activity_tracker.format_markdown(), session_id, session.render_agent_status_bar()
        return

    async with session.query_lock:
        session.touch()
        context_summary = ""
        if session._needs_client_reset:
            context_summary = session.build_context_summary(history)
            await session.reset_client(conversation_history=history)
        session._query_cancelled = False
        session.ensure_run(message)
        if not session.is_initialized:
            await session.initialize()
        require_reference_data()

        session.activity_tracker.clear()
        session.agent_statuses.clear()
        session.current_agent = None
        history = history + [{"role": "user", "content": message}]
        session.activity_tracker.add_event("system", "Processing query", message[:80], "running")
        query_message = message
        if context_summary:
            query_message = f"{context_summary}\n\n[NEW USER MESSAGE:]\n{message}"
            session.activity_tracker.add_event("system", "Context restored", "Previous conversation included", "complete")

        turn_number = len(session.turns) + 1
        turn_start = datetime.now()
        trace_start = len(session.trace_logger.events)
        session.audit.begin_turn(message, turn_number)
        response_text = ""
        # The web view includes collapsible reasoning; saved answers do not.
        display_text = ""
        mcp_tools_used = []
        cumulative_cost = session.previous_cumulative_cost
        previous_cost = cumulative_cost
        result_received = False
        interrupted = True
        error_detail = ""
        history = history + [{"role": "assistant", "content":
            '<span class="typing-indicator">🔄 CSO is analyzing your query...</span>'}]

        try:
            # Start the trace boundary before sending: callbacks can fire during
            # query(), including specialist dispatches without outer Task blocks.
            await session.client.query(query_message)
            yield history, session.activity_tracker.format_markdown(), session_id, session.render_agent_status_bar()
            async for msg in session.client.receive_response():
                if session._query_cancelled:
                    break
                if isinstance(msg, AssistantMessage):
                    session.cost_tracker.process_message(msg)
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            if response_text and not response_text.endswith('\n'):
                                response_text += "\n\n"
                            response_text += block.text
                            if display_text and not display_text.endswith('\n'):
                                display_text += "\n\n"
                            display_text += block.text
                            history[-1]["content"] = _strip_claim_refs(display_text)
                        elif isinstance(block, ThinkingBlock):
                            session.trace_logger.thinking(block.thinking)
                            display_text += f"\n\n<cso-thinking>{block.thinking}</cso-thinking>\n\n"
                            history[-1]["content"] = _strip_claim_refs(display_text)
                        elif isinstance(block, ToolUseBlock):
                            if block.name.startswith('mcp__'):
                                mcp_tools_used.append(block.name)
                                parts = block.name.split('__')
                                session.activity_tracker.add_event("mcp", ': '.join(parts[1:]), "", "running")
                            elif block.name != 'TodoWrite':
                                session.activity_tracker.add_event("tool", block.name, "", "running")
                        yield history, session.activity_tracker.format_markdown(), session_id, session.render_agent_status_bar()
                elif isinstance(msg, (TaskStartedMessage, TaskProgressMessage, TaskNotificationMessage)):
                    yield history, session.activity_tracker.format_markdown(), session_id, session.render_agent_status_bar()
                elif isinstance(msg, ResultMessage):
                    result_received = True
                    session.cost_tracker.process_result(msg)
                    cumulative_cost = session._client_cost_base + (getattr(msg, 'total_cost_usd', 0.0) or 0.0)
                    interrupted = bool(msg.is_error)
                    if not response_text and msg.result:
                        response_text = msg.result
                        display_text += ("\n\n" if display_text else "") + msg.result
                    if interrupted:
                        error_detail = '; '.join(msg.errors or []) or msg.result or 'The model did not complete this turn.'
        except BaseException as error:
            error_detail = str(error) or type(error).__name__
            raise
        finally:
            interrupted = interrupted or not result_received or session._query_cancelled or bool(error_detail)
            events = session.trace_logger.events_since(trace_start)
            agents = agents_in_events(events)
            tools = list(dict.fromkeys(mcp_tools_used + [
                event['tool_name'] for event in events
                if str(event.get('tool_name', '')).startswith('mcp__')
            ]))
            warning = data_failure_notice(events)
            if warning:
                response_text += "\n\n" + warning
                display_text += "\n\n" + warning
                session.activity_tracker.add_event("system", "Data source unavailable", warning, "error")
            if interrupted:
                detail = error_detail or 'The response ended before completion.'
                notice = f"\n\nTurn interrupted: {detail} Evidence coverage is incomplete."
                response_text += notice
                display_text += notice
                session._needs_client_reset = True
            session.turns.append({
                "turn": turn_number, "timestamp": turn_start.isoformat(),
                "prompt": message, "response": response_text,
                "cost_usd": round(max(0.0, cumulative_cost - previous_cost), 6),
                "cumulative_cost_usd": round(cumulative_cost, 6),
                "agents_dispatched": agents, "mcp_tools_used": tools,
                "response_length_chars": len(response_text),
                "thinking_traces": session.trace_logger.extract_thinking(events),
                "subagent_traces": session.trace_logger.extract_subagent_traces(events),
                "tool_errors": tool_failures(events),
                "status": "interrupted" if interrupted else "completed",
            })
            session.previous_cumulative_cost = cumulative_cost
            for index, activity in enumerate(session.activity_tracker.events):
                if activity.status == "running":
                    session.activity_tracker.update_status(index, "error" if interrupted else "complete")
            session.activity_tracker.add_event(
                "system", f"Turn {turn_number}: ${max(0.0, cumulative_cost - previous_cost):.2f}",
                f"Total: ${cumulative_cost:.2f} | Agents: {', '.join(agents) or '(none)'}",
                "error" if interrupted else "complete",
            )
            session.current_agent = None
            for agent in session.agent_statuses:
                session.agent_statuses[agent] = "error" if interrupted else "complete"
            _write_session_cost_report(session)
            _finalize_run(session, interrupted=interrupted)
            history[-1]["content"] = render_claim_refs(display_text, session.claim_set)

        yield history, session.activity_tracker.format_markdown(), session_id, session.render_agent_status_bar()


def _evidence_outputs(session_id: str) -> tuple[str, str]:
    """(panel HTML, static markdown) for the current session's claims.

    Cached on the session and keyed by claim count: this is called on every
    streamed chunk, but claims only change once per turn.
    """
    session = session_manager.sessions.get(session_id)
    cs = session.claim_set if session else ClaimSet()
    key = len(cs.claims)
    if session is not None and getattr(session, "_evidence_cache_key", None) == key:
        return session._evidence_cache
    out = (render_evidence_panel(cs), render_evidence_static(cs))
    if session is not None:
        session._evidence_cache_key = key
        session._evidence_cache = out
    return out


async def async_process_message(message: str, history: list, session_id: str, model_key: str = "Sonnet 4.5 (default)"):
    """Async wrapper for message processing with proper streaming and error recovery"""
    if not message.strip():
        yield (history, "*No activity yet. Send a message to start.*", session_id,
               '<div class="agent-status"></div>', *_evidence_outputs(session_id))
        return

    # Generate session ID if not provided
    if not session_id:
        session_id = str(uuid.uuid4())

    try:
        async for result in process_message(message, history, session_id, model_key=model_key):
            yield (*result, *_evidence_outputs(session_id))
    except Exception as e:
        # Error recovery: display error and reset UI state
        error_msg = str(e)
        error_traceback = traceback.format_exc()

        # Log the full error for debugging
        print(f"[ERROR] Agent execution failed: {error_msg}")
        print(f"[TRACEBACK]\n{error_traceback}")

        # Check if a specialist was running when error occurred
        current_agent = None
        if session_id and session_id in session_manager.sessions:
            session = session_manager.sessions[session_id]
            current_agent = session.current_agent

        # Create user-friendly error message with specialist-specific guidance
        if isinstance(e, DataReadinessError):
            user_error = f"⚠️ **Reference data is not ready.**\n\n{error_msg}"
        elif "exit code" in error_msg.lower():
            user_error = f"⚠️ **A tool command failed.**\n\n```\n{error_msg[:500]}\n```\n\nYou can try rephrasing your query or asking about a different aspect."
        elif "timeout" in error_msg.lower():
            if current_agent == 'single-cell-analyst':
                user_error = f"⚠️ **Single-cell analysis timed out.**\n\nThe CELLxGENE query took too long. Try:\n- Narrowing the cell type or tissue filter\n- Querying fewer genes\n- Using `count_cells()` before downloading to check dataset size"
            elif current_agent == 'genomics-analyst':
                user_error = f"⚠️ **Genomics analysis timed out.**\n\nThe Open Targets query took too long. Try:\n- Querying a single gene instead of multiple\n- Using more specific disease filters\n- Narrowing the association score threshold"
            elif current_agent == 'functional-genomics-analyst':
                user_error = f"⚠️ **Functional genomics analysis timed out.**\n\nThe DepMap query took too long. Try:\n- Querying fewer genes or cell lines\n- Using more specific cancer type filters\n- Simplifying the essentiality comparison"
            else:
                user_error = f"⚠️ **The operation timed out.**\n\nThe analysis took too long to complete. Try a more specific query or smaller scope."
        elif "memory" in error_msg.lower() or "ENOMEM" in error_msg:
            if current_agent == 'single-cell-analyst':
                user_error = f"⚠️ **Single-cell analysis ran out of memory.**\n\nThe dataset was too large. Try:\n- Filtering to protein-coding genes: `adata[:, adata.var['feature_type'] == 'protein_coding']`\n- Reducing cell count with stricter filters\n- Using `get_expression_for_genes()` instead of downloading full dataset"
            else:
                user_error = f"⚠️ **Out of memory.**\n\nThe analysis required too much memory. Try querying fewer items or using filters."
        elif "connection" in error_msg.lower() or "mcp" in error_msg.lower():
            specialist_context = f" during **{current_agent}** analysis" if current_agent else ""
            user_error = f"⚠️ **Data connection failed{specialist_context}.**\n\nThe MCP server connection was interrupted. The session will reconnect automatically on your next query."
        else:
            specialist_context = f" during **{current_agent}** analysis" if current_agent else ""
            user_error = f"⚠️ **An error occurred{specialist_context}.**\n\n```\n{error_msg[:300]}\n```\n\nPlease try again or rephrase your query."

        # Update history with error message
        if history and history[-1].get("role") == "assistant":
            # Append to existing assistant message
            history[-1]["content"] += f"\n\n{user_error}"
        else:
            # Add new assistant message
            history = history + [{"role": "assistant", "content": user_error}]

        # Update activity tracker if session exists
        if session_id and session_id in session_manager.sessions:
            session = session_manager.sessions[session_id]
            # Clear current agent and mark all agent statuses as error
            session.current_agent = None
            for agent in session.agent_statuses:
                if session.agent_statuses[agent] == "running":
                    session.agent_statuses[agent] = "error"
            # Mark all running events as errors
            for i, event in enumerate(session.activity_tracker.events):
                if event.status == "running":
                    session.activity_tracker.update_status(i, "error")
            # Add error event
            session.activity_tracker.add_event(
                "system", "Error occurred",
                error_msg[:50] + "..." if len(error_msg) > 50 else error_msg,
                "error"
            )
            activity_md = session.activity_tracker.format_markdown()

            # Check if this is a critical error that requires session reset
            critical_error_patterns = [
                "connection", "disconnect", "timeout", "initialize",
                "mcp", "sdk", "client", "closed", "broken pipe"
            ]
            error_lower = error_msg.lower()
            if not isinstance(e, DataReadinessError) and any(
                pattern in error_lower for pattern in critical_error_patterns
            ):
                # Mark session for reinitialization on next query
                session.is_initialized = False
                session.client = None
                session.activity_tracker.add_event(
                    "system", "Session reset",
                    "Will reconnect on next query", "complete"
                )
                activity_md = session.activity_tracker.format_markdown()
        else:
            activity_md = "*Error occurred during processing.*"

        agent_status_html = '<div class="agent-status error">❌ Error - Ready for new query</div>'

        yield (history, activity_md, session_id, agent_status_html,
               *_evidence_outputs(session_id))


# =============================================================================
# Helper Functions
# =============================================================================

def check_password(password: str) -> bool:
    """Check if the provided password is correct"""
    return bool(APP_PASSWORD) and isinstance(password, str) and hmac.compare_digest(
        password.encode("utf-8"), APP_PASSWORD.encode("utf-8")
    )


def authenticate(_username: str, password: str) -> bool:
    """Authenticate the shared local app password through Gradio's server."""
    return check_password(password)


def get_session_files(session_id: str) -> tuple[list, list]:
    """
    Get all files generated in THIS SESSION's workspace for display.
    Returns: (image_files_for_gallery, all_files_for_download)
    """
    # Handle case where session_id is not yet initialized or is a function
    if not session_id or callable(session_id) or not isinstance(session_id, str):
        return [], []

    # The run directory for this session (falls back to the legacy flat layout
    # so sessions created before the change remain browsable). A session that
    # has not yet sent a query has no run directory at all.
    session = session_manager.sessions.get(session_id)
    if session is not None and session.workspace_dir is None:
        return [], []
    session_workspace = session.workspace_dir if session else WORKSPACE_DIR / session_id

    # Image extensions to display in gallery
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp'}

    # Get all files from this session's workspace only
    all_files = []
    image_files = []

    if session_workspace.exists():
        for file_path in session_workspace.rglob('*'):
            # Skip dotfiles and dot-directories (including .claude)
            if any(part.startswith('.') for part in file_path.parts):
                continue

            if file_path.is_file():
                all_files.append(str(file_path))
                if file_path.suffix.lower() in image_extensions:
                    image_files.append(str(file_path))

    # Sort by modification time (newest first), handling files that may have been deleted
    def safe_getmtime(path):
        try:
            return os.path.getmtime(path)
        except (OSError, FileNotFoundError):
            return 0  # Deleted files sort to end

    all_files.sort(key=safe_getmtime, reverse=True)
    image_files.sort(key=safe_getmtime, reverse=True)

    # Filter out any files that no longer exist
    all_files = [f for f in all_files if os.path.exists(f)]
    image_files = [f for f in image_files if os.path.exists(f)]

    return image_files, all_files


def export_chat_markdown(history: list) -> str:
    """Export chat history as markdown"""
    if not history:
        return None

    lines = [
        "# The Virtual Biotech - Conversation Export",
        f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        ""
    ]

    for msg in history:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        # Handle content that might be a list (newer Gradio versions)
        if isinstance(content, list):
            content = "\n".join(str(item) for item in content)
        elif not isinstance(content, str):
            content = str(content)

        if role == "user":
            lines.append(f"## 🧑‍🔬 User")
            lines.append(content)
            lines.append("")
        elif role == "assistant":
            lines.append(f"## 🤖 CSO Agent")
            lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def zip_run_dir(run_dir: Path) -> Path:
    """Zip a run directory for download: MANIFEST, audit.html, every artifact,
    claims and provenance — the whole thing a reviewer would need, not just
    the chat transcript.

    Written to ``<RUNS_DIR>/.downloads/``, a sibling of the run directories
    rather than inside one. ``scan_runs`` only picks up directories that
    contain a ``MANIFEST.json``, so a dotdir with none is invisible to the
    Past Runs index and never mistaken for a run of its own.
    """
    cache_dir = run_dir.parent / ".downloads"
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"{run_dir.name}.zip"

    # Rebuild only if some file in the run is newer than the last zip — the
    # run directory only grows during a session, so this is a sufficient
    # staleness check without hashing everything on every click.
    if zip_path.exists():
        newest_member = max((p.stat().st_mtime for p in run_dir.rglob("*") if p.is_file()),
                            default=0)
        if zip_path.stat().st_mtime >= newest_member:
            return zip_path

    tmp_path = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(run_dir.rglob("*")):
            if f.is_file():
                zf.write(f, arcname=str(Path(run_dir.name) / f.relative_to(run_dir)))
    tmp_path.replace(zip_path)  # atomic: a concurrent reader never sees a partial zip
    return zip_path


def get_active_users_display() -> str:
    """Get HTML for active users counter"""
    count = session_manager.get_active_session_count()
    if count == 0:
        return '<div style="text-align: center; padding: 0.5rem;">👤 0 researchers online</div>'
    elif count == 1:
        return '<div style="text-align: center;">👤 1 researcher online</div>'
    else:
        return f'<div style="text-align: center;">👥 {count} researchers online</div>'


# =============================================================================
# Gradio Interface
# =============================================================================

def create_interface(authenticated_by_server: bool = False):
    """Create the Gradio interface"""

    # Stanford Cardinal color ramp for Gradio theme engine
    stanford_cardinal = gr.themes.Color(
        c50='#fef2f2', c100='#fde8e8', c200='#f9c4c4',
        c300='#f09898', c400='#d94f4f', c500='#8C1515',
        c600='#6B1010', c700='#5C0D0D', c800='#450A0A',
        c900='#350808', c950='#2A0606',
    )

    theme = gr.themes.Base(
        primary_hue=stanford_cardinal,
        neutral_hue='gray',
        spacing_size='sm',
    ).set(
        # Primary buttons: Stanford cardinal
        button_primary_background_fill='#8C1515',
        button_primary_background_fill_hover='#6B1010',
        button_primary_text_color='white',
        button_primary_border_color='transparent',
        button_primary_border_color_hover='transparent',
        # Cancel/stop buttons
        button_cancel_background_fill='#DC3545',
        button_cancel_background_fill_hover='#C82333',
        button_cancel_text_color='white',
        button_cancel_border_color='transparent',
        # Focus rings: cardinal instead of default orange
        input_border_color_focus='#8C1515',
        color_accent='#8C1515',
        border_color_accent='#8C1515',
    )

    # Layout CSS for custom components (colors use CSS vars, not hardcoded)
    custom_css = """
    :root {
        --stanford-cardinal: #8C1515;
        --stanford-cardinal-dark: #6B1010;
        --stanford-cool-grey: #4D4F53;
    }

    /* --- Global layout --- */
    body, .gradio-container { font-size: 16px !important; }
    .gradio-container { padding-top: 0 !important; margin-top: 0 !important; }
    .gr-column { padding-top: 0 !important; margin-top: 0 !important; }
    .contain { gap: 0 !important; }

    .main-screen-container {
        padding: 0 3rem !important;
        margin: 0 auto !important;
        max-width: 1600px;
        gap: 0 !important;
    }
    .main-screen-container > .gr-row {
        padding-left: 2.5rem !important;
        padding-right: 2.5rem !important;
    }

    /* --- Font size overrides --- */
    .message { font-size: 1.0em !important; }
    textarea, input[type="text"], input[type="password"] { font-size: 1.1em !important; }
    button { font-size: 1.15em !important; }
    .prose { font-size: 1.15em !important; }

    /* --- Outline / animation cleanup --- */
    *, *::before, *::after { outline: none !important; }
    *:focus, *:focus-visible, *:focus-within { outline: none !important; }
    .chatbot, .chatbot * { caret-color: transparent !important; }

    /* --- Header --- */
    .main-header {
        background: linear-gradient(135deg, var(--stanford-cardinal) 0%, var(--stanford-cardinal-dark) 100%);
        color: white;
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-top: -2rem !important;
        margin-bottom: 0;
        box-shadow: 0 4px 6px rgba(140, 21, 21, 0.2);
    }
    .main-header h1 { margin: 0 0 0.5rem 0; font-size: 2.2rem; font-weight: 600; color: white; }
    .main-header p { margin: 0; opacity: 0.9; font-size: 1.2rem; color: white; }
    .header-logo { height: 40px; filter: brightness(0) invert(1); margin-right: 1rem; }

    /* --- System introduction --- */
    .system-intro {
        background: #F8F8F8;
        border-top: 3px solid var(--stanford-cardinal);
        padding: 1rem 1.5rem;
        margin: 1rem 0 2.5rem 0;
        border-radius: 8px;
        font-size: 1.05rem;
        line-height: 1.6;
        color: #2c3e50;
    }
    .system-intro p { margin: 0.5rem 0; }
    .system-intro p:first-child { margin-top: 0; }
    .system-intro p:last-child { margin-bottom: 0; }

    /* --- Claim → evidence (reviewer comment R2.3) --- */
    /* A numbered marker after an assertion; click opens the evidence panel. */
    sup.claim-ref {
        display: inline-block;
        min-width: 1.15em;
        padding: 0 0.28em;
        margin-left: 0.12em;
        border-radius: 0.55em;
        background: #8C1515;
        color: #fff;
        font-size: 0.66em;
        font-weight: 700;
        line-height: 1.5;
        text-align: center;
        cursor: pointer;
        vertical-align: super;
        user-select: none;
    }
    sup.claim-ref:hover { background: #B83A3A; }
    sup.claim-ref.active { outline: 2px solid #8C1515; outline-offset: 1px; }
    /* Filed, but nothing in it could be independently verified. */
    sup.claim-ref.unverified { background: #B0842B; }
    /* Cited in prose but never filed — deliberately conspicuous. */
    sup.claim-ref.missing {
        background: transparent;
        color: #B83A3A;
        border: 1px dashed #B83A3A;
        font-weight: 600;
    }

    #vbt-evidence { margin-bottom: 0.75rem; }
    .ev-empty {
        font-size: 0.86em;
        color: #6b6b6b;
        line-height: 1.5;
        padding: 0.4rem 0 0.2rem;
    }
    .ev-card {
        background: #fff;
        border: 1px solid #E0E0E0;
        border-left: 3px solid #8C1515;
        border-radius: 8px;
        padding: 0.6rem 0.75rem;
    }
    .ev-id {
        font: 700 0.68em ui-monospace, monospace;
        letter-spacing: 0.06em;
        color: #8C1515;
        text-transform: uppercase;
    }
    .ev-text { font-size: 0.92em; font-weight: 600; margin: 0.15rem 0 0.2rem; }
    .ev-meta { font-size: 0.76em; color: #6b6b6b; margin-bottom: 0.5rem; }
    .ev-list { margin: 0; padding-left: 1rem; font-size: 0.82em; }
    .ev-list li { margin: 0.35rem 0; line-height: 1.45; }
    .ev-list code { font-size: 0.92em; overflow-wrap: anywhere; }
    .ev-kind {
        display: inline-block;
        font-size: 0.82em;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #6b6b6b;
        margin-right: 0.3rem;
    }
    .ev-note { display: block; color: #4D4F53; }
    .ev-dim { color: #6b6b6b; }
    .ev-badge {
        display: inline-block;
        font-size: 0.8em;
        padding: 0 0.4em;
        margin-left: 0.35rem;
        border-radius: 10px;
        white-space: nowrap;
    }
    /* Three states. Only `unresolved` is a defect, so only it is red; an
       external citation is a neutral fact about the evidence kind. */
    .ev-badge.verified   { color: #0C7A0C; border: 1px solid #9ED09E; }
    .ev-badge.external   { color: #6b6b6b; border: 1px solid #D6D6D6; }
    .ev-badge.unresolved { color: #B83A3A; border: 1px solid #E0A8A8; }

    /* One card per claim in the "All claims & evidence" accordion. Same card
       markup as the click-panel above it, so the two surfaces cannot drift. */
    .ev-cards { display: flex; flex-direction: column; gap: 0.6rem; }
    .ev-card-static { box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05); }
    .ev-card-static .ev-id {
        display: flex;
        align-items: center;
        gap: 0.4rem;
        margin-bottom: 0.15rem;
    }
    .ev-num {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 1.35em;
        height: 1.35em;
        padding: 0 0.3em;
        border-radius: 999px;
        background: #8C1515;
        color: #fff;
        font-size: 0.95em;
        letter-spacing: 0;
    }
    .ev-card-weak { border-left-color: #B0842B; }
    .ev-flag {
        margin-left: auto;
        color: #8A6A1F;
        text-transform: none;
        letter-spacing: 0;
        font-weight: 600;
    }
    .ev-none { color: #6b6b6b; font-style: italic; }

    .runs-table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
    .runs-table th {
        text-align: left;
        font-size: 0.85em;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #4D4F53;
        border-bottom: 1px solid #C0C0C0;
        padding: 0.35rem 0.5rem;
    }
    .runs-table td {
        padding: 0.35rem 0.5rem;
        border-bottom: 1px solid #EDEDED;
        vertical-align: top;
    }

    /* --- Activity panel --- */
    .activity-panel {
        background: #F4F4F4;
        border-radius: 12px;
        padding: 1rem;
        border: 1px solid #E0E0E0 !important;
    }
    .activity-panel > * {
        border: none !important;
    }
    .activity-log {
        font-size: 1.0em;
        max-height: 650px;
        overflow-y: auto;
        padding: 1rem;
        line-height: 1.5;
    }

    /* --- Example / secondary buttons --- */
    .example-btn {
        margin: 4px !important;
        border: 1px solid var(--stanford-cardinal) !important;
        color: var(--stanford-cardinal) !important;
        background: white !important;
    }
    .example-btn:hover {
        background: var(--stanford-cardinal) !important;
        color: white !important;
    }

    /* --- Stop button --- */
    .stop-btn { font-weight: 600 !important; }

    /* --- Login screen --- */
    .login-container {
        max-width: 400px;
        margin: 4rem auto;
        padding: 2rem;
        background: white;
        border-radius: 12px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        border-top: 4px solid var(--stanford-cardinal);
    }

    /* --- Sidebar headers --- */
    .sidebar-header {
        color: var(--stanford-cardinal);
        font-weight: 600;
        border-bottom: 2px solid var(--stanford-cardinal);
        padding-bottom: 0.5rem;
        margin-bottom: 1rem;
    }
    .files-header {
        color: var(--stanford-cardinal);
        font-weight: 600;
        border-bottom: 2px solid var(--stanford-cardinal);
        padding-bottom: 0.5rem;
        margin-bottom: 1rem;
    }

    /* --- Division legend --- */
    .division-legend {
        background: white;
        border-radius: 8px;
        padding: 1rem;
        font-size: 1.0em;
        border: 1px solid #E0E0E0;
    }

    /* --- Active users counter --- */
    .active-users {
        background: linear-gradient(135deg, var(--stanford-cardinal) 0%, var(--stanford-cardinal-dark) 100%);
        color: white !important;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        margin-top: 1rem;
        text-align: center;
        font-size: 1.0em;
        font-weight: 500;
        box-shadow: 0 2px 4px rgba(140, 21, 21, 0.2);
    }
    .active-users * { color: white !important; }

    /* --- Files section --- */
    .files-section {
        margin-top: 1rem;
        padding: 1rem;
        background: white;
        border-radius: 8px;
        border: 1px solid #E0E0E0;
    }

    /* --- Typing indicator --- */
    .typing-indicator { color: var(--stanford-cardinal); font-style: italic; opacity: 0.8; }
    .typing-dots::after { content: ''; animation: ellipsis 1.5s infinite; }
    @keyframes ellipsis {
        0% { content: ''; } 25% { content: '.'; }
        50% { content: '..'; } 75% { content: '...'; } 100% { content: ''; }
    }

    /* --- Agent status line --- */
    .agent-status {
        background: #FFF9F0;
        border-left: 3px solid var(--stanford-cardinal);
        padding: 0.75rem 1rem;
        margin: 0.5rem 0;
        border-radius: 6px;
        font-size: 0.95rem;
        color: var(--stanford-cool-grey);
        min-height: 40px;
        display: flex;
        align-items: center;
    }
    .agent-status.active {
        background: #FFF4E6;
        flex-wrap: wrap;
        gap: 0.4rem;
    }
    .agent-status-header {
        font-size: 0.8rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--stanford-cardinal);
        margin-right: 0.5rem;
    }
    .agent-badge {
        display: inline-block;
        padding: 0.3rem 0.75rem;
        border-radius: 12px;
        font-size: 1.0rem;
        font-weight: 500;
        white-space: nowrap;
    }
    .agent-badge.running {
        background: #8B1A1A;
        color: #FFFFFF;
        animation: subtle-pulse 2s ease-in-out infinite;
    }
    .agent-badge.complete {
        background: #D4EDDA;
        color: #155724;
    }
    .agent-badge.error {
        background: #F5C6CB;
        color: #721C24;
    }
    @keyframes subtle-pulse {
        0%, 100% { opacity: 1; } 50% { opacity: 0.7; }
    }
    .agent-status.stopped {
        background: #FFF3CD;
        border-left-color: #856404;
        color: #856404;
        font-weight: 500;
    }
    .agent-status.error {
        background: #F8D7DA;
        border-left-color: #DC3545;
        color: #721C24;
        font-weight: 500;
    }
    """

    # JavaScript to force light mode
    force_light_mode_js = """
    (function() {
        const url = new URL(window.location);
        if (url.searchParams.get('__theme') !== 'light') {
            url.searchParams.set('__theme', 'light');
            window.location.href = url.href;
            return;
        }

        // ── Claim → evidence (reviewer comment R2.3) ──────────────────
        // Clicking a numbered marker in the CSO's answer opens what backs it.
        // Delegated from document so it keeps working as Gradio re-renders
        // the chat on every streamed token.
        function claims() {
            const el = document.getElementById('vbt-claims');
            if (!el) return {};
            try { return JSON.parse(el.textContent || '{}'); } catch (e) { return {}; }
        }
        function esc(s) {
            return String(s == null ? '' : s).replace(/[&<>"]/g,
                c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
        }
        // Three states, not two — mirrors evidence_status() in src/utils/claims.py.
        // A citation we chose not to fetch is not the same thing as a file that
        // does not exist, and labelling both "unverified" said the wrong thing
        // about the first.
        const EV_LABEL = {verified: '\\u2713 verified', external: 'external ref',
                          unresolved: 'not on record'};
        function evStatus(ev) {
            if (ev.verified) return 'verified';
            return ev.kind === 'citation' ? 'external' : 'unresolved';
        }
        function evidenceRow(ev) {
            const st = evStatus(ev);
            const badge = '<span class="ev-badge ' + st + '">' + EV_LABEL[st] + '</span>';
            let what;
            if (ev.kind === 'tool_call') {
                what = '<code>' + esc(ev.tool_name || ev.tool_use_id) + '</code>'
                     + (ev.agent ? ' <span class="ev-dim">by ' + esc(ev.agent) + '</span>' : '');
            } else if (ev.kind === 'citation') {
                what = esc(ev.pmid || ev.doi || ev.url)
                     + (ev.title ? ' <span class="ev-dim">' + esc(ev.title) + '</span>' : '');
            } else {
                what = '<code>' + esc(ev.path) + '</code>'
                     + (ev.line ? ' <span class="ev-dim">line ' + esc(ev.line) + '</span>' : '');
            }
            return '<li><span class="ev-kind">' + esc(ev.kind) + '</span>' + what
                 + (ev.note ? '<span class="ev-note">' + esc(ev.note) + '</span>' : '')
                 + badge + '</li>';
        }
        function show(cid) {
            const panel = document.getElementById('vbt-evidence');
            const c = claims()[cid];
            if (!panel) return;
            if (!c) {
                panel.innerHTML = '<div class="ev-empty">No evidence was filed for '
                    + esc(cid) + '. This citation resolves to nothing.</div>';
                return;
            }
            panel.innerHTML =
                '<div class="ev-card">'
              + '<div class="ev-id">' + esc(c.id) + '</div>'
              + '<div class="ev-text">' + esc(c.text) + '</div>'
              + '<div class="ev-meta">' + esc(c.agent || 'unattributed')
              + ' \\u00b7 confidence ' + esc(c.confidence) + '</div>'
              + '<ul class="ev-list">' + (c.evidence || []).map(evidenceRow).join('') + '</ul>'
              + '</div>';
            panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
        }
        document.addEventListener('click', function(e) {
            const a = e.target.closest ? e.target.closest('.claim-ref') : null;
            if (!a || !a.dataset || !a.dataset.claim) return;
            e.preventDefault();
            document.querySelectorAll('.claim-ref.active')
                    .forEach(n => n.classList.remove('active'));
            a.classList.add('active');
            show(a.dataset.claim);
        });
    })();
    """

    with gr.Blocks(
        title="The Virtual Biotech",
        fill_width=True
    ) as demo:

        # State for authentication and session management
        authenticated = gr.State(authenticated_by_server)
        session_id = gr.State("")  # Generated per user in async_process_message

        # Login screen
        with gr.Column(visible=not authenticated_by_server, elem_classes=["login-container"]) as login_screen:
            gr.HTML(
                f"""
                <div style="text-align: center; margin-bottom: 1.5rem;">
                    <h1 style="color: #8C1515; margin-bottom: 0.5rem;">The Virtual Biotech</h1>
                    <p style="color: #4D4F53;">AI-Powered Drug Target Discovery Platform</p>
                </div>
                """
            )
            gr.Markdown("Enter the access password to continue:")
            password_input = gr.Textbox(
                label="Password",
                type="password",
                placeholder="Enter access password...",
                show_label=False
            )
            login_btn = gr.Button("Login", variant="primary", )
            login_error = gr.Markdown(visible=False)

        # Main interface (hidden until authenticated)
        with gr.Column(visible=authenticated_by_server, elem_classes=["main-screen-container"]) as main_screen:
            # Header with active users counter
            gr.HTML(
                f"""
                <div class="main-header">
                    <div style="display: flex; align-items: center; gap: 1rem;">
                        <div>
                            <h1>The Virtual Biotech</h1>
                            <p>CSO Interface — AI-powered drug target discovery with multi-agent orchestration</p>
                        </div>
                    </div>
                </div>
                """
            )

            # System introduction
            gr.HTML(
                """
                <div class="system-intro">
                    <p>The Virtual Biotech is a multi-agent AI system for therapeutic research and innovation in drug discovery that coordinates teams of agents organized into R&D divisions. These divisions include: <strong>Target Identification and Prioritization</strong>, <strong>Target Safety</strong>, <strong>Clinical Officers</strong>, and <strong>Modality Selection</strong>.</p>
                    <p>Each query is intelligently routed via the virtual CSO to relevant divisions of AI scientist agents who analyze data from Open Targets, CELLxGENE Census, ClinicalTrials.gov, Tahoe-100M, and other biomedical databases. To get started, say <strong>&ldquo;Introduce yourself and your role&rdquo;</strong>.</p>
                    <p><small><em>This UI provides interactive access to the system's analytical capabilities. Queries requiring significant computational resources can also be submitted through the command line interface of the system, which supports asynchronous execution over longer time horizons.</em></small></p>
                </div>
                """
            )

            with gr.Row():
                # Main chat column (wider)
                with gr.Column(scale=3):
                    chatbot = gr.Chatbot(
                        label="Conversation",
                        height=650,
                        buttons=["copy", "copy_all"],
                        editable="user",
                        reasoning_tags=[("<cso-thinking>", "</cso-thinking>")],
                    )

                    with gr.Row():
                        msg_input = gr.Textbox(
                            label="Your message",
                            placeholder="Ask about drug targets, safety, or modalities...",
                            scale=4,
                            lines=2,
                            show_label=False
                        )
                        with gr.Column(scale=1):
                            submit_btn = gr.Button("Send", variant="primary", )
                            stop_btn = gr.Button("⏹ Stop", variant="stop", elem_classes=["stop-btn"], visible=False)
                            model_selector = gr.Dropdown(
                                choices=list(CSOSession.MODEL_CHOICES.keys()),
                                value="Sonnet 4.5 (default)",
                                label="Model",
                                interactive=True,
                                scale=1,
                            )

                    # Agent status line
                    agent_status = gr.HTML(
                        value='<div class="agent-status"></div>',
                        visible=True
                    )

                    # Example queries
                    gr.HTML('<p style="color: #4D4F53; margin: 1rem 0 0.5rem 0; font-size: 1.1em;"><strong>Try an example:</strong></p>')
                    with gr.Row():
                        ex1 = gr.Button(
                            "KRAS clinical landscape in pancreatic cancer",
                            size="sm",
                            elem_classes=["example-btn"]
                        )
                        ex2 = gr.Button(
                            "Evaluate TREM2 as a target for Alzheimer's",
                            size="sm",
                            elem_classes=["example-btn"]
                        )
                        ex3 = gr.Button(
                            "Safety risks of targeting IL-33 in asthma",
                            size="sm",
                            elem_classes=["example-btn"]
                        )

                    with gr.Row():
                        clear_btn = gr.Button("Clear conversation", size="sm")
                        download_btn = gr.Button("📥 Download Chat", size="sm", elem_classes=["example-btn"])
                        # Whole run directory, zipped — MANIFEST, audit.html,
                        # every artifact, claims and provenance. What a reviewer
                        # needs, not just the transcript (reviewer comment R2.5).
                        download_run_btn = gr.Button("📁 Download Artifacts", size="sm", elem_classes=["example-btn"])
                    download_file = gr.File(label="Download", visible=False)
                    download_run_file = gr.File(label="Download run (.zip)", visible=False)

                # Sidebar for activity log (narrower)
                with gr.Column(scale=1, elem_classes=["activity-panel"]):
                    # Evidence panel — populated client-side when a claim marker
                    # in the conversation is clicked (reviewer comment R2.3).
                    evidence_panel = gr.HTML(
                        value=render_evidence_panel(ClaimSet()),
                        elem_id="evidence-panel",
                    )
                    # Server-rendered fallback: the audit path must not depend on
                    # JavaScript. gr.HTML rather than gr.Markdown so it can carry
                    # the same card markup as the panel above — Markdown strips
                    # the class attributes the styling hangs off.
                    with gr.Accordion("🔍 All claims & evidence", open=False,
                                      elem_classes=["files-section"]):
                        evidence_static = gr.HTML(
                            value=render_evidence_static(ClaimSet())
                        )

                    gr.HTML('<div class="sidebar-header">Agent Activity</div>')
                    activity_log = gr.Markdown(
                        "*No activity yet. Send a message to start.*",
                        elem_classes=["activity-log"]
                    )

                    gr.HTML('<div class="division-legend"><strong>R&D Divisions</strong><br><br>'
                            '🧬 <strong>Target Identification and Prioritization</strong><br>'
                            '<small>Genetic evidence, functional genomics, single-cell biology</small><br><br>'
                            '🛡️ <strong>Target Safety</strong><br>'
                            '<small>Pathway context, protein interactions, FDA safety liabilities</small><br><br>'
                            '🏥 <strong>Clinical Officers</strong><br>'
                            '<small>Clinical trials, regulatory safety, cancer genomics</small><br><br>'
                            '💊 <strong>Modality Selection</strong><br>'
                            '<small>Target druggability, medicinal chemistry, modality ranking</small></div>'
                    )

                    active_users_display = gr.HTML(
                        value=get_active_users_display(),
                        elem_id="active-users-counter",
                        elem_classes=["active-users"]
                    )

                    # Generated Files Section
                    with gr.Accordion("📁 Generated Files", open=False, elem_classes=["files-section"]):
                        gr.HTML('<div class="files-header">Session Files</div>')
                        files_gallery = gr.Gallery(
                            label="Figures",
                            show_label=True,
                            columns=2,
                            height="auto",
                            object_fit="contain"
                        )
                        files_list = gr.File(
                            label="Download Files",
                            file_count="multiple",
                            interactive=False
                        )
                        refresh_files_btn = gr.Button("🔄 Refresh Files", size="sm", elem_classes=["example-btn"])

                    # Past Runs — every run is a self-describing directory with
                    # its own README.md and audit.html (reviewer comment R2.5).
                    with gr.Accordion("🗂 Past Runs", open=False,
                                      elem_classes=["files-section"]):
                        runs_table = gr.HTML(value=render_index_html(load_index(RUNS_DIR)))
                        refresh_runs_btn = gr.Button("🔄 Refresh Runs", size="sm",
                                                     elem_classes=["example-btn"])
                        gr.HTML(
                            '<div class="ev-empty">Each run directory holds '
                            '<code>README.md</code> (a human map), '
                            '<code>audit.html</code> (a self-contained report), '
                            '<code>MANIFEST.json</code> (every artifact, hashed and '
                            'attributed) and <code>evidence/</code> (claims and '
                            'provenance). Replay one with '
                            '<code>./run.sh replay &lt;RUN_ID&gt;</code>.</div>'
                        )

        # Event handlers
        def login(password):
            if check_password(password):
                return gr.update(visible=False), gr.update(visible=True), True, gr.update(visible=False)
            else:
                return gr.update(), gr.update(), False, gr.update(visible=True, value="❌ Incorrect password. Please try again.")

        login_btn.click(
            login,
            inputs=[password_input],
            outputs=[login_screen, main_screen, authenticated, login_error]
        )
        password_input.submit(
            login,
            inputs=[password_input],
            outputs=[login_screen, main_screen, authenticated, login_error]
        )

        def user_message(message, history):
            """Handle user message submission"""
            if not message.strip():
                return "", history, ""
            return "", history, message

        def clear_chat(sess_id):
            """Clear the conversation and destroy the session so a new model can be selected"""
            if sess_id and isinstance(sess_id, str) and sess_id in session_manager.sessions:
                session_manager._schedule_cleanup(session_manager.sessions[sess_id])
                del session_manager.sessions[sess_id]
            return [], "*No activity yet. Send a message to start.*", '<div class="agent-status"></div>', "", gr.update(interactive=True)

        # Chat submission
        msg_state = gr.State("")

        def refresh_files(sess_id):
            """Refresh the files display"""
            images, files = get_session_files(sess_id)
            return images, files if files else None

        # Submit button click: show stop button, process message, then hide stop button
        submit_click = submit_btn.click(
            lambda: (gr.update(visible=False), gr.update(visible=True)),
            outputs=[submit_btn, stop_btn]
        ).then(
            user_message,
            inputs=[msg_input, chatbot],
            outputs=[msg_input, chatbot, msg_state]
        ).then(
            async_process_message,
            inputs=[msg_state, chatbot, session_id, model_selector],
            outputs=[chatbot, activity_log, session_id, agent_status,
                     evidence_panel, evidence_static]
        ).then(
            refresh_files,
            inputs=[session_id],
            outputs=[files_gallery, files_list]
        ).then(
            lambda: (gr.update(visible=True), gr.update(visible=False), gr.update(interactive=False)),
            outputs=[submit_btn, stop_btn, model_selector]
        )

        # Input submit: same as button click
        msg_submit = msg_input.submit(
            lambda: (gr.update(visible=False), gr.update(visible=True)),
            outputs=[submit_btn, stop_btn]
        ).then(
            user_message,
            inputs=[msg_input, chatbot],
            outputs=[msg_input, chatbot, msg_state]
        ).then(
            async_process_message,
            inputs=[msg_state, chatbot, session_id, model_selector],
            outputs=[chatbot, activity_log, session_id, agent_status,
                     evidence_panel, evidence_static]
        ).then(
            refresh_files,
            inputs=[session_id],
            outputs=[files_gallery, files_list]
        ).then(
            lambda: (gr.update(visible=True), gr.update(visible=False), gr.update(interactive=False)),
            outputs=[submit_btn, stop_btn, model_selector]
        )

        # Stop button handler - provides visual feedback
        def handle_stop(history, sess_id):
            """Handle stop button click with visual feedback and proper cleanup"""
            # Add cancellation message to chat
            if history:
                # Check if last message is from assistant and appears incomplete
                if history[-1].get("role") == "assistant":
                    history[-1]["content"] += "\n\n⏹️ **Query stopped by user.**"
                else:
                    history = history + [{"role": "assistant", "content": "⏹️ **Query stopped by user.**"}]
            else:
                history = [{"role": "assistant", "content": "⏹️ **Query stopped by user.**"}]

            # Update activity tracker and abort the query if session exists
            if sess_id and sess_id in session_manager.sessions:
                session = session_manager.sessions[sess_id]

                # Abort the current query - releases lock and marks for reset
                session.abort_query()

                # Mark all running agents and events as stopped
                for agent in session.agent_statuses:
                    if session.agent_statuses[agent] == "running":
                        session.agent_statuses[agent] = "error"
                for i, event in enumerate(session.activity_tracker.events):
                    if event.status == "running":
                        session.activity_tracker.update_status(i, "error")
                # Add stop event
                session.activity_tracker.add_event(
                    "system", "Query cancelled",
                    "Stopped by user - session will reset on next query", "complete"
                )
                activity_md = session.activity_tracker.format_markdown()
            else:
                activity_md = "*Query cancelled by user.*"

            agent_status_html = '<div class="agent-status stopped">⏹️ Stopped - Ready for new query</div>'

            return (
                history,
                activity_md,
                agent_status_html,
                gr.update(visible=True),   # show submit_btn
                gr.update(visible=False)   # hide stop_btn
            )

        # Stop button: cancel ongoing operations and provide feedback
        stop_btn.click(
            handle_stop,
            inputs=[chatbot, session_id],
            outputs=[chatbot, activity_log, agent_status, submit_btn, stop_btn],
            cancels=[submit_click, msg_submit]
        )

        # Example buttons
        ex1.click(lambda: "Assess the clinical precedence and competitive landscape for KRAS-targeted therapies in pancreatic cancer. What's been tried, what's working, and where are the gaps?", outputs=[msg_input])
        ex2.click(lambda: "Evaluate TREM2 as a therapeutic target for Alzheimer's disease.", outputs=[msg_input])
        ex3.click(lambda: "What are the safety risks of targeting IL-33 in asthma?", outputs=[msg_input])

        # Clear button
        clear_btn.click(clear_chat, inputs=[session_id], outputs=[chatbot, activity_log, agent_status, session_id, model_selector])

        # Download chat as markdown
        def download_chat(history, sess_id):
            """Generate a downloadable markdown file from chat history"""
            if not history:
                return gr.update(visible=False)

            # Validate session ID
            if not sess_id or not isinstance(sess_id, str) or callable(sess_id):
                return gr.update(visible=False)

            md_content = export_chat_markdown(history)
            if not md_content:
                return gr.update(visible=False)

            # Write to session-specific workspace
            session_workspace = WORKSPACE_DIR / sess_id
            session_workspace.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"biotech_chat_{timestamp}.md"
            filepath = session_workspace / filename

            with open(filepath, 'w') as f:
                f.write(md_content)

            return gr.update(value=str(filepath), visible=True)

        download_btn.click(download_chat, inputs=[chatbot, session_id], outputs=[download_file])

        # Download the full run directory as a zip: MANIFEST, audit.html,
        # every artifact under work/<agent>/, claims and provenance.
        def download_run(sess_id):
            if not sess_id or not isinstance(sess_id, str) or callable(sess_id):
                return gr.update(visible=False)
            session = session_manager.sessions.get(sess_id)
            if session is None or session.run is None:
                return gr.update(visible=False)
            try:
                zip_path = zip_run_dir(session.run.run_dir)
            except OSError as e:
                print(f"[WARNING] Failed to zip run directory: {e}")
                return gr.update(visible=False)
            return gr.update(value=str(zip_path), visible=True)

        download_run_btn.click(download_run, inputs=[session_id], outputs=[download_run_file])

        # Refresh files button
        refresh_runs_btn.click(
            lambda: render_index_html(load_index(RUNS_DIR)),
            outputs=[runs_table]
        )

        refresh_files_btn.click(
            refresh_files,
            inputs=[session_id],
            outputs=[files_gallery, files_list]
        )

        # Update active users counter and files on page load
        def update_active_users():
            return get_active_users_display()

        def load_initial_files(sess_id):
            """Load files when page first loads"""
            images, files = get_session_files(sess_id)
            return get_active_users_display(), images, files if files else None

        demo.load(
            load_initial_files,
            inputs=[session_id],
            outputs=[active_users_display, files_gallery, files_list]
        )

    return demo, custom_css, force_light_mode_js, theme


# =============================================================================
# Main Entry Point
# =============================================================================

def main(share: bool = False):
    """Main entry point"""
    try:
        server_port = int(os.environ.get("GRADIO_SERVER_PORT", SERVER_PORT))
        if not 1 <= server_port <= 65535:
            raise ValueError()
    except ValueError:
        raise SystemExit("GRADIO_SERVER_PORT must be an integer between 1 and 65535.")
    if not APP_PASSWORD:
        raise SystemExit(
            "BIOTECH_APP_PASSWORD is not set. Configure an access password in "
            "the project-root .env or export it before launching the web interface."
        )
    server_host = "0.0.0.0" if share else SERVER_HOST
    print("=" * 70)
    print("VIRTUAL BIOTECH INSTITUTE - WEB INTERFACE")
    print("=" * 70)
    print()
    print(f"Starting server on http://{server_host}:{server_port}")
    print()
    print("For external access via Cloudflare Tunnel:")
    print("  cloudflared tunnel run <tunnel-name>")
    print()
    print("Access requires the configured BIOTECH_APP_PASSWORD (any username).")
    print()
    print("=" * 70)

    demo, custom_css, force_light_mode_js, theme = create_interface(authenticated_by_server=True)
    if share:
        # Preserve the shared launch's original queue defaults (one active event).
        demo.queue()
    else:
        demo.queue(
            default_concurrency_limit=20,  # Allow 20 concurrent users (matches max_sessions)
            max_size=50  # Max queue size (waiting users)
        )

    demo.launch(
        server_name=server_host,
        server_port=server_port,
        share=share,
        auth=authenticate,
        auth_message="Enter any username and the configured access password.",
        show_error=True,
        theme=theme,
        css=custom_css,
        js=force_light_mode_js,
        max_file_size="50mb",
        # RUNS_DIR is where artifacts now live; WORKSPACE_DIR is kept so the
        # legacy flat sessions stay downloadable. Omitting RUNS_DIR makes Gradio
        # refuse to serve any artifact, which shows up as an empty file gallery
        # rather than an error.
        allowed_paths=[str(RUNS_DIR), str(WORKSPACE_DIR)],
        blocked_paths=[str(Path(__file__).parent / "src"), str(Path(__file__).parent / ".env")],
    )


if __name__ == "__main__":
    main()
