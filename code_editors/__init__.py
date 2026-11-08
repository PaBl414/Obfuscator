"""ObfuscatorEdu public package interface."""

from .core import PipelineConfig, process_file, process_folder, transform, transform_layers

__version__ = "2.0.0"

__all__ = [
    "PipelineConfig",
    "process_file",
    "process_folder",
    "transform",
    "transform_layers",
]
