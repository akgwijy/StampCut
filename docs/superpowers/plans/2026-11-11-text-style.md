# Independent text style implementation plan

> **For agentic workers:** Use superpowers:executing-plans for inline implementation and superpowers:requesting-code-review for the final read-only review.

**Goal:** Independent title/caption X/Y, installed/custom font and pixel size with matching previews and exports.
**Architecture:** Extend Settings without changing saved project schema; resolve per-role font paths with legacy fallback; keep font selection in one focused widget.
**Tech Stack:** Python 3.12+, PySide6, existing FFmpeg.
**Spec:** docs/superpowers/specs/2026-11-11-text-style-design.md

## Global constraints

Preserve all pre-existing uncommitted clip-offset and README changes. No new dependencies. X 0..1080; Y 0..1920; size 8..240 px; old defaults preserved. No commits, integration, downloads, or live user-project edits are needed.

## Review focus

- Legacy settings and unavailable custom fonts inherit the common font.
- Title/caption faces and sizes stay independent, including regular versus bold faces.
- Position and font-size clamping agrees between Qt and FFmpeg.
- Full-preview scale and wrapping match final output; style changes invalidate stale previews.
- Font dialogs cancel cleanly; controls remain usable without a selected clip and fit the existing left panel.

## Task 1: Settings, renderer, and cache

Files: core/models.py, core/settings.py, core/renderer.py, core/pipeline.py; tests/test_text_style.py.
Interfaces: Settings.title_x/caption_x=540, title_font_size=64, caption_font_size=60, title_font_path/caption_font_path=""; resolve_font(settings, role=None, fallback=None).
- [x] Add regression tests for saving/legacy fallback, role-specific rendering, scaled wrapping and signature changes.
- [x] Run the new tests and observe missing-feature failures.
- [x] Implement fields, resolution and rendering; include all new style fields in the signature.
- [x] Run the focused tests.

## Task 2: Controls and drag behavior

Files: gui/font_picker.py, gui/preview_widget.py, tests/test_text_style.py, existing drag tests, README.md.
Interfaces: FontPicker.set_path/path, path_changed(str), combo and browse_btn; PreviewWidget title_x_spin/caption_x_spin, title_size_spin/caption_size_spin, title_font_picker/caption_font_picker.
- [x] Add tests for independent controls, font selection, size-aware wrapping, both-axis drag and silent settings sync.
- [x] Implement the picker, compact controls, preview fonts, X placement and drag routing.
- [x] Update existing drag tests for the new (kind, dx, dy) callback.
- [x] Run the full pytest suite, render the UI for visual inspection, and document the controls.
- [x] Obtain one independent read-only review and address material findings with regressions.

Pre-flight: Task 2 consumes Task 1 Settings fields and resolve_font; both use the same bounds and legacy defaults. No interface conflicts.

## Verification record

- Regression phase: 16 expected missing-feature failures; font-face regression reproduced regular 400 versus bold 700 and duplicate font labels, then corrected.
- Full suite after final changes: 348 passed, 3 network tests deselected (31.57s).
- UI: rendered and visually inspected at 1280x820; separate X/Y, size, font and color controls fit the existing panel.
- Offline FFmpeg integration: 1-second synthetic source rendered at 540x960; distinct title/caption fonts, sizes and positions verified in the extracted output frame.
- Independent read-only code review: no actionable findings; reviewer additionally checked all 361 discovered font files and reported zero selected-face mismatches.
- Existing signed clip-offset changes and launch documentation retained. No user project or settings files were changed by verification.
