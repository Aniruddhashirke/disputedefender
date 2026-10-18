"""Lets you run the project from the folder ABOVE it:   python dispute-defender run cases/case_001_inr_delivered
(Python runs a folder that contains __main__.py.)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd.cli import main  # noqa: E402

sys.exit(main())
