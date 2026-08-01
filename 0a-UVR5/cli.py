"""
VoiceSTCut-PostProcess CLI — batch audio separation & dereverberation.

Usage:
    python cli.py --input workspace/project/export --model bs_roformer
    python cli.py --input workspace/project/export --output export_processed --model VR-DeEchoAggressive --format wav
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.registry import list_models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="VoiceSTCut-PostProcess",
        description="Batch audio vocal separation & dereverberation using UVR5 models.",
    )
    p.add_argument(
        "--input", "-i",
        required=False,
        default=None,
        help="Input directory containing .wav files (searched recursively).",
    )
    p.add_argument(
        "--output", "-o",
        default=None,
        help="Output directory root. Defaults to '<input>_processed'.",
    )
    p.add_argument(
        "--model", "-m",
        default="bs_roformer",
        help="Model name. Available: " + ", ".join(list_models()),
    )
    p.add_argument(
        "--device", "-d",
        default=None,
        help="Device override (e.g. 'cuda:0', 'cpu'). Uses auto-detection by default.",
    )
    p.add_argument(
        "--format", "-f",
        default="flac",
        choices=["wav", "flac", "mp3", "m4a"],
        help="Output audio format (default: flac).",
    )
    p.add_argument(
        "--keep-inst",
        action="store_true",
        help="Also save instrument/others output (default: vocal only).",
    )
    p.add_argument(
        "--list-models",
        action="store_true",
        help="List all registered model names and exit.",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_models:
        import engine.vr       # noqa: F401
        import engine.mdx      # noqa: F401
        import engine.roformer # noqa: F401
        print("Registered models:")
        for m in list_models():
            print("  ", m)
        sys.exit(0)

    if not args.input:
        parser.error("--input/-i is required when not using --list-models")

    input_dir = os.path.abspath(args.input)
    if not os.path.isdir(input_dir):
        logger.error("Input directory not found: %s", input_dir)
        sys.exit(1)

    if args.output is None:
        output_dir = input_dir.rstrip(os.sep) + "_processed"
    else:
        output_dir = os.path.abspath(args.output)

    if args.device is not None:
        os.environ["DEVICE_OVERRIDE"] = args.device
        import config
        import torch
        config.INFER_DEVICE = torch.device(args.device)
        logger.info("Device override: %s", args.device)

    # Import processors to trigger @register_model decorators
    import engine.vr       # noqa: F401
    import engine.mdx      # noqa: F401
    import engine.roformer # noqa: F401

    from pipeline.uvr import UVRPipeline

    pipeline = UVRPipeline(model=args.model)
    pipeline.run(input_dir, output_dir, format=args.format, keep_inst=args.keep_inst)


if __name__ == "__main__":
    main()
