"""ObfuscatorEdu public package interface."""

from .core import PipelineConfig, VerificationCase, process_file, process_folder, transform, transform_layers

__version__ = "2.0.0"

__all__ = [
    "PipelineConfig",
    "VerificationCase",
    "process_file",
    "process_folder",
    "transform",
    "transform_layers",
]
