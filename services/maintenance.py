from __future__ import annotations

import gc
from dataclasses import dataclass

import psutil


@dataclass(slots=True)
class MemoryCleanupResult:
    before_mb: float
    after_mb: float
    collected_objects: int


def process_memory_mb() -> float:
    return psutil.Process().memory_info().rss / 1024 / 1024


def collect_garbage() -> MemoryCleanupResult:
    before = process_memory_mb()
    collected = gc.collect()
    after = process_memory_mb()
    return MemoryCleanupResult(before_mb=before, after_mb=after, collected_objects=collected)
