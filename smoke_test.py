"""Direct smoke test of the bridge, bypassing MCP transport."""

import json

from reaper_mcp.server import bridge_status, eval_lua, project_summary, read_rpp


def show(label, value):
    print("=" * 60)
    print(label)
    print("=" * 60)
    print(json.dumps(value, indent=2, ensure_ascii=False)[:2500])
    print()


show("1. bridge_status", bridge_status())

show(
    "2. eval_lua - round-trip types, output capture, JSON encoding",
    eval_lua(
        code="""
print("printed from inside REAPER")
print("tab\tseparated", 42, true)
return {
  version = reaper.GetAppVersion(),
  tracks = reaper.CountTracks(0),
  nested = {1, 2, 3},
  tricky_string = 'quotes " backslash \\\\ newline \\n tab \\t done',
  float = 1/3,
}
""",
        undo_label="",
    ),
)

show(
    "3. eval_lua - error handling (deliberate runtime error)",
    eval_lua(code="local x = nil; return x.y", undo_label=""),
)

show(
    "4. eval_lua - error handling (deliberate syntax error)",
    eval_lua(code="this is not lua", undo_label=""),
)

show("5. project_summary (empty project)", project_summary())

show("6. read_rpp on an unsaved project (expected: actionable error)", read_rpp())
