import asyncio
import logging
import os
from typing import List

import httpx

logger = logging.getLogger("milo-orchestrator.rag_client")


class RAGClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("RAG_SERVICE_URL", "http://localhost:8000")
        if not self.base_url:
            raise ValueError("RAG_SERVICE_URL environment variable is required")

    async def retrieve_context(self, query: str, limit: int = 10) -> List[str]:
        """Fetches vector-matched context chunks from the internal RAG API."""
        url = f"{self.base_url}/search"
        payload = {"query": query, "limit": limit}

        try:
            # reuse client, rather than creating a new one per-request
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                return [item["text"] for item in data.get("results", [])]
        except httpx.HTTPStatusError as e:
            logger.error(
                "RAG service returned HTTP %s for POST %s. Response body: %s",
                e.response.status_code,
                url,
                e.response.text[:500] if e.response.text else "<empty>",
                exc_info=True,
            )
            raise RuntimeError(f"RAG service returned HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(
                "Failed to communicate with RAG service at %s",
                url,
                exc_info=True,
            )
            raise RuntimeError("RAG service is currently unavailable") from e
        except asyncio.CancelledError:
            logger.info("RAG context retrieval cancelled mid-flight for URL: %s", url)
            raise
        except Exception as e:
            logger.error(
                "Unexpected error processing RAG response from %s",
                url,
                exc_info=True,
            )
            raise RuntimeError("RAG service returned an unexpected response") from e