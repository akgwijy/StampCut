# Per-clip caption style plan

Goal: selecting a clip and editing caption font/position changes that clip only; size/color follow the same scope. Title remains common.

Architecture: optional Clip.caption_x/y/font_size/font_path/color fields inherit individual Settings values when None; empty font path remains an explicit base-font choice. Clip.caption_settings(settings) returns effective Settings for both Qt and FFmpeg. Additive project-v2 fields preserve v1/v2 compatibility. Include overrides in full-preview signatures.

UI: default to selected clip when a clip exists; a scope selector retains access to common defaults. Show effective values on selection, scope changes and settings refresh without emitting edits. A caption-specific reset clears overrides only. Clip edits and drag release emit clip_changed for project autosave; common/title edits emit style_changed for settings save. No selected clip means common editing.

- [x] Write regression tests and observe missing-feature failures.
- [x] Add model, project persistence, render and preview-signature integration.
- [x] Add scoped controls, switching, drag and reset behavior; keep the compact existing layout.
- [x] Run full tests and a real offline two-clip FFmpeg check.
- [x] Obtain an independent read-only review; record the final verification.

Preserve all existing uncommitted work. No user project/config writes during verification. No new dependencies or git integration.

## Verification

- Initial regressions: all 16 new per-clip tests failed against the previous common-only implementation.
- Focused suite: 125 passed.
- Review fixes: switching to another clip (including through deselection) resets scope to selected clip; caption color dialog starts from the active scope's effective color. Three regressions failed before fixes, then passed.
- Final full suite: 368 passed, 3 network tests deselected (33.79s).
- Offline integration: rendered two synthetic clips with different caption font, X/Y, size and color; concatenated to a 540x960, 2.02-second video and visually verified both extracted frames.
- UI: rendered 1280x820; verified two-clip switching and compact inline scope/reset controls. Verification used a separate temporary demo project and did not change user settings or projects.
- Independent read-only review completed; both actionable findings addressed with RED-to-GREEN regressions. Prior offset/common-font features excluded from review; native layout and outputs visually checked by implementer.
