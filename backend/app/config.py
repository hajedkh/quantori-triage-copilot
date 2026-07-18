"""Backend runtime configuration helpers.

This module centralizes tunable parameters that were previously hard-coded in
feature modules (for example export/cross-reference knobs).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# .env lives at repository root (one level above backend/).
_ROOT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_ROOT_ENV)


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


class ExportConfig:
    """Tunable settings for export and external cross-reference behavior."""

    def __init__(self) -> None:
        self.xref_timeout_seconds = _env_float("EXPORT_XREF_TIMEOUT", 4.0, minimum=0.1)
        self.xref_budget_seconds = _env_float(
            "EXPORT_XREF_BUDGET_SECONDS", 18.0, minimum=1.0
        )
        self.xref_top_limit = _env_int("EXPORT_XREF_TOP_LIMIT", 250, minimum=1)
        self.xref_probe_n = _env_int("EXPORT_XREF_PROBE_N", 8, minimum=1)
        self.xref_workers = _env_int("EXPORT_XREF_WORKERS", 16, minimum=1)
        self.embed_parallel_min = _env_int("EXPORT_EMBED_PARALLEL_MIN", 4, minimum=1)


def load_export_config() -> ExportConfig:
    return ExportConfig()


class SourcesConfig:
    """Tunable settings for external source lookups (ChEMBL/PubMed)."""

    def __init__(self) -> None:
        self.timeout_seconds = _env_float("SOURCES_TIMEOUT", 6.0, minimum=0.1)
        self.known_actives_limit = _env_int(
            "SOURCES_KNOWN_ACTIVES_LIMIT", 60, minimum=1
        )
        self.pubmed_retmax = _env_int("SOURCES_PUBMED_RETMAX", 6, minimum=1)


def load_sources_config() -> SourcesConfig:
    return SourcesConfig()


class ChemConfig:
    """Tunable settings for chemistry screening parallel execution."""

    def __init__(self) -> None:
        self.parallel_threshold = _env_int("CHEM_PARALLEL_THRESHOLD", 500, minimum=1)
        self.batch_size = _env_int("CHEM_BATCH_SIZE", 64, minimum=1)
        self.max_workers_cap = _env_int("CHEM_MAX_WORKERS_CAP", 8, minimum=1)


def load_chem_config() -> ChemConfig:
    return ChemConfig()


class ToolConfig:
    """Payload-size caps for tool responses and tool inputs."""

    def __init__(self) -> None:
        self.max_examples = _env_int("TOOLS_MAX_EXAMPLES", 3, minimum=1)
        self.max_batch = _env_int("TOOLS_MAX_BATCH", 10, minimum=1)


def load_tool_config() -> ToolConfig:
    return ToolConfig()


class ChatConfig:
    """Runtime knobs for chat loop responsiveness and context sizing."""

    def __init__(self) -> None:
        self.max_iters = _env_int("CHAT_MAX_ITERS", 2, minimum=1)
        self.poll_interval_seconds = _env_float(
            "CHAT_POLL_INTERVAL_SECONDS", 0.05, minimum=0.01
        )
        self.history_turns = _env_int("CHAT_HISTORY_TURNS", 6, minimum=1)
        self.force_tool_first = _env_bool("CHAT_FORCE_TOOL_FIRST", True)


def load_chat_config() -> ChatConfig:
    return ChatConfig()


class DiversifyConfig:
    """Defaults and bounds for diversification options."""

    def __init__(self) -> None:
        self.default_lam = _env_float("DIVERSIFY_DEFAULT_LAM", 0.7, minimum=0.0)
        self.default_cutoff = _env_float("DIVERSIFY_DEFAULT_CUTOFF", 0.35, minimum=0.0)
        self.default_max_generated = _env_int(
            "DIVERSIFY_DEFAULT_MAX_GENERATED", 200, minimum=1
        )
        self.min_max_generated = _env_int("DIVERSIFY_MIN_MAX_GENERATED", 1, minimum=1)
        self.max_max_generated = _env_int(
            "DIVERSIFY_MAX_MAX_GENERATED", 5000, minimum=1
        )


def load_diversify_config() -> DiversifyConfig:
    return DiversifyConfig()
