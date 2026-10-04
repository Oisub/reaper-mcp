"""Load instrument presets by rewriting plugin state - no plugin GUI involved.

Why this exists
---------------
Spectrasonics (Keyscape, Trilian, Omnisphere) and BFD Player instantiate with
no sound loaded, and their patch browsers live inside the plugin GUI. REAPER's
preset list for them is just 2048 empty "Program N" slots. Opening the GUI from
the bridge is worse than useless: a plugin dialog blocks REAPER's main thread
and the bridge with it.

But both vendors keep their state as plain XML wrapped in REAPER's VST3 chunk,
and their patch files are the same XML. So a preset is loaded by splicing the
patch into the state and writing the track chunk back.

VST3 chunk layout in a .RPP (as REAPER writes it)
-------------------------------------------------
    <VST "VST3i: Name" file.vst3 0 "" id{guid} ""
      base64 header      - ends with u32 component_size, u32 1, u32 0xFFFF
      base64 body lines  - component state, 210 bytes per line
      base64 trailer     - program name block
    >
The header is the leading lines up to the first one that does not decode to
210 bytes. The component state starts with u32 (len - 16).

Spectrasonics state: 32-byte prefix (u32 at +24 is xml length incl. NUL), then
<SynthMaster> with 8 <SynthSubEngine> parts, each holding one <SynthEngine>.
A .prt_* patch is <XxxPart><SynthEngine>...</SynthEngine></XxxPart>. Factory
patches live inside .db containers: a <FileSystem> XML index of
(name, offset, size) followed by the payload.

BFD Player state: a <root> document identical in shape to a .bfdplayer preset,
NUL-terminated, followed by JUCE private data. No inner length field.
"""

from __future__ import annotations

import base64
import os
import re
import struct
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

LINE = 210

# --------------------------------------------------------------- vst3 chunks


@dataclass
class Vst3Block:
    start: int          # char offsets of the data lines inside the track chunk
    end: int
    indent: str
    header: bytes
    state: bytes
    trailer: bytes

    def encode(self) -> str:
        size = struct.pack("<I", len(self.state))
        header = self.header[:-12] + size + self.header[-8:]
        lines = [base64.b64encode(header[i:i + LINE]).decode() for i in range(0, len(header), LINE)]
        lines += [base64.b64encode(self.state[i:i + LINE]).decode() for i in range(0, len(self.state), LINE)]
        if self.trailer:
            lines.append(base64.b64encode(self.trailer).decode())
        return "\n".join(self.indent + line for line in lines)


FX_TAGS = ("<VST ", "<JS ", "<CLAP ", "<AU ", "<DX ", "<LV2 ", "<VIDEO_EFFECT", "<CONTAINER")


def fx_blocks(chunk: str) -> list[tuple[int, int, str]]:
    """(start, end, first line) of each FX block directly inside the FXCHAIN."""
    out = []
    depth = 0
    chain_depth = None
    pos = 0
    block_start = None
    for line in chunk.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("<"):
            depth += 1
            # exact tag: <FXCHAIN_REC (input FX) must not be mistaken for it
            if s.split()[0] == "<FXCHAIN" and chain_depth is None and not out:
                chain_depth = depth
            elif chain_depth is not None and depth == chain_depth + 1 and s.startswith(FX_TAGS):
                block_start = pos
        elif s == ">":
            if chain_depth is not None and depth == chain_depth + 1 and block_start is not None:
                out.append((block_start, pos + len(line), chunk[block_start:chunk.index("\n", block_start)].strip()))
                block_start = None
            if chain_depth is not None and depth == chain_depth:
                chain_depth = None
            depth -= 1
        pos += len(line)
    return out


def read_vst3(chunk: str, fx_index: int) -> Vst3Block:
    blocks = fx_blocks(chunk)
    if not 0 <= fx_index < len(blocks):
        raise ValueError("track has %d FX, no index %d" % (len(blocks), fx_index))
    b_start, b_end, first = blocks[fx_index]
    if not first.startswith("<VST ") or ".vst3" not in first.lower():
        raise ValueError("FX %d is not a VST3: %s" % (fx_index, first))
    body = chunk[b_start:b_end]
    nl = body.index("\n") + 1
    close = body.rstrip().rfind("\n")
    data = body[nl:close]
    raw = [ln for ln in data.split("\n") if ln.strip()]
    indent = re.match(r"[ \t]*", raw[0]).group(0)
    dec = [base64.b64decode(ln.strip()) for ln in raw]
    h = next(i for i, d in enumerate(dec) if len(d) != LINE) + 1
    header = b"".join(dec[:h])
    size = struct.unpack("<I", header[-12:-8])[0]
    rest = b"".join(dec[h:])
    state, trailer = rest[:size], rest[size:]
    if struct.unpack("<I", state[:4])[0] != len(state) - 16:
        raise ValueError("unexpected VST3 state layout")
    return Vst3Block(b_start + nl, b_start + close, indent, header, state, trailer)


def write_vst3(chunk: str, block: Vst3Block) -> str:
    return chunk[:block.start] + block.encode() + chunk[block.end:]


def _fix_outer(state: bytes) -> bytes:
    return struct.pack("<I", len(state) - 16) + state[4:]


# ------------------------------------------------------------ spectrasonics

SPECTRA = {
    "keyscape": ("Keyscape", "prt_key"),
    "trilian": ("Trilian", "prt_trl"),
    "omnisphere": ("Omnisphere", "prt_omn"),
}


def _resolve_lnk(path: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(New-Object -ComObject WScript.Shell).CreateShortcut('%s').TargetPath" % str(path)],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out) if out else None


def _spectra_root(name: str) -> Path | None:
    """STEAM (Omnisphere-era products) or SAGE (Stylus RMX): a folder or a .lnk to one."""
    env = os.environ.get("SPECTRASONICS_" + name)
    if env:
        return Path(env)
    for base in (Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Spectrasonics",
                 Path("/Library/Application Support/Spectrasonics")):
        if (base / name).is_dir():
            return base / name
        lnk = base / (name + ".lnk")
        if lnk.exists():
            target = _resolve_lnk(lnk)
            if target and target.is_dir():
                return target
    return None


@lru_cache(maxsize=1)
def steam_dir() -> Path | None:
    return _spectra_root("STEAM")


@lru_cache(maxsize=1)
def sage_dir() -> Path | None:
    return _spectra_root("SAGE")


@lru_cache(maxsize=32)
def db_index(path: Path) -> tuple[int, tuple[tuple[str, int, int], ...]]:
    """Read a Spectrasonics .db <FileSystem> index: (payload base, files)."""
    buf = b""
    with open(path, "rb") as f:
        while b"</FileSystem>" not in buf:
            more = f.read(1 << 20)
            if not more:
                raise ValueError("no <FileSystem> index in " + str(path))
            buf += more
    end = buf.index(b"</FileSystem>") + len(b"</FileSystem>")
    base = end
    while buf[base:base + 1] in (b"\n", b"\r", b"\0"):
        base += 1
    xml = buf[:end].decode("utf-8", "replace")
    stack: list[str] = []
    files = []
    for m in re.finditer(r'<DIR name="([^"]*)"|</DIR>|<FILE name="([^"]*)" offset="(\d+)" size="(\d+)"', xml):
        tok = m.group(0)
        if tok.startswith("<DIR"):
            stack.append(_unescape(m.group(1)))
        elif tok == "</DIR>":
            stack.pop()
        else:
            files.append(("/".join(stack + [_unescape(m.group(2))]), int(m.group(3)), int(m.group(4))))
    return base, tuple(files)


def _unescape(s: str) -> str:
    return (s.replace("&#39;", "'").replace("&quot;", '"').replace("&#34;", '"')
             .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


@dataclass
class Preset:
    instrument: str
    name: str           # display name, e.g. "Rhodes - Classic Suitcase Lush"
    path: str           # category path inside the library
    source: str         # file on disk (.db container or loose file)
    offset: int = -1    # -1: whole file
    size: int = -1

    def read(self) -> bytes:
        with open(self.source, "rb") as f:
            if self.offset < 0:
                return f.read()
            f.seek(self.offset)
            return f.read(self.size)


def _spectra_presets(instrument: str) -> list[Preset]:
    steam = steam_dir()
    if steam is None:
        raise FileNotFoundError("Spectrasonics STEAM folder not found (set SPECTRASONICS_STEAM)")
    folder, ext = SPECTRA[instrument]
    patches = steam / folder / "Settings Library" / "Patches"
    if not patches.is_dir():
        raise FileNotFoundError("%s library not installed: %s missing" % (folder, patches))
    out = []
    for db in sorted(patches.rglob("*.db")):
        # an expansion that is registered but not installed ships a 0-byte .db
        if db.stat().st_size == 0:
            continue
        try:
            base, files = db_index(db)
        except ValueError:
            continue
        for name, off, size in files:
            if name.endswith("." + ext):
                out.append(Preset(instrument, name.rsplit("/", 1)[-1][: -len(ext) - 1].strip(),
                                  db.stem + "/" + name, str(db), base + off, size))
    for f in sorted(patches.rglob("*." + ext)):
        out.append(Preset(instrument, f.stem.strip(), str(f.relative_to(patches)), str(f)))
    return out


def _spectra_load(state: bytes, patch: bytes, part: int) -> tuple[bytes, str]:
    xml_len = struct.unpack("<I", state[24:28])[0]
    xml = state[32:32 + xml_len - 1]
    tail = state[32 + xml_len:]
    engine = re.search(rb"<SynthEngine >.*</SynthEngine>", patch, re.S)
    if not engine:
        raise ValueError("patch has no <SynthEngine>")
    pos = -1
    for _ in range(part):
        pos = xml.find(b"<SynthSubEngine >", pos + 1)
        if pos < 0:
            raise ValueError("state has no part %d" % part)
    a = xml.index(b"<SynthEngine >", pos)
    e = xml.index(b"</SynthEngine>", a) + len(b"</SynthEngine>")
    new_xml = xml[:a] + engine.group(0) + xml[e:]
    entry = re.search(rb'<ENTRYDESCR  name="([^"]*)"  library="([^"]*)"', patch)
    label = ""
    if entry and part == 1:
        label = entry.group(1).decode("utf-8", "replace")
        new_xml = re.sub(rb'<ENTRYDESCR  name="[^"]*"  library="[^"]*"',
                         lambda _m: b'<ENTRYDESCR  name="' + entry.group(1) + b'"  library="' + entry.group(2) + b'"',
                         new_xml, count=1)
    payload = new_xml + b"\0"
    new = state[:24] + struct.pack("<I", len(payload)) + state[28:32] + payload + tail
    return _fix_outer(new), label


def spectra_current(state: bytes) -> list[str]:
    """Patch names currently loaded, per part (empty string = nothing)."""
    names = []
    for sub in re.finditer(rb"<SynthSubEngine >(.*?)</SynthSubEngine>", state, re.S):
        m = re.search(rb'<ENTRYDESCR  name="([^"]*)"', sub.group(1))
        names.append(m.group(1).decode("utf-8", "replace") if m else "")
    return names


# --------------------------------------------------------------- Stylus RMX
# State: u32 len-16, u32 1, then a <StylusRMXMaster> document, NUL padded.
# A .mlt_rmx multi is the same document, so loading one is a whole swap.
# (.kit_rmx kits are single-part and a different shape - not supported yet.)


def _rmx_presets() -> list[Preset]:
    sage = sage_dir()
    if sage is None:
        raise FileNotFoundError("Spectrasonics SAGE folder not found (set SPECTRASONICS_SAGE)")
    root = sage / "Stylus RMX" / "Patches" / "Multis"
    if not root.is_dir():
        raise FileNotFoundError("Stylus RMX library not installed: %s missing" % root)
    return [Preset("rmx", f.stem.strip(), str(f.relative_to(root)).replace("\\", "/"), str(f))
            for f in sorted(root.rglob("*.mlt_rmx"))]


def _swap_root(state: bytes, doc: bytes, tag: bytes) -> bytes:
    xi = state.index(b"<" + tag)
    xe = state.index(b"</" + tag + b">") + len(tag) + 3
    pi = doc.index(b"<" + tag)
    pe = doc.index(b"</" + tag + b">") + len(tag) + 3
    return _fix_outer(state[:xi] + doc[pi:pe] + state[xe:])


def _rmx_load(state: bytes, multi: bytes) -> tuple[bytes, str]:
    m = re.search(rb'MultiName="([^"]*)"', multi[:2000])
    return _swap_root(state, multi, b"StylusRMXMaster"), (m.group(1).decode("utf-8", "replace") if m else "")


# --------------------------------------------------------------------- BFD


def _bfd_data_paths() -> list[Path]:
    cfg = Path(os.environ.get("APPDATA", "")) / "BFD Drums" / "BFDPlayer" / "DataPaths.xml"
    if not cfg.exists():
        return []
    text = cfg.read_text("utf-8", "replace")
    return [Path(p) for p in re.findall(r'<DataPath path="([^"]+)"\s+enabled="true"', text)]


def _bfd_presets() -> list[Preset]:
    out = []
    for root in _bfd_data_paths():
        for f in sorted((root / "Presets").glob("*.bfdplayer")):
            out.append(Preset("bfd", f.stem, f.name, str(f)))
    if not out:
        raise FileNotFoundError("no BFD Player presets found (DataPaths.xml)")
    return out


def _bfd_load(state: bytes, preset: bytes) -> tuple[bytes, str]:
    pi = preset.index(b"<root")
    m = re.search(rb'PresetName="([^"]*)"', preset[pi:pi + 2000])
    return _swap_root(state, preset, b"root"), (m.group(1).decode() if m else "")


# ------------------------------------------------------------------- public

INSTRUMENTS = ("keyscape", "trilian", "omnisphere", "rmx", "bfd")


def instrument_for(fx_name: str) -> str | None:
    n = fx_name.lower()
    for key in ("keyscape", "trilian", "omnisphere"):
        if key in n:
            return key
    if "stylus rmx" in n:
        return "rmx"
    if "bfd" in n:
        return "bfd"
    return None


def list_presets(instrument: str) -> list[Preset]:
    if instrument == "bfd":
        return _bfd_presets()
    if instrument == "rmx":
        return _rmx_presets()
    if instrument in SPECTRA:
        return _spectra_presets(instrument)
    raise ValueError("unsupported instrument %r; supported: %s" % (instrument, ", ".join(INSTRUMENTS)))


def find_preset(instrument: str, query: str) -> Preset:
    presets = list_presets(instrument)
    q = query.strip().lower()
    exact = [p for p in presets if p.name.lower() == q]
    if len(exact) >= 1:
        return exact[0]
    hits = [p for p in presets if q in p.name.lower() or q in p.path.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LookupError("no %s preset matches %r" % (instrument, query))
    raise LookupError("%d %s presets match %r, be more specific: %s"
                      % (len(hits), instrument, query, "; ".join(p.name for p in hits[:15])))


def apply_preset(chunk: str, fx_index: int, preset: Preset, part: int = 1) -> tuple[str, str]:
    block = read_vst3(chunk, fx_index)
    data = preset.read()
    if preset.instrument == "bfd":
        block.state, label = _bfd_load(block.state, data)
    elif preset.instrument == "rmx":
        block.state, label = _rmx_load(block.state, data)
    else:
        block.state, label = _spectra_load(block.state, data, part)
    return write_vst3(chunk, block), label or preset.name


def current_preset(chunk: str, fx_index: int, instrument: str) -> str | list[str]:
    state = read_vst3(chunk, fx_index).state
    if instrument == "bfd":
        m = re.search(rb'PresetName="([^"]*)"', state)
        return m.group(1).decode() if m else ""
    if instrument == "rmx":
        m = re.search(rb'MultiName="([^"]*)"', state)
        return m.group(1).decode("utf-8", "replace") if m else ""
    return spectra_current(state)
