"""Scenario runner package facade."""

from importlib import util
from pathlib import Path
import sys

_PACKAGE_NAME = __name__
_PACKAGE_DIR = Path(__file__).resolve().parent
_LEGACY_PATH = _PACKAGE_DIR.parent / "scenario_runner.py"

_existing = sys.modules.get(_PACKAGE_NAME)
if _existing is not None and getattr(_existing, "__file__", None) == str(_LEGACY_PATH):
    # Legacy module already loaded as package facade.
    _existing.__path__ = [_PACKAGE_DIR.as_posix()]
else:
    spec = util.spec_from_file_location(_PACKAGE_NAME, _LEGACY_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load legacy scenario_runner module from {_LEGACY_PATH}")
    legacy_module = util.module_from_spec(spec)
    legacy_module.__path__ = [_PACKAGE_DIR.as_posix()]
    sys.modules[_PACKAGE_NAME] = legacy_module
    spec.loader.exec_module(legacy_module)
