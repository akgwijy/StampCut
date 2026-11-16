# Independent title and caption styles

Request: 제목하고 자막에서 각각 위치와 폰트 폰트 사이즈 조절할 수 있는 기능 추가.

Implementation choices: retain the current PySide6/FFmpeg app and global style settings shared by all clips. Each text block gets an X center (0..1080), existing Y anchor (0..1920), font file override, and size (8..240 px at final resolution). Title Y remains its vertical center; caption Y remains its top. Defaults stay X=540, title=64px, caption=60px. Dragging moves both coordinates.

Use installed font files in a dropdown plus a custom TTF/OTF/TTC picker; store actual paths so preview and FFmpeg use the same face. Blank override inherits the existing common font; unavailable overrides fall back to that font. Timestamp text follows the caption font and X while retaining its existing 36px size and 42px gap.

Controls update the preview immediately, persist through existing style_changed autosave, and invalidate the full-preview signature. Output and half-resolution full previews scale positions and sizes identically and wrap using the configured full-resolution size. Existing settings, clip offsets, and project data remain compatible.

Approach: extend Settings and existing rendering/preview code; isolate font discovery and selection in gui/font_picker.py. A shared font selector would not satisfy independent styles; rendering text only in Qt would break FFmpeg exports.
