from dataclasses import dataclass
from typing import Any, Protocol


class PhoneNumberUnclear(ValueError):
    """Phone audio could not be verified; never infer omitted digits."""


@dataclass
class CapturedAudio:
    samples: Any
    sample_rate: int = 16000
    endpoint_ms: float = 0


class Listener(Protocol):
    def listen(self) -> CapturedAudio: ...


class Transcriber(Protocol):
    def transcribe(self, audio: CapturedAudio | str) -> str: ...


class Speaker(Protocol):
    def speak(self, text: str) -> dict: ...
