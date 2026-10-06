import sys
from pathlib import Path

# The server modules live one folder up and are run as plain scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
