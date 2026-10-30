"""Compatibility entry point for the single portable verifier."""
from pathlib import Path
import sys

# Support both direct execution and python -m scripts.verify_execution_portable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_portable import main, verify


if __name__ == "__main__":
    main()
