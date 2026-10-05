# DAG Rendering for the Milestone Panel

**Date:** 2026-10-05 · **Decision:** hand-rolled SVG, zero dependencies

## Verdict

No library. The server already computes wave ranks (`_waves` in
`workflow.py`); the client only positions boxes on pre-ranked columns and
draws orthogonal edges. ~80 lines of vanilla JS, full CRT control, nothing to
vendor, offline-safe inside the container.

## Why not the libraries (scout claims corrected)

| Candidate | Reality | Reject reason |
|---|---|---|
| mermaid | ~1MB minified, **not** 15kB; `flowchart` uses dagre, ELK via separate layout package | 60x the code for layout we already compute server-side; theming API fights phosphor glow |
| dagre standalone | ~40kB + needs graphlib; layout-only, still hand-roll SVG rendering | Pays for ranking we already have (`_waves`); rendering half still manual |
| elkjs | ~500kB–1MB WASM/js | Absurd for ≤20 nodes in known columns |
| cytoscape.js | ~300kB+, force/physics-oriented | Wrong layout family; dependency ballast |

(Common ground the scout got right: layered/rank layout is the correct family;
we just get ranks free from the backend.)

## Styling recipe (phosphor skill constraints)

- Panel: existing `section` frame (1px `var(--line)`, 8px radius, `var(--bg2)`).
- Nodes: SVG `rect` (not HTML divs — scanline overlay `body::after` already
  covers SVG), 1px stroke `var(--line)`; status recolors stroke + label:
  `passed → var(--ph)` green, `retry/open → var(--amber)`, `escalated →
  var(--red)`; pending/unrun → `var(--ph-dim)`.
- Node label: task short id (`017` from `AUTH-017`) + outcome glyph
  (`█` passed, `▲` retry, `◆` escalated, `░` pending) — ASCII/block only, no emoji.
- Selected node: stroke → ink + `filter: drop-shadow` phosphor bloom (SVG-safe;
  skill bans CSS `blur()` filters, `drop-shadow` on vector strokes reads as glow).
- Edges: orthogonal polylines (`M x1 y1 H midx V y2 H x2`), 1px `var(--ph-dim)`,
  arrowhead marker; blocked/dead edges dim to 40%.
- Verdict badge: header-line text `MILESTONE AUTH-M1 ▮ MET` in status color;
  round strip: `R1 ●●○ R2 ●●●` per-round pass dots.
- Click node → detail readout (status, attempt, latest report, deps) in a
  `<pre>` under the SVG — matches console readout idiom, keyboard-focusable
  (`tabindex`, `:focus-visible` already global).
- Motion: none autonomous (skill: motion answers user action); round transitions
  repaint instantly. `prefers-reduced-motion` already kills sweep/flicker globally.
- Light scheme: all `var()` so `data-scheme="light"` recolors free.
