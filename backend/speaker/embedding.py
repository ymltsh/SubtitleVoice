import os

import numpy as np

_encoder_instance = None
_encoder_available = None


def _check_encoder():
    global _encoder_available
    if _encoder_available is not None:
        return _encoder_available
    try:
        import torch  # noqa: F401
        import speechbrain  # noqa: F401
        _encoder_available = True
    except ImportError:
        _encoder_available = False
    return _encoder_available


def is_encoder_available():
    return _check_encoder()


SAMPLE_RATE = 16000

_MODEL_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "dependencies", "ecapa", "pretrained_models", "ecapa"
))


class ECAPAEncoder:
    name = "ecapa"
    dimension = 192

    def __init__(self, device: str = "cuda"):
        from speechbrain.inference.speaker import SpeakerRecognition

        self.device = device
        if os.path.isfile(os.path.join(_MODEL_DIR, "hyperparams.yaml")):
            source = _MODEL_DIR
        else:
            source = "speechbrain/spkrec-ecapa-voxceleb"
        self.model = SpeakerRecognition.from_hparams(
            source=source,
            savedir=_MODEL_DIR,
            run_opts={"device": device},
        )

    def encode(self, wav_path: str):
        import torch
        import torchaudio

        waveform, sr = torchaudio.load(wav_path)

        if sr != SAMPLE_RATE:
            waveform = torchaudio.functional.resample(waveform, sr, SAMPLE_RATE)

        waveform = waveform.to(self.device)

        with torch.inference_mode():
            embedding = self.model.encode_batch(waveform)

        return embedding.squeeze().cpu().numpy()

    def encode_batch(self, wav_paths: list[str]):
        embeddings = []
        for path in wav_paths:
            emb = self.encode(path)
            embeddings.append(emb)
        return embeddings


def get_encoder(device: str = "cuda"):
    if not _check_encoder():
        raise RuntimeError(
            "ECAPA encoder not available. Install with: pip install torch speechbrain torchaudio"
        )
    global _encoder_instance
    if _encoder_instance is None:
        import torch
        if device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
        _encoder_instance = ECAPAEncoder(device=device)
    return _encoder_instance
