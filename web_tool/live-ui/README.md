# Live UI overlay (tracked copy)

`frontend/` is an uninitialized git submodule (`Open-LLM-VTuber-Web`), so changes
to its built files cannot be committed in the main OLV repository.

This directory keeps a tracked copy of the live-stage UI customizations:

```text
effects.js       live effects, subtitle context, song queue/player UI
index.html       normal OLV stage entry (loads effects.js?v=23)
live-view.html   read-only live viewer entry
```

## Apply to the running frontend

From the repository root:

```powershell
python scripts/sync_live_ui.py
```

The script copies these files into `frontend/`, preserving the submodule build
assets. After applying, hard-refresh the browser with `Ctrl + F5`.

The same files can be applied by hand:

```text
web_tool/live-ui/effects.js   -> frontend/effects.js
web_tool/live-ui/index.html   -> frontend/index.html
web_tool/live-ui/live-view.html -> frontend/live-view.html
```

## Notes

- `effects.js` owns stage-local overlays, the presence bubble
  (`无聊中` / `听歌中` / `摸鱼中` / reserved `工作中`), subtitle reply context,
  the draggable song queue panel, browser audio playback, volume,
  prev/next/pause/stop controls, and the "偷看弹幕中" label replacement.
- Browser music is served through OLV's `/song-audio` proxy, not loaded from
  the NetEase CDN directly.
- The review log is written by the backend to `data/live-review.jsonl`; export
  it with `python scripts/export_live_review.py`.
- Volume defaults to 10% and is stored in localStorage under
  `dsh-song-volume-v2`.
