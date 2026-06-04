"""Omada Identity MCP Server — entry point."""
import os
from dotenv import load_dotenv

load_dotenv()

from logging_config import setup_logging
setup_logging()

from mcp_instance import mcp

# Import tool modules — side effects register all @mcp.tool() decorators
import tools.query        # noqa: F401
import tools.access_requests  # noqa: F401
import tools.approvals    # noqa: F401
import tools.assignments  # noqa: F401
import tools.admin        # noqa: F401

# Register prompts and completions
from prompts import register_prompts
from completions import register_completions
register_prompts(mcp)
register_completions(mcp)

if __name__ == "__main__":
    mcp.run()
