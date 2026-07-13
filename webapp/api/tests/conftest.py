import sys
from pathlib import Path

# Make the flat Lambda modules (runtime_client, store, handler) importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
