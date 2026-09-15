"""
Environment configuration loader
The Virtual Biotech
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Always load this checkout's configuration and preserve exported values.
load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)


class Config:
    """Application configuration — only the variables the app actually consumes."""

    # API key (also read directly by the Claude Agent SDK)
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

    # Reference data paths
    OPEN_TARGETS_PATH = os.getenv('OPEN_TARGETS_DATA_PATH')

    # Optional external dataset for the functional-genomics Tahoe-100M tools.
    # Unset unless the user has downloaded the pseudobulk DE results (see README).
    TAHOE_DATA_PATH = os.getenv('TAHOE_DATA_PATH')

    @classmethod
    def validate(cls):
        """Validate reference configuration. Called before data is loaded
        (see src/data/loader.py), not at import time, so importing this module
        for inspection or lightweight tooling never crashes."""
        # Reading local reference data does not call the model API. Research
        # entry points validate model credentials before starting the SDK.
        if not cls.OPEN_TARGETS_PATH:
            raise ValueError('OPEN_TARGETS_DATA_PATH not set in environment')
