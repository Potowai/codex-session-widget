# Codex Session Widget — agent context

## What this is
A tiny floating **Windows 11** desktop widget that shows the OpenAI Codex
5-hour usage window (countdown + % used + weekly activity bar). Reconstructed
**transcript-only** from `~/.codex/sessions/**/*.jsonl` — no network, no creds.

Ported from the original macOS Swift/Claude Code widget to Windows + Python/Tkinter.

## Environment
- OS: Windows 11
- Python 3.14 at `C:\Python314\python.exe` (and `pythonw.exe` for no-console launch)
- Tkinter 8.6 is in the stdlib (no pip needed to run the widget)
- Pillow 12.1.1 is installed — only used by `build.ps1` for the README snapshot

## Commands
- Run widget (no install): `pythonw SessionWidget.py`
- Run with console (debug): `python SessionWidget.py`  (blocks on mainloop)
- Render README snapshot: `powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Expanded`
- Install (startup shortcut + launch): `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`
- Uninstall: `powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1`
- Non-blocking render check (verification):
  `python -c "import importlib.util, tkinter as tk; spec=importlib.util.spec_from_file_location('sw', r'SessionWidget.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); root=tk.Tk(); a=m.WidgetApp(root); root.update(); print('OK', a._size); root.destroy()"`
- No tests / lint configured yet.

## File layout
| file | role |
|---|---|
| `SessionWidget.py` | the widget (stdlib + Tkinter, ~570 lines) |
| `install.ps1` / `uninstall.ps1` | per-user startup-shortcut setup / teardown |
| `build.ps1` | PNG snapshot for README (Pillow) |
| `SessionWidget.swift` | original macOS widget (kept as reference) |
| `session_usage_poll.py` | original macOS Claude poller (kept as reference) |
| `build.sh` / `install.sh` / `uninstall.sh` | original macOS scripts (kept as reference) |

## Runtime locations
- Installed widget: `%LOCALAPPDATA%\CodexSessionWidget\SessionWidget.py`
- Startup shortcut: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Codex Session Widget.lnk`
- Widget state: `~/.codex/widget-state.json` (window position + weekly toggle)
- Data source: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (line field `timestamp`)

## Conventions
- No comments in source unless explicitly requested.
- No network calls, no credentials read, no telemetry.
- Keep the widget dependency-free (stdlib only) — Pillow stays optional, build-only.
- Magic transparent color `#F0ABCD` must never appear in the card art (used for `-transparentcolor`).
- UI strings flagged `est` because the window is reconstructed locally.

## Verification checklist
1. `python -c "import SessionWidget as S; print(S.compute())"` — prints a SessionState with `active=True` when Codex was used in the last 5h.
2. Non-blocking render check (command above) prints `RENDER OK size=(264, 124)` or `(264, 166)`.
3. After `install.ps1`, a `pythonw.exe` process matching `*SessionWidget.py*` is running.
4. `uninstall.ps1` leaves `~/.codex/sessions` untouched.
