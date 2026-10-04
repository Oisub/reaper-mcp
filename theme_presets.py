"""Theme presets for REAPER's Default 7.0 theme, applied through the bridge.

The Default 7 theme exposes 280 parameters via ThemeLayout_GetParameter /
ThemeLayout_SetParameter. The stock Theme Adjuster is a GUI over these; driving
them from here means a preset can be applied and compared in one call, and the
baseline can always be restored.

Usage:
    uv run python theme_presets.py            # list presets
    uv run python theme_presets.py compact    # apply one
    uv run python theme_presets.py baseline   # restore captured defaults
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from reaper_mcp.server import eval_lua

BASELINE_FILE = Path(__file__).parent / "theme_baseline.json"

# index -> (name, description) for the parameters these presets touch
PARAMS = {
    1: "textBrightness",
    3: "customColorDepthParam",
    4: "selectStrength",
    7: "sectionMargin",
    8: "xMargin",
    9: "yMargin",
    12: "tcpDivOpacity",
    13: "tcpBgColR",
    14: "tcpBgColG",
    15: "tcpBgColB",
    47: "LayoutA-tcpLabelSize",
    51: "LayoutA-tcpMeterWidth",
    52: "LayoutA-tcpMeterBorder",
    152: "mcpDivOpacity",
    153: "mcpBgColR",
    154: "mcpBgColG",
    155: "mcpBgColB",
}

# values captured from a clean REAPER 7.81 / Default_7.0 (theme version 251214)
STOCK = {
    1: 100, 3: 30, 4: 25, 7: 6, 8: 6, 9: 6, 12: 80,
    13: 129, 14: 137, 15: 137,
    47: 4, 51: 20, 52: 2,
    152: 80, 153: 129, 154: 137, 155: 137,
}

# Two independent axes: how dense the panels are, and how they are coloured.
# Keeping them separate is what makes combinations like compact-dark trivial.

_DENSITY_COMPACT = {7: 2, 8: 3, 9: 3, 47: 3, 51: 14, 52: 1}
_DENSITY_AIRY = {7: 11, 8: 9, 9: 9, 47: 4, 51: 26, 52: 3}

_COLOR_SOFT_DARK = {
    13: 38, 14: 41, 15: 44,      # TCP background
    153: 34, 154: 37, 155: 40,   # MCP background
    12: 28, 152: 28,             # dividers almost invisible
    3: 48,                       # track colours carry more of the signal
    4: 30,
    1: 92,
}
_COLOR_CONTRAST = {1: 132, 3: 62, 4: 48, 12: 120, 152: 120}

PRESETS: dict[str, tuple[str, dict[int, int]]] = {
    "baseline": ("REAPER 7 原样", dict(STOCK)),
    "compact": (
        "紧凑高密度 — 边距收紧、分隔线淡化、表更窄，一屏装更多轨",
        {**_DENSITY_COMPACT, 12: 40, 152: 40, 1: 110},
    ),
    "airy": (
        "通透留白 — 边距放开、字稍大，适合大屏少轨精修",
        {**_DENSITY_AIRY, 12: 55, 152: 55, 1: 100},
    ),
    "soft-dark": (
        "深色低对比 — 面板压暗、分隔线几乎隐形、轨道色更鲜明，长时间盯屏友好",
        {**_COLOR_SOFT_DARK, 51: 18, 52: 1},
    ),
    "contrast": (
        "高对比鲜明 — 文字更亮、轨道色更重、选中更明显",
        {**_COLOR_CONTRAST, 51: 22, 52: 2},
    ),
    "compact-dark": (
        "紧凑 + 深色 — 密度取 compact，配色取 soft-dark，文字稍提亮补偿暗背景",
        {**_COLOR_SOFT_DARK, **_DENSITY_COMPACT, 1: 105, 12: 30, 152: 30},
    ),
    "airy-dark": (
        "通透 + 深色 — 深色配色但留白放开",
        {**_COLOR_SOFT_DARK, **_DENSITY_AIRY, 1: 100, 12: 32, 152: 32},
    ),
}

APPLY_LUA = """
local sets = { __SETS__ }
for _, p in ipairs(sets) do
  reaper.ThemeLayout_SetParameter(p[1], p[2], true)
end
reaper.ThemeLayout_RefreshAll()
local back = {}
for _, p in ipairs(sets) do
  local name, _, val = reaper.ThemeLayout_GetParameter(p[1])
  back[#back+1] = name .. "=" .. tostring(val)
end
return back
"""


def capture_baseline() -> dict[int, int]:
    """Read the live values of every parameter the presets touch."""
    idx = sorted(PARAMS)
    code = "local out = {}\n"
    code += "for _, i in ipairs({%s}) do\n" % ",".join(str(i) for i in idx)
    code += "  local _, _, v = reaper.ThemeLayout_GetParameter(i)\n"
    code += "  out[tostring(i)] = v\nend\nreturn out"
    r = eval_lua(code=code, undo_label="")
    if r.get("status") != "ok":
        raise SystemExit("could not read theme parameters: " + str(r.get("error")))
    return {int(k): v for k, v in (r.get("result") or {}).items()}


def apply(values: dict[int, int]) -> dict:
    sets = ",".join("{%d,%d}" % (i, v) for i, v in sorted(values.items()))
    return eval_lua(code=APPLY_LUA.replace("__SETS__", sets), undo_label="", timeout=30)


def main() -> int:
    if not BASELINE_FILE.exists():
        base = capture_baseline()
        BASELINE_FILE.write_text(json.dumps(base, indent=2), encoding="utf-8")
        print("baseline captured -> " + BASELINE_FILE.name)

    if len(sys.argv) < 2:
        print("presets:")
        for key, (desc, _) in PRESETS.items():
            print("  %-10s %s" % (key, desc))
        return 0

    key = sys.argv[1]
    if key == "baseline" and BASELINE_FILE.exists():
        values = {int(k): v for k, v in json.loads(BASELINE_FILE.read_text()).items()}
        desc = "回滚到捕获的基线"
    elif key in PRESETS:
        desc, overrides = PRESETS[key]
        # start from stock so presets do not accumulate on each other
        values = dict(STOCK)
        values.update(overrides)
    else:
        print("unknown preset: " + key)
        return 1

    print(desc)
    r = apply(values)
    print(json.dumps(r, indent=2, ensure_ascii=False)[:1200])
    return 0 if r.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
