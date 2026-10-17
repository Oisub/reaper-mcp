"""Stem separation jobs that load their results back into REAPER.

The separation itself runs in a separate Python that has audio-separator (and a
torch build for the user's GPU) installed, via ``separate_worker.py``. That
keeps reaper-mcp's own environment small, and lets the heavy environment be
whatever the machine needs: CUDA, Intel XPU, or plain CPU.

Configuration, first match wins:
  python     REAPER_MCP_SEPARATOR_PYTHON, separator.json "python", this interpreter
  model_dir  REAPER_MCP_SEPARATOR_MODELS, separator.json "model_dir", ~/.reaper-mcp/separator-models
where separator.json is ``~/.reaper-mcp/separator.json``.

Jobs run in a background thread, because one song takes minutes; each song is
loaded into REAPER as soon as it finishes, so nobody has to poll for it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CONFIG_FILE = Path.home() / ".reaper-mcp" / "separator.json"
WORKER = Path(__file__).with_name("separate_worker.py")
AUDIO_EXT = {".wav", ".flac", ".mp3", ".m4a", ".aiff", ".aif", ".ogg", ".opus"}

# Track order when stems are laid out in REAPER; unknown stems go last.
STEM_ORDER = ["vocals", "instrumental", "drums", "bass", "guitar", "piano", "other"]


@dataclass(frozen=True)
class Model:
    alias: str
    filename: str
    stems: tuple[str, ...]
    note: str
    # files to fetch from a mirror when audio-separator's own download source is gone
    mirror: dict[str, str] = field(default_factory=dict)


_SW_MIRROR = "https://huggingface.co/enerjazzer/BS-ROFO-SW-Fixed/resolve/main/"

MODELS = [
    Model("bs_roformer_sw", "BS-Roformer-SW.ckpt",
          ("vocals", "drums", "bass", "guitar", "piano", "other"),
          "Default. Best public multi-stem model (MVSep Multisong SDR: bass 14.62, drums 14.11, "
          "vocals 11.30, guitar 9.05, piano 7.83).",
          {"BS-Roformer-SW.ckpt": _SW_MIRROR + "BS-Rofo-SW-Fixed.ckpt",
           "BS-Roformer-SW.yaml": _SW_MIRROR + "BS-Rofo-SW-Fixed.yaml"}),
    Model("htdemucs_ft", "htdemucs_ft.yaml", ("vocals", "drums", "bass", "other"),
          "Demucs v4 fine-tuned, 4 stems (bass SDR ~12.0). Slower than SW and clearly worse."),
    Model("htdemucs", "htdemucs.yaml", ("vocals", "drums", "bass", "other"),
          "Demucs v4, 4 stems. The fastest usable model; good for quick previews."),
    Model("htdemucs_6s", "htdemucs_6s.yaml", ("vocals", "drums", "bass", "guitar", "piano", "other"),
          "Demucs v4, 6 stems. Weak guitar/piano; prefer bs_roformer_sw."),
    Model("bs_roformer_vocals", "model_bs_roformer_ep_317_sdr_12.9755.ckpt", ("vocals", "instrumental"),
          "BS-RoFormer viperx 1297: vocals / instrumental."),
    Model("mel_roformer_vocals", "vocals_mel_band_roformer.ckpt", ("vocals", "other"),
          "Mel-Band RoFormer (Kim): vocals / other."),
    Model("karaoke", "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt", ("vocals", "instrumental"),
          "Lead vocal vs everything else (backing vocals stay in the instrumental)."),
    Model("denoise", "denoise_mel_band_roformer_aufr33_aggr_sdr_27.9768.ckpt", ("dry", "other"),
          "Noise removal, aggressive."),
]
DEFAULT_MODEL = "bs_roformer_sw"


# --------------------------------------------------------------------- config


def config() -> dict[str, str]:
    file_cfg: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        file_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    python = os.environ.get("REAPER_MCP_SEPARATOR_PYTHON") or file_cfg.get("python") or sys.executable
    model_dir = (os.environ.get("REAPER_MCP_SEPARATOR_MODELS") or file_cfg.get("model_dir")
                 or str(Path.home() / ".reaper-mcp" / "separator-models"))
    return {"python": python, "model_dir": model_dir, "config_file": str(CONFIG_FILE)}


def resolve_model(name: str) -> Model:
    key = (name or DEFAULT_MODEL).strip()
    for m in MODELS:
        if key.lower() in (m.alias, m.filename.lower()):
            return m
    # any other audio-separator filename is passed through as-is
    return Model(Path(key).stem, key, (), "audio-separator catalog model")


def model_on_disk(m: Model, model_dir: str) -> bool:
    return (Path(model_dir) / m.filename).exists()


def fetch_mirror(m: Model, model_dir: str) -> list[str]:
    """Download a model from its mirror if missing. Returns the files fetched."""
    fetched = []
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    for name, url in m.mirror.items():
        target = Path(model_dir) / name
        if target.exists():
            continue
        part = target.with_suffix(target.suffix + ".part")
        urllib.request.urlretrieve(url, part)
        part.replace(target)
        fetched.append(name)
    return fetched


def catalog(query: str = "") -> list[dict[str, Any]]:
    """audio-separator's whole catalog, filtered by a substring of name/filename/stem."""
    cfg = config()
    proc = subprocess.run([cfg["python"], str(WORKER), "--list"], capture_output=True, text=True,
                          encoding="utf-8", timeout=180)
    if proc.returncode != 0:
        raise RuntimeError("model listing failed: " + (proc.stderr.strip().splitlines() or ["?"])[-1])
    rows = json.loads(proc.stdout)
    q = query.lower()
    return [r for r in rows if not q or q in (r["name"] + " " + r["filename"] + " " + " ".join(r["stems"])).lower()]


# ----------------------------------------------------------------------- jobs


@dataclass
class Item:
    src: str
    out: str
    # where the source sat in REAPER (selected_item sources): used to align the stems
    placement: dict[str, Any] | None = None
    state: str = "queued"
    stems: dict[str, str] = field(default_factory=dict)
    seconds: float | None = None
    error: str = ""
    loaded: Any = None


@dataclass
class Job:
    id: str
    model: Model
    device: str
    load: str
    audition: str
    items: list[Item]
    state: str = "starting"
    device_used: str = ""
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    proc: Any = None

    def summary(self) -> dict[str, Any]:
        done = sum(i.state in ("done", "skipped") for i in self.items)
        return {
            "job_id": self.id,
            "state": self.state,
            "model": self.model.alias,
            "device": self.device_used or self.device,
            "load": self.load,
            "progress": f"{done}/{len(self.items)}",
            "elapsed_s": round((self.finished or time.time()) - self.started),
            "error": self.error or None,
            "items": [
                {k: v for k, v in {
                    "src": i.src, "state": i.state, "out": i.out, "seconds": i.seconds,
                    "stems": sorted(i.stems) or None, "error": i.error or None, "loaded": i.loaded,
                }.items() if v is not None}
                for i in self.items
            ],
        }


JOBS: dict[str, Job] = {}


def song_dir(root: Path, src: Path, model: Model) -> Path:
    return root / src.stem / model.alias


def expand_sources(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXT)
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(str(p))
    return files


def start(job: Job, call: Callable[..., dict[str, Any]], loader: Callable[[Job, Item], Any]) -> None:
    JOBS[job.id] = job
    threading.Thread(target=_run, args=(job, call, loader), daemon=True).start()


def _run(job: Job, call: Callable[..., dict[str, Any]], loader: Callable[[Job, Item], Any]) -> None:
    cfg = config()
    try:
        if job.model.mirror and not model_on_disk(job.model, cfg["model_dir"]):
            job.state = "downloading model"
            fetch_mirror(job.model, cfg["model_dir"])
        spec = {
            "model": job.model.filename,
            "model_dir": cfg["model_dir"],
            "device": job.device,
            "items": [{"src": i.src, "out": i.out} for i in job.items],
        }
        fd, spec_path = tempfile.mkstemp(suffix=".json", prefix="reaper-mcp-sep-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)

        job.state = "loading model"
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen([cfg["python"], str(WORKER), spec_path], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                env=env, creationflags=flags)
        job.proc = proc
        stderr_tail: list[str] = []
        threading.Thread(target=_drain, args=(proc.stderr, stderr_tail), daemon=True).start()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("event")
            item = job.items[ev["index"] - 1] if "index" in ev else None
            if kind == "model_loaded":
                job.state, job.device_used = "running", ev.get("device", "")
            elif kind == "started":
                item.state = "separating"
            elif kind in ("done", "skipped"):
                item.state, item.stems, item.seconds = kind, ev.get("stems", {}), ev.get("seconds")
                if job.load != "none":
                    try:
                        item.loaded = loader(job, item)
                    except Exception as exc:
                        item.loaded = {"status": "error", "error": str(exc)}
            elif kind == "error":
                item.state, item.error = "error", ev.get("error", "")
        proc.wait()
        os.unlink(spec_path)
        if job.state == "cancelling":
            job.state = "cancelled"
        elif proc.returncode != 0:
            job.state = "failed"
            job.error = "worker exited with %d: %s" % (proc.returncode, "".join(stderr_tail[-5:]).strip())
        else:
            job.state = "finished" if all(i.state != "error" for i in job.items) else "finished with errors"
    except Exception as exc:
        job.state, job.error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        job.finished = time.time()


def _drain(stream, tail: list[str]) -> None:
    # keep stderr flowing (a full pipe would stall the worker) and remember its end for errors
    for line in stream:
        tail.append(line)
        del tail[:-40]


def cancel(job: Job) -> None:
    if job.proc and job.proc.poll() is None:
        job.state = "cancelling"
        job.proc.kill()


def ordered_stems(stems: dict[str, str]) -> list[tuple[str, str]]:
    rank = {s: i for i, s in enumerate(STEM_ORDER)}
    return sorted(stems.items(), key=lambda kv: (rank.get(kv[0], len(rank)), kv[0]))


def new_id() -> str:
    return uuid.uuid4().hex[:8]
