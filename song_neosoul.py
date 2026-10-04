"""A neo-soul song on real instruments: Keyscape Rhodes, Trilian bass, BFD drums.

F major, 84 BPM, 4/4 with a lazy 16th swing. 52 bars, about 2:29.

Runs against the open project, which must already have tracks
Keys (Keyscape) / Pad / Bass (Trilian) / Drums (BFD Player). Instrument sounds
are loaded with load_instrument_preset, the tool this song was the test case
for. Track FX are kept; only items, markers and the mix are rewritten.

The harmony: IVmaj9 - iii9 - ii9 - V9sus in the verse, a vi-led chorus with a
C7#9 turnaround, and a bVI - bVII bridge (Dbmaj9 - Eb9) - the borrowed chords
are what make it sound like neo-soul rather than pop.
"""

from __future__ import annotations

import random
from pathlib import Path

from reaper_mcp.server import eval_lua, load_instrument_preset, read_rpp

random.seed(7)

BPM = 84.0
SPB = 60.0 / BPM
BAR = 4 * SPB
SIX = SPB / 4                 # a 16th
SWING = 0.17 * SIX            # late offbeat 16ths - the "lazy" feel

# bass note (Trilian, E1=28 upward) and a rootless keys voicing (7-9-3-5 style)
C = {
    "Bbmaj9": dict(bass=34, keys=[57, 60, 62, 65]),
    "Am9":    dict(bass=33, keys=[55, 59, 60, 64]),
    "Gm9":    dict(bass=31, keys=[53, 57, 58, 62]),
    "C9sus":  dict(bass=36, keys=[58, 62, 65, 67]),
    "C13":    dict(bass=36, keys=[58, 62, 64, 69]),
    "C7#9":   dict(bass=36, keys=[52, 58, 63, 67]),
    "Dm9":    dict(bass=38, keys=[60, 64, 65, 69]),
    "Fmaj9":  dict(bass=41, keys=[57, 60, 64, 67]),
    "Dbmaj9": dict(bass=37, keys=[56, 60, 63, 65]),
    "Eb9":    dict(bass=39, keys=[55, 61, 65, 70]),
}

B = lambda n: [(n, 0.0, 4.0)]                        # noqa: E731
H = lambda a, b: [(a, 0.0, 2.0), (b, 2.0, 2.0)]      # noqa: E731

INTRO = [B("Bbmaj9"), B("Am9"), B("Gm9"), B("C9sus")]
VERSE = [B("Bbmaj9"), B("Am9"), B("Gm9"), H("C9sus", "C13")] * 2
CHORUS = [B("Dm9"), B("Bbmaj9"), B("Gm9"), B("C13"),
          B("Dm9"), B("Bbmaj9"), H("Gm9", "C7#9"), B("Fmaj9")]
BRIDGE = [B("Dbmaj9"), B("Eb9"), B("Dbmaj9"), H("Eb9", "C7#9")]
OUTRO = [B("Bbmaj9"), B("Am9"), B("Gm9"), B("Fmaj9")]

SONG = [
    ("Intro",    INTRO,  dict(drums=0, bass=False, pad=True)),
    ("Verse 1",  VERSE,  dict(drums=1, bass=True,  pad=False)),
    ("Chorus 1", CHORUS, dict(drums=2, bass=True,  pad=True)),
    ("Verse 2",  VERSE,  dict(drums=1, bass=True,  pad=True)),
    ("Chorus 2", CHORUS, dict(drums=2, bass=True,  pad=True)),
    ("Bridge",   BRIDGE, dict(drums=3, bass=True,  pad=True)),
    ("Chorus 3", CHORUS, dict(drums=2, bass=True,  pad=True, lift=True)),
    ("Outro",    OUTRO,  dict(drums=0, bass=True,  pad=True, last=True)),
]

# BFD Player default map (General MIDI layout)
KICK, SNARE, STICK, HAT_C, HAT_P, HAT_O, CRASH, RIDE = 36, 38, 37, 42, 44, 46, 49, 51


class Notes:
    def __init__(self) -> None:
        self.items: list[tuple[int, float, float, int]] = []

    def add(self, pitch: int, start: float, length: float, vel: int, jitter: float = 0.006) -> None:
        start = max(0.0, start + random.uniform(-jitter, jitter))
        vel = int(vel + random.uniform(-5, 5))
        if length > 0.01 and 0 <= pitch <= 127:
            self.items.append((pitch, start, length, max(1, min(127, vel))))

    def pack(self) -> str:
        return ";".join("%d,%.4f,%.4f,%d" % n for n in sorted(self.items, key=lambda x: x[1]))


def sixteenth(t0: float, k: int) -> float:
    """Start of 16th k in a bar, with the offbeat 16ths pushed late."""
    return t0 + k * SIX + (SWING if k % 2 else 0.0)


def build():
    tr = {k: Notes() for k in ("keys", "pad", "bass", "drums")}
    plan, sections, bar = [], [], 1
    for name, bars, flags in SONG:
        sections.append((name, bar, bar + len(bars)))
        for i, chords in enumerate(bars):
            plan.append((bar + i, chords, flags, i, len(bars)))
        bar += len(bars)
    total = bar - 1

    for idx, (barno, chords, flags, pos, n) in enumerate(plan):
        t0 = (barno - 1) * BAR
        last_bar = flags.get("last") and pos == n - 1
        nxt = plan[idx + 1][1][0][0] if idx + 1 < len(plan) else None

        for cname, off, beats in chords:
            ch = C[cname]
            cs, cl = t0 + off * SPB, beats * SPB

            # ---- Rhodes: intro and outro hold, elsewhere comp with pushes
            if flags["drums"] == 0 or last_bar:
                hold = cl * (2.0 if last_bar else 0.98)
                for j, p in enumerate(ch["keys"]):
                    tr["keys"].add(p, cs + j * 0.018, hold, 74 - j * 2)   # slight roll
                if not last_bar:
                    tr["keys"].add(ch["keys"][-1] + 5, cs + 2.5 * SPB, 0.5 * SPB, 58)
            else:
                lvl = 8 if flags.get("lift") else 0
                for p in ch["keys"]:
                    tr["keys"].add(p, cs, 1.3 * SPB, 80 + lvl)
                if beats >= 4:
                    for p in ch["keys"][1:]:
                        tr["keys"].add(p, sixteenth(cs, 6), 0.4 * SPB, 62 + lvl)
                        tr["keys"].add(p, sixteenth(cs, 11), 0.35 * SPB, 56 + lvl)
                else:
                    for p in ch["keys"][1:]:
                        tr["keys"].add(p, sixteenth(cs, 3), 0.3 * SPB, 60 + lvl)

            # ---- pad: the upper voices, held
            if flags["pad"]:
                for p in ch["keys"][1:]:
                    tr["pad"].add(p + 12, cs, cl * (2.0 if last_bar else 1.0), 60, jitter=0)

            # ---- bass
            if flags["bass"]:
                r = ch["bass"]
                if last_bar:
                    tr["bass"].add(r, cs, 4 * SPB, 96)
                elif beats >= 4:
                    tr["bass"].add(r, cs, 1.1 * SPB, 100)
                    tr["bass"].add(r, sixteenth(cs, 7), 0.2 * SPB, 58)         # ghost
                    tr["bass"].add(r + 7, sixteenth(cs, 8) - SIX * 0, 0.45 * SPB, 84)
                    tr["bass"].add(r + 12, sixteenth(cs, 10), 0.3 * SPB, 72)
                    if nxt:
                        tgt = C[nxt]["bass"]
                        step = 1 if tgt > r else -1
                        approach = tgt - step if tgt != r else r + 7
                        tr["bass"].add(approach, sixteenth(cs, 14), 0.4 * SPB, 80)
                else:
                    tr["bass"].add(r, cs, 0.9 * SPB, 96)
                    tr["bass"].add(r + 7, sixteenth(cs, 5), 0.4 * SPB, 78)

        # ---- drums
        lvl = flags["drums"]
        d = tr["drums"]
        if pos == 0 and lvl > 0:
            d.add(CRASH, t0, 0.5, 96)
        if last_bar:
            d.add(KICK, t0, 0.2, 100)
            d.add(CRASH, t0, 0.5, 92)
            continue
        if lvl == 0:
            if flags.get("last"):
                d.add(RIDE, t0, 0.2, 54)
                d.add(RIDE, t0 + 2 * SPB, 0.2, 48)
            continue

        cym = RIDE if lvl == 3 else HAT_C
        for k in range(0, 16, 2):
            d.add(cym, sixteenth(t0, k), 0.1, 78 if k % 4 == 0 else 60)
        if lvl >= 2:
            for k in (3, 7, 11, 15):                     # hat 16th pickups
                d.add(HAT_C, sixteenth(t0, k), 0.08, 40)
        # kick: 1, the "a" of 2, and of 3
        for k, v in ((0, 108), (7, 82), (10, 96)):
            d.add(KICK, sixteenth(t0, k), 0.12, v)
        if lvl >= 2:
            d.add(KICK, sixteenth(t0, 13), 0.12, 74)
        # backbeat laid back ~20 ms, the neo-soul lag
        back = 0.02
        snare = STICK if lvl == 1 and pos < 4 else SNARE
        d.add(snare, t0 + 1 * SPB + back, 0.12, 104 if snare == SNARE else 90)
        d.add(snare, t0 + 3 * SPB + back, 0.12, 106 if snare == SNARE else 92)
        for k in (6, 9, 15):                              # ghost notes
            d.add(SNARE, sixteenth(t0, k), 0.06, 30)
        if pos % 4 == 3:
            d.add(HAT_O, sixteenth(t0, 14), 0.2, 72)
        if pos == n - 1 and lvl >= 2 and not flags.get("last"):
            for j, k in enumerate((12, 13, 14, 15)):      # fill into the next section
                d.add(SNARE, sixteenth(t0, k), 0.07, 70 + 8 * j)

    return tr, sections, total


SETUP = r"""
local function db(x) return 10 ^ (x / 20) end
local function track(name)
  for i = 0, reaper.CountTracks(0) - 1 do
    local t = reaper.GetTrack(0, i)
    if select(2, reaper.GetSetMediaTrackInfo_String(t, "P_NAME", "", false)) == name then return t end
  end
end
reaper.SetCurrentBPM(0, __BPM__, false)
reaper.SoloAllTracks(0)
for i = 0, reaper.CountTracks(0) - 1 do
  local t = reaper.GetTrack(0, i)
  for j = reaper.CountTrackMediaItems(t) - 1, 0, -1 do
    reaper.DeleteTrackMediaItem(t, reaper.GetTrackMediaItem(t, j))
  end
end
local _, nm, nr = reaper.CountProjectMarkers(0)
for _ = 1, nm + nr do reaper.DeleteProjectMarkerByIndex(0, 0) end

-- pad: ReaSynth with slow envelope, then darken and widen it
local pad = track("Pad (ReaSynth)") or track("Pad")
reaper.GetSetMediaTrackInfo_String(pad, "P_NAME", "Pad", true)
while reaper.TrackFX_GetCount(pad) > 1 do reaper.TrackFX_Delete(pad, 1) end
-- param 11 (global detune) stays at 0.5 = 0 cents; anything else detunes the whole pad
local P = { [0] = 0.55, [1] = 0.35, [3] = 0.30, [4] = 0.55, [5] = 0.35, [11] = 0.5 }
for k, v in pairs(P) do reaper.TrackFX_SetParam(pad, 0, k, v) end
local lp = reaper.TrackFX_AddByName(pad, "JS: Resonant Lowpass Filter", false, -1)
local ch = reaper.TrackFX_AddByName(pad, "JS: Chorus (Stereo)", false, -1)

-- reverb bus
local rev = track("Reverb")
if not rev then
  reaper.InsertTrackAtIndex(reaper.CountTracks(0), true)
  rev = reaper.GetTrack(0, reaper.CountTracks(0) - 1)
  reaper.GetSetMediaTrackInfo_String(rev, "P_NAME", "Reverb", true)
  reaper.TrackFX_AddByName(rev, "ReaVerbate", false, -1)
end
for _, s in ipairs({ {"Keys", -12}, {"Pad", -8}, {"Drums", -20} }) do
  local src = track(s[1])
  while reaper.GetTrackNumSends(src, 0) > 0 do reaper.RemoveTrackSend(src, 0, 0) end
  local i = reaper.CreateTrackSend(src, rev)
  reaper.SetTrackSendInfo_Value(src, 0, i, "D_VOL", db(s[2]))
end

-- levels are measured, not guessed: the first pass had Chorus 3 at +2.6 dB on
-- the master, with bass and Rhodes riding over the drums (verify_audio.py)
for name, v in pairs({ Keys = -6, Pad = 0, Bass = -6, Drums = -1, Reverb = 0 }) do
  reaper.SetMediaTrackInfo_Value(track(name), "D_VOL", db(v))
end
reaper.SetMediaTrackInfo_Value(track("Keys"), "D_PAN", -0.15)
reaper.SetMediaTrackInfo_Value(reaper.GetMasterTrack(0), "D_VOL", db(-2))

local fx = {}
for _, n in ipairs({"Pad"}) do
  local t = track(n)
  for f = 0, reaper.TrackFX_GetCount(t) - 1 do
    local params = {}
    for p = 0, math.min(reaper.TrackFX_GetNumParams(t, f), 12) - 1 do
      local _, pn = reaper.TrackFX_GetParamName(t, f, p, "")
      local _, pv = reaper.TrackFX_GetFormattedParamValue(t, f, p, "")
      params[#params + 1] = pn .. "=" .. pv
    end
    fx[#fx + 1] = select(2, reaper.TrackFX_GetFXName(t, f, "")) .. ": " .. table.concat(params, ", ")
  end
end
return fx
"""

WRITE = r"""
local NAME, PACKED, LABEL, T1 = "__TRACK__", "__PACKED__", "__LABEL__", __T1__
local tr
for i = 0, reaper.CountTracks(0) - 1 do
  local t = reaper.GetTrack(0, i)
  if select(2, reaper.GetSetMediaTrackInfo_String(t, "P_NAME", "", false)) == NAME then tr = t end
end
local item = reaper.CreateNewMIDIItemInProj(tr, 0, T1, false)
local take = reaper.GetActiveTake(item)
reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", LABEL, true)
local n = 0
for e in PACKED:gmatch("[^;]+") do
  local p, s, l, v = e:match("^(%d+),([%d%.]+),([%d%.]+),(%d+)$")
  p, s, l, v = tonumber(p), tonumber(s), tonumber(l), tonumber(v)
  reaper.MIDI_InsertNote(take, false, false, reaper.MIDI_GetPPQPosFromProjTime(take, s),
                         reaper.MIDI_GetPPQPosFromProjTime(take, s + l), 0, p, v, true)
  n = n + 1
end
reaper.MIDI_Sort(take)
return { track = NAME, inserted = n, counted = select(2, reaper.MIDI_CountEvts(take)) }
"""

FINISH = r"""
local S, BAR, END = __SECTIONS__, __BAR__, __END__
for i, s in ipairs(S) do
  reaper.AddProjectMarker2(0, true, (s[2] - 1) * BAR, (s[3] - 1) * BAR, s[1], i, 0)
end
reaper.GetSet_LoopTimeRange(true, true, 0, END, false)
reaper.SetEditCurPos(0, true, false)
reaper.UpdateArrange()
return reaper.CountProjectMarkers(0)
"""


def main() -> int:
    for track, preset in (("Keys", "Rhodes - Classic Suitcase Lush"),
                          ("Bass", "Fingered - Clean Fender Jazz Bass"),
                          ("Drums", "Dry Pop")):
        r = load_instrument_preset(track, preset)
        print("preset %-6s %-36s verified=%s %s" % (track, preset, r.get("verified"), r.get("error", "")))
        if r.get("status") != "ok":
            return 1

    tracks, sections, total = build()
    end = total * BAR + 2 * BAR          # room for the last ring-out
    print("%d bars, %d:%02d at %g BPM" % (total, int(total * BAR // 60), int(total * BAR % 60), BPM))

    r = eval_lua(code=SETUP.replace("__BPM__", str(BPM)), undo_label="MCP: neo-soul - setup", timeout=60)
    if r.get("status") != "ok":
        print("setup failed:", r.get("error"))
        return 1
    for line in r["result"]:
        print("  " + line)

    for key, label in (("keys", "Rhodes"), ("pad", "Pad"), ("bass", "Bass"), ("drums", "Groove")):
        code = (WRITE.replace("__TRACK__", key.capitalize()).replace("__PACKED__", tracks[key].pack())
                .replace("__LABEL__", label).replace("__T1__", "%.4f" % end))
        w = eval_lua(code=code, undo_label="MCP: neo-soul - " + key, timeout=120)
        if w.get("status") != "ok":
            print(key, "FAILED:", w.get("error"))
            return 1
        print("  %-6s %4d notes (take: %s)" % (key, w["result"]["inserted"], w["result"]["counted"]))

    secs = "{" + ", ".join('{"%s", %d, %d}' % s for s in sections) + "}"
    m = eval_lua(code=FINISH.replace("__SECTIONS__", secs).replace("__BAR__", "%.6f" % BAR)
                 .replace("__END__", "%.4f" % end), undo_label="MCP: neo-soul - sections")
    print("markers:", m.get("result"))

    saved = read_rpp(save_first=True, save_as=str(Path(__file__).parent / "demo" / "neosoul.rpp"), max_chars=100)
    print("saved:", saved.get("path") or saved.get("error"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
