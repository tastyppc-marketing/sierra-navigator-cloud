"""Pytest config for the sierra_mcp suite.

The MCP server now **fails closed** without WorkOS auth (see
``sierra_mcp.auth.build_auth``). Importing ``sierra_mcp.server`` evaluates
``build_auth()`` at module load, so we pin a hermetic local-dev auth state here
BEFORE any test module imports the server: no AuthKit domain + the explicit
opt-in to auth-disabled mode. conftest.py is imported before the test modules in
its directory, so this runs first.
"""
import os

os.environ.pop("AUTHKIT_DOMAIN", None)
os.environ.pop("MCP_PUBLIC_BASE_URL", None)
os.environ["SIERRA_MCP_ALLOW_NO_AUTH"] = "1"
