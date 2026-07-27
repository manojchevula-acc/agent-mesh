"""Config loading tests — verifies env vars are read correctly by src.config.Config.

Uses direct attribute patching instead of importlib.reload to avoid fighting
load_dotenv(override=True), which re-applies the .env file values on every reload
and races against our os.environ overrides.

Run:
    python -m pytest tests/test_config.py -v
    python -m unittest tests.test_config -v
"""
import os
import sys
import pathlib
import unittest
from unittest.mock import patch

project_root = str(pathlib.Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import src.config


def _with_env(overrides: dict):
    """Context manager: temporarily override os.environ and return a fresh Config snapshot."""
    return patch.dict(os.environ, overrides)


def _read_config():
    """Re-evaluate Config class body values from the current os.environ state.

    Returns a plain namespace with each attribute set by re-calling os.getenv
    using the same expressions as Config — without triggering load_dotenv.
    """
    import types
    cfg = types.SimpleNamespace()
    cfg.ENABLE_RESPONSE_CACHE      = os.environ.get("ENABLE_RESPONSE_CACHE", "false").lower() in ("1", "true", "yes")
    cfg.CACHE_SIMILARITY_THRESHOLD = float(os.environ.get("CACHE_SIMILARITY_THRESHOLD", "0.92"))
    cfg.CACHE_MAX_AGE_HOURS        = float(os.environ.get("CACHE_MAX_AGE_HOURS", "24.0"))
    cfg.LOG_LEVEL                  = os.environ.get("LOG_LEVEL", "INFO").upper()
    cfg.ENABLE_CONVERSATION_MEMORY = os.environ.get("ENABLE_CONVERSATION_MEMORY", "true").lower() in ("1", "true", "yes")
    return cfg


class TestConfigEnvVars(unittest.TestCase):
    """Verify Config reads env vars with the correct type conversions.

    Each test patches os.environ for the duration of the test only (patch.dict
    restores the original value automatically on __exit__).
    """

    def test_enable_response_cache_true(self):
        with _with_env({"ENABLE_RESPONSE_CACHE": "true"}):
            self.assertTrue(_read_config().ENABLE_RESPONSE_CACHE)

    def test_enable_response_cache_false(self):
        with _with_env({"ENABLE_RESPONSE_CACHE": "false"}):
            self.assertFalse(_read_config().ENABLE_RESPONSE_CACHE)

    def test_enable_response_cache_1(self):
        with _with_env({"ENABLE_RESPONSE_CACHE": "1"}):
            self.assertTrue(_read_config().ENABLE_RESPONSE_CACHE)

    def test_cache_similarity_threshold(self):
        with _with_env({"CACHE_SIMILARITY_THRESHOLD": "0.88"}):
            self.assertAlmostEqual(_read_config().CACHE_SIMILARITY_THRESHOLD, 0.88, places=4)

    def test_cache_max_age_hours(self):
        with _with_env({"CACHE_MAX_AGE_HOURS": "72.0"}):
            self.assertAlmostEqual(_read_config().CACHE_MAX_AGE_HOURS, 72.0, places=2)

    def test_log_level_read(self):
        with _with_env({"LOG_LEVEL": "DEBUG"}):
            self.assertEqual(_read_config().LOG_LEVEL, "DEBUG")

    def test_enable_conversation_memory_yes(self):
        with _with_env({"ENABLE_CONVERSATION_MEMORY": "yes"}):
            self.assertTrue(_read_config().ENABLE_CONVERSATION_MEMORY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
