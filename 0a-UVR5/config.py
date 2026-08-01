"""
VoiceSTCut-PostProcess configuration.

Determines the optimal inference device and precision at import time.
"""
import os
import re
import torch

WEIGHTS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dependencies")

def _get_device_dtype_sm(idx: int) -> tuple[torch.device, torch.dtype, float, float]:
    cpu = torch.device("cpu")
    if not torch.cuda.is_available():
        return cpu, torch.float32, 0.0, 0.0
    cuda = torch.device(f"cuda:{idx}")
    capability = torch.cuda.get_device_capability(idx)
    name = torch.cuda.get_device_name(idx)
    mem_bytes = torch.cuda.get_device_properties(idx).total_memory
    mem_gb = mem_bytes / (1024 ** 3) + 0.4
    major, minor = capability
    sm_version = major + minor / 10.0
    is_16_series = bool(re.search(r"16\d{2}", name)) and sm_version == 7.5
    if mem_gb < 4 or sm_version < 5.3:
        return cpu, torch.float32, 0.0, 0.0
    if sm_version == 6.1 or is_16_series:
        return cuda, torch.float32, sm_version, mem_gb
    if sm_version > 6.1:
        return cuda, torch.float16, sm_version, mem_gb
    return cpu, torch.float32, 0.0, 0.0


tmp = []
for i in range(max(torch.cuda.device_count(), 1)):
    tmp.append(_get_device_dtype_sm(i))

INFER_DEVICE = max(tmp, key=lambda x: (x[2], x[3]))[0]
IS_HALF = any(dtype == torch.float16 for _, dtype, _, _ in tmp)
