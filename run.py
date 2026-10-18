r"""Run from any folder:   python path/to/dispute-defender/run.py run cases/case_001_inr_delivered --backend ollama"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
