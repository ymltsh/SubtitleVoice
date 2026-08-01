import os
import re

from ..models import SubtitleLine

_RE_ASS_TAGS = re.compile(r"\{[^}]*\}")

_ASS_CTRL = [
    (r"\\N", ""),
    (r"\\n", ""),
    (r"\\h", " "),
    (r"\N", ""),
    (r"\n", ""),
    (r"\h", " "),
]


def parse_subtitle(filepath: str) -> list:
    try:
        import pysubs2
    except ImportError:
        raise ImportError("pysubs2 is required. Install with: pip install pysubs2")

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Subtitle file not found: {filepath}")

    subs = pysubs2.load(filepath, encoding="utf-8")

    lines = []
    for i, ev in enumerate(subs.events, 1):
        text = _RE_ASS_TAGS.sub("", ev.text)

        for tag, repl in _ASS_CTRL:
            text = text.replace(tag, repl)

        text = " ".join(text.split())
        if text.strip():
            lines.append(
                SubtitleLine(
                    id=i,
                    start=ev.start / 1000.0,
                    end=ev.end / 1000.0,
                    text=text,
                )
            )

    return lines
