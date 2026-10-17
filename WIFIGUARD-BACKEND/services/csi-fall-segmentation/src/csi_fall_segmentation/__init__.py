"""Runtime and training support for the in-house temporal segmentation model."""

# Preserve public imports without loading PyTorch in the ONNX-only edge process.
def __getattr__(name):
    from importlib import import_module

    modules = {
        "SegmentationInferenceEngine": "engine",
        "SegmentationFeatureBuilder": "online",
        "ABTriggerConfig": "postprocess",
        "ABTriggerState": "postprocess",
    }
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(f".{modules[name]}", __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "ABTriggerConfig",
    "ABTriggerState",
    "SegmentationFeatureBuilder",
    "SegmentationInferenceEngine",
]
