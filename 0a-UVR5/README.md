# VoiceSTCut-PostProcess

Batch audio vocal separation & dereverberation CLI tool.

Extracted from the UVR5 engine of GPT-SoVITS.

## Directory Structure

```
VoiceSTCut-PostProcess/
    cli.py                   # CLI entry point
    config.py                # Device / precision auto-detection
    engine/                  # Processing engines
        base.py              # Processor ABC + process_files utility
        registry.py          # Model -> Processor factory
        vr.py                # VRProcessor (HP2/HP3/HP5) + VRDeEchoProcessor
        mdx.py               # MDXProcessor (ONNX dereverb)
        roformer.py          # RoformerProcessor (BS-Roformer / Mel-Band Roformer)
    pipeline/
        uvr.py               # UVRPipeline orchestration
    lib/                     # Model library (CascadedASPPNet, CascadedNet, spec_utils)
    bs_roformer/             # BSRoformer / MelBandRoformer transformer modules
    dependencies/            # Model checkpoint files (built-in)
    requirements.txt
```

## Installation

```bash
pip install -r requirements.txt
```

System requirements: `ffmpeg` and `ffprobe` must be available on PATH.

## Models

All model weights are included in the `dependencies/` directory:

```
dependencies/
    HP2_all_vocals.pth
    HP5_only_main_vocal.pth
    VR-DeEchoNormal.pth
    VR-DeEchoAggressive.pth
    VR-DeEchoDeReverb.pth
    model_bs_roformer_ep_317_sdr_12.9755.ckpt
    onnx_dereverb_By_FoxJoy/
        vocals.onnx
```

## Usage

```bash
# Vocal separation with BS-Roformer
python cli.py --input workspace/Mushoku/export --model model_bs_roformer_ep_317_sdr_12.9755

# Vocal separation (keep all vocals + harmony)
python cli.py -i workspace/Mushoku/export -m HP2_all_vocals

# Vocal separation (only main vocal, no harmony)
python cli.py -i workspace/Mushoku/export -m HP5_only_main_vocal

# Dereverb with ONNX model
python cli.py -i workspace/Mushoku/export -m onnx_dereverb_By_FoxJoy

# Dereverb + DeEcho
python cli.py -i workspace/Mushoku/export -m VR-DeEchoAggressive

# Specify output format
python cli.py -i workspace/Mushoku/export -m bs_roformer -f wav

# Specify CPU
python cli.py -i workspace/Mushoku/export -m HP2_all_vocals -d cpu

# List all registered models
python cli.py --list-models
```

## Output Structure

```
input_dir/
    Sylphy/
        000001.wav
        000002.wav

Output:
input_dir_processed/
    vocal/
        Sylphy/
            000001.flac
            000002.flac
    instrument/
        Sylphy/
            000001.flac
            000002.flac
```

## Adding a New Processor

1. Implement a class inheriting from `engine.base.Processor`
2. Register with the `@register_model("name")` decorator
3. Import the module in `cli.py` to trigger registration
4. That's it — the registry handles model resolution automatically
