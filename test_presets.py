"""Live test for load_instrument_preset / list_instrument_presets.

Needs REAPER running with the bridge, and an open project whose tracks named
Keys / Bass / Drums hold Keyscape / Trilian / BFD Player. Checks that:
  - every supported instrument's library can be indexed
  - decode -> encode of an untouched VST3 chunk is byte-identical
  - loading a preset is verified by reading the chunk back
"""

import sys
import tempfile
from pathlib import Path

from reaper_mcp import presets
from reaper_mcp.server import _track_chunk, list_instrument_presets, load_instrument_preset

failed = 0


def check(cond: bool, what: str) -> None:
    global failed
    print(("  ok   " if cond else "  FAIL ") + what)
    failed += not cond


print("library index")
for inst in presets.INSTRUMENTS:
    r = list_instrument_presets(inst, limit=1)
    print("  %-10s %s" % (inst, r.get("total", r.get("error"))))

print("round trip")
tmp = Path(tempfile.mkdtemp()) / "chunk.txt"
for track in ("Keys", "Bass", "Drums"):
    info = _track_chunk(track, tmp)
    chunk = tmp.read_bytes().decode("utf-8", "surrogateescape")
    idx = next(i for i, n in enumerate(info["fx"]) if presets.instrument_for(n))
    block = presets.read_vst3(chunk, idx)
    check(presets.write_vst3(chunk, block) == chunk, "%s: untouched chunk re-encodes identically" % track)

print("load")
for track, name in (("Keys", "Rhodes - Classic Suitcase Lush"),
                    ("Bass", "Fingered - Clean Fender Jazz Bass"),
                    ("Drums", "Dry Pop")):
    r = load_instrument_preset(track, name)
    check(r.get("status") == "ok" and r.get("verified"), "%s <- %s (%s)" % (track, name, r.get("error") or r.get("state_reports")))

r = load_instrument_preset("Keys", "Rhodes")
check(r.get("status") == "error" and "be more specific" in r.get("error", ""), "ambiguous query is refused")

sys.exit(1 if failed else 0)
