"""langchain bridges for the JudgeClient — the ONLY module where the judge SDK
surface (langchain-core) meets the judge boundary. RagasEngine imports this;
business layers never do (Spec: SDK isolated in JudgeClient, never leaks)."""

from __future__ import annotations

from typing import Any

from app.engines.judge import EmbeddingsClient, JudgeClient


def _langchain_core():
    from langchain_core.callbacks import CallbackManagerForLLMRun
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    return BaseChatModel, Embeddings, AIMessage, BaseMessage, ChatGeneration, ChatResult, CallbackManagerForLLMRun


class RagasLLMBridge:
    """Adapts JudgeClient to what ragas's LangchainLLMWrapper expects."""

    def __init__(self, judge: JudgeClient) -> None:
        BaseChatModel, _, AIMessage, _, ChatGeneration, ChatResult, _ = _langchain_core()
        self._judge = judge
        # dynamic subclass bound to this instance's deps (langchain is optional;
        # class must exist only when ragas path is used)
        bridge = self

        class _Bridge(BaseChatModel):  # type: ignore[misc, valid-type]
            @property
            def _llm_type(self) -> str:
                return "rageval-judge"

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                # ragas 0.4 scoring is async-only; sync path stays unsupported.
                raise NotImplementedError("ragas scoring uses the async path")

            async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
                text = _extract_text(messages)
                content = await bridge._judge.ainvoke(text)
                return ChatResult(
                    generations=[ChatGeneration(message=AIMessage(content=content))]
                )

        self._impl = _Bridge()

    @property
    def impl(self):
        return self._impl

    def __getattr__(self, item: str) -> Any:
        return getattr(self._impl, item)


def _extract_text(messages: Any) -> str:
    parts = []
    for m in messages:
        content = getattr(m, "content", m)
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.append(str(content))
    return "\n".join(parts)


class RagasEmbeddingsBridge:
    """Adapts EmbeddingsClient to langchain's Embeddings interface."""

    @staticmethod
    def default_from_judge(judge: JudgeClient) -> EmbeddingsClient:
        from app.engines.judge import PlaceholderEmbeddings

        desc = judge.describe()
        return PlaceholderEmbeddings(str(desc.get("model", "")))

    def __init__(self, client: EmbeddingsClient) -> None:
        _, Embeddings, *_ = _langchain_core()
        self._client = client
        bridge = self

        class _Bridge(Embeddings):  # type: ignore[misc, valid-type]
            def embed_query(self, text: str) -> list[float]:
                return bridge._client.embed_query(text)

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                return bridge._client.embed_documents(texts)

        self._impl = _Bridge()

    @property
    def impl(self):
        return self._impl

    def __getattr__(self, item: str) -> Any:
        return getattr(self._impl, item)
