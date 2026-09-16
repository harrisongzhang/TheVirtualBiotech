"""Check local reference availability before starting a research turn."""
from __future__ import annotations

import os
from pathlib import Path


class DataReadinessError(ValueError):
    """The required reference data is not ready for research."""


def require_reference_data(value: str | None = None) -> dict[str, list[Path]]:
    """Validate the same inventory as doctor, without loading tables or networking.

    Check on every turn, including follow-ups: downloads can be interrupted or
    files removed after a conversation starts. The downloader's manifest detects
    an incomplete inventory even when each dataset already has a Parquet shard.
    Manually supplied archives retain doctor's layout-and-size validation.
    """
    from tools.doctor import reference_files

    if value is None:
        value = os.environ.get("OPEN_TARGETS_DATA_PATH")
    try:
        return reference_files(value)
    except (OSError, ValueError, TypeError) as exc:
        raise DataReadinessError(
            f"Open Targets reference data is not ready: {exc}. "
            "Finish or repair the download with python tools/download_open_targets.py <data-directory> "
            "and check it with python tools/doctor.py --skip-api-key before retrying. "
            "This turn has not been sent to the model."
        ) from exc
