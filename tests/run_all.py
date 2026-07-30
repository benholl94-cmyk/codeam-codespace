#!/usr/bin/env python3
"""Run every test under tests/ in one go.

Stdlib-only: no pytest needed.

    python tests/run_all.py            # quiet
    python tests/run_all.py -v         # verbose
"""
import sys
import unittest


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir="tests", top_level_dir=".")
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
