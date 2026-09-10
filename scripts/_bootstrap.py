"""Make `import ianrelief` work when a script is run directly (python scripts/x.py) without install."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
