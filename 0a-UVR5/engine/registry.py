"""
Model-to-processor registry with factory method.

All model routing logic lives here in one place — no scattered if/elif chains.
"""
import os
from typing import Optional

from config import WEIGHTS_ROOT, INFER_DEVICE, IS_HALF
from engine.base import Processor


_REGISTRY: dict[str, type[Processor]] = {}


def register_model(name: str):
    """Decorator to register a Processor subclass for a given model name."""

    def decorator(cls: type[Processor]) -> type[Processor]:
        _REGISTRY[name] = cls
        return cls

    return decorator


def list_models() -> list[str]:
    """Return all registered model names."""
    return list(_REGISTRY.keys())


def load_model(checkpoint_name: str) -> Optional["Processor"]:
    """
    Factory: given a checkpoint name, instantiate the correct Processor.

    Detection order:
      1. Direct match in registry
      2. "onnx_dereverb" substring match
      3. "roformer" substring match
      4. "DeEcho" substring match
      5. Fallback to default VR processor (HP2/HP5 family)
    """
    if checkpoint_name in _REGISTRY:
        return _REGISTRY[checkpoint_name](checkpoint_name)

    if "onnx_dereverb" in checkpoint_name.lower():
        return _REGISTRY["onnx_dereverb"](checkpoint_name)

    if "roformer" in checkpoint_name.lower():
        return _REGISTRY["bs_roformer"](checkpoint_name)

    if "DeEcho" in checkpoint_name:
        return _REGISTRY["deecho"](checkpoint_name)

    # default: HP2/HP3/HP5 VR models
    return _REGISTRY["vr"](checkpoint_name)


class ProcessorRegistry:
    """Explicit registry class (alternative to the module-level functions)."""

    def find(self, checkpoint_name: str) -> list[str]:
        """Scan weights/ directory for matching checkpoints."""
        matches = []
        for fname in os.listdir(WEIGHTS_ROOT):
            if fname.startswith(checkpoint_name) and (
                fname.endswith(".pth") or fname.endswith(".ckpt") or "onnx" in fname
            ):
                matches.append(fname.replace(".pth", "").replace(".ckpt", ""))
        if not matches:
            matches = [name for name in _REGISTRY if name.lower().startswith(checkpoint_name.lower())]
        return matches
