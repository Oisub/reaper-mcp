"""Confirm every track actually produces signal, by metering during playback.

The .RPP readback proves the notes are written. It says nothing about whether
a synth responds to them - ReaSynDr's note mapping is not documented and not
exposed as parameters. So: start playback, sample track peaks across several
bridge calls, and report the maximum each track reached.
"""

import sys
import time

from reaper_mcp.server import eval_lua

START_BAR = int(sys.argv[1]) if len(sys.argv) > 1 else 13
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0

# Tracks that are legitimately absent in the measured section - a choir that
# only sings in the chorus is not a fault, and flagging it hides real ones.
ALLOW_SILENT = {"Reverb"}
for arg in sys.argv[3:]:
    if arg.startswith("--allow-silent="):
        ALLOW_SILENT |= {s.strip() for s in arg.split("=", 1)[1].split(",") if s.strip()}

PEAK_LUA = """
local out = {}
for ti = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, ti)
  local nm = select(2, reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false))
  local l = reaper.Track_GetPeakInfo(tr, 0)
  local r = reaper.Track_GetPeakInfo(tr, 1)
  out[#out+1] = { name = nm, peak = math.max(l, r) }
end
local m = reaper.GetMasterTrack(0)
out[#out+1] = {
  name = "MASTER",
  peak = math.max(reaper.Track_GetPeakInfo(m, 0), reaper.Track_GetPeakInfo(m, 1)),
}
return { play_state = reaper.GetPlayState(), pos = reaper.GetPlayPosition(), tracks = out }
"""


def db(v: float) -> str:
    if v <= 0.0000001:
        return "  -inf"
    import math

    return "%6.1f" % (20 * math.log10(v))


def main() -> int:
    # read the tempo from the project rather than assuming it
    t = eval_lua(code="return reaper.Master_GetTempo()", undo_label="", timeout=10)
    if t.get("status") != "ok":
        print("could not read tempo:", t.get("error"))
        return 1
    bpm = t.get("result") or 120.0
    bar_len = 4 * 60 / bpm
    start = (START_BAR - 1) * bar_len
    print("tempo %g BPM -> bar %d starts at %.1fs" % (bpm, START_BAR, start))

    r = eval_lua(
        code="reaper.SetEditCurPos(%f, true, true)\nreaper.OnPlayButton()\nreturn reaper.GetPlayState()"
        % start,
        undo_label="",
    )
    if r.get("status") != "ok":
        print("could not start playback:", r.get("error"))
        return 1

    peaks: dict[str, float] = {}
    samples = 0
    deadline = time.monotonic() + SECONDS
    while time.monotonic() < deadline:
        s = eval_lua(code=PEAK_LUA, undo_label="", timeout=10)
        if s.get("status") != "ok":
            print("meter read failed:", s.get("error"))
            break
        res = s.get("result") or {}
        samples += 1
        for t in res.get("tracks", []):
            peaks[t["name"]] = max(peaks.get(t["name"], 0.0), t.get("peak") or 0.0)
        if samples == 1:
            print("play_state=%s  pos=%.2fs" % (res.get("play_state"), res.get("pos") or 0))
        time.sleep(0.12)

    eval_lua(code="reaper.OnStopButton()", undo_label="")

    print()
    print("sampled %d times over ~%.1fs from bar %d" % (samples, SECONDS, START_BAR))
    print("%-8s %8s  %s" % ("track", "peak dB", "verdict"))
    silent, clipping = [], False
    for name, v in peaks.items():
        if name == "MASTER":
            clipping = v > 1.0
            verdict = "*** CLIPPING ***" if clipping else "headroom ok"
        else:
            quiet = v <= 0.0005
            if quiet:
                verdict = "silent (expected here)" if name in ALLOW_SILENT else "*** SILENT ***"
                if name not in ALLOW_SILENT:
                    silent.append(name)
            else:
                verdict = "SIGNAL"
        print("%-8s %8s  %s" % (name, db(v), verdict))
    print()
    problems = []
    if silent:
        problems.append("silent: " + ", ".join(silent))
    if clipping:
        problems.append("master is clipping")
    if problems:
        print("RESULT:", "; ".join(problems))
        return 2
    print("RESULT: every track sounding, master has headroom")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
