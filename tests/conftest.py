import os
import sys

# Set required env vars before flow/ is imported (module-level reads)
os.environ.setdefault("CKAN_HOST",   "http://test-ckan")
os.environ.setdefault("LAKEFS_HOST", "http://test-lakefs")
os.environ.setdefault("DOIP_HOST",   "http://test-doip")

# Ensure project root is in sys.path so `flow.*` imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
