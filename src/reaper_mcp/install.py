"""Install the Lua bridge into REAPER's resource path.

REAPER runs ``<resource path>/Scripts/__startup.lua`` automatically at launch,
so the bridge needs no action registration, no extension and no GUI step.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from reaper_mcp.server import resource_path

MARKER = "REAPER MCP bridge"


BRIDGE_FILES = ("__startup.lua", "mcp_bridge.lua")


def bridge_dir() -> Path:
    packaged = Path(__file__).parent / "bridge"
    if (packaged / "__startup.lua").exists():
        return packaged
    # running from a source checkout
    repo = Path(__file__).resolve().parents[2] / "bridge"
    if (repo / "__startup.lua").exists():
        return repo
    raise FileNotFoundError("cannot locate the bridge/ directory")


def main() -> int:
    res = resource_path()
    if not res.exists():
        print("REAPER resource path not found: " + str(res))
        print("Launch REAPER once so it creates its config, then re-run this.")
        return 1

    scripts = res / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    src_dir = bridge_dir()
    target = scripts / "__startup.lua"

    if target.exists():
        existing = target.read_text(encoding="utf-8", errors="replace")
        if MARKER in existing:
            print("Replacing existing MCP bridge at " + str(target))
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = scripts / ("__startup.lua.backup-" + stamp)
            shutil.copy2(target, backup)
            print("You already had a __startup.lua that is not the MCP bridge.")
            print("Backed it up to: " + str(backup))
            print("Merge your own startup code back in by hand if you need it.")

    for name in BRIDGE_FILES:
        shutil.copy2(src_dir / name, scripts / name)
        print("Installed -> " + str(scripts / name))
    print("Errors (if any) are logged to " + str(res / "mcp-bridge.log"))
    print("Restart REAPER (or run the script once from Actions) to activate it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
