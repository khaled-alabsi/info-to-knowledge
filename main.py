#!/usr/bin/env python3
# <!-- sync: seed/main v1 -->
"""Entry point.

Adds `src/` to the path and hands off. Everything real lives in `src/`. This file exists so the playbook can
be run as `python3 main.py ...` without being installed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__" and sys.argv[1:2] == ["mcp"]:
    # `i2k mcp` starts before the CLI's other commands load the pipeline: an MCP client waits only so long for its
    # first answer (DEFECTS 475)
    from services import mcp_server_service  # noqa: E402

    sys.exit(mcp_server_service.run_from_command_line(sys.argv[2:]))

# OpenCV and PyAV each bundle their own libavdevice; macOS prints two lines about its duplicate classes when the second
# loads, on every run's console. Both are loaded here once with those lines kept off the console; every later import
# reuses them
from utils import native_stderr_utils  # noqa: E402

with native_stderr_utils.hide_native_stderr():
    import av  # noqa: E402,F401
    import cv2  # noqa: E402,F401

import cli  # noqa: E402

if __name__ == "__main__":
    sys.exit(cli.main())
