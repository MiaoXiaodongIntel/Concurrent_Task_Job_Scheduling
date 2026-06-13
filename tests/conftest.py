"""Root-level pytest conftest: ensures the e2e helper directory is importable."""
import sys
from pathlib import Path

# Make tests/e2e/ importable so test modules can do `from conftest import ...`
_E2E_DIR = Path(__file__).resolve().parent / "e2e"
if str(_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_E2E_DIR))
