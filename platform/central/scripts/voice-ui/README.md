# Voice controls integration

The active admin-ui entry must include the local voice controls and avatar layout.
Version 16 merges them into the resizable-file-panel bundle at commit 8e4db3b.
Do not deploy the base index-real-analysis-v22.js entry by itself: it omits voice controls.

Rebuild example, from the repository root (use the current unpatched bundle and entry):
python scripts/voice-ui/merge-voice-ui.py admin-ui/assets/index-real-analysis-v22.js BASE_INDEX.html scripts/voice-ui/root-voice-settings.js scripts/voice-ui/avatar-layout-v14.css OUTPUT_DIR

The builder checks all four integration points and verifies unchanged surrounding code.
Deploy the resulting entry, JS and CSS to both admin-ui and admin-ui/dist, keeping existing file-browser CSS.
Include this directory, admin-ui/index.html, the new bundle and layout CSS in the next reviewed Git commit.
Do not stage unrelated host changes or credential backups.
