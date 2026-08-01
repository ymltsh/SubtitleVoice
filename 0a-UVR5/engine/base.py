"""
Abstract base class for all audio processors.
"""
import os
import traceback
import logging
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class Processor(ABC):
    """Unified interface for all audio separation/dereverberation processors."""

    model_name: str = "base"

    @abstractmethod
    def process(
        self,
        input_dir: str,
        output_dir: str,
        format: str = "flac",
        **kwargs,
    ) -> None:
        """
        Process all .wav files in input_dir, writing results to output_dir.

        Args:
            input_dir: Path to directory containing .wav files.
            output_dir: Path to directory for processed output.
            format: Output audio format (wav, flac, mp3, m4a).
        """
        ...

    def cleanup(self) -> None:
        """Release GPU memory. Override in subclasses that own models."""
        pass


def process_files(infer_fn, input_dir: str, output_dir: str, format: str, is_hp3: bool, keep_inst: bool = False):
    """
    Shared file iteration and audio reformatting logic.

    Finds all .wav files recursively under input_dir, checks each for
    44100Hz stereo, reformats via ffmpeg if needed, then calls infer_fn.

    Args:
        infer_fn: The _path_audio_ method from the underlying backend class.
        input_dir: Root directory to scan for audio files.
        output_dir: Root directory for processed output.
        format: Output format (wav, flac, mp3, m4a).
        is_hp3: HP3 mode flag (swaps vocal/instrument labels).
        keep_inst: Whether to also save instrument/others output.
    """
    import ffmpeg

    input_path = Path(input_dir)
    wav_files = list(input_path.rglob("*.wav"))
    if not wav_files:
        wav_files = list(input_path.rglob("*"))
        wav_files = [f for f in wav_files if f.suffix.lower() in (".wav", ".flac", ".mp3", ".m4a")]

    for wav_file in wav_files:
        inp = str(wav_file)
        rel = wav_file.relative_to(input_path)
        vocal_dir = os.path.join(output_dir, "vocal", str(rel.parent))
        inst_dir = os.path.join(output_dir, "instrument", str(rel.parent)) if keep_inst else None

        need_reformat = True
        done = False
        try:
            info = ffmpeg.probe(inp, cmd="ffprobe")
            if info["streams"][0]["channels"] == 2 and info["streams"][0]["sample_rate"] == "44100":
                need_reformat = False
                infer_fn(inp, inst_dir, vocal_dir, format, is_hp3)
                done = True
        except Exception:
            traceback.print_exc()

        if need_reformat:
            tmp_path = os.path.join(
                os.environ.get("TEMP", "C:\\Windows\\Temp" if os.name == "nt" else "/tmp"),
                os.path.basename(inp) + ".reformatted.wav",
            )
            os.system(
                'ffmpeg -i "{}" -vn -acodec pcm_s16le -ac 2 -ar 44100 "{}" -y'.format(inp, tmp_path)
            )
            try:
                if not done:
                    infer_fn(tmp_path, inst_dir, vocal_dir, format, is_hp3)
            except Exception:
                traceback.print_exc()
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        logger.info("%s -> Success", os.path.basename(inp))
