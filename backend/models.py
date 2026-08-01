from dataclasses import dataclass
from typing import Optional


@dataclass
class Clip:
    id: Optional[int] = None
    episode: str = ""
    start: float = 0.0
    end: float = 0.0
    text: str = ""
    selected_speaker_id: Optional[int] = None
    trim_start: float = 0.0
    trim_end: float = 0.0

    @property
    def effective_start(self) -> float:
        return self.start + self.trim_start

    @property
    def effective_end(self) -> float:
        return self.end + self.trim_end

    @property
    def effective_duration(self) -> float:
        return self.effective_end - self.effective_start

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "episode": self.episode,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "selected_speaker_id": self.selected_speaker_id,
            "trim_start": round(self.trim_start, 3),
            "trim_end": round(self.trim_end, 3),
            "effective_start": round(self.effective_start, 3),
            "effective_end": round(self.effective_end, 3),
            "duration": round(self.effective_duration, 3),
        }


@dataclass
class SubtitleLine:
    id: int
    start: float
    end: float
    text: str


@dataclass
class Speaker:
    id: Optional[int] = None
    name: str = ""
    color: str = "#0ea5e9"
    created_at: str = ""
