import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC  = ROOT / "src"

# Make all source packages importable without installation
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "analysis"))
sys.path.insert(0, str(SRC / "scraping"))
sys.path.insert(0, str(SRC / "viz"))
sys.path.insert(0, str(SRC / "deck-classification"))
