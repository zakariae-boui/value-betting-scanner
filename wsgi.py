# PythonAnywhere WSGI entry point.
# Paste the contents of this file into your PythonAnywhere WSGI config file.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dashboard import app as application  # noqa: F401
