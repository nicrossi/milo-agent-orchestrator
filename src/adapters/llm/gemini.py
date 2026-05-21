import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional

import google.genai as genai
from google.genai import types

_EVAL_CACHE_TTL_SECONDS = 3600
_EVAL_CACHE_SAFETY_MARGIN_SECONDS = 60

_TURN_CACHE_TTL_SECONDS = 3600
_TURN_CACHE_SAFETY_MARGIN_SECONDS = 60
# Cap concurrent activity-prefixes held in memory. Real-world load fits well
# inside this; trimming is best-effort LRU on insert.
_TURN_CACHE_MAX_ENTRIES = 16


@dataclass
class _TurnCacheEntry:
    name: str
    expires_at: float

from src.adapters.llm.base import BaseLLMAdapter
from src.schemas.chat import MessageDTO

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
        vertex_project = os.getenv("VERTEX_PROJECT")
        vertex_location = os.getenv("VERTEX_LOCATION", "us-central1")

        if vertex_project:
            self.client = genai.Client(vertexai=True, project=vertex_project, location=vertex_location)
        else:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("Either VERTEX_PROJECT or GOOGLE_API_KEY environment variable is required")
            self.client = genai.Client(api_key=api_key)

        # Default model — kept for backward compatibility (eval cache,
        # non-streaming generate_answer, legacy callers).
        legacy_model = os.getenv("LLM_MODEL", "gemini-2.5-flash")
        self.model_name = legacy_model
        # Per-mode model selection. Voice favors latency (Flash); text favors
        # depth (Pro). Both fall back to LLM_MODEL if set, otherwise sensible
        # per-mode defaults.
        self.model_voice = os.getenv("LLM_MODEL_VOICE", legacy_model or "gemini-2.5-flash")
        self.model_text = os.getenv("LLM_MODEL_TEXT", legacy_model or "gemini-2.5-pro")
        # Voice replies should be 1–2 sentences so the first TTS sentence
        # lands quickly; text replies keep the larger budget.
        self.max_output_tokens_voice = int(os.getenv("LLM_MAX_TOKENS_VOICE", "512"))
        self.max_output_tokens_text = int(os.getenv("LLM_MAX_TOKENS_TEXT", "8192"))

        self._config = types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=self.max_output_tokens_text,
            system_instruction=SYSTEM_INSTRUCTION,
        )

        self._eval_cache_name: Optional[str] = None
        self._eval_cache_key: Optional[str] = None
        self._eval_cache_expires_at: float = 0.0
        self._eval_cache_lock = asyncio.Lock()

        # Per-(static_prefix) Gemini context cache used by streaming turns.
        # Keyed by sha256 of the prefix so two activities with identical
        # prefixes share an entry.
        self._turn_caches: Dict[str, _TurnCacheEntry] = {}
        self._turn_cache_lock = asyncio.Lock()

    @staticmethod
    def _to_gemini_history(
            history: Optional[List[MessageDTO]],
    ) -> List[types.Content]:
        """
        Convert our generic history format into Gemini Content objects.
        """
        if not history:
            return []

        return [
            types.Content(
                role=msg.role if msg.role in ("user", "model") else "model",
                parts=[types.Part(text=msg.content)]
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
        history: Optional[List[MessageDTO]] = None,
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

    async def _get_or_create_eval_cache(self, static_prefix: str) -> str:
        key = hashlib.sha256(static_prefix.encode("utf-8")).hexdigest()
        now = time.monotonic()
        if (
            self._eval_cache_name
            and self._eval_cache_key == key
            and now < self._eval_cache_expires_at
        ):
            return self._eval_cache_name

        async with self._eval_cache_lock:
            if (
                self._eval_cache_name
                and self._eval_cache_key == key
                and time.monotonic() < self._eval_cache_expires_at
            ):
                return self._eval_cache_name

            cache = await self.client.aio.caches.create(
                model=self.model_name,
                config=types.CreateCachedContentConfig(
                    contents=[
                        types.Content(
                            role="user",
                            parts=[types.Part(text=static_prefix)],
                        )
                    ],
                    ttl=f"{_EVAL_CACHE_TTL_SECONDS}s",
                ),
            )
            self._eval_cache_name = cache.name
            self._eval_cache_key = key
            self._eval_cache_expires_at = (
                time.monotonic() + _EVAL_CACHE_TTL_SECONDS - _EVAL_CACHE_SAFETY_MARGIN_SECONDS
            )
            logger.info(
                "Created Gemini eval context cache %s (model=%s, ttl=%ds)",
                cache.name, self.model_name, _EVAL_CACHE_TTL_SECONDS,
            )
            return self._eval_cache_name

    @staticmethod
    def _turn_cache_key(static_prefix: str, model: str) -> str:
        # Caches are bound to a specific Gemini model, so include the model
        # name in the key — otherwise switching voice/text models would reuse
        # a stale cache name that the new model rejects.
        return hashlib.sha256(
            f"{model}\n{static_prefix}".encode("utf-8")
        ).hexdigest()

    async def _get_or_create_turn_cache(
        self, static_prefix: str, model: Optional[str] = None
    ) -> Optional[str]:
        """Look up (or lazily create) the Gemini context cache for a streaming
        turn's static prefix. Returns the cache resource name, or None if the
        cache could not be created (e.g. prefix below the min-token threshold).
        Failures are non-fatal — callers degrade to an uncached call.
        """
        effective_model = model or self.model_name
        key = self._turn_cache_key(static_prefix, effective_model)
        now = time.monotonic()
        entry = self._turn_caches.get(key)
        if entry and now < entry.expires_at:
            return entry.name

        async with self._turn_cache_lock:
            entry = self._turn_caches.get(key)
            if entry and time.monotonic() < entry.expires_at:
                return entry.name
            try:
                cache = await self.client.aio.caches.create(
                    model=effective_model,
                    config=types.CreateCachedContentConfig(
                        contents=[
                            types.Content(
                                role="user",
                                parts=[types.Part(text=static_prefix)],
                            )
                        ],
                        ttl=f"{_TURN_CACHE_TTL_SECONDS}s",
                    ),
                )
            except Exception as exc:
                # Most common: prefix below Gemini's minimum cacheable token
                # count. Log once and move on — the turn still runs uncached.
                logger.warning(
                    "Gemini turn cache create failed (model=%s): %s — proceeding uncached",
                    effective_model, exc,
                )
                return None

            self._turn_caches[key] = _TurnCacheEntry(
                name=cache.name,
                expires_at=(
                    time.monotonic()
                    + _TURN_CACHE_TTL_SECONDS
                    - _TURN_CACHE_SAFETY_MARGIN_SECONDS
                ),
            )
            # Best-effort LRU trim.
            if len(self._turn_caches) > _TURN_CACHE_MAX_ENTRIES:
                oldest_key = min(
                    self._turn_caches.items(),
                    key=lambda kv: kv[1].expires_at,
                )[0]
                self._turn_caches.pop(oldest_key, None)

            logger.info(
                "Created Gemini turn context cache %s (model=%s, ttl=%ds, prefix_key=%s)",
                cache.name, effective_model, _TURN_CACHE_TTL_SECONDS, key[:12],
            )
            return cache.name

    def _invalidate_turn_cache(
        self, static_prefix: Optional[str], model: Optional[str] = None
    ) -> None:
        if not static_prefix:
            return
        effective_model = model or self.model_name
        key = self._turn_cache_key(static_prefix, effective_model)
        self._turn_caches.pop(key, None)

    async def generate_evaluation(self, static_prefix: str, dynamic_suffix: str) -> str:
        try:
            cache_name = await self._get_or_create_eval_cache(static_prefix)
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=dynamic_suffix,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                    cached_content=cache_name,
                ),
            )
            return response.text
        except Exception as e:
            self._eval_cache_name = None
            self._eval_cache_key = None
            self._eval_cache_expires_at = 0.0
            logger.error(
                "LLM evaluation failed (model=%s)",
                self.model_name,
                exc_info=True,
            )
            raise RuntimeError(f"Failed to generate evaluation from the LLM: {str(e)}") from e

    # Async streaming generation

    async def generate_answer_stream(
        self,
        query: str,
        context: List[str],
        history: Optional[List[MessageDTO]] = None,
        static_prefix: Optional[str] = None,
        is_voice: bool = False,
    ) -> AsyncIterator[str]:
        from google.genai.errors import ServerError as _GenaiServerError

        gemini_history = self._to_gemini_history(history)

        # Pick the right model + token budget for this mode. Voice uses Flash
        # for snappy first-token; text uses Pro for depth.
        effective_model = self.model_voice if is_voice else self.model_text
        max_tokens = (
            self.max_output_tokens_voice if is_voice else self.max_output_tokens_text
        )

        # Try to put the session-stable prefix in Gemini's context cache so
        # subsequent turns skip re-processing it. On cache miss/failure, fall
        # back to inlining the prefix into the user message.
        cache_name: Optional[str] = None
        if static_prefix:
            cache_name = await self._get_or_create_turn_cache(
                static_prefix, model=effective_model
            )

        # Voice mode disables Gemini's "thinking" tokens: they consume the same
        # output budget and silently truncate the visible reply mid-sentence.
        thinking_config = types.ThinkingConfig(thinking_budget=0) if is_voice else None

        if cache_name:
            user_message = self._build_user_message(query, context)
            config = types.GenerateContentConfig(
                temperature=self._config.temperature,
                max_output_tokens=max_tokens,
                system_instruction=self._config.system_instruction,
                cached_content=cache_name,
                thinking_config=thinking_config,
            )
        else:
            inline_context = [static_prefix, *context] if static_prefix else context
            user_message = self._build_user_message(query, inline_context)
            config = types.GenerateContentConfig(
                temperature=self._config.temperature,
                max_output_tokens=max_tokens,
                system_instruction=self._config.system_instruction,
                thinking_config=thinking_config,
            )

        max_attempts = 3
        first_chunk = None
        iterator = None
        cache_dropped_on_retry = False

        try:
            for attempt in range(max_attempts):
                try:
                    chat = self.client.aio.chats.create(
                        model=effective_model,
                        history=gemini_history,
                        config=config,
                    )
                    response_stream = await asyncio.wait_for(
                        chat.send_message_stream(user_message),
                        timeout=15.0,
                    )
                    iterator = response_stream.__aiter__()
                    first_chunk = await iterator.__anext__()
                    break
                except StopAsyncIteration:
                    # Empty stream — nothing to yield.
                    return
                except _GenaiServerError as exc:
                    is_last = attempt == max_attempts - 1
                    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                    if is_last or code not in (503, 429, "UNAVAILABLE", "RESOURCE_EXHAUSTED"):
                        raise
                    backoff = 1.0 * (2 ** attempt)  # 1s, 2s, 4s
                    logger.warning(
                        "LLM transient error %s on attempt %d/%d (model=%s) — retrying in %.1fs",
                        code, attempt + 1, max_attempts, effective_model, backoff,
                    )
                    await asyncio.sleep(backoff)
                except Exception:
                    # If we were using a server-side cache and the request
                    # failed for any other reason, the cache may be stale
                    # (TTL expired server-side, deleted, etc). Drop it once
                    # and fall back to an inline-prefix retry before bubbling.
                    if cache_name and not cache_dropped_on_retry and attempt < max_attempts - 1:
                        cache_dropped_on_retry = True
                        self._invalidate_turn_cache(static_prefix, model=effective_model)
                        inline_context = (
                            [static_prefix, *context] if static_prefix else context
                        )
                        user_message = self._build_user_message(query, inline_context)
                        config = types.GenerateContentConfig(
                            temperature=self._config.temperature,
                            max_output_tokens=max_tokens,
                            system_instruction=self._config.system_instruction,
                        )
                        cache_name = None
                        logger.warning(
                            "Dropped Gemini turn cache after stream error — retrying uncached",
                        )
                        continue
                    raise

            # First chunk acquired (or skipped). Stream the rest.
            if first_chunk and first_chunk.text:
                yield first_chunk.text
            if iterator is not None:
                async for chunk in iterator:
                    if chunk.text:
                        yield chunk.text

        except asyncio.TimeoutError as e:
            logger.error("LLM connection timed out (model=%s)", self.model_name)
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