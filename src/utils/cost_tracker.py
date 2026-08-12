"""
Cost Tracking Utility
The Virtual Biotech - Python Version

Tracks token usage and costs for Claude Agent SDK interactions
Based on: https://docs.claude.com/en/docs/claude-code/cost-tracking
"""

from datetime import datetime
from typing import Dict, List, Optional, Any
from claude_agent_sdk import AssistantMessage, ResultMessage


class CostTracker:
    """Tracks token usage and costs for agent interactions"""

    # Current Claude API pricing (as of 2025)
    # Update these as pricing changes
    PRICING = {
        'claude-sonnet-4-5': {
            'input': 0.000003,      # $3 per MTok
            'output': 0.000015,     # $15 per MTok
            'cache_write': 0.00000375,  # $3.75 per MTok
            'cache_read': 0.00000030    # $0.30 per MTok
        },
        'claude-haiku-4-5': {
            'input': 0.000001,      # $1 per MTok
            'output': 0.000005,     # $5 per MTok
            'cache_write': 0.00000125,  # $1.25 per MTok
            'cache_read': 0.00000010    # $0.10 per MTok
        }
    }

    def __init__(self, model: str = 'claude-sonnet-4-5'):
        """Initialize cost tracker

        Args:
            model: Model name for pricing calculation
        """
        self.model = model
        self.processed_message_ids = set()
        self.step_usages: List[Dict[str, Any]] = []
        self.total_cost_usd = 0.0

    def process_message(self, message: Any, debug: bool = False) -> None:
        """Process a message and track usage

        Args:
            message: Message from Claude Agent SDK query stream
            debug: If True, print debug info about usage data
        """
        # Only process assistant messages with usage
        if not isinstance(message, AssistantMessage):
            return

        if not hasattr(message, 'usage'):
            if debug:
                print(f"  [CostTracker] AssistantMessage has no usage attribute")
            return

        # Skip if already processed this message ID (avoid double-counting)
        message_id = getattr(message, 'id', None)
        if not message_id or message_id in self.processed_message_ids:
            return

        # Mark as processed and record usage
        self.processed_message_ids.add(message_id)

        usage_data = message.usage
        cost = self.calculate_cost(usage_data)

        if debug:
            print(f"  [CostTracker] Processing message {message_id[:8]}...")
            print(f"    Usage: {usage_data}")
            print(f"    Cost: ${cost:.6f}")

        self.step_usages.append({
            "message_id": message_id,
            "timestamp": datetime.now().isoformat(),
            "usage": usage_data,
            "cost_usd": cost
        })

    def process_result(self, message: ResultMessage, debug: bool = False) -> None:
        """Process final result message with cumulative usage

        Args:
            message: Final ResultMessage with total usage
            debug: If True, print debug info
        """
        if hasattr(message, 'total_cost_usd'):
            self.total_cost_usd = message.total_cost_usd
            if debug:
                print(f"  [CostTracker] ResultMessage total_cost_usd: ${self.total_cost_usd:.6f}")
        elif hasattr(message, 'usage'):
            # Fallback: calculate from usage if total_cost_usd not available
            self.total_cost_usd = self.calculate_cost(message.usage)
            if debug:
                print(f"  [CostTracker] ResultMessage usage: {message.usage}")
                print(f"  [CostTracker] Calculated cost: ${self.total_cost_usd:.6f}")
        else:
            if debug:
                print(f"  [CostTracker] ResultMessage has no total_cost_usd or usage")

    def calculate_cost(self, usage: Dict[str, Any]) -> float:
        """Calculate cost from usage data

        Args:
            usage: Usage dict with token counts

        Returns:
            Cost in USD
        """
        pricing = self.PRICING.get(self.model, self.PRICING['claude-sonnet-4-5'])

        input_cost = usage.get("input_tokens", 0) * pricing['input']
        output_cost = usage.get("output_tokens", 0) * pricing['output']
        cache_read_cost = usage.get("cache_read_input_tokens", 0) * pricing['cache_read']
        cache_write_cost = usage.get("cache_creation_input_tokens", 0) * pricing['cache_write']

        return input_cost + output_cost + cache_read_cost + cache_write_cost

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of tracked usage and costs

        Returns:
            Dict with usage statistics
        """
        # Accumulate from step usages
        total_input = sum(step['usage'].get('input_tokens', 0) for step in self.step_usages)
        total_output = sum(step['usage'].get('output_tokens', 0) for step in self.step_usages)
        total_cache_read = sum(step['usage'].get('cache_read_input_tokens', 0) for step in self.step_usages)
        total_cache_write = sum(step['usage'].get('cache_creation_input_tokens', 0) for step in self.step_usages)

        # Calculate total cost
        total_cost = self.total_cost_usd if self.total_cost_usd else sum(step['cost_usd'] for step in self.step_usages)

        # If we have cost but no tokens (SDK didn't provide usage in messages),
        # estimate tokens from cost (reverse calculation)
        if total_cost > 0 and total_input == 0 and total_output == 0:
            # Estimate: assume 60% input, 40% output (typical ratio)
            # Use pricing to reverse-engineer token counts
            pricing = self.PRICING.get(self.model, self.PRICING['claude-sonnet-4-5'])
            # This is an approximation - actual tokens unknown
            estimated_total_tokens = int(total_cost / ((0.6 * pricing['input']) + (0.4 * pricing['output'])))
            total_input = int(estimated_total_tokens * 0.6)
            total_output = int(estimated_total_tokens * 0.4)

        return {
            'model': self.model,
            'steps': len(self.step_usages),
            'tokens': {
                'input': total_input,
                'output': total_output,
                'cache_read': total_cache_read,
                'cache_write': total_cache_write,
                'total': total_input + total_output + total_cache_read + total_cache_write
            },
            'cost_usd': total_cost,
            'step_details': self.step_usages,
            'note': 'Token counts estimated from cost' if (total_cost > 0 and len(self.step_usages) == 0) else None
        }

    def format_cost_summary(self) -> str:
        """Format a human-readable cost summary

        Returns:
            Formatted string with cost details
        """
        summary = self.get_summary()

        lines = [
            f"\n{'='*70}",
            "COST TRACKING SUMMARY",
            f"{'='*70}",
            f"Model: {summary['model']}",
            f"Steps: {summary['steps']}",
            f"",
            f"Token Usage:",
            f"  Input tokens:        {summary['tokens']['input']:>10,}",
            f"  Output tokens:       {summary['tokens']['output']:>10,}",
            f"  Cache read tokens:   {summary['tokens']['cache_read']:>10,}",
            f"  Cache write tokens:  {summary['tokens']['cache_write']:>10,}",
            f"  ----------------------------------------",
            f"  Total tokens:        {summary['tokens']['total']:>10,}",
            f"",
            f"Total Cost: ${summary['cost_usd']:.6f}",
            f"{'='*70}",
        ]

        return '\n'.join(lines)
