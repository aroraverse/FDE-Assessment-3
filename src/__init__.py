"""Shared package initialization for the assessment starter pack.

Local dotenv files are loaded automatically (without overriding variables already
set by the operating system). Load order:
  1. ``.env``        - shared, non-secret defaults (safe to commit via .env.example)
  2. ``.env.local``  - developer-local secrets (e.g. GROQ_API_KEY); gitignored

``.env.local`` takes precedence over ``.env`` but neither overrides variables
already present in the OS environment. This keeps ``python run_local.py``, the
public eval runner, and direct module usage consistent and secret-safe.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Snapshot which variables the OS already provides; these must never be overwritten.
_os_preset = set(os.environ)

# Base defaults first; do not override real OS environment variables.
load_dotenv(ROOT / ".env", override=False)

# Local overrides (secrets) next: apply .env.local, overriding values that came
# from .env, but NOT variables that were already set in the OS environment.
_local = ROOT / ".env.local"
if _local.is_file():
    for _key, _val in dotenv_values(_local).items():
        if _val is None:
            continue
        if _key in _os_preset:
            continue  # respect OS-provided value
        os.environ[_key] = _val
