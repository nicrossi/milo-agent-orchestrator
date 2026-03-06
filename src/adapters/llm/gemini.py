import asyncio
import logging
import os
from typing import AsyncIterator, Dict, List, Optional

import google.genai as genai
from google.genai import types

from src.adapters.llm.base import BaseLLMAdapter

logger = logging.getLogger("milo-orchestrator.llm")

# Probably, we don't want this here!
SYSTEM_INSTRUCTION = """You are Milo, an intelligent assistant. Your goal is to provide accurate, \
context-aware answers by synthesizing provided documentation with your \
internal reasoning capabilities.

### Context Handling
* The user's message may include a [Context] section with data retrieved from a knowledge base.
* Your primary priority is to answer using the information found in the [Context].
* If the [Context] does not contain the answer, state clearly that the \
  information is unavailable in the current records, rather than making up facts. \
  In that case, provide an answer by using trusted references from the internet.

### Guidelines
1. **Accuracy First:** If there is a conflict between your general training \
   and the provided context, defer to the [Context].
2. **Tone:** Be professional, thorough, and helpful. Provide detailed and complete answers.
3. **Attribution:** When possible, refer to the specific parts of the context used.
4. **Continuity:** Maintain context from the conversation and avoid repeating \
   information already discussed."""


class GeminiAdapter(BaseLLMAdapter):
    def __init__(self) -> None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is required")

        self.client = genai.Client(api_key=api_key)
        self.model_name = os.getenv("LLM_MODEL", "gemini-2.5-flash")
        self._config = types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=8192,
            system_instruction=SYSTEM_INSTRUCTION,
        )

    @staticmethod
    def _to_gemini_history(
            history: Optional[List[Dict[str, str]]],
    ) -> List[types.Content]:
        """
        Convert our generic history format into Gemini Content objects.
        """
        if not history:
            return []

        return [
            types.Content(
                role=msg["role"] if msg.get("role") in ("user", "model") else "model",
                parts=[types.Part(text=msg["content"])]
            )
            for msg in history
        ]

    @staticmethod
    def _build_user_message(query: str, context: List[str]) -> str:
        """Build the user turn, optionally injecting RAG context."""
        if not context:
            return query
        context_text = "\n\n---\n\n".join(context)
        return f"[Context]\n{context_text}\n\n---\n[User Question]\n{query}"

    def generate_answer(
            self,
            query: str,
            context: List[str],
            history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        gemini_history = self._to_gemini_history(history)
        user_message = self._build_user_message(query, context)

        try:
            chat = self.client.chats.create(
                model=self.model_name,
                history=gemini_history,
                config=self._config,
            )
            response = chat.send_message(user_message)
            return response.text
        except Exception as e:
            logger.error(
                "LLM generation failed (model=%s)",
                self.model_name,
                exc_info=True,
            )
            raise RuntimeError("Failed to generate response from the LLM") from e

    # Async streaming generation

    async def generate_answer_stream(
            self,
            query: str,
            context: List[str],
            history: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[str]:
        gemini_history = self._to_gemini_history(history)
        user_message = self._build_user_message(query, context)

        try:
            chat = self.client.aio.chats.create(
                model=self.model_name,
                history=gemini_history,
                config=self._config,
            )
            response_stream = await asyncio.wait_for(
                chat.send_message_stream(user_message),
                timeout=15.0  # 15 secs to connect and start receiving tokens
            )

            async for chunk in response_stream:
                if chunk.text:
                    yield chunk.text
        except asyncio.TimeoutError as e:
            logger.error("LLM connection timed out after 30 seconds (model=%s)", self.model_name)
            raise RuntimeError("The LLM service took too long to respond.") from e
        except asyncio.CancelledError:
            logger.info("LLM stream cancelled mid-flight (model=%s).", self.model_name)
            raise
        except Exception as e:
            logger.error(
                "LLM streaming failed (model=%s)",
                self.model_name,
                exc_info=True,
            )
            raise RuntimeError("Failed to stream response from the LLM") from e