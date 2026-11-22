# Channel-period and video mode implementation plan

> **For agentic workers:** use scoped subagent implementation for independent backend modules, root integration, and independent review before completion.

**Goal:** one StampCut supports Shorts and landscape videos from URL lists or channel/date selection.
**Architecture:** shared pipeline; source lookup and output layout are focused modules; mode-specific settings are preserved by pure helpers.
**Tech stack:** existing Python3.12, PySide6, requests, FFmpeg, yt-dlp.
**Spec:** docs/superpowers/specs/2026-11-16-channel-period-video-design.md

## Global constraints

Preserve all pre-existing authorized uncommitted features. No new dependencies. Dates inclusive in KST. No fixed200 cap or early stop on a single old video. Shorts remains1080x1920; video1920x1080; half-scale video preview960x540. Same comments/replies, edit controls, caption overrides, BGM and export. No actual API-key, user-project or user-settings access in verification. Do not commit existing mixed changes.

## Review focus

Date-boundary/timezone correctness, missing/duplicate/unordered videos and pagination beyond200; cancellation before subsequent requests. Legacy settings/projects. Switching modes without losing either layout's styling or per-clip crop. Channel/date changes invalidating stale selection. Preview/export geometry, captions and signatures agreeing in both modes.

### Task1: channel-period backend

Owned files: core/channel.py, core/youtube_api.py; new tests/test_channel_period.py.
Produces find_channel_videos_in_period(client,text,start:date,end:date,progress,cancel)->ChannelPage with next_token=None and complete chronological videos; optional client.cancel_event checked before API requests.
- [x] RED tests for KST inclusive dates, all pages beyond200, out-of-order dates, dedupe/no-comment/unavailable videos, invalid input and cancellation.
- [x] Implement source service without changing existing lookup behavior; run new and existing channel/API tests.
- [x] Self-review and independent scoped review.

### Task2: layout and FFmpeg renderer

Owned files: core/layouts.py, core/renderer.py; new tests/test_landscape_renderer.py.
Consumes Settings.output_mode and normalize_output_settings(settings) from root-owned core/output_modes.py.
Produces layout_for(mode)->dict with canvas_w/h,frame_w/h,frame_x/y,max_text_width and existing font/fps constants. Keep renderer.LAYOUT alias to Shorts dictionary. compute_frame_geometry(width,height,zoom,pan_x,pan_y,frame_w,frame_h,contain=False)->SquareGeometry; compute_square_geometry compatibility preserved.
- [x] RED tests for1920x1080 and960x540, contain/pad/zoom/pan, role-specific fonts/text coordinates and unchanged Shorts.
- [x] Implement layout and renderer, run new/existing renderer tests.
- [x] Self-review and independent scoped review.

### Task3: mode state, source UI, preview and persistence (root)

Owned files: core/models.py, core/output_modes.py, core/project_io.py, core/settings.py, core/pipeline.py; gui/source_panel.py, gui/period_video_dialog.py, gui/url_panel.py, gui/main_window.py, gui/preview_widget.py; new mode/source/preview integration tests; README.
Settings: output_mode and style_mode defaultshorts, mode_settings dict. Clip: mode_settings dict. Project: output_mode defaultshorts plus source_kind defaulturls and channel/start/end strings. Pure switch_output_mode(settings,mode,clips=())->Settings snapshots current common styles and per-clip style/crop, loads target snapshots or target coordinates; normalize_output_settings ensures style_mode equals output_mode. No changes to duration selection policy.
- [x] RED tests for switching/roundtrip/legacy defaults before implementing state helpers.
- [x] Add source selector/date input/query confirmation and worker/error/cancel handling; persist source metadata.
- [x] Add output selector; adapt preview geometry/spin ranges/text placement and full-preview signature to mode.
- [x] Full tests; render real synthetic videos in both modes, verify frames and UI layouts; document workflow.
- [x] Whole-feature independent review, address material findings with RED/GREEN tests and full suite.

## Coordination

One implementation subagent at a time; root integration can proceed on disjoint files. Task1/2 do not edit root files. Root-owned models/helpers are available before Task2. Scoped diffs compare task snapshots because prior authorized features are still uncommitted. Full-suite runs are coordinated by root after integration.

## Final validation

432 passed,3 deselected (45.79s); no live channel/API requests. Real synthetic FFmpeg output verified Shorts1080x1920,video1920x1080,preview960x540,portrait containment,concat+BGM and two-line caption fit. Both UI layouts visually checked. Final independent review found two P2 edge cases; one fix wave plus ten regressions closed both, scoped re-review Ready Yes with no new findings. Test app-data isolation now automatic. Existing uncommitted features preserved; no commits or user-data resets.

Subsequent authorization (2026-11-16): the user explicitly requested committing the completed work and pushing it to GitHub, including the earlier authorized start-offset and text-style changes preserved in this checkout. This supersedes the original no-commit constraint.
