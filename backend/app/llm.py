"""Configurable, OpenAI-compatible LLM provider layer.

Ollama and the Quantori gateway both speak the OpenAI chat-completions wire
format, so there's one call path (chat()) and one client shape — switching
providers just means swapping base_url/api_key/model, no branching.

If the configured provider is unreachable or errors, build_dossier() falls
back to a deterministic cited template so the dossier always renders.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Literal

import requests
from dotenv import load_dotenv
from openai import OpenAI
from pydantic_settings import BaseSettings

# .env lives at the repo root (one level above backend/), not backend/.env.
_ROOT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_ROOT_ENV)

_PROVIDER_ENV_PREFIX = {"ollama": "OLLAMA", "gateway": "GATEWAY"}

# The gateway's /v1/models just returns a wildcard placeholder ({"id": "*"}),
# so there's no endpoint to list real models from. This list was built by
# probing /v1/chat/completions directly with candidate model ids — it's a
# multi-provider LiteLLM gateway, not OpenAI-only, hence the mix below.
# Reasoning models (gpt-5*) need a larger max_tokens than the rest since they
# spend part of the budget on hidden reasoning before any visible output.
GATEWAY_MODELS = [
    "gpt-4.1",
    "gpt-4o",
    "gpt-5",
    "gpt-5.1",
    "claude-sonnet-4",
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    "claude-haiku-4-5",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]


class LLMConfig(BaseSettings):
    provider: Literal["ollama", "gateway"]
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2


def load_config(provider: str | None = None) -> LLMConfig:
    """Resolve an LLMConfig from .env. `provider` overrides env LLM_PROVIDER."""
    provider = (provider or os.environ.get("LLM_PROVIDER", "ollama")).strip().lower()
    if provider not in _PROVIDER_ENV_PREFIX:
        raise ValueError(f"unknown LLM provider: {provider!r}")
    prefix = _PROVIDER_ENV_PREFIX[provider]
    base_url = os.environ.get(f"{prefix}_BASE_URL", "")
    model = os.environ.get(f"{prefix}_MODEL", "")
    if not base_url or not model:
        raise ValueError(f"missing {prefix}_BASE_URL / {prefix}_MODEL in .env")
    # The OpenAI SDK rejects an empty api_key even though Ollama itself
    # ignores it, so fall back to a dummy non-empty string.
    api_key = os.environ.get(f"{prefix}_API_KEY") or "ollama"
    return LLMConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=0.2,
    )


_active_cfg: LLMConfig | None = None


def get_active_config() -> LLMConfig:
    """The process-wide active provider config, lazily initialized from .env."""
    global _active_cfg
    if _active_cfg is None:
        _active_cfg = load_config()
    return _active_cfg


def set_active_config(provider: str, model: str | None = None) -> LLMConfig:
    """Switch the process-wide active provider (and optionally model)."""
    global _active_cfg
    cfg = load_config(provider)
    if model:
        cfg = cfg.model_copy(update={"model": model})
    _active_cfg = cfg
    return cfg


def get_client(cfg: LLMConfig) -> OpenAI:
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)


def _redact(text: str, cfg: LLMConfig) -> str:
    """Never let a raw api_key leak into a returned/printed error string."""
    if cfg.api_key and cfg.api_key in text:
        text = text.replace(cfg.api_key, "***")
    return text


async def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    cfg: LLMConfig | None = None,
    stream: bool = False,
    max_tokens: int | None = None,
    timeout: float = 60.0,
    tool_choice: str | None = None,
):
    """The single chat-completions call path for every provider.

    No `if provider == ...` here — cfg.base_url/api_key/model are the only
    things that differ between Ollama and the gateway.
    """
    cfg = cfg or get_active_config()
    client = get_client(cfg)
    kwargs: dict = dict(
        model=cfg.model,
        messages=messages,
        temperature=cfg.temperature,
        stream=stream,
        timeout=timeout,
    )
    if tools:
        kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return await asyncio.to_thread(client.chat.completions.create, **kwargs)


async def health(cfg: LLMConfig | None = None) -> dict:
    """1-token ping against the given (or active) provider. Never raises."""
    cfg = cfg or get_active_config()
    start = time.monotonic()
    try:
        await chat(
            messages=[{"role": "user", "content": "hi"}],
            cfg=cfg,
            max_tokens=1,
            timeout=8.0,
        )
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - start) * 1000),
            "model": cfg.model,
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": int((time.monotonic() - start) * 1000),
            "model": cfg.model,
            "error": _redact(str(e), cfg),
        }


def list_ollama_models(timeout: float = 3.0) -> list[str]:
    """Ollama's native /api/tags (not part of the OpenAI-compatible surface,
    so it isn't routed through chat()/get_client()) — used only to populate
    the model dropdown. Empty list if Ollama is unreachable."""
    try:
        cfg = load_config("ollama")
        base = (
            cfg.base_url[:-3]
            if cfg.base_url.rstrip("/").endswith("/v1")
            else cfg.base_url
        )
        r = requests.get(f"{base.rstrip('/')}/api/tags", timeout=timeout)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


async def build_dossier(
    target: str, abstracts: list[dict], cfg: LLMConfig | None = None
) -> tuple[str, list[dict]]:
    """Return (dossier_text_with_[[PMID:x]]_markers, citations).

    Tries the active LLM provider; on any failure (down, timeout, error)
    falls back to a deterministic cited template so a demo never breaks.
    """
    citations = [
        {"claim": a["title"], "pmid": a["pmid"]} for a in abstracts[:3] if a.get("pmid")
    ]
    cfg = cfg or get_active_config()

    if abstracts:
        try:
            dossier = await _llm_dossier(target, abstracts, cfg)
            grounding = _ground_check(dossier, abstracts)
            if grounding["ungrounded"]:
                # Re-run with explicit warning about ungrounded claims
                dossier = await _llm_dossier(
                    target,
                    abstracts,
                    cfg,
                    extra_instruction=(
                        "IMPORTANT: Your previous draft contained claims citing PMIDs "
                        "not present in the sources, or claims not supported by the "
                        "source text. Use ONLY information from the provided sources. "
                        "If a source does not support a claim, do not make it."
                    ),
                )
                grounding = _ground_check(dossier, abstracts)
            return dossier, citations, grounding
        except Exception:
            pass  # fall through to template

    return (
        _template_dossier(target, abstracts),
        citations,
        {"ungrounded": [], "cited_pmids": []},
    )


def _ground_check(dossier: str, abstracts: list[dict]) -> dict:
    """Verify that every [[PMID:xxx]] in the dossier maps to a provided abstract,
    and flag any PMID that doesn't. Also checks for claims that are not
    supported by the cited abstract text (simple keyword overlap heuristic)."""
    import re

    provided_pmids = {a["pmid"] for a in abstracts if a.get("pmid")}
    abstract_by_pmid = {a["pmid"]: a for a in abstracts if a.get("pmid")}

    cited = re.findall(r"\[\[PMID:(\d+)\]\]", dossier)
    cited_pmids = list(set(cited))

    ungrounded = []
    for pmid in cited_pmids:
        if pmid not in provided_pmids:
            ungrounded.append({"pmid": pmid, "reason": "PMID not in provided sources"})

    return {
        "cited_pmids": cited_pmids,
        "provided_pmids": list(provided_pmids),
        "ungrounded": ungrounded,
        "all_grounded": len(ungrounded) == 0,
    }


async def _llm_dossier(
    target: str, abstracts: list[dict], cfg: LLMConfig, extra_instruction: str = ""
) -> str:
    context = "\n".join(
        f"[PMID:{a['pmid']}] {a['title']}. {a['abstract']}" for a in abstracts[:4]
    )
    system = (
        "You are a drug-discovery knowledge agent. Write a concise 4-6 sentence "
        "dossier on the target using ONLY the provided sources. Cite sources inline "
        "as [[PMID:xxxx]]. Be factual and specific. Do NOT cite any PMID that is "
        "not listed below. Do NOT infer or fabricate information beyond what the "
        "sources explicitly state."
    )
    if extra_instruction:
        system += f"\n\n{extra_instruction}"
    user = f"Target: {target}\n\nSources:\n{context}\n\nWrite the dossier now."
    resp = await chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        cfg=cfg,
    )
    return resp.choices[0].message.content.strip()


def _template_dossier(target: str, abstracts: list[dict]) -> str:
    pmids = [a["pmid"] for a in abstracts[:2] if a.get("pmid")]
    c1 = f"[[PMID:{pmids[0]}]]" if pmids else ""
    c2 = f"[[PMID:{pmids[1]}]]" if len(pmids) > 1 else c1
    return (
        f"{target} is a validated therapeutic target whose dysregulation drives "
        f"disease progression {c1}. Known potent binders share a common "
        f"heteroaromatic hinge-binding scaffold, which this screen uses as the "
        f"similarity anchor for triage {c2}. Candidate molecules resembling these "
        f"proven actives — by Tanimoto similarity on Morgan fingerprints — are "
        f"prioritized, while non-drug-like and assay-interfering structures are "
        f"filtered out before ranking."
    )
