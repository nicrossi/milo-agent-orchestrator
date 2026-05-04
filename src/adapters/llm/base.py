from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, List, Optional
from src.schemas.chat import MessageDTO

class BaseLLMAdapter(ABC):
    """Abstract base class for all LLM integrations."""

    @abstractmethod
    def generate_answer(
        self,
        query: str,
        context: List[str],
        history: Optional[List[MessageDTO]] = None,
    ) -> str:
        """
        Synthesizes an answer based on the user query and provided context.

        Args:
            query: The user's input question.
            context: A list of text chunks retrieved from the knowledge base.
            history: Optional list of previous messages as MessageDTO.

        Returns:
            The generated string response from the LLM.
        """
        pass

    @abstractmethod
    async def generate_evaluation(
        self,
        static_prefix: str,
        dynamic_suffix: str,
    ) -> str:
        """
        Generates a structured evaluation, splitting the prompt into a stable
        cacheable prefix (rubric, framework, few-shot examples, schema) and a
        per-call dynamic suffix (transcript and activity context).

        Implementations should leverage provider-side context caching on the
        static prefix to amortize token cost across calls.

        Args:
            static_prefix: Stable portion safe to cache across sessions.
            dynamic_suffix: Per-session input (transcript, teacher goal, activity).

        Returns:
            The generated JSON string response from the LLM.
        """
        pass

    @abstractmethod
    async def generate_answer_stream(
        self,
        query: str,
        context: List[str],
        history: Optional[List[MessageDTO]] = None,
    ) -> AsyncIterator[str]:
        """
        Streams an answer token-by-token based on the user query and provided context.

        Args:
            query: The user's input question.
            context: A list of text chunks retrieved from the knowledge base.
            history: Optional list of previous messages as MessageDTO.

        Yields:
            String chunks of the generated response.
        """
        pass
