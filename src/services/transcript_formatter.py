from typing import List
from src.schemas.chat import MessageDTO

class TranscriptFormatter:
    """Transforms raw conversation messages into a clean text script intended for LLM evaluation."""

    @staticmethod
    def format_transcript(messages: List[MessageDTO]) -> str:
        """
        Takes a list of MessageDTOs and returns a formatted text script.
        Omits any IDs, metadata, or timestamps to reduce context bloat for the LLM.
        """
        lines = []
        for msg in messages:
            speaker = "Student" if msg.role == "user" else "Milo"
            content = str(msg.content or "").strip()
            if not content:
                continue
            lines.append(f"{speaker}: {content}")
        
        return "\n\n".join(lines)

