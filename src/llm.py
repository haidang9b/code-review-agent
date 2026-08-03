"""Model factories for Azure OpenAI chat and embeddings.

Modern LangChain v1 pattern: build model objects directly from
``langchain_openai`` and reuse them via ``with_structured_output`` at the call
site. No deprecated ``LLMChain`` wrapper.

``get_review_model`` points at ``AZURE_OPENAI_REVIEW_DEPLOYMENT`` when one is
configured, falling back to the default deployment. Reviewing code and verifying
groundedness are the only model calls this pipeline makes, and both benefit from
the strongest available model.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

from .config import Settings, load_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings so we validate the environment only once per process."""
    return load_settings()


def _chat(deployment: str, temperature: float | None) -> AzureChatOpenAI:
    s = get_settings()
    kwargs = {
        "azure_endpoint": s.azure_endpoint,
        "azure_deployment": deployment,
        "api_version": s.azure_api_version,
        "api_key": s.azure_api_key,
    }
    # Reasoning-family deployments reject an explicit temperature; pass ``None``
    # to leave it at the service default.
    if temperature is not None:
        kwargs["temperature"] = temperature
    return AzureChatOpenAI(**kwargs)


@lru_cache(maxsize=4)
def get_review_model(temperature: float | None = 0.0) -> AzureChatOpenAI:
    """Return the model used for code review and groundedness verification."""
    return _chat(get_settings().review_deployment, temperature)


@lru_cache(maxsize=1)
def get_embeddings() -> AzureOpenAIEmbeddings:
    """Return the Azure OpenAI embeddings model used for the vector stores."""
    s = get_settings()
    return AzureOpenAIEmbeddings(
        azure_endpoint=s.azure_endpoint,
        azure_deployment=s.azure_embedding_deployment,
        api_version=s.azure_api_version,
        api_key=s.azure_api_key,
    )
