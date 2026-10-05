# SmallWorks Console — DESIGN.md

MU-TH-UR 6000 science-officer terminal for the supervised factory. One screen,
no nav: the whole factory state visible at once, like ship systems on the
Nostromo bridge. Read + command surface only — the board stays GitHub Issues.

Skills: `frontend-design` (distinct identity, plan-then-critique, copy as UX),
`crt-phosphor-terminal` (scanlines, glow, ASCII frames, boot sequence).

## Screen regions (single viewport, no scroll page)

```
╔════════ SMALLWORKS // MU-TH-UR 6000 ════════ SCHEME: DARK ◄► ╗
║ SYS.CHECK ▓▓▓▓▓░░░░░ 043/078 │ UPLINK: LOCAL │ PWR: NOMINAL ║
╠══ RUNS ═════════╦══ DETAIL ════════════════╦══ COMMS ══════╣
║ ►demo  PENDING  ║ TASK DEMO-001            ║ ORCHESTRATOR  ║
║  svc-1 RUNNING  ║ ROLE orchestrator        ║ > queued: …   ║
║  svc-2 BLOCKED  ║ MODEL unassigned         ║ < status      ║
║                 ║ ATTEMPT 0  TESTS -       ║ > approve     ║
║                 ║ COST $0.0000             ║ [cmd______]   ║
╠══ CONTROLS ═════╩══ LOGS ══════════════════╩══════════════╣
║ [HOLD][RESUME][RETRY][ESCALATE][SEAL][REJECT]  COMPRESSED  ║
║ line 0… line 79 … [+48 OMITTED → RAW] … tail              ║
╚═══════════════════ BUILDING BETTER WORLDS ════════════════╝
```

1. **Header strip** — wordmark, scheme toggle (DARK ◄► LIGHT), boot status
   line (`SYS.CHECK`, uplink, power). One orchestrated boot moment on load.
2. **RUNS roster** (left) — every run as a telemetry row: id, task, status
   glyph (`►` running, `■` blocked/paused, `✓` passed, `✗` failed), worker,
   model. Click selects → detail + logs + chat rebind. Polls `/api/runs`.
3. **DETAIL readout** (center) — §10 board fields for the selected run:
   `GET /api/runs/{id}` + `board` block + `/api/tasks/{task}/cost`.
4. **COMMS terminal** (right) — orchestrator chat as transcript
   (`>` user, `<` ship). `GET/POST /api/runs/{id}/chat`, autocomplete off,
   aggressive auto-scroll, `EventSource` snapshot refresh w/ poll fallback.
5. **CONTROLS keybank** (bottom-left) — six sealed keys mapping to
   `POST /control`: HOLD (pause), RESUME (retry), RETRY, ESCALATE, SEAL
   (approve gate), REJECT. Two-key feel: arm → confirm; disabled unless the
   selected run's state allows it (e.g. SEAL only on `pending_approval`).
6. **LOGS scope** (bottom-right) — RTK-compressed `GET logs` head+tail with
   `[+N OMITTED → RAW]` toggle per chunk flipping `?raw=true`. Monospace
   readout, no prose.

## Functions (JS, all against the existing API — no backend changes)

| fn | binds | calls |
|---|---|---|
| `boot()` | header strip | staged `SYS.CHECK` lines → `refresh()` |
| `refresh()` | runs + detail | `GET /api/runs` → `select(first)` |
| `select(id)` | detail, logs, chat, controls | `GET run`, `GET logs`, `GET chat`, `GET cost` |
| `sendChat(text)` | comms | `POST chat {content}` → append transcript |
| `command(action, arg?)` | keybank | `POST control {action, argument}` → `refresh()` |
| `toggleRaw(i)` | logs scope | flips chunk `compressed ⇄ raw` via `?raw=true` |
| `stream()` | all live regions | `EventSource /events` snapshot → repaint; `onerror` → 5s poll |
| `setScheme(mode)` | `:root` tokens | toggles `data-scheme`, persists `localStorage` |

State rules: controls enable from `run.state` (`SEAL`/`REJECT` only when
`pending_approval` set; `RESUME` only when paused/blocked). Chat input parses
leading `/` commands (`/approve`, `/retry …`) into `command()`. Empty logs →
`NO TELEMETRY — AWAITING UPLINK`, never a blank panel.

## Colour schemes

Shared: pure monospace stack, 1px panel borders, scanline + vignette overlay,
ASCII framing, `reduced-motion` kills boot/flicker, focus = 2px phosphor
outline, contrast ≥ 7:1 body text.

### DARK — Nostromo bridge (default)
| token | hex | role |
|---|---|---|
| `--bg` | `#0a0a0a` | hull black |
| `--bg2` | `#0d120f` | panel |
| `--ph` | `#00ff7a` | phosphor primary |
| `--ph-dim` | `#3e6a57` | dim labels, offline |
| `--amber` | `#ffb000` | caution, escalate, attempts |
| `--red` | `#ff2b2b` | failure, reject, containment |
| `--ink` | `#c6ffdf` | bright readout text |
| `--line` | `rgba(0,255,122,.18)` | borders, dividers |
| `--glow` | `0 0 14px rgba(0,255,122,.08)` | hover/active |

Statuses: running = phosphor, paused/blocked = amber, failed = red,
passed = bright ink `✓`. Boot line types green; `PWR: NOMINAL` amber only on
budget breach.

### LIGHT — Weyland day-shift (paper terminal)
Same geometry, inverted tube: sun-bleached deck plate with dark-phosphor ink,
for bright rooms. Amber/red keep meaning, deepened for contrast on paper.

| token | hex | role |
|---|---|---|
| `--bg` | `#e8e4d8` | deck paper |
| `--bg2` | `#dfdacd` | panel |
| `--ph` | `#0b5c36` | dark phosphor primary |
| `--ph-dim` | `#7a8a7e` | dim labels |
| `--amber` | `#8a5a00` | caution |
| `--red` | `#a31212` | failure |
| `--ink` | `#0a1f14` | readout text |
| `--line` | `rgba(11,92,54,.28)` | borders |
| `--glow` | `0 0 0 rgba(0,0,0,0)` | flat, no glow on paper |

Scanlines stay but drop to ~3% alpha; vignette flips to edge-darkening paper
shade. Toggle is a physical-style `DARK ◄► LIGHT` switch in the header.

## Build order (ui.html only, single file, no deps)
1. Tokens + shell grid + scanline overlay + boot sequence.
2. Runs roster + detail readout (read paths).
3. Keybank + chat (write paths) with state-gated enables.
4. Logs scope with raw toggle + stream/poll + scheme toggle + a11y pass.

Out of scope: new endpoints, auth, multi-select, charts, sounds.
