"""Model selection shared by the interactive CLI, headless runner and web UI."""
import argparse
import re

DEFAULT_MODEL_ID = "claude-sonnet-4-5-20250929"
MODEL_CHOICES = {
    "Sonnet 4.5 (default)": DEFAULT_MODEL_ID,
    "Sonnet 4.6": "claude-sonnet-4-6",
    "Haiku 4.5 (fast)": "claude-haiku-4-5-20251001",
    "Opus 4.6": "claude-opus-4-6",
    "Opus 4.7": "claude-opus-4-7",
    "Opus 4.8": "claude-opus-4-8",
}
MODEL_HELP = (
    "Anthropic model ID (e.g. claude-opus-4-6) or one of these quoted labels: "
    + ", ".join(MODEL_CHOICES)
    + ". Model IDs are passed unchanged to Anthropic for validation. "
    "The chief-of-staff and scientific-reviewer always use Haiku."
)


def resolve_model(value: str) -> str:
    value = value.strip()
    if value in MODEL_CHOICES:
        return MODEL_CHOICES[value]
    if re.fullmatch(r"claude-[a-z0-9]+(?:-[a-z0-9]+)*", value):
        return value
    raise ValueError(
        f"Unknown model {value!r}. Use an Anthropic model ID such as "
        "claude-opus-4-6 or a label listed in --help."
    )


def model_argument(value: str) -> str:
    try:
        return resolve_model(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
