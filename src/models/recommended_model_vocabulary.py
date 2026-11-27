"""The models a new machine downloads for this playbook (`i2k setup-models`), each a Hugging Face repository and the
files of it LM Studio loads: the weights and the vision projector. The names are the `models` entries that use them."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RecommendedModel", "RECOMMENDED_MODELS"]


@dataclass(frozen=True)
class RecommendedModel:
    """`repository` is `<publisher>/<name>` on Hugging Face, also the folder LM Studio lists it under; `spec` is the
    `models` entry's spec that LM Studio serves it as."""

    used_by: str                    # the `models` entries that call it
    repository: str
    files: tuple[str, ...]
    spec: str


RECOMMENDED_MODELS = (
    RecommendedModel("frame, document", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
                     ("Qwen3.6-35B-A3B-UD-Q3_K_M.gguf", "mmproj-F32.gguf"), "lmstudio:qwen3.6-35b-a3b-mtp"),
    RecommendedModel("lfm-vl-3b (the redundancy filter)", "LiquidAI/LFM2.5-VL-3B-GGUF",
                     ("LFM2.5-VL-3B-Q8_0.gguf", "mmproj-LFM2.5-VL-3B-F16.gguf"), "lmstudio:lfm2.5-vl-3b"),
)
