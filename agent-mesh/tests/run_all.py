"""Convenience runner — discovers and runs all tests in this directory.

Usage:
    python tests/run_all.py

Individual suites:
    python -m pytest tests/ -v
    python -m unittest discover -s tests -v
"""
import sys
import pathlib
import unittest

project_root = str(pathlib.Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

tests_dir = str(pathlib.Path(__file__).parent)
loader = unittest.TestLoader()
suite = loader.discover(tests_dir, pattern="test_*.py")
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
