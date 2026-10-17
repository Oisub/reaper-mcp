"""Live test for separate_stems: both load layouts, resume, and cancel.

Needs REAPER with the bridge and a configured separator (see separate.py).
Works on a synthetic 12 s clip in demo/, in throwaway project tabs, so the
user's projects are untouched. Usage: uv run python test_separate.py [model]
"""

import json
import math
import random
import struct
import sys
import time
import wave
from pathlib import Path

from reaper_mcp.server import (cancel_separation, eval_lua, list_separation_models, separate_stems,
                               separation_status)

DEMO = Path(__file__).parent / "demo" / "separate"
CLIP = DEMO / "clip.wav"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "bs_roformer_sw"
failures = []


def check(label, ok, detail=None):
    print(("PASS " if ok else "FAIL ") + label + ("" if ok or detail is None else "  " + json.dumps(detail, ensure_ascii=False)[:600]))
    if not ok:
        failures.append(label)


def make_clip():
    # bass line + kick + hats, enough for every stem model to produce output
    DEMO.mkdir(parents=True, exist_ok=True)
    sr, secs = 44100, 12
    rnd = random.Random(1)
    notes = [41.2, 41.2, 55.0, 49.0]
    frames = bytearray()
    for n in range(sr * secs):
        t = n / sr
        beat = t * 2  # 120 bpm
        f = notes[int(beat) % 4]
        bass = 0.35 * math.sin(2 * math.pi * f * t) * min(1.0, (beat % 1) * 20)
        kt = (beat % 1) / 2
        kick = 0.6 * math.sin(2 * math.pi * 55 * kt) * math.exp(-kt * 30)
        ht = ((beat * 2) % 1) / 4
        hat = 0.08 * rnd.uniform(-1, 1) * math.exp(-ht * 200)
        s = max(-1.0, min(1.0, bass + kick + hat))
        v = int(s * 32000)
        frames += struct.pack("<hh", v, v)
    with wave.open(str(CLIP), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def wait(job_id, limit=900):
    t = time.time()
    while time.time() - t < limit:
        s = separation_status(job_id)
        if s["state"] in ("finished", "finished with errors", "failed", "cancelled"):
            return s
        time.sleep(2)
    return separation_status(job_id)


make_clip()
for p in DEMO.rglob("stems.json"):
    p.unlink()

models = list_separation_models()
check("list_separation_models", models["status"] == "ok" and models["models"], models)
cat = list_separation_models(query="karaoke")
check("catalog search", bool(cat.get("catalog_matches")), cat.get("catalog_error"))

# 1. a file -> new project tab
r = separate_stems(source=str(CLIP), model=MODEL, output_dir=str(DEMO / "out"), audition="bass")
check("start file job", r["status"] == "ok" and r["load"] == "new_tab", r)
s = wait(r["job_id"])
item = s["items"][0]
check("file job finished", s["state"] == "finished" and item["state"] == "done", s)
loaded = item.get("loaded") or {}
check("loaded into a new tab and saved", str(loaded.get("project", "")).endswith(".rpp"), loaded)
tab = eval_lua(code=r"""
local t = {}
for i = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, i)
  t[#t + 1] = { name = select(2, reaper.GetTrackName(tr)), mute = reaper.GetMediaTrackInfo_Value(tr, "B_MUTE") == 1,
                items = reaper.CountTrackMediaItems(tr) }
end
return t
""", undo_label="")["result"]
check("mix muted, only the bass stem unmuted",
      tab[0]["mute"] and [x["name"] for x in tab if not x["mute"]] == ["Bass"] and all(x["items"] == 1 for x in tab), tab)

# 2. rerun -> skipped, no separation
t0 = time.time()
r = separate_stems(source=str(CLIP), model=MODEL, output_dir=str(DEMO / "out"), load="none")
s = wait(r["job_id"])
check("rerun skips the finished song", s["items"][0]["state"] == "skipped", s)
print("     rerun took %.0fs (model load only)" % (time.time() - t0))

# 3. a selected item -> stem folder under its track, aligned
setup = eval_lua(code=r"""
reaper.Main_OnCommand(40859, 0)
reaper.InsertTrackAtIndex(0, false)
reaper.InsertTrackAtIndex(1, false)
local tr = reaper.GetTrack(0, 0)
reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "Source", true)
reaper.GetSetMediaTrackInfo_String(reaper.GetTrack(0, 1), "P_NAME", "Below", true)
local item = reaper.AddMediaItemToTrack(tr)
local take = reaper.AddTakeToMediaItem(item)
reaper.SetMediaItemTake_Source(take, reaper.PCM_Source_CreateFromFile(__CLIP__))
reaper.SetMediaItemInfo_Value(item, "D_POSITION", 3)
reaper.SetMediaItemInfo_Value(item, "D_LENGTH", 6)
reaper.SetMediaItemTakeInfo_Value(take, "D_STARTOFFS", 2)
reaper.SelectAllMediaItems(0, false)
reaper.SetMediaItemSelected(item, true)
reaper.Main_SaveProjectEx(0, __RPP__, 8) -- titled, so cleanup can find it and close it without a prompt
return reaper.CountSelectedMediaItems(0)
""".replace("__CLIP__", json.dumps(str(CLIP))).replace("__RPP__", json.dumps(str(DEMO / "selected.rpp"))),
    undo_label="")
check("set up a trimmed, offset item", setup.get("result") == 1, setup)
r = separate_stems(source="selected_item", model=MODEL, output_dir=str(DEMO / "out"))
check("start selected-item job", r["status"] == "ok" and r["load"] == "tracks", r)
s = wait(r["job_id"])
check("selected-item job finished", s["state"] == "finished", s)
lay = eval_lua(code=r"""
local t = {}
for i = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, i)
  local it = reaper.GetTrackMediaItem(tr, 0)
  local tk = it and reaper.GetActiveTake(it)
  t[#t + 1] = { name = select(2, reaper.GetTrackName(tr)), depth = reaper.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH"),
                pos = it and reaper.GetMediaItemInfo_Value(it, "D_POSITION"), len = it and reaper.GetMediaItemInfo_Value(it, "D_LENGTH"),
                offs = tk and reaper.GetMediaItemTakeInfo_Value(tk, "D_STARTOFFS"), muted = it and reaper.GetMediaItemInfo_Value(it, "B_MUTE") == 1 }
end
return t
""", undo_label="")["result"]
stems = lay[2:-1]
check("folder sits between Source and Below",
      lay[0]["name"] == "Source" and lay[1]["name"].startswith("Stems: ") and lay[1]["depth"] == 1
      and lay[-1]["name"] == "Below" and stems and stems[-1]["depth"] == -1, lay)
check("stems aligned to the item (pos 3, len 6, offset 2)",
      all(abs(x["pos"] - 3) < 1e-6 and abs(x["len"] - 6) < 1e-6 and abs(x["offs"] - 2) < 1e-6 for x in stems), stems)
check("source item muted", lay[0]["muted"], lay[0])

# 4. cancel
for p in (DEMO / "out").rglob("stems.json"):
    p.unlink()
r = separate_stems(source=str(CLIP), model=MODEL, output_dir=str(DEMO / "out"), load="none")
time.sleep(3)
cancel_separation(r["job_id"])
s = wait(r["job_id"], 60)
check("cancel", s["state"] == "cancelled", s)

# close only the tabs this test created (their files live under demo/), saving first so no prompt opens
closed = eval_lua(code=r"""
local root, n = __ROOT__, 0
local function find()
  local i = 0
  while true do
    local p, fn = reaper.EnumProjects(i, "")
    if not p then return nil end
    if fn:gsub("\\", "/"):sub(1, #root) == root then return p end
    i = i + 1
  end
end
local p = find()
while p do
  reaper.SelectProjectInstance(p)
  reaper.Main_SaveProject(p, false)
  reaper.Main_OnCommand(40860, 0) -- close current project tab
  n = n + 1
  p = find()
end
return n
""".replace("__ROOT__", json.dumps(DEMO.as_posix())), undo_label="")
check("closed the test tabs", closed.get("result") == 2, closed)
print("\n%d failure(s)" % len(failures) + ("" if not failures else ": " + ", ".join(failures)))
sys.exit(1 if failures else 0)
