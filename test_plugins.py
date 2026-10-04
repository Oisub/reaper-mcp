"""Smoke-test newly installed plugins in the live REAPER.

Load each plugin on a test track, play a chord, and compare the peak with the
plugin bypassed. A licensed/working effect passes audio; a muted one does not.
Instruments (--inst) are played directly and must make sound.

WARNING: deletes every track in the current project. Run it in a scratch tab.

    uv run python test_plugins.py "VST3: Pro-Q 4 (FabFilter)|VST3: Pro-C 3 (FabFilter)"
    uv run python test_plugins.py "VST3i: Twin 3 (FabFilter)" --inst

Watch for plugin dialogs while it runs: a licence prompt blocks the bridge.
"""
import math
import sys
import time

from reaper_mcp.server import eval_lua

PLUGINS = sys.argv[1].split("|")
INSTRUMENT = "--inst" in sys.argv

SETUP = r"""
reaper.OnStopButton()
for i = reaper.CountTracks(0) - 1, 0, -1 do reaper.DeleteTrack(reaper.GetTrack(0, i)) end
reaper.InsertTrackAtIndex(0, true)
local tr = reaper.GetTrack(0, 0)
reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "FX test", true)
if not __INST__ then reaper.TrackFX_AddByName(tr, "ReaSynth", false, -1) end
local item = reaper.CreateNewMIDIItemInProj(tr, 0, 8, false)
local take = reaper.GetActiveTake(item)
for b = 0, 7 do for _, p in ipairs({48, 55, 60, 64}) do
  reaper.MIDI_InsertNote(take, false, false, reaper.MIDI_GetPPQPosFromProjTime(take, b),
    reaper.MIDI_GetPPQPosFromProjTime(take, b + 0.9), 0, p, 100, true)
end end
reaper.MIDI_Sort(take)
reaper.GetSet_LoopTimeRange(true, true, 0, 8, false)
reaper.GetSetRepeat(1)
return true
"""

ADD = r"""
local tr = reaper.GetTrack(0, 0)
local fx = reaper.TrackFX_AddByName(tr, "__NAME__", false, -1)
if fx < 0 then return { ok = false } end
return { ok = true, fx = fx, params = reaper.TrackFX_GetNumParams(tr, fx),
         name = select(2, reaper.TrackFX_GetFXName(tr, fx, "")) }
"""

PEAK = r"""
local tr = reaper.GetTrack(0, 0)
return math.max(reaper.Track_GetPeakInfo(tr, 0), reaper.Track_GetPeakInfo(tr, 1))
"""


def run(code, t=30):
    r = eval_lua(code=code, undo_label="", timeout=t)
    if r.get("status") != "ok":
        raise RuntimeError(r.get("error"))
    return r.get("result")


def measure(seconds=2.5):
    run("reaper.SetEditCurPos(0.5, true, true) reaper.OnPlayButton() return 1")
    peak, t0 = 0.0, time.monotonic()
    time.sleep(0.3)
    while time.monotonic() - t0 < seconds:
        peak = max(peak, run(PEAK) or 0.0)
        time.sleep(0.1)
    run("reaper.OnStopButton() return 1")
    return peak


def db(v):
    return "-inf" if v <= 1e-7 else "%.1f dB" % (20 * math.log10(v))


for name in PLUGINS:
    run(SETUP.replace("__INST__", "true" if INSTRUMENT else "false"))
    added = run(ADD.replace("__NAME__", name), t=60)
    if not added.get("ok"):
        print("%-28s LOAD FAILED" % name)
        continue
    fx = added["fx"]
    time.sleep(1.0)
    on = measure()
    if INSTRUMENT:
        print("%-28s params=%-5d out=%-9s %s" % (name, added["params"], db(on), "SOUND" if on > 0.001 else "*** SILENT ***"))
        continue
    run("reaper.TrackFX_SetEnabled(reaper.GetTrack(0,0), %d, false) return 1" % fx)
    off = measure()
    ratio = on / off if off > 0 else 0
    verdict = "passes audio" if on > 0.001 and 0.05 < ratio < 20 else "*** CHECK ***"
    print("%-28s params=%-5d on=%-9s bypassed=%-9s %s" % (name, added["params"], db(on), db(off), verdict))
