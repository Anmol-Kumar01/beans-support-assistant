from abc import ABC, abstractmethod

from evals.schema import BotResponse, EvalQuestion


class Target(ABC):
    """A bot under evaluation. Implementations must never raise for a bad bot reply;
    they return a BotResponse with ``error`` set so the run continues."""

    name: str

    @abstractmethod
    async def answer(self, question: EvalQuestion, session_id: str) -> BotResponse: ...

    @abstractmethod
    def describe(self) -> dict:
        """Non-secret settings recorded in the run manifest."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass
