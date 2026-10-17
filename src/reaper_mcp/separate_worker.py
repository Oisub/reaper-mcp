"""Stem separation worker, run as a subprocess in the separator's own Python.

It imports only audio-separator and torch, never reaper_mcp, so it can live in
any environment that has those installed (see separate.py for configuration).

    python separate_worker.py <job.json>

job.json: {"model": <audio-separator filename>, "model_dir": ..., "device": "auto|cpu|cuda|xpu",
           "items": [{"src": <audio file>, "out": <folder for this song's stems>}]}

Progress goes to stdout as one JSON object per line. Each finished song gets a
`stems.json` manifest in its folder; a song that already has one is skipped.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

MANIFEST = "stems.json"


def emit(**event) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def pick_device(requested: str) -> str | None:
    """Return a torch device to force, or None to let audio-separator choose (CUDA/MPS/CPU)."""
    import torch

    if requested in ("", "auto"):
        if torch.cuda.is_available():
            return None
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            return "xpu"
        return None
    return requested


def force_device(device: str | None) -> None:
    # audio-separator only knows CUDA, MPS and DirectML; an Intel XPU has to be pinned by hand.
    if device is None:
        return
    import torch
    from audio_separator.separator import Separator

    original = Separator.setup_torch_device

    def setup(self, system_info):
        original(self, system_info)
        self.torch_device = torch.device(device)

    Separator.setup_torch_device = setup


def stem_name(path: Path) -> str:
    # audio-separator names outputs "<base>_(<Stem>)_<model>.<ext>"; keep the last "(...)".
    found = re.findall(r"_\(([^)]+)\)", path.stem)
    return (found[-1] if found else path.stem).strip().lower().replace(" ", "_")


def list_models() -> None:
    """Print audio-separator's whole model catalog as one flat JSON list."""
    from audio_separator.separator import Separator

    sep = Separator(info_only=True, log_level=logging.ERROR)
    rows = []
    for arch, models in sep.list_supported_model_files().items():
        for name, info in models.items():
            scores = info.get("scores") or {}
            rows.append({
                "arch": arch,
                "name": name,
                "filename": info.get("filename"),
                "stems": info.get("stems") or [],
                "sdr": {k: v.get("SDR") for k, v in scores.items() if isinstance(v, dict)},
            })
    print(json.dumps(rows, ensure_ascii=False))


def main() -> None:
    if sys.argv[1] == "--list":
        list_models()
        return
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    device = pick_device(job.get("device", "auto"))
    force_device(device)

    from audio_separator.separator import Separator

    sep = Separator(
        model_file_dir=job["model_dir"],
        output_format=job.get("format", "FLAC"),
        log_level=logging.WARNING,
    )
    t = time.time()
    sep.load_model(job["model"])
    emit(event="model_loaded", model=job["model"], device=str(sep.torch_device), seconds=round(time.time() - t, 1))

    items = job["items"]
    for i, item in enumerate(items, 1):
        src, out = Path(item["src"]), Path(item["out"])
        manifest = out / MANIFEST
        if manifest.exists():
            emit(event="skipped", index=i, total=len(items), src=str(src),
                 stems=json.loads(manifest.read_text(encoding="utf-8"))["stems"])
            continue
        emit(event="started", index=i, total=len(items), src=str(src))
        t = time.time()
        try:
            tmp = out / ".partial"
            tmp.mkdir(parents=True, exist_ok=True)
            sep.output_dir = str(tmp)
            sep.model_instance.output_dir = str(tmp)
            produced = [Path(p) if Path(p).is_absolute() else tmp / p for p in sep.separate(str(src))]
            stems = {}
            for p in produced:
                name = stem_name(p)
                target = out / (name + p.suffix)
                p.replace(target)
                stems[name] = str(target)
            tmp.rmdir()
            manifest.write_text(json.dumps({"src": str(src), "model": job["model"], "stems": stems},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
            emit(event="done", index=i, total=len(items), src=str(src), stems=stems,
                 seconds=round(time.time() - t, 1))
        except Exception as exc:  # keep going with the rest of a batch
            emit(event="error", index=i, total=len(items), src=str(src), error=f"{type(exc).__name__}: {exc}")
    emit(event="finished")


if __name__ == "__main__":
    main()
