"""Reliable local entrypoint for the enterprise operations MCP server."""

from pathlib import Path
import sys

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from enterprise_ops_mcp.app.server import main  # noqa: E402


if __name__ == "__main__":
    main()
