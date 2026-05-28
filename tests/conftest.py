import os
import sys

# Set required env vars before flows/ is imported (module-level reads)
os.environ.setdefault("CKAN_HOST",   "http://test-ckan")
os.environ.setdefault("LAKEFS_HOST", "http://test-lakefs")

# Make `flows/` importable without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "flows"))
