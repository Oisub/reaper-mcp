# reaper-mcp

An MCP server that drives a **live REAPER instance** through a ReaScript Lua bridge.

The design goal is not tool count. It is the **closed loop**: mutations go in
through `eval_lua`, and `read_rpp` reads the project back as text so the agent
can verify what it actually did. REAPER's `.RPP` is a plain-text format, which
is the reason this is possible at all — and the reason REAPER was chosen over
DAWs with larger APIs but opaque project files.

## Architecture

```
Claude  ──stdio──>  reaper-mcp (Python)
                         │
                         │  file IPC, atomic renames
                         │  <resource>/mcp-bridge/{req,resp,heartbeat}
                         v
                    mcp_bridge.lua  (deferred poll loop inside REAPER)
                         │
                         v
                    full reaper.* ReaScript API
```

- **`bridge/__startup.lua`** — a thin loader. REAPER runs
  `<resource path>/Scripts/__startup.lua` automatically at launch, so the bridge
  needs no action registration, no SWS, no Python-in-REAPER and no GUI step.
  Its only job is to `loadfile` the real bridge and log failures, because
  **REAPER silently swallows errors in startup scripts**.
- **`bridge/mcp_bridge.lua`** — polls for request files, executes each as Lua in
  a sandboxed env with `print` captured, JSON-encodes the return value, writes a
  response file. Every request is wrapped in one `Undo_BeginBlock` /
  `Undo_EndBlock` pair, so an agent action is a single undoable step.
- **File IPC, not sockets** — REAPER's Lua has no socket support. Atomic
  `rename` into the watched directory avoids partial reads. Latency is one
  defer tick (~30ms), which is irrelevant next to model latency.

## Install

```bash
uv sync
uv run reaper-mcp-install-bridge   # copies both Lua files into REAPER
# restart REAPER
```

Register with Claude Code:

```bash
claude mcp add reaper --scope user "<repo>/.venv/Scripts/reaper-mcp.exe"
```

Then **restart the Claude Code session** — a newly added server is not picked
up by `/mcp`. (`/mcp` reconnect is enough later, after code changes.)

Verify: `uv run python smoke_test.py` — exercises type round-tripping, output
capture, runtime errors and compile errors. `uv run python demo_build.py` builds
a 4-track session from nothing and asserts on the resulting `.RPP`.

## Tools

| Tool | Purpose |
|---|---|
| `bridge_status` | Is REAPER up with the bridge loaded? Heartbeat age, install state. Call this first when anything fails. |
| `eval_lua` | Run arbitrary ReaScript Lua. The whole API is reachable here; `print` is captured, the return value is JSON-encoded. |
| `project_summary` | Structured live state: tempo, cursor, tracks (name, dB, pan, mute/solo, folder depth, FX chain, items with MIDI note counts), markers/regions. |
| `read_rpp` | Save and read the project back as raw `.RPP` text. The verification path. |
| `list_instrument_presets` | Browse presets of Keyscape / Trilian / Omnisphere / Stylus RMX / BFD Player from their libraries on disk. |
| `load_instrument_preset` | Load one of those presets into a track's plugin **without opening its GUI**, then read the chunk back to verify. |
| `list_separation_models` | Stem-separation models: the curated set (default BS-RoFormer SW, 6 stems) plus a search over audio-separator's full catalog. |
| `separate_stems` | Split the selected item(s), a file or a folder into stems in the background, and load each song into REAPER as it finishes. |
| `separation_status` / `cancel_separation` | Follow or stop a separation job. |

`eval_lua` already reaches everything in the ReaScript API; wrapping a thousand
functions one-by-one would add surface area without adding capability, and
would still leave the agent unable to check its own work — which is what
`read_rpp` is for. The preset tools exist because they reach something the API
does *not*: sounds that only a plugin's own GUI browser can load.

## Instrument presets without the GUI

Spectrasonics plugins and BFD Player instantiate **silent**, and REAPER's preset
menu for them is 2048 empty `Program N` slots. Their patch browsers live in the
plugin GUI — and opening that GUI from the bridge is dangerous (see gotchas).

Both vendors store state as XML inside REAPER's VST3 chunk, and their preset
files are the same XML, so `src/reaper_mcp/presets.py` splices the preset into
the state and writes the track chunk back:

- **Spectrasonics**: state is a 32-byte prefix (u32 at +24 = XML length incl.
  NUL) + `<SynthMaster>` with 8 `<SynthSubEngine>` parts. A `.prt_key` /
  `.prt_trl` / `.prt_omn` patch is `<XxxPart><SynthEngine>…`; its
  `<SynthEngine>` replaces the part's. Factory patches sit inside `.db`
  containers: a `<FileSystem>` XML index of (name, offset, size), then payload.
  STEAM is found via `C:\ProgramData\Spectrasonics\STEAM(.lnk)` or
  `SPECTRASONICS_STEAM`.
- **Stylus RMX**: an older engine with its own layout — state is `u32 len-16,
  u32 1` then a `<StylusRMXMaster>` document, and a `.mlt_rmx` multi is that
  same document, so it is swapped whole. Library is found via
  `C:\ProgramData\Spectrasonics\SAGE(.lnk)` or `SPECTRASONICS_SAGE`. Unlike the
  STEAM products it is not silent when fresh: it loads a "Sound Check" loop.
  `.kit_rmx` kits are single-part and not supported yet.
  RMX renames a loaded multi to "Untitled", so a load is verified by the loops
  in its parts, not by `MultiName`. Playing it from MIDI: part n listens on
  channel n, each loop's slices are mapped chromatically from note 36, velocity
  is live (~24 dB from 30 to 127), a note-off cuts the slice, and grooves do not
  auto-play with the transport.
- **BFD Player**: state is a `<root>` document shaped exactly like a
  `.bfdplayer` preset; swap it whole. Library paths come from
  `%APPDATA%\BFD Drums\BFDPlayer\DataPaths.xml`.
- **REAPER's VST3 chunk**: header lines (up to the first line that does not
  decode to 210 bytes) ending in `u32 component_size, 1, 0xFFFF`; then the
  component state in 210-byte lines, starting with `u32 len-16`; then a
  program-name trailer. Both length fields must be rewritten.

`test_presets.py` checks that an untouched chunk re-encodes byte-identically,
and that every load is confirmed by reading the state back.

## Stem separation

Not reachable from ReaScript at all, hence a tool. `separate_stems` runs
[python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator)
in a **separate Python environment** through `separate_worker.py`, so this
server stays a small `mcp`-only install and the heavy environment can carry
whatever torch build the machine needs:

```bash
uv venv <sep-env> --python 3.12
uv pip install --python <sep-env>/python torch torchaudio --index-url https://download.pytorch.org/whl/xpu  # or cu126 / cpu
uv pip install --python <sep-env>/python audio-separator onnxruntime audioread
```

Point the server at it in `~/.reaper-mcp/separator.json`
(`{"python": "<sep-env>/python", "model_dir": "<models>"}`) or with
`REAPER_MCP_SEPARATOR_PYTHON` / `REAPER_MCP_SEPARATOR_MODELS`. Without either it
uses its own interpreter and `~/.reaper-mcp/separator-models`.

- **Models.** Default `bs_roformer_sw` (vocals, drums, bass, guitar, piano,
  other) is the strongest public multi-stem model on MVSep's Multisong
  leaderboard (bass SDR 14.62 vs ~12.0 for `htdemucs_ft`). Its original host is
  gone, so it is fetched from a Hugging Face mirror on first use. Any other
  audio-separator filename can be passed as `model`.
- **Devices.** `auto` takes CUDA, then **Intel XPU**, then CPU. audio-separator
  has no XPU support of its own; the worker pins the torch device. On an
  Intel Arc iGPU that is ~2.4x CPU with identical output.
- **Jobs run in the background** (minutes per song) and each song is loaded as
  soon as it is done: `tracks` puts a stem folder under the source track,
  aligned to the item (position, length, offset, rate) and mutes the source
  item; `new_tab` opens a project with the mix (muted) and the stems, saved next
  to them. `audition="bass"` leaves just that stem unmuted.
- **Output** is `<root>/<song>/<model>/<stem>.flac` plus a `stems.json`
  manifest; a song with a manifest is skipped, so batches resume.
- The worker runs at below-normal priority so the machine stays usable.

`test_separate.py` covers both layouts, resume and cancel on a synthetic clip.

## Gotchas found the hard way

- **REAPER swallows startup-script errors silently.** A compile error in
  `__startup.lua` produces no dialog, no console, nothing — the script simply
  never runs. Hence the loader/logger split; check
  `<resource path>/mcp-bridge.log`.
- **Never write request files in text mode on Windows.** Python's
  `Path.write_text` translates LF to CRLF, which breaks the bridge's section
  markers. The server uses `write_bytes`; the bridge also tolerates `\r` now.
- **`.RPP` quotes values only when they contain spaces.** `NAME Drums` but
  `NAME "drop here"`. Any assertion against the file must accept both.
- **A region is two `MARKER` lines** in the file — one carrying the name, one
  with `""` marking the end.
- **`Main_SaveProjectEx` needs `options=8` to adopt the filename.** With `0` it
  writes the file but leaves the project untitled — so the *next* save would
  open a modal Save-As dialog and hang the bridge. The options field is a
  bitmask: `&1`/`&2`/`&4` are track-template flags, `&8` is "set as the new
  project filename for this ReaProject". Covered by `test_save_adopt.py`.
- **Don't let `eval_lua` open a modal dialog.** Anything that blocks REAPER's
  main thread (an unsaved-project save prompt, a render dialog) stalls the defer
  loop and every call times out. `read_rpp` refuses to save an untitled project
  for exactly this reason and asks for an explicit path instead.
- **Never open a plugin GUI from the bridge** (`TrackFX_Show`). Spectrasonics
  with a missing library (e.g. Omnisphere registered but its STEAM folder holding
  only `Defaults`) raises "is not a valid STEAM folder" — and re-raises it on
  every GUI timer tick while its window is open, an endless modal loop. REAPER
  still reports "Responding", but the bridge is dead until the dialogs stop.
  Recovery without killing REAPER: `PostMessage(WM_CLOSE)` to the plugin window
  plus `WM_COMMAND IDOK` to the `#32770` "Error" dialogs.
- **An installed-but-missing expansion ships a 0-byte `.db`** (Trilian VIP).
  Skip it; don't let it fail the whole preset listing.
- **Discarding unsaved changes without a dialog:** `Main_openProject("noprompt:" .. path)`.
  To restart REAPER (e.g. to rescan plugins), switch to a clean project that
  way, then quit with `reaper.defer(function() reaper.Main_OnCommand(40004, 0) end)`
  so the bridge call returns before REAPER exits.
- **Installers that ignore the standard folder.** A plugin installed to a
  custom path is simply absent from the FX list, with no error. Add the folder
  to `vstpath64` in `reaper.ini` while REAPER is closed; it rescans at startup.
  Test new plugins with `test_plugins.py`.
- **Metering from inside one Lua call doesn't work.** A busy loop in the bridge
  blocks the main thread, so `Track_GetPeakInfo` never updates. Sample across
  separate calls (`verify_audio.py`).

## Layout

```
bridge/__startup.lua     loader + error logging
bridge/mcp_bridge.lua    the bridge itself
src/reaper_mcp/server.py MCP server and tools
src/reaper_mcp/presets.py GUI-free preset loading (VST3 chunk + vendor state codecs)
src/reaper_mcp/install.py bridge installer (backs up a pre-existing __startup.lua)
src/reaper_mcp/separate.py stem separation: model catalog, config, background jobs
src/reaper_mcp/separate_worker.py runs in the separator's own Python (audio-separator)
test_separate.py         live: stem separation, both load layouts, resume, cancel
test_presets.py          live: library index, byte-identical round trip, verified loads
test_plugins.py          live: does each plugin load, make sound / pass audio, stay silent on dialogs
song_neosoul.py          a song on Keyscape / Trilian / BFD, presets loaded by the tool
smoke_test.py            transport / encoding / error-path tests
test_save_adopt.py       regression: save_as must adopt the project filename
demo_build.py            end-to-end build + .RPP verification
```
