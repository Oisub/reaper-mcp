"""Regression test for the read_rpp save_as path.

Main_SaveProjectEx with options=0 writes the file but leaves the project
untitled, which would make the next save_first open a modal Save-As dialog and
hang the bridge. options=8 adopts the filename. This exercises that path in a
throwaway project tab so the user's own project is untouched.
"""

import json
from pathlib import Path

from reaper_mcp.server import eval_lua, read_rpp

TMP = (Path(__file__).parent / "demo" / "adopt_test.rpp").as_posix()
Path(TMP).unlink(missing_ok=True)


def step(label, value):
    print("-" * 60)
    print(label)
    print(json.dumps(value, indent=2, ensure_ascii=False)[:900])


# new project tab, so the live session is not disturbed
step(
    "open a throwaway project tab and put one track in it",
    eval_lua(
        code="""
reaper.Main_OnCommand(40859, 0)  -- New project tab
reaper.InsertTrackAtIndex(0, true)
local tr = reaper.GetTrack(0, 0)
reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "AdoptProbe", true)
return {
  untitled = select(2, reaper.EnumProjects(-1, "")) == "",
  tracks = reaper.CountTracks(0),
}
""",
        undo_label="",
    ),
)

r = read_rpp(save_as=TMP)
step("read_rpp(save_as=...) on the untitled project", {k: v for k, v in r.items() if k != "rpp"})

after = eval_lua(
    code='return {path = select(2, reaper.EnumProjects(-1, "")), '
    "name = reaper.GetProjectName(0, \"\"), dirty = reaper.IsProjectDirty(0)}",
    undo_label="",
)
step("did REAPER adopt the filename?", after)

adopted = (after.get("result") or {}).get("path", "")

# the real point: a second read_rpp with save_first must NOT hang
r2 = read_rpp(save_first=True)
step(
    "second read_rpp(save_first=True) - must not open a dialog",
    {k: v for k, v in r2.items() if k != "rpp"},
)

step(
    "close the throwaway tab",
    eval_lua(code="reaper.Main_OnCommand(40860, 0)\nreturn reaper.CountTracks(0)", undo_label=""),
)

print()
print("=" * 60)
checks = [
    ("filename adopted", adopted.endswith("adopt_test.rpp")),
    ("project no longer dirty", (after.get("result") or {}).get("dirty") == 0),
    ("first read_rpp ok", r.get("status") == "ok"),
    ("second read_rpp ok (no modal hang)", r2.get("status") == "ok"),
    ("probe track present in the file", "AdoptProbe" in (r.get("rpp") or "")),
]
for label, passed in checks:
    print(("  PASS  " if passed else "  FAIL  ") + label)
print("RESULT:", "all passed" if all(p for _, p in checks) else "FAILED")
