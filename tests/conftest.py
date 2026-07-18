"""Shared test setup: repo root on sys.path, deterministic auth secret."""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# api.auth reads the secret lazily; set one before any test imports it.
os.environ.setdefault("POOLSIDE_SESSION_SECRET", "test-secret-not-for-prod")

FIXTURES = Path(__file__).parent / "fixtures"
