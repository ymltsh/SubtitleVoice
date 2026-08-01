"""
VR (Vocal Remover) processor for HP2/HP3/HP5 and DeEcho/DeReverb models.

Wraps the original AudioPre / AudioPreDeEcho classes with minimal changes
to the core inference logic.
"""
import os
import logging

import librosa
import numpy as np
import soundfile as sf
import torch
from lib.lib_v5 import nets_61968KB as Nets
from lib.lib_v5 import spec_utils
from lib.lib_v5.model_param_init import ModelParameters
from lib.lib_v5.nets_new import CascadedNet
from lib.utils import inference

from config import WEIGHTS_ROOT, INFER_DEVICE, IS_HALF
from engine.base import Processor, process_files
from engine.registry import register_model

logger = logging.getLogger(__name__)


class _AudioPre:
    """Original AudioPre logic for HP2/HP3/HP5 models."""

    def __init__(self, agg, model_path, device, is_half, tta=False):
        self.model_path = model_path
        self.device = device
        self.data = {
            "postprocess": False,
            "tta": tta,
            "window_size": 512,
            "agg": agg,
            "high_end_process": "mirroring",
        }
        mp = ModelParameters(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "lib", "lib_v5", "modelparams", "4band_v2.json")
        )
        model = Nets.CascadedASPPNet(mp.param["bins"] * 2)
        cpk = torch.load(model_path, map_location="cpu")
        model.load_state_dict(cpk)
        model.eval()
        if is_half:
            model = model.half().to(device)
        else:
            model = model.to(device)
        self.mp = mp
        self.model = model

    def _path_audio_(self, music_file, ins_root=None, vocal_root=None, format="flac", is_hp3=False):
        if vocal_root is None:
            return "No save root."
        raw = os.path.basename(music_file)
        name = raw.replace(".reformatted.wav", "") if ".reformatted" in raw else raw
        os.makedirs(vocal_root, exist_ok=True)
        if ins_root is not None:
            os.makedirs(ins_root, exist_ok=True)
        X_wave, y_wave, X_spec_s, y_spec_s = {}, {}, {}, {}
        bands_n = len(self.mp.param["band"])
        for d in range(bands_n, 0, -1):
            bp = self.mp.param["band"][d]
            if d == bands_n:
                X_wave[d], _ = librosa.load(
                    music_file, sr=bp["sr"], mono=False, dtype=np.float32, res_type=bp["res_type"],
                )
                if X_wave[d].ndim == 1:
                    X_wave[d] = np.asfortranarray([X_wave[d], X_wave[d]])
            else:
                X_wave[d] = librosa.resample(
                    X_wave[d + 1],
                    orig_sr=self.mp.param["band"][d + 1]["sr"],
                    target_sr=bp["sr"],
                    res_type=bp["res_type"],
                )
            X_spec_s[d] = spec_utils.wave_to_spectrogram_mt(
                X_wave[d], bp["hl"], bp["n_fft"],
                self.mp.param["mid_side"], self.mp.param["mid_side_b2"], self.mp.param["reverse"],
            )
            if d == bands_n and self.data["high_end_process"] != "none":
                input_high_end_h = (bp["n_fft"] // 2 - bp["crop_stop"]) + (
                    self.mp.param["pre_filter_stop"] - self.mp.param["pre_filter_start"]
                )
                input_high_end = X_spec_s[d][:, bp["n_fft"] // 2 - input_high_end_h: bp["n_fft"] // 2, :]

        X_spec_m = spec_utils.combine_spectrograms(X_spec_s, self.mp)
        aggresive_set = float(self.data["agg"] / 100)
        aggressiveness = {
            "value": aggresive_set,
            "split_bin": self.mp.param["band"][1]["crop_stop"],
        }
        with torch.no_grad():
            pred, X_mag, X_phase = inference(X_spec_m, self.device, self.model, aggressiveness, self.data)
        if self.data["postprocess"]:
            pred_inv = np.clip(X_mag - pred, 0, np.inf)
            pred = spec_utils.mask_silence(pred, pred_inv)
        y_spec_m = pred * X_phase
        v_spec_m = X_spec_m - y_spec_m

        if is_hp3:
            y_spec_m, v_spec_m = v_spec_m, y_spec_m

        if self.data["high_end_process"].startswith("mirroring"):
            input_high_end_ = spec_utils.mirroring(self.data["high_end_process"], v_spec_m, input_high_end, self.mp)
            wav_vocals = spec_utils.cmb_spectrogram_to_wave(
                v_spec_m, self.mp, input_high_end_h, input_high_end_,
            )
        else:
            wav_vocals = spec_utils.cmb_spectrogram_to_wave(v_spec_m, self.mp)
        logger.info("%s vocals done", name)
        self._save_audio(wav_vocals, os.path.join(vocal_root, name), format, self.mp)

        if ins_root is not None:
            if self.data["high_end_process"].startswith("mirroring"):
                input_high_end_ = spec_utils.mirroring(self.data["high_end_process"], y_spec_m, input_high_end, self.mp)
                wav_inst = spec_utils.cmb_spectrogram_to_wave(
                    y_spec_m, self.mp, input_high_end_h, input_high_end_,
                )
            else:
                wav_inst = spec_utils.cmb_spectrogram_to_wave(y_spec_m, self.mp)
            logger.info("%s instrument done", name)
            self._save_audio(wav_inst, os.path.join(ins_root, name), format, self.mp)

    @staticmethod
    def _save_audio(wav, path, format, mp):
        if format in ("wav", "flac"):
            ext = "." + format
            if not path.lower().endswith(ext):
                path = path + ext
            sf.write(path, (np.array(wav) * 32768).astype("int16"), mp.param["sr"])
        else:
            wav_path = path.replace("." + format, "") + ".wav"
            sf.write(wav_path, (np.array(wav) * 32768).astype("int16"), mp.param["sr"])
            os.system('ffmpeg -i "{}" -vn "{}" -q:a 2 -y'.format(wav_path, path + "." + format))
            try:
                os.remove(wav_path)
            except Exception:
                pass


class _AudioPreDeEcho:
    """Original AudioPreDeEcho logic for VR-DeEchoNormal/Aggressive/DeReverb models."""

    def __init__(self, agg, model_path, device, is_half, tta=False):
        self.model_path = model_path
        self.device = device
        self.data = {
            "postprocess": False,
            "tta": tta,
            "window_size": 512,
            "agg": agg,
            "high_end_process": "mirroring",
        }
        mp = ModelParameters(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "lib", "lib_v5", "modelparams", "4band_v3.json")
        )
        nout = 64 if "DeReverb" in model_path else 48
        model = CascadedNet(mp.param["bins"] * 2, nout)
        cpk = torch.load(model_path, map_location="cpu")
        model.load_state_dict(cpk)
        model.eval()
        if is_half:
            model = model.half().to(device)
        else:
            model = model.to(device)
        self.mp = mp
        self.model = model

    def _path_audio_(self, music_file, ins_root=None, vocal_root=None, format="flac", is_hp3=False):
        if vocal_root is None:
            return "No save root."
        raw = os.path.basename(music_file)
        name = raw.replace(".reformatted.wav", "") if ".reformatted" in raw else raw
        os.makedirs(vocal_root, exist_ok=True)
        if ins_root is not None:
            os.makedirs(ins_root, exist_ok=True)
        X_wave, y_wave, X_spec_s, y_spec_s = {}, {}, {}, {}
        bands_n = len(self.mp.param["band"])
        for d in range(bands_n, 0, -1):
            bp = self.mp.param["band"][d]
            if d == bands_n:
                X_wave[d], _ = librosa.load(
                    music_file, sr=bp["sr"], mono=False, dtype=np.float32, res_type=bp["res_type"],
                )
                if X_wave[d].ndim == 1:
                    X_wave[d] = np.asfortranarray([X_wave[d], X_wave[d]])
            else:
                X_wave[d] = librosa.resample(
                    X_wave[d + 1],
                    orig_sr=self.mp.param["band"][d + 1]["sr"],
                    target_sr=bp["sr"],
                    res_type=bp["res_type"],
                )
            X_spec_s[d] = spec_utils.wave_to_spectrogram_mt(
                X_wave[d], bp["hl"], bp["n_fft"],
                self.mp.param["mid_side"], self.mp.param["mid_side_b2"], self.mp.param["reverse"],
            )
            if d == bands_n and self.data["high_end_process"] != "none":
                input_high_end_h = (bp["n_fft"] // 2 - bp["crop_stop"]) + (
                    self.mp.param["pre_filter_stop"] - self.mp.param["pre_filter_start"]
                )
                input_high_end = X_spec_s[d][:, bp["n_fft"] // 2 - input_high_end_h: bp["n_fft"] // 2, :]

        X_spec_m = spec_utils.combine_spectrograms(X_spec_s, self.mp)
        aggresive_set = float(self.data["agg"] / 100)
        aggressiveness = {
            "value": aggresive_set,
            "split_bin": self.mp.param["band"][1]["crop_stop"],
        }
        with torch.no_grad():
            pred, X_mag, X_phase = inference(X_spec_m, self.device, self.model, aggressiveness, self.data)
        if self.data["postprocess"]:
            pred_inv = np.clip(X_mag - pred, 0, np.inf)
            pred = spec_utils.mask_silence(pred, pred_inv)
        y_spec_m = pred * X_phase
        v_spec_m = X_spec_m - y_spec_m

        if self.data["high_end_process"].startswith("mirroring"):
            input_high_end_ = spec_utils.mirroring(self.data["high_end_process"], v_spec_m, input_high_end, self.mp)
            wav_vocals = spec_utils.cmb_spectrogram_to_wave(
                v_spec_m, self.mp, input_high_end_h, input_high_end_,
            )
        else:
            wav_vocals = spec_utils.cmb_spectrogram_to_wave(v_spec_m, self.mp)
        logger.info("%s vocals done", name)
        _AudioPre._save_audio(wav_vocals, os.path.join(vocal_root, name), format, self.mp)

        if ins_root is not None:
            if self.data["high_end_process"].startswith("mirroring"):
                input_high_end_ = spec_utils.mirroring(self.data["high_end_process"], y_spec_m, input_high_end, self.mp)
                wav_inst = spec_utils.cmb_spectrogram_to_wave(
                    y_spec_m, self.mp, input_high_end_h, input_high_end_,
                )
            else:
                wav_inst = spec_utils.cmb_spectrogram_to_wave(y_spec_m, self.mp)
            logger.info("%s instrument done", name)
            _AudioPre._save_audio(wav_inst, os.path.join(ins_root, name), format, self.mp)


@register_model("vr")
@register_model("HP2_all_vocals")
@register_model("HP5_only_main_vocal")
class VRProcessor(Processor):
    """Processor for HP2/HP3/HP5 vocal separation models."""

    model_name = "vr"

    def __init__(self, checkpoint_name: str):
        checkpoint_path = os.path.join(WEIGHTS_ROOT, checkpoint_name + ".pth")
        self._backend = _AudioPre(agg=10, model_path=checkpoint_path, device=INFER_DEVICE, is_half=IS_HALF)
        self._is_hp3 = "HP3" in checkpoint_name

    def process(self, input_dir: str, output_dir: str, format: str = "flac", **kwargs) -> None:
        process_files(self._backend._path_audio_, input_dir, output_dir, format, self._is_hp3, keep_inst=kwargs.get("keep_inst", False))

    def cleanup(self):
        if hasattr(self._backend, "model"):
            del self._backend.model
        del self._backend
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


@register_model("deecho")
@register_model("VR-DeEchoNormal")
@register_model("VR-DeEchoAggressive")
@register_model("VR-DeEchoDeReverb")
class VRDeEchoProcessor(Processor):
    """Processor for VR-DeEchoNormal/Aggressive/DeReverb models."""

    model_name = "deecho"

    def __init__(self, checkpoint_name: str):
        agg_value = 10
        if "Aggressive" in checkpoint_name:
            agg_value = 10
        self._backend = _AudioPreDeEcho(
            agg=agg_value,
            model_path=os.path.join(WEIGHTS_ROOT, checkpoint_name + ".pth"),
            device=INFER_DEVICE,
            is_half=IS_HALF,
        )
        self._is_hp3 = False

    def process(self, input_dir: str, output_dir: str, format: str = "flac", **kwargs) -> None:
        process_files(self._backend._path_audio_, input_dir, output_dir, format, False, keep_inst=kwargs.get("keep_inst", False))

    def cleanup(self):
        if hasattr(self._backend, "model"):
            del self._backend.model
        del self._backend
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
