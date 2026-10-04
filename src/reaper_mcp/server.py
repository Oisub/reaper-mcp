"""MCP server driving a live REAPER instance through a ReaScript Lua bridge.

Design notes
------------
The point of this server is not tool count. It is the closed loop: every
mutation goes in through ``eval_lua``, and ``read_rpp`` reads the project back
as text so the agent can verify what it actually did. REAPER's .RPP format is
plain text, which is the whole reason this is possible.

Transport is file IPC against ``<REAPER resource path>/mcp-bridge``. REAPER's
Lua has no sockets, and file IPC with atomic renames is boring and reliable.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from reaper_mcp import presets

POLL_INTERVAL = 0.025
DEFAULT_TIMEOUT = 20.0
HEARTBEAT_STALE_AFTER = 10.0

mcp = MCPServer("reaper")

READ_ONLY = ToolAnnotations(readOnlyHint=True)


# --------------------------------------------------------------------- paths


def resource_path() -> Path:
    override = os.environ.get("REAPER_RESOURCE_PATH")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "REAPER"
    mac = Path.home() / "Library" / "Application Support" / "REAPER"
    if mac.exists():
        return mac
    return Path.home() / ".config" / "REAPER"


def ipc_dir() -> Path:
    return resource_path() / "mcp-bridge"


# ------------------------------------------------------------------ transport


class BridgeError(RuntimeError):
    pass


def _heartbeat_age() -> float | None:
    hb = ipc_dir() / "heartbeat"
    if not hb.exists():
        return None
    try:
        stamp = int(hb.read_text(encoding="utf-8", errors="replace").splitlines()[0])
    except (ValueError, IndexError, OSError):
        return None
    return max(0.0, time.time() - stamp)


def _parse_response(raw: str) -> dict[str, Any]:
    def section(name: str, text: str) -> tuple[str, str]:
        marker = "\n---" + name + "---\n"
        head, _, tail = text.partition(marker)
        return head, tail

    header, rest = section("OUTPUT", raw)
    output, rest = section("RESULT", rest)
    result_json, error = section("ERROR", rest)

    status = "error"
    for line in header.splitlines():
        if line.startswith("status="):
            status = line[len("status=") :].strip()

    try:
        result = json.loads(result_json) if result_json.strip() else None
    except json.JSONDecodeError:
        result = {"_unparsed": result_json}

    out: dict[str, Any] = {"status": status}
    if output.strip():
        out["output"] = output
    if status == "ok":
        out["result"] = result
    else:
        out["error"] = error.strip() or "unknown error"
    return out


def _call(
    code: str,
    undo_label: str = "MCP",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    req_dir = ipc_dir() / "req"
    resp_dir = ipc_dir() / "resp"
    if not req_dir.exists():
        raise BridgeError(
            "IPC directory missing: "
            + str(req_dir)
            + "\nThe bridge has never run. Install it, then restart REAPER."
        )

    call_id = uuid.uuid4().hex
    payload = "undo=" + undo_label + "\n---CODE---\n" + code

    # write_bytes, not write_text: in text mode Windows translates the LF
    # separators to CRLF and the bridge markers stop matching.
    tmp = req_dir / (call_id + ".tmp")
    tmp.write_bytes(payload.encode("utf-8"))
    tmp.replace(req_dir / (call_id + ".req"))

    resp_file = resp_dir / (call_id + ".resp")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if resp_file.exists():
            raw = resp_file.read_text(encoding="utf-8", errors="replace")
            resp_file.unlink(missing_ok=True)
            return _parse_response(raw)
        time.sleep(POLL_INTERVAL)

    (req_dir / (call_id + ".req")).unlink(missing_ok=True)
    age = _heartbeat_age()
    if age is None:
        hint = "no heartbeat file - the bridge is not running (restart REAPER)"
    elif age > HEARTBEAT_STALE_AFTER:
        hint = "heartbeat is " + str(round(age)) + "s stale - REAPER is closed or the bridge stopped"
    else:
        hint = "bridge is alive, so the script itself probably blocked (a modal dialog?)"
    raise BridgeError("timed out after " + str(timeout) + "s; " + hint)


# ---------------------------------------------------------------------- tools


@mcp.tool(annotations=READ_ONLY)
def bridge_status() -> dict[str, Any]:
    """Check whether REAPER is running with the MCP bridge loaded.

    Call this first when anything else fails.
    """
    res = resource_path()
    age = _heartbeat_age()
    info: dict[str, Any] = {
        "resource_path": str(res),
        "resource_path_exists": res.exists(),
        "bridge_installed": (res / "Scripts" / "__startup.lua").exists(),
        "ipc_dir": str(ipc_dir()),
        "heartbeat_age_seconds": None if age is None else round(age, 1),
    }
    if age is None:
        info["state"] = "bridge has never run"
    elif age > HEARTBEAT_STALE_AFTER:
        info["state"] = "stale - REAPER closed or bridge stopped"
    else:
        info["state"] = "alive"
        try:
            probe = _call(
                'return {version = reaper.GetAppVersion(), '
                'project = select(2, reaper.EnumProjects(-1, "")), '
                "tracks = reaper.CountTracks(0)}",
                undo_label="",
                timeout=5,
            )
            if probe.get("status") == "ok":
                info["probe"] = probe.get("result")
            else:
                info["state"] = "bridge reachable but eval failed"
                info["probe_error"] = probe.get("error")
        except BridgeError as exc:
            info["state"] = "heartbeat fresh but call failed"
            info["probe_error"] = str(exc)
    return info


@mcp.tool()
def eval_lua(
    code: str,
    undo_label: str = "MCP",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run ReaScript Lua inside the live REAPER instance and return its result.

    The full reaper.* ReaScript API is in scope. print(...) is captured and
    returned as `output`. A returned value is JSON-encoded into `result`
    (Lua tables become JSON objects or arrays).

    Everything runs inside one undo block, so `undo_label` is what the user
    sees in REAPER's undo history - name it after the change being made. Pass
    an empty label for read-only queries.
    """
    try:
        return _call(code, undo_label=undo_label, timeout=timeout)
    except BridgeError as exc:
        return {"status": "error", "error": str(exc)}


_SUMMARY_LUA = r"""
local function db(v) if v <= 0 then return -150 end return 20 * math.log(v, 10) end
local function round(v) return math.floor(v * 100 + 0.5) / 100 end

local tracks = {}
for i = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, i)
  local fx = {}
  for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
    fx[#fx + 1] = select(2, reaper.TrackFX_GetFXName(tr, f, ""))
  end
  local items = {}
  for it = 0, reaper.CountTrackMediaItems(tr) - 1 do
    local item = reaper.GetTrackMediaItem(tr, it)
    local take = reaper.GetActiveTake(item)
    local is_midi = take ~= nil and reaper.TakeIsMIDI(take)
    local notes = 0
    if is_midi then notes = select(2, reaper.MIDI_CountEvts(take)) end
    items[#items + 1] = {
      pos = round(reaper.GetMediaItemInfo_Value(item, "D_POSITION")),
      len = round(reaper.GetMediaItemInfo_Value(item, "D_LENGTH")),
      midi = is_midi,
      notes = notes,
      name = take and select(2, reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", "", false)) or "",
    }
  end
  tracks[#tracks + 1] = {
    index = i + 1,
    name = select(2, reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false)),
    volume_db = round(db(reaper.GetMediaTrackInfo_Value(tr, "D_VOL"))),
    pan = round(reaper.GetMediaTrackInfo_Value(tr, "D_PAN")),
    muted = reaper.GetMediaTrackInfo_Value(tr, "B_MUTE") == 1,
    soloed = reaper.GetMediaTrackInfo_Value(tr, "I_SOLO") ~= 0,
    folder_depth = reaper.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH"),
    fx = fx,
    items = items,
  }
end

local markers = {}
local _, n_mk, n_rg = reaper.CountProjectMarkers(0)
for i = 0, n_mk + n_rg - 1 do
  local ok, isrgn, pos, rgnend, name, idx = reaper.EnumProjectMarkers(i)
  if ok then
    markers[#markers + 1] = {
      region = isrgn, pos = round(pos), stop = round(rgnend), name = name, id = idx,
    }
  end
end

return {
  project_path = select(2, reaper.EnumProjects(-1, "")),
  app_version = reaper.GetAppVersion(),
  tempo = reaper.Master_GetTempo(),
  play_state = reaper.GetPlayState(),
  cursor = round(reaper.GetCursorPosition()),
  length = round(reaper.GetProjectLength(0)),
  track_count = reaper.CountTracks(0),
  unsaved_changes = reaper.IsProjectDirty(0) == 1,
  tracks = tracks,
  markers = markers,
}
"""


@mcp.tool(annotations=READ_ONLY)
def project_summary() -> dict[str, Any]:
    """Read the live state of the open REAPER project.

    Returns tempo, cursor, play state, every track (name, volume in dB, pan,
    mute/solo, folder depth, FX chain, media items with MIDI note counts) and
    all markers/regions. Read-only.
    """
    try:
        return _call(_SUMMARY_LUA, undo_label="", timeout=30)
    except BridgeError as exc:
        return {"status": "error", "error": str(exc)}


@mcp.tool()
def read_rpp(
    save_first: bool = True,
    save_as: str = "",
    max_chars: int = 40000,
) -> dict[str, Any]:
    """Read the project back as raw .RPP text - the verification path.

    .RPP is a plain-text format, so this is how to confirm a change actually
    landed as intended, diff before against after, or inspect state the
    ReaScript API does not expose.

    Args:
        save_first: save the project before reading, so the file matches the
            live session.
        save_as: absolute path to save an as-yet-unsaved project to. Required
            the first time, because saving an untitled project would otherwise
            open a modal dialog and hang the bridge.
        max_chars: truncation limit; the response always reports full size.
    """
    try:
        probe = _call(
            'return {path = select(2, reaper.EnumProjects(-1, "")), '
            "dirty = reaper.IsProjectDirty(0) == 1}",
            undo_label="",
            timeout=10,
        )
    except BridgeError as exc:
        return {"status": "error", "error": str(exc)}
    if probe.get("status") != "ok":
        return probe

    path = (probe.get("result") or {}).get("path") or ""

    if not path:
        if not save_as:
            return {
                "status": "error",
                "error": "project has never been saved, so there is no .RPP to read. "
                "Pass save_as with an absolute path to save it first.",
            }
        target = save_as.replace("\\", "/")
        if not target.lower().endswith(".rpp"):
            target += ".rpp"
        # options=8 means "set as the new project filename for this ReaProject".
        # With 0 the file is written but the project stays untitled, so the next
        # save_first would open a modal Save-As dialog and hang the bridge.
        saved = _call(
            'reaper.Main_SaveProjectEx(0, "' + target + '", 8)\n'
            'return select(2, reaper.EnumProjects(-1, ""))',
            undo_label="",
            timeout=30,
        )
        if saved.get("status") != "ok":
            return saved
        path = saved.get("result") or ""
        if not path:
            return {
                "status": "error",
                "error": "saved to " + target + " but REAPER did not adopt it as the "
                "project filename, so the project is still untitled. Refusing to "
                "continue, because a later save would open a modal dialog and hang "
                "the bridge.",
            }
    elif save_first:
        res = _call("reaper.Main_SaveProject(0, false)", undo_label="", timeout=30)
        if res.get("status") != "ok":
            return res

    file = Path(path)
    if not file.exists():
        return {"status": "error", "error": "project file not found on disk: " + str(path)}

    text = file.read_text(encoding="utf-8", errors="replace")
    out: dict[str, Any] = {
        "status": "ok",
        "path": str(file),
        "total_chars": len(text),
        "lines": text.count("\n") + 1,
    }
    if len(text) > max_chars:
        out["truncated"] = True
        out["rpp"] = text[:max_chars]
    else:
        out["rpp"] = text
    return out


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


_FIND_TRACK_LUA = r"""
local want = __TRACK__
local tr
local n = tonumber(want)
if n then tr = reaper.GetTrack(0, n - 1) end
if not tr then
  for i = 0, reaper.CountTracks(0) - 1 do
    local t = reaper.GetTrack(0, i)
    if select(2, reaper.GetSetMediaTrackInfo_String(t, "P_NAME", "", false)) == want then tr = t break end
  end
end
if not tr then error("no track " .. want) end
"""

_DUMP_CHUNK_LUA = _FIND_TRACK_LUA + r"""
local ok, chunk = reaper.GetTrackStateChunk(tr, "", false)
if not ok then error("GetTrackStateChunk failed") end
local f = assert(io.open(__PATH__, "wb")) f:write(chunk) f:close()
local fx = {}
for i = 0, reaper.TrackFX_GetCount(tr) - 1 do fx[#fx + 1] = select(2, reaper.TrackFX_GetFXName(tr, i, "")) end
return { fx = fx, name = select(2, reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false)) }
"""

_LOAD_CHUNK_LUA = _FIND_TRACK_LUA + r"""
local f = assert(io.open(__PATH__, "rb")) local chunk = f:read("*a") f:close()
if not reaper.SetTrackStateChunk(tr, chunk, false) then error("SetTrackStateChunk failed") end
return true
"""


def _track_chunk(track: str, path: Path) -> dict[str, Any]:
    code = _DUMP_CHUNK_LUA.replace("__TRACK__", _lua_str(track)).replace("__PATH__", _lua_str(str(path)))
    res = _call(code, undo_label="", timeout=30)
    if res.get("status") != "ok":
        raise BridgeError(res.get("error", "chunk read failed"))
    return res["result"]


@mcp.tool(annotations=READ_ONLY)
def list_instrument_presets(instrument: str, query: str = "", limit: int = 40) -> dict[str, Any]:
    """List presets for an instrument whose sounds live inside its own GUI browser.

    Supported: keyscape, trilian, omnisphere (Spectrasonics) and bfd (BFD
    Player). These plugins load with no sound and REAPER's preset menu for them
    is empty; use this to find a name, then load_instrument_preset.

    Args:
        instrument: keyscape | trilian | omnisphere | bfd
        query: case-insensitive substring matched against name and category path.
        limit: max rows returned (the total is always reported).
    """
    try:
        rows = presets.list_presets(instrument.lower())
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    q = query.lower()
    hits = [p for p in rows if q in p.name.lower() or q in p.path.lower()]
    return {
        "status": "ok",
        "total": len(rows),
        "matches": len(hits),
        "presets": [{"name": p.name, "category": p.path.rsplit("/", 1)[0]} for p in hits[:limit]],
    }


@mcp.tool()
def load_instrument_preset(track: str, preset: str, fx_index: int = -1, part: int = 1) -> dict[str, Any]:
    """Load a preset into Keyscape / Trilian / Omnisphere / BFD Player without its GUI.

    Rewrites the plugin's state inside the track chunk (see presets.py). One
    undo step. Never open these plugins' GUIs from eval_lua instead: a plugin
    error dialog blocks REAPER's main thread and hangs the bridge.

    Args:
        track: 1-based track index, or exact track name.
        preset: preset name, or a unique substring of it (list_instrument_presets).
        fx_index: 0-based FX slot; -1 picks the first supported instrument.
        part: Spectrasonics multi part 1-8 (ignored for BFD).
    """
    work = ipc_dir() / "chunks"
    work.mkdir(parents=True, exist_ok=True)
    src = work / (uuid.uuid4().hex + ".in")
    dst = work / (uuid.uuid4().hex + ".out")
    try:
        info = _track_chunk(track, src)
        names = info.get("fx") or []
        if fx_index < 0:
            fx_index = next((i for i, n in enumerate(names) if presets.instrument_for(n)), -1)
            if fx_index < 0:
                return {"status": "error", "error": "no supported instrument on track: " + ", ".join(names)}
        if fx_index >= len(names):
            return {"status": "error", "error": "track has %d FX" % len(names)}
        instrument = presets.instrument_for(names[fx_index])
        if not instrument:
            return {"status": "error", "error": "unsupported plugin: " + names[fx_index]}

        found = presets.find_preset(instrument, preset)
        chunk = src.read_bytes().decode("utf-8", "surrogateescape")
        new_chunk, label = presets.apply_preset(chunk, fx_index, found, part)
        dst.write_bytes(new_chunk.encode("utf-8", "surrogateescape"))

        code = _LOAD_CHUNK_LUA.replace("__TRACK__", _lua_str(track)).replace("__PATH__", _lua_str(str(dst)))
        res = _call(code, undo_label="MCP: load preset " + label, timeout=60)
        if res.get("status") != "ok":
            return res

        # read back: the closed loop this server is built around
        _track_chunk(track, src)
        now = presets.current_preset(src.read_bytes().decode("utf-8", "surrogateescape"), fx_index, instrument)
        loaded = now if isinstance(now, str) else (now[part - 1] if len(now) >= part else "")
        return {
            "status": "ok",
            "track": info.get("name"),
            "fx": names[fx_index],
            "instrument": instrument,
            "preset": found.name,
            "category": found.path.rsplit("/", 1)[0],
            "verified": loaded == label,
            "state_reports": now,
            "note": "samples stream in over a few seconds; meter before judging silence",
        }
    except (BridgeError, LookupError, FileNotFoundError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
