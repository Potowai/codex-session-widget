"""Codex Session Widget — a tiny floating Windows 11 desktop widget that shows
the current OpenAI Codex 5-hour usage window and a live "resets in" countdown.

Standalone, no network: it reconstructs the rolling 5-hour usage window from
~/.codex/sessions/**/*.jsonl transcript timestamps. No external dependency
beyond the Python standard library (Tkinter). Pillow is only used by build.ps1
to render a PNG snapshot for the README.

Run:    pythonw SessionWidget.py
Snap:   python SessionWidget.py --snapshot [path.png] [--expanded]
"""

import glob
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import font as tkfont
from typing import List, Optional, Tuple

try:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
except Exception:
    pass


# --- palette (OpenAI brand) --------------------------------------------------

IVORY = "#F7F7F8"
GREEN = "#10A37F"
GREEN_DARK = "#0D8A6A"
NEAR_BLACK = "#202123"
MUTED = "#6E6E80"
AMBER = "#C2703D"
URGENT_RED = "#BF4D43"
CODEX_ACCENT = "#3941FF"
TRANSPARENT = "#F0ABCD"  # magic key for -transparentcolor (must not appear in the card)

WINDOW_MS = 5 * 3600 * 1000
LOOKBACK_S = 36 * 3600
WEEK_S = 7 * 24 * 3600

CODEX_HOME = os.path.join(os.path.expanduser("~"), ".codex")
SESSIONS_DIR = os.path.join(CODEX_HOME, "sessions")
STATE_FILE = os.path.join(CODEX_HOME, "widget-state.json")
ICON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "codex-icon.ico")


# --- session reconstruction --------------------------------------------------

@dataclass
class SessionState:
    active: bool = False
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    last_activity_ms: Optional[int] = None
    week_active: Optional[float] = None
    used_5h_pct: Optional[float] = None
    used_7d_pct: Optional[float] = None
    reset_5h_ms: Optional[int] = None
    reset_7d_ms: Optional[int] = None
    plan_type: Optional[str] = None
    source: str = "transcript"


def _parse_iso(ts: str) -> Optional[float]:
    try:
        s = ts.replace("Z", "+00:00")
        import datetime as dt
        return dt.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _activity_timestamps(now: float) -> List[int]:
    since_ms = int((now - LOOKBACK_S) * 1000)
    stamps: List[int] = []
    if not os.path.isdir(SESSIONS_DIR):
        return stamps
    for f in glob.glob(os.path.join(SESSIONS_DIR, "**", "*.jsonl"), recursive=True):
        name = os.path.basename(f).lower()
        if "agent" in name:
            continue
        try:
            if os.path.getmtime(f) < now - LOOKBACK_S:
                continue
        except OSError:
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"timestamp"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    ts = obj.get("timestamp")
                    if not isinstance(ts, str):
                        continue
                    s = _parse_iso(ts)
                    if s is None:
                        continue
                    ms = int(s * 1000)
                    if ms >= since_ms:
                        stamps.append(ms)
        except OSError:
            continue
    stamps.sort()
    return stamps


def _chain_blocks(stamps: List[int], window_ms: int) -> List[Tuple[int, int]]:
    if not stamps:
        return []
    s, e = stamps[0], stamps[0] + window_ms
    blocks: List[Tuple[int, int]] = []
    for ms in stamps:
        if ms > e:
            blocks.append((s, e))
            s, e = ms, ms + window_ms
    blocks.append((s, e))
    return blocks


def _latest_rate_limits(now: float) -> Optional[dict]:
    """Scan transcripts for the most recent token_count event carrying
    rate_limits. Returns the rate_limits dict + its timestamp, or None."""
    since_ms = int((now - LOOKBACK_S) * 1000)
    best_ms = -1
    best = None
    if not os.path.isdir(SESSIONS_DIR):
        return None
    for f in glob.glob(os.path.join(SESSIONS_DIR, "**", "*.jsonl"), recursive=True):
        name = os.path.basename(f).lower()
        if "agent" in name:
            continue
        try:
            if os.path.getmtime(f) < now - LOOKBACK_S:
                continue
        except OSError:
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"token_count"' not in line or '"rate_limits"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("type") != "event_msg":
                        continue
                    pl = obj.get("payload") or {}
                    if pl.get("type") != "token_count":
                        continue
                    rl = pl.get("rate_limits")
                    if not rl or not rl.get("primary"):
                        continue
                    ts = obj.get("timestamp")
                    s = _parse_iso(ts) if isinstance(ts, str) else None
                    if s is None:
                        continue
                    ms = int(s * 1000)
                    if ms >= since_ms and ms > best_ms:
                        best_ms = ms
                        best = rl
        except OSError:
            continue
    return best


def compute(now: Optional[float] = None) -> SessionState:
    if now is None:
        now = time.time()
    now_ms = int(now * 1000)

    rl = _latest_rate_limits(now)
    if rl:
        prim = rl.get("primary") or {}
        sec = rl.get("secondary") or {}
        reset_5h_s = prim.get("resets_at")
        used_5h = prim.get("used_percent")
        reset_7d_s = sec.get("resets_at")
        used_7d = sec.get("used_percent")
        reset_5h_ms = int(reset_5h_s * 1000) if isinstance(reset_5h_s, (int, float)) else None
        reset_7d_ms = int(reset_7d_s * 1000) if isinstance(reset_7d_s, (int, float)) else None
        active = bool(reset_5h_ms and reset_5h_ms > now_ms)
        start_ms = (reset_5h_ms - WINDOW_MS) if reset_5h_ms else None
        return SessionState(
            active=active,
            start_ms=start_ms,
            end_ms=reset_5h_ms,
            last_activity_ms=None,
            week_active=(used_7d / 100.0) if isinstance(used_7d, (int, float)) else None,
            used_5h_pct=float(used_5h) if isinstance(used_5h, (int, float)) else None,
            used_7d_pct=float(used_7d) if isinstance(used_7d, (int, float)) else None,
            reset_5h_ms=reset_5h_ms,
            reset_7d_ms=reset_7d_ms,
            plan_type=rl.get("plan_type"),
            source="rate_limits",
        )

    stamps = _activity_timestamps(now)
    if not stamps:
        return SessionState(active=False, week_active=0.0)
    blocks = _chain_blocks(stamps, WINDOW_MS)
    s, e = blocks[-1]
    active = now_ms < e
    week_start = now_ms - WEEK_S * 1000
    week_stamps = [ms for ms in stamps if ms >= week_start]
    covered = 0
    for bs, be in _chain_blocks(week_stamps, WINDOW_MS):
        a = max(bs, week_start)
        b = min(be, now_ms)
        if b > a:
            covered += b - a
    week_active = max(0.0, min(1.0, covered / (WEEK_S * 1000)))
    return SessionState(active=active, start_ms=s, end_ms=e,
                        last_activity_ms=stamps[-1], week_active=week_active)


# --- drawing helpers ---------------------------------------------------------

def round_rect_pts(x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    n = 10
    pts = []
    cx = [x + w - r, x + w - r, x + r, x + r]
    cy = [y + r, y + h - r, y + h - r, y + r]
    start = [270, 0, 90, 180]
    for i in range(4):
        for j in range(n + 1):
            a = math.radians(start[i] + j * 90 / n)
            pts.append(cx[i] + r * math.cos(a))
            pts.append(cy[i] + r * math.sin(a))
    return pts


class Canvas:
    def __init__(self, tk_canvas, scale):
        self.c = tk_canvas
        self.S = scale

    def rrect(self, x, y, w, h, r, **kw):
        pts = round_rect_pts(x * self.S, y * self.S, w * self.S, h * self.S, r * self.S)
        return self.c.create_polygon(pts, smooth=False, **kw)

    def rect(self, x, y, w, h, **kw):
        return self.c.create_rectangle(x * self.S, y * self.S,
                                       (x + w) * self.S, (y + h) * self.S, **kw)

    def oval(self, cx, cy, rx, ry, **kw):
        return self.c.create_oval((cx - rx) * self.S, (cy - ry) * self.S,
                                  (cx + rx) * self.S, (cy + ry) * self.S, **kw)

    def line(self, x1, y1, x2, y2, w, **kw):
        return self.c.create_line(x1 * self.S, y1 * self.S,
                                  x2 * self.S, y2 * self.S,
                                  width=w * self.S, **kw)

    def poly(self, pts, **kw):
        spts = []
        for px, py in pts:
            spts.append(px * self.S)
            spts.append(py * self.S)
        return self.c.create_polygon(spts, **kw)

    def polyline(self, pts, w, **kw):
        spts = []
        for px, py in pts:
            spts.append(px * self.S)
            spts.append(py * self.S)
        return self.c.create_line(spts, width=w * self.S, **kw)

    def text(self, x, y, s, **kw):
        return self.c.create_text(x * self.S, y * self.S, text=s, **kw)

    def poly_abs(self, pts, **kw):
        spts = []
        for x, y in pts:
            spts.append(x * self.S)
            spts.append(y * self.S)
        return self.c.create_polygon(spts, **kw)


# --- Codex mascot (rendered from the official codex-color.svg) ---------------
#
# Tkinter has no SVG support, so we parse the path data once at import, flatten
# the bezier/arc segments into polygons, and paint the bloom with a banded
# vertical gradient (#B1A7FF -> #7A9DFF -> #3941FF). The ">_" prompt subpaths
# are drawn in white on top to punch through the bloom as negative space.

_CODEX_PATH_D = (
    "M9.064 3.344a4.578 4.578 0 012.285-.312c1 .115 1.891.54 2.673 1.275.01.01.024.017.037.021a.09.09 0 00.043 0 4.55 4.55 0 013.046.275l.047.022.116.057a4.581 4.581 0 012.188 2.399c.209.51.313 1.041.315 1.595a4.24 4.24 0 01-.134 1.223.123.123 0 00.03.115c.594.607.988 1.33 1.183 2.17.289 1.425-.007 2.71-.887 3.854l-.136.166a4.548 4.548 0 01-2.201 1.388.123.123 0 00-.081.076c-.191.551-.383 1.023-.74 1.494-.9 1.187-2.222 1.846-3.711 1.838-1.187-.006-2.239-.44-3.157-1.302a.107.107 0 00-.105-.024c-.388.125-.78.143-1.204.138a4.441 4.441 0 01-1.945-.466 4.544 4.544 0 01-1.61-1.335c-.152-.202-.303-.392-.414-.617a5.81 5.81 0 01-.37-.961 4.582 4.582 0 01-.014-2.298.124.124 0 00.006-.056.085.085 0 00-.027-.048 4.467 4.467 0 01-1.034-1.651 3.896 3.896 0 01-.251-1.192 5.189 5.189 0 01.141-1.6c.337-1.112.982-1.985 1.933-2.618.212-.141.413-.251.601-.33.215-.089.43-.164.646-.227a.098.098 0 00.065-.066 4.51 4.51 0 01.829-1.615 4.535 4.535 0 011.837-1.388z"
    "m3.482 10.565a.637.637 0 000 1.272h3.636a.637.637 0 100-1.272h-3.636z"
    "M8.462 9.23a.637.637 0 00-1.106.631l1.272 2.224-1.266 2.136a.636.636 0 101.095.649l1.454-2.455a.636.636 0 00.005-.64L8.462 9.23z"
)

_GRAD_STOPS = ((0.0, (0x7C, 0x3A, 0xED)), (1.0, (0xEC, 0x48, 0x99)))


def _grad_color(t: float) -> str:
    t = max(0.0, min(1.0, t))
    for k in range(len(_GRAD_STOPS) - 1):
        t0, c0 = _GRAD_STOPS[k]
        t1, c1 = _GRAD_STOPS[k + 1]
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = c0[0] + f * (c1[0] - c0[0])
            g = c0[1] + f * (c1[1] - c0[1])
            b = c0[2] + f * (c1[2] - c0[2])
            return f"#{int(r):02X}{int(g):02X}{int(b):02X}"
    return "#3941FF"


def _read_number(s: str, i: int):
    n = len(s)
    while i < n and s[i] in " ,\t\n\r":
        i += 1
    start = i
    if i < n and s[i] in "+-":
        i += 1
    seen_dot = False
    while i < n and (s[i].isdigit() or (s[i] == "." and not seen_dot)):
        if s[i] == ".":
            seen_dot = True
        i += 1
    if i < n and s[i] in "eE":
        i += 1
        if i < n and s[i] in "+-":
            i += 1
        while i < n and s[i].isdigit():
            i += 1
    return float(s[start:i]), i


def _read_flag(s: str, i: int):
    n = len(s)
    while i < n and s[i] in " ,\t\n\r":
        i += 1
    v = 1 if s[i] == "1" else 0
    return v, i + 1


def _flatten_cubic(p0, p1, p2, p3, out, depth=0):
    x0, y0 = p0; x1, y1 = p1; x2, y2 = p2; x3, y3 = p3
    d = math.hypot(x1 - x0, y1 - y0) + math.hypot(x2 - x1, y2 - y1) + math.hypot(x3 - x2, y3 - y2)
    if depth > 18 or d < 0.25:
        out.append(p3)
        return
    x01, y01 = (x0 + x1) / 2, (y0 + y1) / 2
    x12, y12 = (x1 + x2) / 2, (y1 + y2) / 2
    x23, y23 = (x2 + x3) / 2, (y2 + y3) / 2
    xa, ya = (x01 + x12) / 2, (y01 + y12) / 2
    xb, yb = (x12 + x23) / 2, (y12 + y23) / 2
    xm, ym = (xa + xb) / 2, (ya + yb) / 2
    _flatten_cubic(p0, (x01, y01), (xa, ya), (xm, ym), out, depth + 1)
    _flatten_cubic((xm, ym), (xb, yb), (x23, y23), p3, out, depth + 1)


def _arc_to_beziers(x0, y0, rx, ry, phi, large, sweep, x1, y1):
    cp = math.cos(phi); sp = math.sin(phi)
    dx = (x0 - x1) / 2; dy = (y0 - y1) / 2
    x1p = cp * dx + sp * dy
    y1p = -sp * dx + cp * dy
    rx = abs(rx); ry = abs(ry)
    lam = (x1p * x1p) / (rx * rx + 1e-12) + (y1p * y1p) / (ry * ry + 1e-12)
    if lam > 1:
        s = math.sqrt(lam); rx *= s; ry *= s
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    num = rx * rx * ry * ry - den
    coef = (-1 if large == sweep else 1) * math.sqrt(max(0.0, num / den)) if den > 0 else 0.0
    cxp = coef * (rx * y1p / ry)
    cyp = coef * -(ry * x1p / rx)
    cx = cp * cxp - sp * cyp + (x0 + x1) / 2
    cy = sp * cxp + cp * cyp + (y0 + y1) / 2

    def ang(ux, uy, vx, vy):
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        c = max(-1.0, min(1.0, (ux * vx + uy * vy) / n if n else 1.0))
        a = math.acos(c)
        return -a if ux * vy - uy * vx < 0 else a

    theta1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = ang((x1p - cxp) / rx, (y1p - cyp) / ry,
                 (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    if sweep and dtheta < 0:
        dtheta += 2 * math.pi
    nseg = max(1, int(math.ceil(abs(dtheta) / (math.pi / 2))))
    delta = dtheta / nseg
    alpha = (4 / 3) * math.tan(delta / 4)
    beziers = []
    sx = cx + rx * math.cos(theta1)
    sy = cy + ry * math.sin(theta1)
    for k in range(nseg):
        a0 = theta1 + delta * k
        a1 = theta1 + delta * (k + 1)
        ex = cx + rx * math.cos(a1)
        ey = cy + ry * math.sin(a1)
        c1x = cx + rx * (math.cos(a0) - alpha * math.sin(a0))
        c1y = cy + ry * (math.sin(a0) + alpha * math.cos(a0))
        c2x = cx + rx * (math.cos(a1) + alpha * math.sin(a1))
        c2y = cy + ry * (math.sin(a1) - alpha * math.cos(a1))
        beziers.append(((sx, sy), (c1x, c1y), (c2x, c2y), (ex, ey)))
        sx, sy = ex, ey
    return beziers


def _parse_path(d: str):
    i = 0; n = len(d)
    subs = []; cur = []; cx = cy = 0.0; sx = sy = 0.0; cmd = None
    while i < n:
        c = d[i]
        if c in "MmLlHhVvCcSsQqTtAaZz":
            cmd = c; i += 1
            if c in "Zz":
                if cur:
                    cur.append((sx, sy)); subs.append(cur); cur = []
                cx, cy = sx, sy
                continue
        elif c in " ,\t\n\r":
            i += 1; continue
        if cmd in "Mm":
            x, i = _read_number(d, i); y, i = _read_number(d, i)
            if cmd == "m":
                x += cx; y += cy
            if cur:
                subs.append(cur); cur = []
            cur = [(x, y)]; sx, sy = x, y; cx, cy = x, y
            cmd = "L" if cmd == "M" else "l"
        elif cmd in "Ll":
            x, i = _read_number(d, i); y, i = _read_number(d, i)
            if cmd == "l":
                x += cx; y += cy
            cur.append((x, y)); cx, cy = x, y
        elif cmd in "Hh":
            x, i = _read_number(d, i)
            if cmd == "h":
                x += cx
            cur.append((x, cy)); cx = x
        elif cmd in "Vv":
            y, i = _read_number(d, i)
            if cmd == "v":
                y += cy
            cur.append((cx, y)); cy = y
        elif cmd in "Cc":
            x1, i = _read_number(d, i); y1, i = _read_number(d, i)
            x2, i = _read_number(d, i); y2, i = _read_number(d, i)
            x, i = _read_number(d, i); y, i = _read_number(d, i)
            if cmd == "c":
                x1 += cx; y1 += cy; x2 += cx; y2 += cy; x += cx; y += cy
            _flatten_cubic((cx, cy), (x1, y1), (x2, y2), (x, y), cur)
            cx, cy = x, y
        elif cmd in "Aa":
            rx, i = _read_number(d, i); ry, i = _read_number(d, i)
            rot, i = _read_number(d, i)
            large, i = _read_flag(d, i); sweep, i = _read_flag(d, i)
            x, i = _read_number(d, i); y, i = _read_number(d, i)
            if cmd == "a":
                x += cx; y += cy
            for p0, p1, p2, p3 in _arc_to_beziers(cx, cy, rx, ry, math.radians(rot), large, sweep, x, y):
                _flatten_cubic(p0, p1, p2, p3, cur)
            cx, cy = x, y
        else:
            i += 1
    if cur:
        subs.append(cur)
    return subs


def _clip_above(poly, ylo):
    out = []
    for i in range(len(poly)):
        cx, cy = poly[i]; px, py = poly[i - 1]
        cin = cy >= ylo; pin = py >= ylo
        if cin:
            if not pin:
                t = (ylo - py) / (cy - py) if cy != py else 0.0
                out.append((px + t * (cx - px), ylo))
            out.append((cx, cy))
        elif pin:
            t = (ylo - py) / (cy - py) if cy != py else 0.0
            out.append((px + t * (cx - px), ylo))
    return out


def _clip_below(poly, yhi):
    out = []
    for i in range(len(poly)):
        cx, cy = poly[i]; px, py = poly[i - 1]
        cin = cy <= yhi; pin = py <= yhi
        if cin:
            if not pin:
                t = (yhi - py) / (cy - py) if cy != py else 0.0
                out.append((px + t * (cx - px), yhi))
            out.append((cx, cy))
        elif pin:
            t = (yhi - py) / (cy - py) if cy != py else 0.0
            out.append((px + t * (cx - px), yhi))
    return out


_CODEX_SUBS = _parse_path(_CODEX_PATH_D)
_BLOOM_PTS = _CODEX_SUBS[0]
_US_PTS = _CODEX_SUBS[1]
_GT_PTS = _CODEX_SUBS[2]
_GRAD_BANDS = 12


def _bloom_y_range(pts):
    ys = [p[1] for p in pts]
    return min(ys), max(ys)


def draw_mascot(cv: Canvas, ox, oy, size, tint, use_gradient: bool):
    scale = size / 24.0
    cv.rrect(ox, oy, size, size, size * (4.5 / 24.0), fill="white", outline="")
    bloom = [(ox + px * scale, oy + py * scale) for px, py in _BLOOM_PTS]
    if use_gradient:
        ymn, ymx = _bloom_y_range(bloom)
        span = (ymx - ymn) or 1.0
        for k in range(_GRAD_BANDS):
            ylo = ymn + span * k / _GRAD_BANDS
            yhi = ymn + span * (k + 1) / _GRAD_BANDS
            band = _clip_below(_clip_above(bloom, ylo), yhi)
            if len(band) >= 3:
                t = (((ylo + yhi) / 2) - (oy + 3 * scale)) / (18 * scale)
                cv.poly_abs(band, fill=_grad_color(t), outline="")
    else:
        cv.poly_abs(bloom, fill=tint, outline="")
    for sub in (_GT_PTS, _US_PTS):
        pts = [(ox + px * scale, oy + py * scale) for px, py in sub]
        cv.poly_abs(pts, fill="white", outline="")


# --- system tray (Windows, stdlib ctypes) -----------------------------------

HAS_TRAY = False
try:
    import ctypes
    from ctypes import wintypes
    HAS_TRAY = True
except Exception:
    pass

if HAS_TRAY:
    _U32 = ctypes.windll.user32
    _S32 = ctypes.windll.shell32
    _K32 = ctypes.windll.kernel32
    _G32 = ctypes.windll.gdi32

    _WM_APP = 0x8000
    _WM_TRAY = _WM_APP + 1
    _WM_CLOSE_TRAY = _WM_APP + 2
    _WM_LBUTTONUP = 0x0202
    _WM_RBUTTONUP = 0x0205
    _WM_CLOSE = 0x0010

    _NIM_ADD = 0
    _NIM_MODIFY = 1
    _NIM_DELETE = 2
    _NIF_MESSAGE = 0x01
    _NIF_ICON = 0x02
    _NIF_TIP = 0x04
    _TPM_RETURNCMD = 0x0100
    _MF_SEPARATOR = 0x0800
    _IDI_APPLICATION = 32512

    _WNDPROC = ctypes.WINFUNCTYPE(
        wintypes.LPARAM, wintypes.HWND, wintypes.UINT,
        wintypes.WPARAM, wintypes.LPARAM)

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD), ("guidItem", _GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class _WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC), ("cbClsExtra", wintypes.INT),
            ("cbWndExtra", wintypes.INT), ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    _U32.CreateWindowExW.restype = wintypes.HWND
    _U32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.INT, wintypes.INT, wintypes.INT, wintypes.INT,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    _U32.LoadIconW.restype = wintypes.HICON
    _U32.CreatePopupMenu.restype = wintypes.HMENU
    _U32.DefWindowProcW.restype = wintypes.LPARAM
    _U32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _U32.GetMessageW.restype = wintypes.BOOL
    _U32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _U32.PostMessageW.restype = wintypes.BOOL
    _U32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _U32.DestroyWindow.restype = wintypes.BOOL
    _U32.DestroyWindow.argtypes = [wintypes.HWND]
    _U32.AppendMenuW.restype = wintypes.BOOL
    _U32.AppendMenuW.argtypes = [
        wintypes.HMENU, wintypes.UINT, wintypes.UINT, wintypes.LPCWSTR]
    _U32.DestroyMenu.restype = wintypes.BOOL
    _U32.DestroyMenu.argtypes = [wintypes.HMENU]
    _U32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _U32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _U32.TrackPopupMenuEx.restype = wintypes.UINT
    _U32.TrackPopupMenuEx.argtypes = [
        wintypes.HMENU, wintypes.UINT, wintypes.INT, wintypes.INT,
        wintypes.HWND, ctypes.c_void_p]
    _S32.Shell_NotifyIconW.restype = wintypes.BOOL
    _S32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]

    _U32.CreateIconIndirect.restype = wintypes.HICON
    _U32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
    _U32.DestroyIcon.argtypes = [wintypes.HICON]
    _G32.CreateCompatibleDC.restype = wintypes.HDC
    _G32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _G32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    _G32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, wintypes.INT, wintypes.INT]
    _G32.CreateBitmap.restype = wintypes.HBITMAP
    _G32.CreateBitmap.argtypes = [
        wintypes.INT, wintypes.INT, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
    _G32.SelectObject.restype = wintypes.HGDIOBJ
    _G32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _G32.DeleteDC.argtypes = [wintypes.HDC]
    _G32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _G32.CreateSolidBrush.restype = wintypes.HBRUSH
    _G32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
    _G32.Ellipse.argtypes = [
        wintypes.HDC, wintypes.INT, wintypes.INT, wintypes.INT, wintypes.INT]
    _G32.Polygon.restype = wintypes.BOOL
    _G32.Polygon.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.INT]
    _G32.PatBlt.restype = wintypes.BOOL
    _G32.PatBlt.argtypes = [
        wintypes.HDC, wintypes.INT, wintypes.INT, wintypes.INT, wintypes.INT, wintypes.DWORD]
    _G32.SetPixelV.argtypes = [
        wintypes.HDC, wintypes.INT, wintypes.INT, wintypes.COLORREF]
    _G32.MoveToEx.argtypes = [
        wintypes.HDC, wintypes.INT, wintypes.INT, ctypes.POINTER(wintypes.POINT)]
    _G32.LineTo.argtypes = [wintypes.HDC, wintypes.INT, wintypes.INT]
    _G32.CreatePen.restype = wintypes.HPEN
    _G32.CreatePen.argtypes = [wintypes.INT, wintypes.INT, wintypes.COLORREF]
    _G32.GetDeviceCaps.argtypes = [wintypes.HDC, wintypes.INT]
    _U32.GetDC.restype = wintypes.HDC
    _U32.GetDC.argtypes = [wintypes.HWND]
    _U32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _U32.LoadImageW.restype = wintypes.HANDLE
    _U32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        wintypes.INT, wintypes.INT, wintypes.UINT]

    _IMAGE_ICON = 1
    _LR_LOADFROMFILE = 0x00000010

    def _load_icon_from_file(path: str, size: int = 0):
        """Load a .ico as HICON. Returns None if the file is missing/invalid."""
        try:
            if not os.path.isfile(path):
                return None
        except Exception:
            return None
        h = _U32.LoadImageW(0, path, _IMAGE_ICON, size, size, _LR_LOADFROMFILE)
        return h if h else None

    class _ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP),
        ]

    _PS_SOLID = 0

    def _grad_color_bgr(t: float) -> int:
        """Same gradient as _GRAD_STOPS but returns a BGR COLORREF for GDI."""
        t = max(0.0, min(1.0, t))
        for k in range(len(_GRAD_STOPS) - 1):
            t0, c0 = _GRAD_STOPS[k]
            t1, c1 = _GRAD_STOPS[k + 1]
            if t <= t1:
                f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                r = int(c0[0] + f * (c1[0] - c0[0]))
                g = int(c0[1] + f * (c1[1] - c0[1]))
                b = int(c0[2] + f * (c1[2] - c0[2]))
                return (b << 16) | (g << 8) | r
        c = _GRAD_STOPS[-1][1]
        return (c[2] << 16) | (c[1] << 8) | c[0]

    def _create_codex_tray_icon(size=32):
        """Draw the Codex bloom + '>_' prompt as a tray icon — same logo as
        the widget mascot, with the purple->pink gradient."""
        hdc_screen = _U32.GetDC(None)
        hdc = _G32.CreateCompatibleDC(hdc_screen)
        hbm_color = _G32.CreateCompatibleBitmap(hdc_screen, size, size)
        hbm_mask = _G32.CreateBitmap(size, size, 1, 1, None)
        _U32.ReleaseDC(None, hdc_screen)

        scale = size / 24.0
        bloom = [(px * scale, py * scale) for px, py in _BLOOM_PTS]
        ys = [p[1] for p in bloom]
        ymn, ymx = min(ys), max(ys)
        span = (ymx - ymn) or 1.0

        def to_points(pts):
            arr = (wintypes.POINT * len(pts))()
            for i, (px, py) in enumerate(pts):
                arr[i] = wintypes.POINT(int(px), int(py))
            return arr

        def fill_polygon(hdc, pts, brush_color):
            brush = _G32.CreateSolidBrush(brush_color)
            old = _G32.SelectObject(hdc, brush)
            _G32.Polygon(hdc, to_points(pts), len(pts))
            _G32.SelectObject(hdc, old)
            _G32.DeleteObject(brush)

        def clip_band(poly, ylo, yhi):
            return _clip_below(_clip_above(poly, ylo), yhi)

        # Color bitmap: magenta bg (masked out), gradient bloom, white ">_"
        old_bm = _G32.SelectObject(hdc, hbm_color)
        bg = _G32.CreateSolidBrush(0xFF00FF)
        _G32.SelectObject(hdc, bg)
        _G32.PatBlt(hdc, 0, 0, size, size, 0x0042)
        _G32.DeleteObject(bg)
        for k in range(_GRAD_BANDS):
            ylo = ymn + span * k / _GRAD_BANDS
            yhi = ymn + span * (k + 1) / _GRAD_BANDS
            band = clip_band(bloom, ylo, yhi)
            if len(band) >= 3:
                t = (((ylo + yhi) / 2) - 3 * scale) / (18 * scale)
                fill_polygon(hdc, band, _grad_color_bgr(t))
        fill_polygon(hdc, [(px * scale, py * scale) for px, py in _GT_PTS], 0xFFFFFF)
        fill_polygon(hdc, [(px * scale, py * scale) for px, py in _US_PTS], 0xFFFFFF)
        _G32.SelectObject(hdc, old_bm)
        _G32.DeleteDC(hdc)

        # Mask bitmap: white bg (transparent), black bloom (opaque)
        hdc_mask = _G32.CreateCompatibleDC(None)
        old_mask = _G32.SelectObject(hdc_mask, hbm_mask)
        _G32.PatBlt(hdc_mask, 0, 0, size, size, 0x0042)  # WHITENESS
        fill_polygon(hdc_mask, bloom, 0x000000)
        _G32.SelectObject(hdc_mask, old_mask)
        _G32.DeleteDC(hdc_mask)

        ii = _ICONINFO()
        ii.fIcon = True
        ii.xHotspot = size // 2
        ii.yHotspot = size // 2
        ii.hbmMask = hbm_mask
        ii.hbmColor = hbm_color
        hicon = _U32.CreateIconIndirect(ctypes.byref(ii))
        _G32.DeleteObject(hbm_color)
        _G32.DeleteObject(hbm_mask)
        return hicon

    class TrayIcon:
        def __init__(self, tooltip, on_show, on_quit):
            self._tooltip = tooltip[:127]
            self._on_show = on_show
            self._on_quit = on_quit
            self._hwnd = None
            self._hicon = None
            self._nid = None
            self._thread = None
            self._wndproc_ref = None
            self._cls_name = "CodexSessionWidgetTray"
            self._hinst = _K32.GetModuleHandleW(None)

        def start(self):
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def _run(self):
            try:
                self._run_inner()
            except Exception as e:
                import traceback
                print(f"TrayIcon error: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)

        def _run_inner(self):
            def proc(hwnd, msg, wparam, lparam):
                if msg == _WM_TRAY:
                    mouse = lparam & 0xFFFF
                    if mouse == _WM_LBUTTONUP:
                        self._on_show()
                    elif mouse == _WM_RBUTTONUP:
                        self._show_menu(hwnd)
                    return 0
                if msg == _WM_CLOSE_TRAY:
                    _U32.DestroyWindow(hwnd)
                    return 0
                if msg == 0x0002:
                    _U32.PostQuitMessage(0)
                    return 0
                return _U32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wndproc_ref = _WNDPROC(proc)

            wc = _WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(wc)
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = self._hinst
            wc.lpszClassName = self._cls_name
            _U32.RegisterClassExW(ctypes.byref(wc))

            self._hwnd = _U32.CreateWindowExW(
                0, self._cls_name, "Codex Tray", 0, 0, 0, 0, 0,
                ctypes.c_void_p(-3), None, self._hinst, None)

            self._hicon = _load_icon_from_file(ICON_FILE, 32) or _create_codex_tray_icon(32)

            nid = _NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(nid)
            nid.hWnd = self._hwnd
            nid.uID = 1
            nid.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
            nid.uCallbackMessage = _WM_TRAY
            nid.hIcon = self._hicon
            nid.szTip = self._tooltip
            _S32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid))
            self._nid = nid

            msg = wintypes.MSG()
            while _U32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _U32.TranslateMessage(ctypes.byref(msg))
                _U32.DispatchMessageW(ctypes.byref(msg))

            if self._nid:
                _S32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(self._nid))
            if self._hwnd:
                _U32.DestroyWindow(self._hwnd)
            if self._hicon:
                _U32.DestroyIcon(self._hicon)
            _U32.UnregisterClassW(self._cls_name, self._hinst)

        def _show_menu(self, hwnd):
            menu = _U32.CreatePopupMenu()
            _U32.AppendMenuW(menu, 0, 1, "Show widget")
            _U32.AppendMenuW(menu, _MF_SEPARATOR, 0, "")
            _U32.AppendMenuW(menu, 0, 2, "Quit")
            pt = wintypes.POINT()
            _U32.GetCursorPos(ctypes.byref(pt))
            _U32.SetForegroundWindow(hwnd)
            cmd = _U32.TrackPopupMenuEx(
                menu, _TPM_RETURNCMD, pt.x, pt.y, hwnd, None)
            _U32.DestroyMenu(menu)
            if cmd == 1:
                self._on_show()
            elif cmd == 2:
                self._on_quit()

        def update_tooltip(self, text):
            if not self._nid or not self._hwnd:
                return
            self._nid.szTip = text[:127]
            _S32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(self._nid))

        def stop(self):
            if self._hwnd:
                _U32.PostMessageW(self._hwnd, _WM_CLOSE_TRAY, 0, 0)
else:
    class TrayIcon:
        def __init__(self, *a, **kw):
            pass
        def start(self):
            pass
        def stop(self):
            pass
        def update_tooltip(self, text):
            pass


# --- widget app --------------------------------------------------------------

COLLAPSED_W, COLLAPSED_H = 264, 132
EXPANDED_W, EXPANDED_H = 264, 176


class WidgetApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=TRANSPARENT)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            pass

        dpi = self.root.winfo_fpixels("1i")
        self.S = max(1.0, dpi / 96.0)

        self.state = SessionState(active=False, week_active=0.0)
        self.on_top = True
        self.weekly_expanded = self._load_state().get("weeklyExpanded", False)
        self._size = (EXPANDED_W, EXPANDED_H) if self.weekly_expanded else (COLLAPSED_W, COLLAPSED_H)

        self.canvas = tk.Canvas(root, bg=TRANSPARENT, highlightthickness=0,
                                width=int(self._size[0] * self.S),
                                height=int(self._size[1] * self.S))
        self.canvas.pack()

        self.cv = Canvas(self.canvas, self.S)

        self._fonts = {
            "title": tkfont.Font(size=int(11 * self.S), weight="bold",
                                 family="Segoe UI"),
            "big": tkfont.Font(size=int(24 * self.S), weight="bold",
                               family="Cascadia Mono"),
            "sub": tkfont.Font(size=int(10 * self.S), weight="normal",
                               family="Cascadia Mono"),
            "week": tkfont.Font(size=int(10 * self.S), weight="normal",
                                family="Cascadia Mono"),
        }

        self._drag = None
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<Button-2>", self._on_disclosure)

        self._place()
        self._menu = self._build_menu()
        self.refresh_data()
        self.render()

        self._tray = TrayIcon(
            "Codex Session Widget",
            on_show=lambda: self.root.after(0, self._show_from_tray),
            on_quit=lambda: self.root.after(0, self.quit),
        )
        self._tray.start()

        self.root.after(1000, self._tick)
        self.root.after(60000, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

    def _load_state(self) -> dict:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_state(self):
        data = self._load_state()
        data["weeklyExpanded"] = self.weekly_expanded
        data["frame"] = f"+{self.root.winfo_x()}+{self.root.winfo_y()}"
        try:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, STATE_FILE)
        except Exception:
            pass

    def _place(self):
        w, h = self._size
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        st = self._load_state()
        frame = st.get("frame")
        if frame and isinstance(frame, str) and "+" in frame:
            try:
                xs = frame.split("+")
                x, y = int(xs[-2]), int(xs[-1])
                if 0 <= x <= sw - 40 and 0 <= y <= sh - 40:
                    self.root.geometry(f"+{x}+{y}")
                    return
            except Exception:
                pass
        x = sw - int(w * self.S) - int(24 * self.S)
        y = int(24 * self.S)
        self.root.geometry(f"+{x}+{y}")

    def _build_menu(self) -> tk.Menu:
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Always on top", command=self.toggle_top)
        m.add_command(label="Show weekly usage", command=self.toggle_weekly)
        m.add_command(label="Refresh now", command=self.refresh_now)
        m.add_separator()
        if HAS_TRAY:
            m.add_command(label="Hide to tray", command=self._hide_to_tray)
            m.add_command(label="Quit", command=self.quit)
        else:
            m.add_command(label="Quit", command=self.quit)
        return m

    def _on_press(self, e):
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, e):
        if not self._drag:
            return
        dx = e.x_root - self._drag[0]
        dy = e.y_root - self._drag[1]
        self.root.geometry(f"+{self._drag[2] + dx}+{self._drag[3] + dy}")

    def _on_release(self, e):
        self._save_state()
        self._drag = None

    def _on_right(self, e):
        self._menu.tk_popup(e.x_root, e.y_root)

    def _on_disclosure(self, e):
        h = self._size[1]
        y = e.y / self.S
        if h - 24 < y < h - 2:
            self.toggle_weekly()

    def toggle_top(self):
        self.on_top = not self.on_top
        self.root.attributes("-topmost", self.on_top)

    def refresh_now(self):
        self.refresh_data()
        self.render()

    def toggle_weekly(self):
        self.weekly_expanded = not self.weekly_expanded
        self._size = (EXPANDED_W, EXPANDED_H) if self.weekly_expanded else (COLLAPSED_W, COLLAPSED_H)
        w, h = self._size
        cur_x = self.root.winfo_x()
        cur_y = self.root.winfo_y()
        self.canvas.configure(width=int(w * self.S), height=int(h * self.S))
        self.root.geometry(f"+{cur_x}+{cur_y}")
        self._save_state()
        self.render()

    def _hide_to_tray(self):
        self._save_state()
        self.root.withdraw()

    def _show_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(100, lambda: self.root.attributes("-topmost", self.on_top))

    def quit(self):
        self._save_state()
        if hasattr(self, "_tray"):
            self._tray.stop()
        self.root.destroy()

    def refresh_data(self):
        self.state = compute()

    def _tick(self):
        self.render()
        self.root.after(1000, self._tick)

    def _poll(self):
        self.refresh_data()
        self.root.after(60000, self._poll)

    def _usage_color(self, p: float) -> str:
        p = max(0.0, min(1.0, p))
        if p <= 0:
            return ""
        if p <= 0.50:
            return CODEX_ACCENT
        if p <= 0.80:
            return AMBER
        return URGENT_RED

    def _fmt_remaining(self, ms: int) -> str:
        s = ms // 1000
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        if h >= 1:
            return f"{h}h {m}m"
        return f"{m}m {sec:02d}s"

    def _fmt_clock(self, ms: int) -> str:
        import datetime as dt
        return dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M")

    def _fmt_day_clock(self, ms: int) -> str:
        import datetime as dt
        return dt.datetime.fromtimestamp(ms / 1000).strftime("%a %H:%M")

    def render(self):
        c = self.canvas
        c.delete("all")
        w, h = self._size
        self.cv.rrect(0, 0, w, h, 16, fill=IVORY, outline="")

        mascot_tint = MUTED
        use_gradient = False
        big_text = "Idle"
        big_color = NEAR_BLACK
        sub_text = "opens on next msg"
        progress = 0.0
        progress_color = ""

        st = self.state
        now_ms = int(time.time() * 1000)
        if st.active and st.end_ms:
            rem = st.end_ms - now_ms
            if rem <= 0:
                self.refresh_data()
                return self.render()
            if st.used_5h_pct is not None:
                remaining_pct = max(0.0, 100.0 - st.used_5h_pct)
                big_text = f"{int(round(remaining_pct))}%"
                sub_text = f"{int(round(st.used_5h_pct))}% used  resets {self._fmt_clock(st.end_ms)}"
                progress = max(0.0, min(1.0, st.used_5h_pct / 100.0))
                progress_color = self._usage_color(progress)
                if st.used_5h_pct >= 90:
                    mascot_tint = big_color = URGENT_RED
                elif st.used_5h_pct >= 75:
                    mascot_tint = big_color = AMBER
                else:
                    use_gradient = True
                    big_color = CODEX_ACCENT
            else:
                big_text = self._fmt_remaining(rem)
                start = st.start_ms if st.start_ms is not None else (st.end_ms - WINDOW_MS)
                sub_text = f"resets {self._fmt_clock(st.end_ms)} est"
                progress = max(0.0, min(1.0, (now_ms - start) / WINDOW_MS))
                progress_color = self._usage_color(progress)
                if rem < 15 * 60 * 1000:
                    mascot_tint = big_color = URGENT_RED
                elif rem < 60 * 60 * 1000:
                    mascot_tint = big_color = AMBER
                else:
                    use_gradient = True
                    big_color = CODEX_ACCENT

        msize = 72
        mx = w - msize - 10
        my = 12
        draw_mascot(self.cv, mx, my, msize, mascot_tint, use_gradient)

        pad = 16
        col_w = mx - pad - 6
        self.cv.text(pad, 14, "Codex session", anchor="nw",
                     fill=MUTED, font=self._fonts["title"])
        self.cv.text(pad, 40, big_text, anchor="nw",
                     fill=big_color, font=self._fonts["big"])
        self.cv.text(pad, 88, sub_text, anchor="nw",
                     fill=MUTED, font=self._fonts["sub"])

        ty = 110
        self.cv.rrect(pad, ty, col_w, 8, 4, fill="#E6E6E8", outline="")
        if progress > 0 and progress_color:
            self.cv.rrect(pad, ty, col_w * progress, 8, 4,
                          fill=progress_color, outline="")

        if self.weekly_expanded:
            if st.used_7d_pct is not None:
                wlabel = f"7d  {int(round(st.used_7d_pct))}% used"
                if st.reset_7d_ms:
                    wlabel += f"  resets {self._fmt_day_clock(st.reset_7d_ms)}"
                wp = max(0.0, min(1.0, st.used_7d_pct / 100.0))
            elif st.week_active is not None and (st.active or st.week_active > 0):
                wlabel = f"7d activity {int(round(st.week_active * 100))}% est"
                wp = st.week_active
            else:
                wlabel = "7d activity unavailable"
                wp = 0.0
            self.cv.text(pad, 128, wlabel, anchor="nw",
                         fill=MUTED, font=self._fonts["week"])
            wy = 150
            self.cv.rrect(pad, wy, w - pad * 2, 8, 4, fill="#E6E6E8", outline="")
            wc = self._usage_color(wp)
            if wc and wp > 0:
                self.cv.rrect(pad, wy, (w - pad * 2) * wp, 8, 4,
                              fill=wc, outline="")

        if hasattr(self, "_tray"):
            if st.active and st.used_5h_pct is not None:
                tip = f"Codex: {int(round(100 - st.used_5h_pct))}% left"
            elif st.active and st.end_ms:
                tip = f"Codex: {self._fmt_remaining(st.end_ms - now_ms)} left"
            else:
                tip = "Codex: idle"
            self._tray.update_tooltip(tip)


def write_snapshot(path: str, expanded: bool):
    from PIL import Image, ImageDraw, ImageFont
    w, h = (EXPANDED_W, EXPANDED_H) if expanded else (COLLAPSED_W, COLLAPSED_H)
    scale = 3
    img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rr = 16 * scale

    def rrect(x, y, ww, hh, r, **kw):
        d.rounded_rectangle([x, y, x + ww, y + hh], radius=r, **kw)

    rrect(0, 0, w * scale, h * scale, rr, fill=IVORY)
    # mascot: real Codex SVG (white tile + gradient bloom + ">_" cutout)
    msize = 72
    mx = w - msize - 10
    my = 12
    mox = mx * scale
    moy = my * scale
    ms = msize * scale / 24.0
    rrect(mox, moy, msize * scale, msize * scale, msize * scale * (4.5 / 24.0), fill="white")
    bloom = [(mox + px * ms, moy + py * ms) for px, py in _BLOOM_PTS]
    ys = [p[1] for p in bloom]; ymn, ymx = min(ys), max(ys); span = (ymx - ymn) or 1.0
    for k in range(_GRAD_BANDS):
        ylo = ymn + span * k / _GRAD_BANDS
        yhi = ymn + span * (k + 1) / _GRAD_BANDS
        band = _clip_below(_clip_above(bloom, ylo), yhi)
        if len(band) >= 3:
            t = (((ylo + yhi) / 2) - (moy + 3 * ms)) / (18 * ms)
            d.polygon([coord for pt in band for coord in pt], fill=_grad_color(t))
    for sub in (_GT_PTS, _US_PTS):
        pts = [(mox + px * ms, moy + py * ms) for px, py in sub]
        d.polygon([coord for pt in pts for coord in pt], fill="white")

    try:
        font_title = ImageFont.truetype("segoeui.ttf", int(11 * scale))
        font_big = ImageFont.truetype("consola.ttf", int(24 * scale))
        font_sub = ImageFont.truetype("consola.ttf", int(10 * scale))
    except Exception:
        font_title = ImageFont.load_default()
        font_big = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    pad = 16 * scale
    col_w = (mx - 16 - 6) * scale
    d.text((pad, 14 * scale), "Codex session", fill=MUTED, font=font_title)
    d.text((pad, 40 * scale), "55%", fill=CODEX_ACCENT, font=font_big)
    d.text((pad, 88 * scale), "45% used  resets 22:50", fill=MUTED, font=font_sub)
    ty = 110 * scale
    d.rounded_rectangle([pad, ty, pad + col_w, ty + 8 * scale],
                        radius=4 * scale, fill="#E6E6E8")
    d.rounded_rectangle([pad, ty, pad + col_w * 0.45, ty + 8 * scale],
                        radius=4 * scale, fill=CODEX_ACCENT)
    if expanded:
        d.text((pad, 128 * scale), "7d  22% used  resets Thu 18:00", fill=MUTED, font=font_sub)
        wy = 150 * scale
        d.rounded_rectangle([pad, wy, pad + (w - 32) * scale, wy + 8 * scale],
                            radius=4 * scale, fill="#E6E6E8")
        d.rounded_rectangle([pad, wy, pad + (w - 32) * scale * 0.32, wy + 8 * scale],
                            radius=4 * scale, fill=CODEX_ACCENT)
    img.save(path)
    print(f"snapshot: {path}")


def _render_icon_image(size: int = 256):
    """Render the Codex bloom + '>_' on a transparent RGBA image."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    scale = size / 24.0
    bloom = [(px * scale, py * scale) for px, py in _BLOOM_PTS]
    ys = [p[1] for p in bloom]; ymn, ymx = min(ys), max(ys); span = (ymx - ymn) or 1.0
    for k in range(_GRAD_BANDS):
        ylo = ymn + span * k / _GRAD_BANDS
        yhi = ymn + span * (k + 1) / _GRAD_BANDS
        band = _clip_below(_clip_above(bloom, ylo), yhi)
        if len(band) >= 3:
            t = (((ylo + yhi) / 2) - 3 * scale) / (18 * scale)
            d.polygon([coord for pt in band for coord in pt], fill=_grad_color(t))
    for sub in (_GT_PTS, _US_PTS):
        pts = [(px * scale, py * scale) for px, py in sub]
        d.polygon([coord for pt in pts for coord in pt], fill="white")
    return img


def write_icon(path: str, png_path: Optional[str] = None):
    """Render the Codex bloom + '>_' as a multi-size .ico (transparent bg,
    purple->pink gradient bloom, white prompt). Optionally also save a PNG
    (for the README header). Pillow-only (build step)."""
    img = _render_icon_image(256)
    img.save(path, format="ICO",
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"icon: {path}")
    if png_path:
        img.save(png_path, format="PNG")
        print(f"icon png: {png_path}")


def main():
    if "--snapshot" in sys.argv:
        expanded = "--expanded" in sys.argv
        path = sys.argv[-1] if sys.argv[-1].endswith(".png") else "codex-session-widget.png"
        write_snapshot(path, expanded)
        return
    if "--icon" in sys.argv:
        ico_path = sys.argv[-1] if sys.argv[-1].endswith(".ico") else ICON_FILE
        png_path = ico_path[:-4] + ".png" if ico_path.endswith(".ico") else "codex-icon.png"
        write_icon(ico_path, png_path)
        return
    root = tk.Tk()
    WidgetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
