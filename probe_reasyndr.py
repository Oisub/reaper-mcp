"""Discover ReaSynDr's note mapping empirically.

ReaSynDr exposes no note-mapping parameters and GM drum notes produce silence,
so the mapping has to be measured.

Two things made the naive version lie:

1. Sampling from Python costs ~100ms per bridge round trip, coarser than the
   probe spacing, so most notes were missed. The sampler therefore runs inside
   REAPER as a defer loop (~30 ticks/sec) and parks results in ExtState.
2. A drum voice rings far longer than the note, and the peak meter decays
   slowly, so one hit smears across several slots and every slot looks like a
   trigger. So each slot records an ONSET level (just after the note) and a
   TAIL level (just before the next note); a slot only counts as a real
   trigger when its onset clearly rises above the previous slot's tail.
"""

import math
import time

from reaper_mcp.server import eval_lua

LOW, HIGH = 28, 52
STEP = 1.20        # long enough for a drum tail to decay
NOTE_LEN = 0.10
ONSET_WINDOW = 0.30   # measure the attack inside this much of the slot
TAIL_START = 0.90     # and the residue after this much
LATENCY = 0.05
RISE = 3.0            # onset must be this many times the previous tail

SETUP = """
local LOW, HIGH = %d, %d
local STEP, NOTE_LEN = %f, %f
local ONSET, TAIL, LAT = %f, %f, %f
local n = HIGH - LOW + 1
local total = n * STEP

reaper.SetExtState("mcp_probe", "done", "0", false)
reaper.SetExtState("mcp_probe", "result", "", false)

reaper.InsertTrackAtIndex(reaper.CountTracks(0), true)
local tr = reaper.GetTrack(0, reaper.CountTracks(0) - 1)
reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "__probe", true)
reaper.TrackFX_AddByName(tr, "ReaSynDr", false, -1000)

local item = reaper.CreateNewMIDIItemInProj(tr, 0, total + 1, false)
local take = reaper.GetActiveTake(item)
for i = 0, n - 1 do
  local p0 = reaper.MIDI_GetPPQPosFromProjTime(take, i * STEP)
  local p1 = reaper.MIDI_GetPPQPosFromProjTime(take, i * STEP + NOTE_LEN)
  reaper.MIDI_InsertNote(take, false, false, p0, p1, 0, LOW + i, 110, true)
end
reaper.MIDI_Sort(take)

local muted = {}
for ti = 0, reaper.CountTracks(0) - 2 do
  local t = reaper.GetTrack(0, ti)
  muted[#muted+1] = t
  reaper.SetMediaTrackInfo_Value(t, "B_MUTE", 1)
end

local onset, tail = {}, {}
reaper.SetEditCurPos(0, true, true)
reaper.OnPlayButton()

local function tick()
  local pos = reaper.GetPlayPosition() - LAT
  local st = reaper.GetPlayState()
  if pos >= 0 then
    local i = math.floor(pos / STEP)
    local off = pos - i * STEP
    if i >= 0 and i < n then
      local p = math.max(reaper.Track_GetPeakInfo(tr, 0), reaper.Track_GetPeakInfo(tr, 1))
      if off <= ONSET then
        if (onset[i] or 0) < p then onset[i] = p end
      elseif off >= TAIL then
        if (tail[i] or 0) < p then tail[i] = p end
      end
    end
  end
  if st == 0 or pos > total then
    reaper.OnStopButton()
    local parts = {}
    for i = 0, n - 1 do
      parts[#parts+1] = string.format("%%d:%%.6f:%%.6f", LOW + i, onset[i] or 0, tail[i] or 0)
    end
    reaper.SetExtState("mcp_probe", "result", table.concat(parts, ","), false)
    for _, t in ipairs(muted) do reaper.SetMediaTrackInfo_Value(t, "B_MUTE", 0) end
    reaper.DeleteTrack(tr)
    reaper.SetEditCurPos(0, true, true)
    reaper.UpdateArrange()
    reaper.SetExtState("mcp_probe", "done", "1", false)
    return
  end
  reaper.defer(tick)
end
tick()

return { notes = n, duration = total }
""" % (LOW, HIGH, STEP, NOTE_LEN, ONSET_WINDOW, TAIL_START, LATENCY)

COLLECT = """
return {
  done = reaper.GetExtState("mcp_probe", "done"),
  result = reaper.GetExtState("mcp_probe", "result"),
  tracks = reaper.CountTracks(0),
}
"""

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def db(v: float) -> float:
    return 20 * math.log10(v) if v > 1e-7 else -120.0


def name(pitch: int) -> str:
    return "%s%d" % (NAMES[pitch % 12], pitch // 12 - 1)


def main() -> int:
    r = eval_lua(code=SETUP, undo_label="MCP: ReaSynDr probe", timeout=60)
    if r.get("status") != "ok":
        print("setup failed:", r.get("error"))
        return 1
    info = r.get("result") or {}
    dur = info.get("duration", 0)
    print("probing %d-%d: %d slots x %.2fs = %.0fs, sampled inside REAPER"
          % (LOW, HIGH, info.get("notes", 0), STEP, dur))

    deadline = time.monotonic() + dur + 15
    raw = ""
    while time.monotonic() < deadline:
        time.sleep(1.5)
        c = eval_lua(code=COLLECT, undo_label="", timeout=10)
        if c.get("status") != "ok":
            print("collect failed:", c.get("error"))
            return 1
        res = c.get("result") or {}
        if res.get("done") == "1":
            raw = res.get("result") or ""
            print("done; %s tracks remain" % res.get("tracks"))
            break
    if not raw:
        print("sampler never reported done")
        return 2

    rows = []
    for part in raw.split(","):
        bits = part.split(":")
        rows.append((int(bits[0]), float(bits[1]), float(bits[2])))

    print()
    print("%-5s %-5s %9s %9s  %s" % ("pitch", "note", "onset", "tail", "trigger?"))
    triggers = []
    prev_tail = 0.0
    for pitch, on, tl in rows:
        is_trig = on > 0.004 and on > prev_tail * RISE
        if is_trig:
            triggers.append((pitch, on))
        print("%-5d %-5s %9.1f %9.1f  %s"
              % (pitch, name(pitch), db(on), db(tl), "<== TRIGGER" if is_trig else ""))
        prev_tail = tl

    print()
    if not triggers:
        print("no clean onset found")
        return 2
    print("ReaSynDr voices:")
    for pitch, on in triggers:
        print("  %d (%s)  %.1f dB" % (pitch, name(pitch), db(on)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
