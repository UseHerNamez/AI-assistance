from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HardwareProfile:
    cpu_name: str
    gpu_names: list[str]
    allow_llm: bool
    reason: str
    has_dedicated_gpu: bool


_STRONG_CPU_PATTERNS = (
    r"\bi[79][- ]",
    r"\bcore\(tm\)\s+i[79][- ]",
    r"\bryzen\s+[79]\b",
    r"\bthreadripper\b",
    r"\bxeon\b",
)
_MID_CPU_PATTERNS = (
    r"\bi5[- ]",
    r"\bcore\(tm\)\s+i5[- ]",
    r"\bryzen\s+5\b",
    r"\bultra\s+[579]\b",
)
_WEAK_CPU_PATTERNS = (
    r"\bcore\s*2\b",
    r"\bcore\(tm\)\s*2\b",
    r"\bi3[- ]",
    r"\bcore\(tm\)\s+i3[- ]",
    r"\bryzen\s+3\b",
    r"\bceleron\b",
    r"\bpentium\b",
    r"\bathalon\b",
    r"\batom\b",
)
_DEDICATED_GPU_PATTERNS = (
    r"\bnvidia\b",
    r"\bgeforce\b",
    r"\bgtx\b",
    r"\brtx\b",
    r"\bquadro\b",
    r"\bradeon\b",
    r"\brx\s*\d",
    r"\bfirepro\b",
    r"\barc\b",
)
_INTEGRATED_GPU_PATTERNS = (
    r"\biris\b",
    r"\buhd\b",
    r"\bhd graphics\b",
    r"\bintegrated\b",
    r"\bvega\b",
)


def detect_hardware_profile() -> HardwareProfile:
    override = os.environ.get("JARVIS_LLM_MODE", "").strip().lower()
    cpu_name = _query_first("Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name")
    gpu_names = _query_lines("Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name")

    has_dedicated_gpu = any(_is_dedicated_gpu(gpu) for gpu in gpu_names)
    cpu_class = _classify_cpu(cpu_name)

    if override == "force":
        return HardwareProfile(cpu_name, gpu_names, True, "LLM forced by JARVIS_LLM_MODE", has_dedicated_gpu)
    if override == "parser":
        return HardwareProfile(cpu_name, gpu_names, False, "parser forced by JARVIS_LLM_MODE", has_dedicated_gpu)

    if has_dedicated_gpu:
        return HardwareProfile(cpu_name, gpu_names, True, "dedicated GPU detected", has_dedicated_gpu)

    if cpu_class == "strong":
        return HardwareProfile(cpu_name, gpu_names, True, "strong CPU detected", has_dedicated_gpu)

    if cpu_class == "mid":
        return HardwareProfile(cpu_name, gpu_names, True, "mid-range CPU detected; latency fallback remains active", has_dedicated_gpu)

    return HardwareProfile(cpu_name, gpu_names, False, "weak or unknown CPU and no dedicated GPU", has_dedicated_gpu)


def _classify_cpu(cpu_name: str) -> str:
    name = cpu_name.lower()
    if _matches_any(name, _WEAK_CPU_PATTERNS):
        return "weak"
    if _matches_any(name, _STRONG_CPU_PATTERNS):
        return "strong"
    if _matches_any(name, _MID_CPU_PATTERNS):
        return "mid"
    return "unknown"


def _is_dedicated_gpu(gpu_name: str) -> bool:
    name = gpu_name.lower()
    if not _matches_any(name, _DEDICATED_GPU_PATTERNS):
        return False
    if _matches_any(name, _INTEGRATED_GPU_PATTERNS) and "arc" not in name:
        return False
    return True


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)


def _query_first(command: str) -> str:
    lines = _query_lines(command)
    return lines[0] if lines else "Unknown CPU"


def _query_lines(command: str) -> list[str]:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

