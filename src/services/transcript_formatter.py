from typing import List
from src.schemas.chat import MessageDTO

class TranscriptFormatter:
    """Transforms raw conversation messages into a clean text script intended for LLM evaluation."""

    @staticmethod
    def format_transcript(messages: List[MessageDTO], user: str, activity_id: str) -> str:
        """
        Takes a list of MessageDTOs and returns a formatted text script.
        Omits any IDs, metadata, or timestamps to reduce context bloat for the LLM.
        """
        lines = []
        for msg in messages:
            role = msg.role.lower()
            speaker = "Student" if role == "user" else "Milo"
            content = str(msg.content or "").strip()
            if role == "system" or not content:
                continue
            lines.append(f"{speaker}: {content}")
        
        formatted_transcript = "\n\n".join(lines)

        # Dump the transcript to a file for debugging purposes
        file_name = f"{user}_{activity_id}_transcript.txt"
        with open(file_name, "w") as file:
            file.write(formatted_transcript)

        return formatted_transcript
