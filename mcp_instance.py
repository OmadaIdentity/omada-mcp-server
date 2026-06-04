# mcp_instance.py
"""Shared FastMCP instance used by all tool modules."""
from mcp.server.fastmcp.server import FastMCP

mcp = FastMCP("OmadaIdentityMCP")
