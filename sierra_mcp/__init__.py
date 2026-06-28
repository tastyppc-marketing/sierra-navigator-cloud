"""Sierra Navigator Cloud — FastMCP server layer over the browserless
``sierra_core`` HTTP client.

This package exposes Sierra's admin backend to MCP clients (Claude Desktop,
ChatGPT, Claude Code, ...) behind WorkOS OAuth. Tier-1 is **read-only**:
every tool drives ``sierra_core`` through a session-refreshing runtime that
never performs a write.
"""

__version__ = "0.1.0"
