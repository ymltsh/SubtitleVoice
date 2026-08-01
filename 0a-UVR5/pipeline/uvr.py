"""
Pipeline orchestration for UVR5-based audio processing.

Provides a single entry point that selects the correct Processor and
runs it on an entire directory tree.
"""
import logging

from engine.registry import load_model

logger = logging.getLogger(__name__)


class UVRPipeline:
    """
    Top-level pipeline for UVR5 audio post-processing.

    Responsibilities:
      1. Accept input/output paths and model name
      2. Instantiate the correct Processor via the registry
      3. Ensure the Processor implements the Processor interface
      4. Call processor.process(input_dir, output_dir, format)
      5. Call processor.cleanup()
    """

    def __init__(self, model: str):
        self._model_name = model
        self._processor = load_model(model)
        if self._processor is None:
            raise ValueError("Unknown model: '{}'. Available: {}".format(
                model, _available_models(),
            ))
        logger.info("Loaded processor for model '%s' -> %s", model, type(self._processor).__name__)

    def run(self, input_dir: str, output_dir: str, format: str = "flac", keep_inst: bool = False) -> None:
        """Run the pipeline: process all files in input_dir."""
        logger.info("Processing: %s -> %s", input_dir, output_dir)
        logger.info("Model: %s, Format: %s, KeepInst: %s", self._model_name, format, keep_inst)
        try:
            self._processor.process(input_dir, output_dir, format=format, keep_inst=keep_inst)
        finally:
            self._processor.cleanup()
        logger.info("Done.")


def _available_models() -> list[str]:
    from engine.registry import _REGISTRY
    return sorted(_REGISTRY.keys())
