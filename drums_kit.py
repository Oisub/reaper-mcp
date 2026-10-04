"""Build the drum kit on the Drums track from generated samples.

One ReaSamplOmatic5000 instance per drum, each with its note range pinned to a
single note, so the mapping is ours rather than an undocumented plugin's.

RS5k maps its note range onto a pitch range (unity at note 69 by default), so
both pitch-range ends are set to 0 semitones - otherwise a kick at note 36
would play 33 semitones down.
"""

from __future__ import annotations

import json
from pathlib import Path

from reaper_mcp.server import eval_lua

SAMPLES = Path(__file__).parent / "demo" / "samples"

# note -> (file, volume dB, label)
KIT = {
    36: ("kick.wav", 0.0, "Kick"),
    38: ("snare.wav", -2.5, "Snare"),
    42: ("hat_closed.wav", -9.0, "HatClosed"),
    46: ("hat_open.wav", -11.0, "HatOpen"),
}

NOTE_DEN = 127.0

# Measured, not derived: the -69 / +59 in the parameter dump are the DEFAULT
# values, not the parameter's range. Sweeping raw values showed the pitch
# parameters are symmetric about 0.5, where 0.5 == 0 semitones. Computing this
# from the defaults gave +6 semitones and a kick that played sharp.
PITCH_ZERO = 0.5


def build() -> dict:
    entries = []
    for note, (fname, vol_db, label) in sorted(KIT.items()):
        path = (SAMPLES / fname).as_posix()
        entries.append(
            '{note=%d, file="%s", vol=%f, label="%s"}'
            % (note, path, 10 ** (vol_db / 20), label)
        )

    lua = """
local KIT = { %s }
local NOTE_DEN, PITCH_ZERO = %f, %f

local tr = reaper.GetTrack(0, 0)   -- Drums

-- clear the chain and rebuild it
for i = reaper.TrackFX_GetCount(tr) - 1, 0, -1 do
  reaper.TrackFX_Delete(tr, i)
end

local report = {}
for i, d in ipairs(KIT) do
  local fx = reaper.TrackFX_AddByName(tr, "ReaSamplOmatic5000", false, -1)
  reaper.TrackFX_SetNamedConfigParm(tr, fx, "FILE0", d.file)
  reaper.TrackFX_SetNamedConfigParm(tr, fx, "DONE", "")

  reaper.TrackFX_SetParam(tr, fx, 3, d.note / NOTE_DEN)   -- note range start
  reaper.TrackFX_SetParam(tr, fx, 4, d.note / NOTE_DEN)   -- note range end
  reaper.TrackFX_SetParam(tr, fx, 5, PITCH_ZERO)          -- no transposition
  reaper.TrackFX_SetParam(tr, fx, 6, PITCH_ZERO)
  reaper.TrackFX_SetParam(tr, fx, 11, 0)                  -- ignore note-offs
  reaper.TrackFX_SetParam(tr, fx, 9, 0)                   -- instant attack
  reaper.TrackFX_SetParam(tr, fx, 0, d.vol)               -- volume

  reaper.TrackFX_SetNamedConfigParm(tr, fx, "renamed_name", d.label)

  local function fmt(p)
    return select(2, reaper.TrackFX_GetFormattedParamValue(tr, fx, p, ""))
  end
  local loaded = select(2, reaper.TrackFX_GetNamedConfigParm(tr, fx, "FILE0"))
  report[#report+1] = {
    slot = fx, label = d.label, note = d.note,
    range = fmt(3) .. ".." .. fmt(4),
    pitch = fmt(5) .. ".." .. fmt(6),
    note_offs = fmt(11),
    volume = fmt(0),
    file_loaded = loaded ~= "" and loaded:match("([^\\\\/]+)$") or "<NONE>",
  }
end

reaper.TrackFX_AddByName(tr, "ReaComp", false, -1)

local chain = {}
for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
  chain[#chain+1] = (select(2, reaper.TrackFX_GetFXName(tr, f, "")):gsub(" %%(Cockos%%).*", ""))
end

return { chain = chain, kit = report }
""" % (", ".join(entries), NOTE_DEN, PITCH_ZERO)

    return eval_lua(code=lua, undo_label="MCP: build drum kit", timeout=60)


if __name__ == "__main__":
    missing = [f for f, *_ in KIT.values() if not (SAMPLES / f).exists()]
    if missing:
        raise SystemExit("missing samples: %s - run gen_drum_samples.py first" % missing)
    r = build()
    print(json.dumps(r, indent=2, ensure_ascii=False))
    raise SystemExit(0 if r.get("status") == "ok" else 1)
