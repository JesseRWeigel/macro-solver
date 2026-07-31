"""Shared test plumbing. Deliberately thin: the checks themselves live in the
test files or in checker/, never in macrosolver/."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FOODS = os.path.join(ROOT, "data", "foods.json")
FIXTURES = os.path.join(ROOT, "fixtures")


def checker_module():
    """Import the independent checker without making it a package."""
    import importlib.util

    path = os.path.join(ROOT, "checker", "independent_check.py")
    spec = importlib.util.spec_from_file_location("independent_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)
