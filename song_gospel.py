"""A complete R&B / gospel song, built in REAPER from generated samples.

Eb major, 72 BPM, 12/8 feel (triplet eighths). 60 bars, about 3:20.

The harmony is the point: ninths, thirteenths and sus voicings, a secondary
dominant into the ii, a bVII (Db9) as the surprise, and a 6-2-5-1 vamp. Keys
voicings are written by hand rather than stacked from intervals, because the
voicing is what makes it sound like gospel rather than like a chord chart.

Notes are shipped to the bridge as compact "pitch,start,len,vel;" strings, one
call per track, so no single request carries 3000 table literals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from reaper_mcp.server import eval_lua, read_rpp

sys.path.insert(0, str(Path(__file__).parent))
import gen_instruments as GI  # noqa: E402

BPM = 72.0
SPB = 60.0 / BPM          # 0.8333 s per beat
BAR = 4 * SPB             # 3.3333 s per bar
TRIP = SPB / 3            # triplet eighth - the 12/8 grid

SAMPLES = Path(__file__).parent / "demo" / "samples"

# --------------------------------------------------------------- the harmony
# bass: one note. keys: hand voicing. choir: close triad / stack.

C = {
    "Ebmaj9":  dict(bass=39, keys=[51, 58, 62, 65, 67], choir=[63, 67, 70]),
    "Cm9":     dict(bass=36, keys=[48, 58, 62, 63, 67], choir=[62, 63, 67]),
    "Fm9":     dict(bass=41, keys=[53, 63, 67, 68, 72], choir=[63, 68, 72]),
    "Bb13":    dict(bass=46, keys=[46, 56, 60, 62, 67], choir=[62, 65, 68]),
    "Bb7sus":  dict(bass=46, keys=[46, 56, 60, 63, 65], choir=[63, 65, 68]),
    "Abmaj9":  dict(bass=44, keys=[56, 63, 67, 70, 72], choir=[63, 67, 72]),
    "Gm7":     dict(bass=43, keys=[55, 62, 65, 70],     choir=[62, 65, 70]),
    "C7#9":    dict(bass=36, keys=[48, 58, 64, 75],     choir=[64, 70, 75]),
    "Db9":     dict(bass=37, keys=[49, 59, 63, 65, 68], choir=[63, 65, 68]),
    "Ab13":    dict(bass=44, keys=[56, 66, 70, 72, 77], choir=[66, 70, 72]),
    "Eb/G":    dict(bass=43, keys=[55, 63, 67, 70, 74], choir=[63, 67, 70]),
    "Edim7":   dict(bass=40, keys=[52, 58, 61, 67],     choir=[58, 61, 67]),
}

# Each bar is a list of (chord, beat offset, beats)
B = lambda name: [(name, 0.0, 4.0)]                      # noqa: E731
H = lambda a, b: [(a, 0.0, 2.0), (b, 2.0, 2.0)]          # noqa: E731

INTRO = [B("Ebmaj9"), B("Abmaj9"), B("Fm9"), B("Bb13")]

VERSE = [
    B("Ebmaj9"), B("Cm9"), B("Fm9"), B("Bb13"),
    B("Ebmaj9"), H("Gm7", "C7#9"), B("Fm9"), B("Bb13"),
]

PRE = [B("Abmaj9"), B("Gm7"), B("Fm9"), H("Bb7sus", "Bb13")]

CHORUS = [
    B("Abmaj9"), B("Eb/G"), B("Fm9"), B("Bb13"),
    B("Ebmaj9"), B("Cm9"), H("Fm9", "Bb13"), B("Ebmaj9"),
]

# 6-2-5-1 vamp, with the bVII dropped in at bar 7
VAMP = [
    B("Cm9"), B("Fm9"), B("Bb13"), B("Ebmaj9"),
    B("Cm9"), H("Fm9", "Edim7"), B("Db9"), B("Bb13"),
]

OUTRO = [B("Abmaj9"), B("Fm9"), H("Bb13", "Ab13"), B("Ebmaj9")]

# name, bars, arrangement flags
SONG = [
    ("Intro",        INTRO,  dict(drums=0, bass=False, choir=False, ride=False)),
    ("Verse 1",      VERSE,  dict(drums=1, bass=True,  choir=False, ride=False)),
    ("Pre-chorus",   PRE,    dict(drums=2, bass=True,  choir=False, ride=False)),
    ("Chorus 1",     CHORUS, dict(drums=3, bass=True,  choir=True,  ride=True)),
    ("Verse 2",      VERSE,  dict(drums=1, bass=True,  choir=False, ride=False)),
    ("Chorus 2",     CHORUS, dict(drums=3, bass=True,  choir=True,  ride=True)),
    ("Vamp",         VAMP,   dict(drums=4, bass=True,  choir=True,  ride=True)),
    ("Final chorus", CHORUS, dict(drums=4, bass=True,  choir=True,  ride=True, octave=True)),
    ("Outro",        OUTRO,  dict(drums=0, bass=True,  choir=True,  ride=False)),
]

# drum note map - ours, not General MIDI's
KICK, SNARE, HAT_C, HAT_O, CLAP, TAMB, RIDE = 36, 38, 42, 46, 39, 54, 51


# ------------------------------------------------------------- note building


class Notes:
    def __init__(self) -> None:
        self.items: list[tuple[int, float, float, int]] = []

    def add(self, pitch: int, start: float, length: float, vel: int) -> None:
        if length <= 0.01 or not (0 <= pitch <= 127):
            return
        self.items.append((pitch, start, max(0.02, length), max(1, min(127, vel))))

    def pack(self) -> str:
        return ";".join(
            "%d,%.4f,%.4f,%d" % (p, s, l, v) for p, s, l, v in sorted(self.items, key=lambda x: x[1])
        )

    def __len__(self) -> int:
        return len(self.items)


def build_song() -> dict[str, Notes]:
    tracks = {k: Notes() for k in ("drums", "bass", "ep", "organ", "choir")}
    sections = []

    bar = 1
    # flatten the arrangement, remembering where each section starts
    plan = []
    for name, bars, flags in SONG:
        sections.append((name, bar, bar + len(bars)))
        for i, chords in enumerate(bars):
            plan.append((bar + i, chords, flags, i, len(bars)))
        bar += len(bars)
    total_bars = bar - 1

    for idx, (barno, chords, flags, pos_in_sec, sec_len) in enumerate(plan):
        t0 = (barno - 1) * BAR
        nxt = plan[idx + 1] if idx + 1 < len(plan) else None
        next_bass = C[nxt[1][0][0]]["bass"] if nxt else None

        # ---- keys (Rhodes comping) and organ (sustained bed)
        for cname, off, beats in chords:
            ch = C[cname]
            cs = t0 + off * SPB
            cl = beats * SPB

            tracks["organ"].add_many = None  # noqa: B010  (kept simple below)
            for p in ch["keys"]:
                tracks["organ"].add(p, cs, cl * 0.98, 72)

            # Rhodes: full voicing on the downbeat, then light upper stabs on
            # the last triplet of each beat - the gospel comping push
            for p in ch["keys"]:
                tracks["ep"].add(p, cs, min(cl, 1.6 * SPB), 92)
            tops = ch["keys"][-3:]
            stabs = [(2, 70), (5, 78), (8, 72), (11, 66)]
            for k, vel in stabs:
                st = cs + k * TRIP
                if st < cs + cl - TRIP * 0.5:
                    for p in tops:
                        tracks["ep"].add(p, st, TRIP * 1.4, vel)

            # ---- choir
            if flags.get("choir"):
                voices = list(ch["choir"])
                if flags.get("octave"):
                    voices.append(voices[-1] + 12)
                for p in voices:
                    tracks["choir"].add(p, cs, cl * 0.97, 74)

            # ---- bass
            if flags.get("bass"):
                r = ch["bass"]
                if beats >= 4.0:
                    tracks["bass"].add(r, cs, TRIP * 2.6, 104)
                    tracks["bass"].add(r + 12, cs + 4 * TRIP, TRIP * 1.4, 74)
                    tracks["bass"].add(r, cs + 6 * TRIP, TRIP * 2.4, 96)
                    if next_bass is not None:
                        step = -2 if next_bass < r else 2
                        tracks["bass"].add(next_bass + step, cs + 10 * TRIP, TRIP * 1.6, 82)
                else:
                    tracks["bass"].add(r, cs, TRIP * 2.4, 100)
                    tracks["bass"].add(r, cs + 3 * TRIP, TRIP * 2.0, 88)

        # ---- drums
        lvl = flags.get("drums", 0)
        if lvl > 0:
            d = tracks["drums"]
            K = lambda k: t0 + k * TRIP  # noqa: E731
            use_ride = flags.get("ride")

            # the 12/8 bed
            cym = RIDE if use_ride else HAT_C
            for k in range(12):
                accent = k % 3 == 0
                d.add(cym, K(k), TRIP * 0.8, (66 if accent else 40) + (6 if lvl >= 3 else 0))

            d.add(KICK, K(0), 0.14, 106)
            d.add(KICK, K(6), 0.14, 98)
            d.add(SNARE, K(3), 0.12, 86 if lvl == 1 else 102)
            d.add(SNARE, K(9), 0.12, 90 if lvl == 1 else 104)
            # ghost notes are what makes the backbeat breathe
            for k in (2, 5, 8, 11):
                d.add(SNARE, K(k), 0.08, 34 if lvl == 1 else 42)

            if lvl >= 2:
                d.add(KICK, K(8), 0.12, 88)
                for k in (1, 2, 4, 5, 7, 8, 10, 11):
                    d.add(TAMB, K(k), 0.12, 58 if lvl == 2 else 66)
            if lvl >= 3:
                d.add(CLAP, K(3), 0.12, 96)
                d.add(CLAP, K(9), 0.12, 98)
            if lvl >= 4:
                d.add(KICK, K(4), 0.12, 82)
                d.add(KICK, K(10), 0.12, 86)
            # phrase ends: open hat and a short snare pickup
            if pos_in_sec % 4 == 3:
                d.add(HAT_O, K(11), 0.25, 78)
                if lvl >= 3:
                    for j, k in enumerate((9, 10, 11)):
                        d.add(SNARE, K(k), 0.08, 70 + j * 10)

    return tracks, sections, total_bars


# ------------------------------------------------------------ REAPER plumbing

TRACK_SETUP = r"""
local SPEC = __SPEC__
local function db(d) return 10 ^ (d / 20) end
local function pitchRaw(semi) return 0.5 + semi / 160.0 end

reaper.SetCurrentBPM(0, __BPM__, false)
local _, nm, nr = reaper.CountProjectMarkers(0)
for _ = 1, nm + nr do reaper.DeleteProjectMarkerByIndex(0, 0) end
while reaper.CountTracks(0) > 0 do reaper.DeleteTrack(reaper.GetTrack(0, 0)) end

local made = {}
for i, s in ipairs(SPEC) do
  reaper.InsertTrackAtIndex(i - 1, true)
  local tr = reaper.GetTrack(0, i - 1)
  reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", s.name, true)
  reaper.SetMediaTrackInfo_Value(tr, "D_VOL", db(s.vol))
  reaper.SetMediaTrackInfo_Value(tr, "D_PAN", s.pan)
  made[s.name] = tr

  for _, z in ipairs(s.zones or {}) do
    local fx = reaper.TrackFX_AddByName(tr, "ReaSamplOmatic5000", false, -1)
    reaper.TrackFX_SetNamedConfigParm(tr, fx, "FILE0", z.file)
    reaper.TrackFX_SetNamedConfigParm(tr, fx, "DONE", "")
    reaper.TrackFX_SetParam(tr, fx, 3, z.lo / 127)
    reaper.TrackFX_SetParam(tr, fx, 4, z.hi / 127)
    reaper.TrackFX_SetParam(tr, fx, 5, pitchRaw(z.lo - z.base))
    reaper.TrackFX_SetParam(tr, fx, 6, pitchRaw(z.hi - z.base))
    reaper.TrackFX_SetParam(tr, fx, 11, z.hold and 1 or 0)
    reaper.TrackFX_SetParam(tr, fx, 9, 0)
    reaper.TrackFX_SetParam(tr, fx, 0, db(z.vol or 0))
    reaper.TrackFX_SetNamedConfigParm(tr, fx, "renamed_name", z.label)
  end
  for _, f in ipairs(s.fx or {}) do
    reaper.TrackFX_AddByName(tr, f, false, -1)
  end
end

-- reverb bus and sends
local rev = made["Reverb"]
for name, amount in pairs(__SENDS__) do
  local src = made[name]
  if src and rev then
    local idx = reaper.CreateTrackSend(src, rev)
    reaper.SetTrackSendInfo_Value(src, 0, idx, "D_VOL", db(amount))
  end
end
reaper.SetMediaTrackInfo_Value(reaper.GetMasterTrack(0), "D_VOL", db(-4.0))

local out = {}
for ti = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, ti)
  local chain = {}
  for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
    chain[#chain+1] = (select(2, reaper.TrackFX_GetFXName(tr, f, "")):gsub(" %(Cockos%).*", ""):gsub("^VSTi?: ", ""))
  end
  out[#out+1] = select(2, reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false))
             .. " [" .. table.concat(chain, ", ") .. "]"
end
return out
"""

WRITE_NOTES = r"""
local TRACK, PACKED, LABEL, T0, T1 = "__TRACK__", "__PACKED__", "__LABEL__", __T0__, __T1__
local tr
for ti = 0, reaper.CountTracks(0) - 1 do
  local t = reaper.GetTrack(0, ti)
  if select(2, reaper.GetSetMediaTrackInfo_String(t, "P_NAME", "", false)) == TRACK then tr = t end
end
if not tr then return { error = "no track " .. TRACK } end

local item = reaper.CreateNewMIDIItemInProj(tr, T0, T1, false)
local take = reaper.GetActiveTake(item)
reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", LABEL, true)

local n = 0
for entry in PACKED:gmatch("[^;]+") do
  local p, s, l, v = entry:match("^(%-?%d+),([%d%.]+),([%d%.]+),(%d+)$")
  if p then
    p, s, l, v = tonumber(p), tonumber(s), tonumber(l), tonumber(v)
    local q0 = reaper.MIDI_GetPPQPosFromProjTime(take, s)
    local q1 = reaper.MIDI_GetPPQPosFromProjTime(take, s + l)
    reaper.MIDI_InsertNote(take, false, false, q0, q1, 0, p, v, true)
    n = n + 1
  end
end
reaper.MIDI_Sort(take)
return { track = TRACK, inserted = n, counted = select(2, reaper.MIDI_CountEvts(take)) }
"""

MARKERS = r"""
local S = __SECTIONS__
local BAR = __BAR__
local _, nm, nr = reaper.CountProjectMarkers(0)
for _ = 1, nm + nr do reaper.DeleteProjectMarkerByIndex(0, 0) end
for i, s in ipairs(S) do
  reaper.AddProjectMarker2(0, true, (s[2] - 1) * BAR, (s[3] - 1) * BAR, s[1], i, 0)
end
reaper.GetSet_LoopTimeRange(true, true, 0, __END__, false)
reaper.SetEditCurPos(0, true, false)
reaper.UpdateArrange()
return reaper.CountProjectMarkers(0)
"""


def lua_zones() -> str:
    """Build the per-track spec, including RS5k multisample zones."""

    def f(name: str) -> str:
        return (SAMPLES / name).as_posix()

    def pitched(inst: str, vol: float) -> str:
        parts = []
        for base, lo, hi in GI.ZONES[inst]:
            parts.append(
                '{file="%s", lo=%d, hi=%d, base=%d, hold=true, vol=%f, label="%s %d"}'
                % (f(GI.sample_name(inst, base)), lo, hi, base, vol, inst, base)
            )
        return ", ".join(parts)

    drum_zones = []
    for note, fname, vol, label in [
        (KICK, "g_kick.wav", 0.0, "Kick"),
        (SNARE, "g_snare.wav", -1.0, "Snare"),
        (HAT_C, "g_hat_closed.wav", -7.0, "HatC"),
        (HAT_O, "g_hat_open.wav", -9.0, "HatO"),
        (CLAP, "g_clap.wav", -4.0, "Clap"),
        (TAMB, "g_tamb.wav", -11.0, "Tamb"),
        (RIDE, "g_ride.wav", -13.0, "Ride"),
    ]:
        drum_zones.append(
            '{file="%s", lo=%d, hi=%d, base=%d, hold=false, vol=%f, label="%s"}'
            % (f(fname), note, note, note, vol, label)
        )

    # Levels are measured, not guessed: the first pass clipped the master at
    # +4.5 dB because nine drawbars times five held organ notes stacks hard.
    # See verify_audio.py.
    spec = [
        '{name="Drums",  vol=-8.0,  pan=0.0,   zones={%s}, fx={"ReaComp"}}' % ", ".join(drum_zones),
        '{name="Bass",   vol=-4.0,  pan=0.0,   zones={%s}, fx={"ReaComp"}}' % pitched("bass", 0),
        '{name="EP",     vol=-18.0,  pan=-0.18, zones={%s}, fx={"ReaEQ"}}' % pitched("ep", 0),
        '{name="Organ",  vol=-28.0, pan=0.22,  zones={%s}, fx={"ReaEQ"}}' % pitched("organ", 0),
        '{name="Choir",  vol=-23.0, pan=0.0,   zones={%s}, fx={"ReaEQ"}}' % pitched("choir", 0),
        '{name="Reverb", vol=-16.0, pan=0.0,   zones={}, fx={"ReaVerbate"}}',
    ]
    return "{ " + ", ".join(spec) + " }"


SENDS = {"EP": -11.0, "Organ": -9.0, "Choir": -4.0, "Drums": -17.0, "Bass": -26.0}

TRACK_LABEL = {
    "drums": ("Drums", "Beat"),
    "bass": ("Bass", "Bass"),
    "ep": ("EP", "Rhodes"),
    "organ": ("Organ", "Organ bed"),
    "choir": ("Choir", "Choir"),
}


def main() -> int:
    missing = [n for n in GI.DRUMS if not (SAMPLES / n).exists()]
    for inst, zones in GI.ZONES.items():
        for base, _, _ in zones:
            if not (SAMPLES / GI.sample_name(inst, base)).exists():
                missing.append(GI.sample_name(inst, base))
    if missing:
        print("generating %d missing samples..." % len(missing))
        GI.generate_all()

    tracks, sections, total_bars = build_song()
    end = total_bars * BAR
    print("%d bars, %.1fs (%d:%02d) at %g BPM"
          % (total_bars, end, int(end // 60), int(end % 60), BPM))
    for name, a, b in sections:
        print("   %-13s bars %2d-%-2d  %5.1fs" % (name, a, b - 1, (b - a) * BAR))

    sends = "{" + ", ".join('["%s"]=%f' % (k, v) for k, v in SENDS.items()) + "}"
    code = (TRACK_SETUP
            .replace("__SPEC__", lua_zones())
            .replace("__BPM__", str(BPM))
            .replace("__SENDS__", sends))
    r = eval_lua(code=code, undo_label="MCP: gospel - tracks", timeout=180)
    if r.get("status") != "ok":
        print("track setup failed:", r.get("error"))
        return 1
    print()
    for line in r.get("result") or []:
        print("  " + line)

    print()
    total = 0
    for key, notes in tracks.items():
        if len(notes) == 0:
            continue
        tname, label = TRACK_LABEL[key]
        code = (WRITE_NOTES
                .replace("__TRACK__", tname)
                .replace("__PACKED__", notes.pack())
                .replace("__LABEL__", label)
                .replace("__T0__", "0")
                .replace("__T1__", "%.4f" % end))
        w = eval_lua(code=code, undo_label="MCP: gospel - %s" % tname, timeout=180)
        if w.get("status") != "ok":
            print("%-7s FAILED: %s" % (tname, w.get("error")))
            return 1
        res = w.get("result") or {}
        total += res.get("inserted", 0)
        print("  %-7s inserted %4d notes (take reports %s)"
              % (tname, res.get("inserted"), res.get("counted")))
    print("  %d notes total" % total)

    secs = "{" + ", ".join('{"%s", %d, %d}' % s for s in sections) + "}"
    m = eval_lua(
        code=(MARKERS.replace("__SECTIONS__", secs)
              .replace("__BAR__", "%.6f" % BAR)
              .replace("__END__", "%.4f" % end)),
        undo_label="MCP: gospel - sections", timeout=60)
    print()
    print("markers:", m.get("result"))

    saved = read_rpp(save_first=True, save_as=str(Path(__file__).parent / "demo" / "gospel.rpp"),
                     max_chars=200)
    print("saved:", saved.get("path"), "->", saved.get("total_chars"), "chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
