"""End-to-end demo: build a session from nothing, then verify it from the .RPP.

This is the loop that justified choosing REAPER: mutate through eval_lua, then
read the project back as text and assert on it. The agent checks its own work
instead of trusting that the commands landed.
"""

import json
import re
from pathlib import Path

from reaper_mcp.server import eval_lua, project_summary, read_rpp

DEMO_DIR = Path(__file__).parent / "demo"
DEMO_DIR.mkdir(exist_ok=True)
PROJECT = (DEMO_DIR / "mcp_demo.rpp").as_posix()

BUILD = """
-- start from a clean slate
while reaper.CountTracks(0) > 0 do
  reaper.DeleteTrack(reaper.GetTrack(0, 0))
end
local _, n_mk, n_rg = reaper.CountProjectMarkers(0)
for _ = 1, n_mk + n_rg do
  reaper.DeleteProjectMarkerByIndex(0, 0)
end

reaper.SetCurrentBPM(0, 96, false)

local function db2vol(d) return 10 ^ (d / 20) end

local specs = {
  { name = "Drums", vol = -3.0, fx = "ReaComp" },
  { name = "Bass",  vol = -5.0, fx = "ReaEQ"   },
  { name = "Keys",  vol = -8.0, fx = "ReaDelay" },
  { name = "Vox",   vol = -4.5, fx = "ReaComp" },
}

local created = {}
for i, spec in ipairs(specs) do
  reaper.InsertTrackAtIndex(i - 1, true)
  local tr = reaper.GetTrack(0, i - 1)
  reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", spec.name, true)
  reaper.SetMediaTrackInfo_Value(tr, "D_VOL", db2vol(spec.vol))
  local fx = reaper.TrackFX_AddByName(tr, spec.fx, false, -1)
  created[#created + 1] = { name = spec.name, fx = spec.fx, fx_index = fx }
end

-- a MIDI bassline on track 2, two bars at 96 BPM
local bass = reaper.GetTrack(0, 1)
local bar = 4 * 60 / 96
local item = reaper.CreateNewMIDIItemInProj(bass, 0, bar * 2, false)
local take = reaper.GetActiveTake(item)
reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", "Bassline", true)

local beat = 60 / 96
local pattern = { 40, 40, 43, 45, 40, 40, 47, 45 }
for i, pitch in ipairs(pattern) do
  local t0 = (i - 1) * beat
  local ppq0 = reaper.MIDI_GetPPQPosFromProjTime(take, t0)
  local ppq1 = reaper.MIDI_GetPPQPosFromProjTime(take, t0 + beat * 0.9)
  reaper.MIDI_InsertNote(take, false, false, ppq0, ppq1, 0, pitch, 96, true)
end
reaper.MIDI_Sort(take)

reaper.AddProjectMarker2(0, true, 0, bar * 2, "Intro", 1, 0)
reaper.AddProjectMarker2(0, true, bar * 2, bar * 6, "Verse", 2, 0)
reaper.AddProjectMarker2(0, false, bar * 6, 0, "drop here", 3, 0)

reaper.UpdateArrange()
print("built " .. #created .. " tracks, " .. #pattern .. " notes")

return {
  tracks = created,
  notes = #pattern,
  tempo = reaper.Master_GetTempo(),
}
"""


def head(label):
    print()
    print("=" * 64)
    print(label)
    print("=" * 64)


head("STEP 1 - build the session (one undo block)")
built = eval_lua(code=BUILD, undo_label="MCP: build demo session", timeout=60)
print(json.dumps(built, indent=2, ensure_ascii=False))
if built.get("status") != "ok":
    raise SystemExit("build failed")

head("STEP 2 - live state via the ReaScript API")
summary = project_summary()
res = summary.get("result", {})
print("tempo:", res.get("tempo"), "| tracks:", res.get("track_count"))
for tr in res.get("tracks", []):
    items = ", ".join(
        "%s(%d notes)" % (it["name"] or "item", it["notes"]) for it in tr["items"]
    )
    print(
        "  %-6s %6.1f dB  fx=%-10s %s"
        % (tr["name"], tr["volume_db"], ",".join(tr["fx"]) or "-", items)
    )
print("markers:", [(m["name"], m["region"]) for m in res.get("markers", [])])

head("STEP 3 - save and read the .RPP back as text")
rpp = read_rpp(save_as=PROJECT)
if rpp.get("status") != "ok":
    print(json.dumps(rpp, indent=2))
    raise SystemExit("readback failed")
text = rpp["rpp"]
print("path:", rpp["path"])
print("size:", rpp["total_chars"], "chars /", rpp["lines"], "lines")

head("STEP 4 - verify from the file, not from the API's word")
def token(v):
    """REAPER only quotes .RPP values that contain spaces, so accept either."""
    return r'(?:"%s"|%s)' % (re.escape(v), re.escape(v))


def has_name(v):
    return re.search(r"^\s*NAME %s\s*$" % token(v), text, re.M) is not None


def has_marker(v, is_region):
    return re.search(
        r"^\s*MARKER \d+ [\d.]+ %s %d" % (token(v), 1 if is_region else 0), text, re.M
    ) is not None


note_ons = sum(
    1
    for line in text.splitlines()
    if re.match(r"^\s*E \d+ 9[0-9a-f] ", line)
)

checks = [
    ("tempo 96 recorded", re.search(r"^\s*TEMPO 96 ", text, re.M) is not None),
    ("4 named tracks present", all(has_name(n) for n in ("Drums", "Bass", "Keys", "Vox"))),
    ("ReaComp instantiated", "ReaComp" in text),
    ("ReaEQ instantiated", "ReaEQ" in text),
    ("ReaDelay instantiated", "ReaDelay" in text),
    ("MIDI take named Bassline", has_name("Bassline")),
    ("MIDI source block present", "<SOURCE MIDI" in text),
    ("exactly 8 note-ons in the MIDI chunk (got %d)" % note_ons, note_ons == 8),
    ("region Intro", has_marker("Intro", True)),
    ("region Verse", has_marker("Verse", True)),
    ("marker 'drop here'", has_marker("drop here", False)),
]
ok = True
for label, passed in checks:
    print(("  PASS  " if passed else "  FAIL  ") + label)
    ok = ok and passed

head("STEP 5 - the MIDI chunk as it sits in the file")
inside = False
shown = 0
for line in text.splitlines():
    if "<SOURCE MIDI" in line:
        inside = True
    if inside and shown < 16:
        print("   " + line.strip())
        shown += 1

print()
print("RESULT:", "all checks passed" if ok else "SOME CHECKS FAILED")
