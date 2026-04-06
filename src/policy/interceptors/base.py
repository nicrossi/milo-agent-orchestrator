from abc import ABC, abstractmethod


class BaseOutputInterceptor(ABC):
    name: str

    @abstractmethod
    def process(self, llm_output: str, question_text: str) -> tuple[bool, str]:
        """Return (was_modified, final_text)."""
