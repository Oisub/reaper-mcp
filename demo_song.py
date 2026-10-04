"""Build an audible demo song in REAPER using only stock instruments.

A minor, 92 BPM, 32 bars. Instruments are ReaSynDr (drums) and ReaSynth
(bass / keys / lead) because this machine has no third-party VSTi - the point
is that the whole thing is reproducible on a clean REAPER install.

Idempotent: it clears items, FX and markers first, so re-running gives the
same result instead of stacking.
"""

import json

from reaper_mcp.server import eval_lua, project_summary, read_rpp

BUILD = r"""
local BPM  = 92
local SPB  = 60 / BPM
local BAR  = 4 * SPB
local function T(bar, beat) return (bar - 1) * BAR + (beat - 1) * SPB end

reaper.SetCurrentBPM(0, BPM, false)
local function db(d) return 10 ^ (d / 20) end

---------------------------------------------------------------- clean slate

local _, n_mk, n_rg = reaper.CountProjectMarkers(0)
for _ = 1, n_mk + n_rg do reaper.DeleteProjectMarkerByIndex(0, 0) end

while reaper.CountTracks(0) > 0 do
  reaper.DeleteTrack(reaper.GetTrack(0, 0))
end

----------------------------------------------------------------- the tracks

-- levels are the measured balance, not guesses: see verify_audio.py
local SPECS = {
  { name = "Drums",  vol = -9.5,  pan =  0.00 },
  { name = "Bass",   vol = -4.0,  pan =  0.00 },
  { name = "Keys",   vol = -10.0, pan = -0.25 },
  { name = "Lead",   vol = -8.0,  pan =  0.20 },
  { name = "Reverb", vol = -12.0, pan =  0.00 },
}
reaper.SetMediaTrackInfo_Value(reaper.GetMasterTrack(0), "D_VOL", db(-1.0))
for i, s in ipairs(SPECS) do
  reaper.InsertTrackAtIndex(i - 1, true)
  local tr = reaper.GetTrack(0, i - 1)
  reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", s.name, true)
  reaper.SetMediaTrackInfo_Value(tr, "D_VOL", db(s.vol))
  reaper.SetMediaTrackInfo_Value(tr, "D_PAN", s.pan)
end

local DRUMS  = reaper.GetTrack(0, 0)
local BASS   = reaper.GetTrack(0, 1)
local KEYS   = reaper.GetTrack(0, 2)
local LEAD   = reaper.GetTrack(0, 3)
local REVERB = reaper.GetTrack(0, 4)

------------------------------------------------------------- the fx chains

-- instantiate at slot 0 so the synth sits ahead of the processing
local function addAt0(tr, name)
  return reaper.TrackFX_AddByName(tr, name, false, -1000)
end

-- the Drums chain is built by drums_kit.py: ReaSynDr's note mapping could not
-- be determined, so the kit is sampled one-shots in ReaSamplOmatic5000 instead.

addAt0(BASS, "ReaSynth")
reaper.TrackFX_AddByName(BASS, "ReaComp", false, -1)

addAt0(KEYS, "ReaSynth")
reaper.TrackFX_AddByName(KEYS, "ReaDelay", false, -1)

addAt0(LEAD, "ReaSynth")
reaper.TrackFX_AddByName(LEAD, "ReaComp", false, -1)

reaper.TrackFX_AddByName(REVERB, "ReaVerbate", false, -1)

-- ReaSynth voicing. Values are normalised; the readback below reports what
-- they actually became, because the time params are non-linear.
local SYNTH = {
  [1] = { track = BASS, p = { [4] = 0.70, [3] = 0.20, [2] = 0.00,
                              [0] = 0.004, [6] = 0.10, [9] = 1.00, [1] = 0.020,
                              [5] = 0.55 } },
  [2] = { track = KEYS, p = { [4] = 0.50, [7] = 0.30, [3] = 0.00,
                              [0] = 0.120, [6] = 0.25, [9] = 0.90, [1] = 0.250,
                              [5] = 0.45 } },
  [3] = { track = LEAD, p = { [2] = 0.50, [10] = 0.60, [4] = 0.20,
                              [0] = 0.020, [6] = 0.15, [9] = 0.85, [1] = 0.080,
                              [5] = 0.50 } },
}
for _, s in ipairs(SYNTH) do
  for idx, val in pairs(s.p) do
    reaper.TrackFX_SetParam(s.track, 0, idx, val)
  end
end

-- reverb sends from keys and lead
local function send(src, amount_db)
  local idx = reaper.CreateTrackSend(src, REVERB)
  reaper.SetTrackSendInfo_Value(src, 0, idx, "D_VOL", db(amount_db))
  return idx
end
send(KEYS, -4.0)
send(LEAD, -7.0)

--------------------------------------------------------------- note writing

local function newItem(tr, barFrom, barTo, label)
  local item = reaper.CreateNewMIDIItemInProj(tr, T(barFrom, 1), T(barTo, 1), false)
  local take = reaper.GetActiveTake(item)
  reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", label, true)
  return take
end

local function note(take, pitch, startT, lenT, vel)
  local p0 = reaper.MIDI_GetPPQPosFromProjTime(take, startT)
  local p1 = reaper.MIDI_GetPPQPosFromProjTime(take, startT + lenT)
  reaper.MIDI_InsertNote(take, false, false, p0, p1, 0, pitch, vel, true)
end

-- i - VI - III - VII in A minor, one bar each, with light voice leading
local CHORDS = {
  { 57, 60, 64 },  -- Am
  { 57, 60, 65 },  -- F
  { 55, 60, 64 },  -- C
  { 55, 59, 62 },  -- G
}
local ROOTS = { 45, 41, 48, 43 }  -- A2 F2 C3 G2
local function cyc(bar) return ((bar - 1) % 4) + 1 end

-- KEYS: sustained pad across the whole piece
local keys = newItem(KEYS, 1, 33, "Keys pad")
for bar = 1, 32 do
  for _, p in ipairs(CHORDS[cyc(bar)]) do
    note(keys, p, T(bar, 1), BAR * 0.96, 70)
  end
end

-- BASS: root on 1 and 3, octave lift into the next bar
local bass = newItem(BASS, 5, 29, "Bassline")
for bar = 5, 28 do
  local r = ROOTS[cyc(bar)]
  note(bass, r, T(bar, 1), SPB * 1.4, 100)
  note(bass, r, T(bar, 3), SPB * 0.9, 88)
  if bar % 4 == 0 then
    note(bass, r + 12, T(bar, 4) + SPB * 0.5, SPB * 0.4, 84)
  else
    note(bass, r + 7, T(bar, 4) + SPB * 0.5, SPB * 0.4, 78)
  end
end

-- DRUMS: GM mapping (36 kick, 38 snare, 42 closed hat, 46 open hat)
local drums = newItem(DRUMS, 5, 29, "Beat")
for bar = 5, 28 do
  note(drums, 36, T(bar, 1), 0.12, 102)
  note(drums, 36, T(bar, 3), 0.12, 96)
  note(drums, 38, T(bar, 2), 0.10, 92)
  note(drums, 38, T(bar, 4), 0.10, 96)
  for k = 0, 7 do
    note(drums, 42, T(bar, 1) + k * SPB * 0.5, 0.06, (k % 2 == 0) and 74 or 56)
  end
  if bar % 8 == 4 then                       -- fill every 8 bars
    note(drums, 36, T(bar, 4) + SPB * 0.5, 0.10, 90)
    note(drums, 46, T(bar, 4) + SPB * 0.75, 0.18, 92)
  end
end

-- LEAD: A minor pentatonic over bars 13-20
local MELODY = {
  { 13, 1.0, 1.5, 76 }, { 13, 2.5, 0.5, 74 }, { 13, 3.0, 1.0, 72 }, { 13, 4.0, 1.0, 69 },
  { 14, 1.0, 1.0, 72 }, { 14, 2.0, 1.0, 74 }, { 14, 3.0, 2.0, 72 },
  { 15, 1.0, 1.0, 76 }, { 15, 2.0, 1.5, 79 }, { 15, 3.5, 0.5, 76 }, { 15, 4.0, 1.0, 74 },
  { 16, 1.0, 3.0, 74 }, { 16, 4.0, 1.0, 71 },
  { 17, 1.0, 1.0, 69 }, { 17, 2.0, 1.0, 72 }, { 17, 3.0, 2.0, 76 },
  { 18, 1.0, 1.5, 74 }, { 18, 2.5, 0.5, 72 }, { 18, 3.0, 2.0, 69 },
  { 19, 1.0, 1.0, 76 }, { 19, 2.0, 1.0, 74 }, { 19, 3.0, 1.0, 72 }, { 19, 4.0, 1.0, 69 },
  { 20, 1.0, 4.0, 71 },
}
local lead = newItem(LEAD, 13, 21, "Lead")
for _, m in ipairs(MELODY) do
  note(lead, m[4], T(m[1], m[2]), SPB * m[3] * 0.95, 88)
end

for _, take in ipairs({ keys, bass, drums, lead }) do
  reaper.MIDI_Sort(take)
end

------------------------------------------------------------------- sections

local SECTIONS = {
  { "Intro", 1, 5 }, { "A", 5, 13 }, { "B - lead", 13, 21 },
  { "A2", 21, 29 }, { "Outro", 29, 33 },
}
for i, s in ipairs(SECTIONS) do
  reaper.AddProjectMarker2(0, true, T(s[2], 1), T(s[3], 1), s[1], i, 0)
end

reaper.GetSet_LoopTimeRange(true, true, 0, T(33, 1), false)
reaper.SetEditCurPos(0, true, false)
reaper.UpdateArrange()

--------------------------------------------------------- readback for report

local function synthReport(tr, label)
  local out = { label }
  for _, idx in ipairs({ 0, 1, 2, 3, 4, 5, 6, 9, 10 }) do
    local _, pname = reaper.TrackFX_GetParamName(tr, 0, idx, "")
    local _, fmt = reaper.TrackFX_GetFormattedParamValue(tr, 0, idx, "")
    out[#out + 1] = pname .. "=" .. fmt
  end
  return table.concat(out, "  ")
end

local counts = {}
for ti = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, ti)
  local nm = select(2, reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false))
  local total = 0
  for ii = 0, reaper.CountTrackMediaItems(tr) - 1 do
    local tk = reaper.GetActiveTake(reaper.GetTrackMediaItem(tr, ii))
    if tk and reaper.TakeIsMIDI(tk) then
      total = total + select(2, reaper.MIDI_CountEvts(tk))
    end
  end
  local chain = {}
  for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
    chain[#chain + 1] = (select(2, reaper.TrackFX_GetFXName(tr, f, "")):gsub("^VSTi?: ", ""):gsub(" %(Cockos%).*", ""))
  end
  counts[#counts + 1] = {
    track = nm, notes = total, fx = table.concat(chain, " > "),
    sends = reaper.GetTrackNumSends(tr, 0),
  }
end

return {
  tempo = reaper.Master_GetTempo(),
  length_seconds = math.floor(T(33, 1) * 10 + 0.5) / 10,
  tracks = counts,
  synths = {
    synthReport(BASS, "Bass:"),
    synthReport(KEYS, "Keys:"),
    synthReport(LEAD, "Lead:"),
  },
}
"""


def main() -> int:
    r = eval_lua(code=BUILD, undo_label="MCP: build demo song", timeout=90)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    if r.get("status") != "ok":
        return 1

    # drums are sampled one-shots; generate them if they are not there yet
    import drums_kit
    import gen_drum_samples

    missing = [f for f in drums_kit.KIT.values() if not (drums_kit.SAMPLES / f[0]).exists()]
    if missing:
        print()
        print("generating drum samples...")
        for name, buf in gen_drum_samples.KIT.items():
            gen_drum_samples.write_wav(name, buf)

    print()
    print("building drum kit...")
    k = drums_kit.build()
    if k.get("status") != "ok":
        print("drum kit failed:", k.get("error"))
        return 1
    print("  chain:", " > ".join((k.get("result") or {}).get("chain", [])))

    saved = read_rpp(save_first=True, max_chars=200)
    print()
    print("saved:", saved.get("path"), "->", saved.get("total_chars"), "chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
