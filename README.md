# FrameRateViewer

FrameRateViewer is a small desktop AVI review tool for videos saved with the wrong FPS metadata.

It was built for cases where the camera actually captured around 3 FPS, but the `.avi` file was written as 20 or 30 FPS. Normal video players then play the footage too fast. This player lets you review the original AVI at a corrected real FPS, with speed control, zoom, panning, seeking, and frame-by-frame stepping.

中文名：帧率回放查看器。

## Features

- Open `.avi` files from the current folder or any selected folder.
- Set the real capture FPS, defaulting to `3`.
- Adjust playback speed from `0.05x` to `20x`.
- Zoom and pan the video view.
- Pause, seek, and step frame by frame.
- Build a standalone Windows `.exe` with PyInstaller.

## Run From Source

```bash
python avi_player.py
```

On Windows:

```bat
python avi_player.py
```

## Dependencies

```bash
pip install -r requirements.txt
```

## Build Windows EXE

On Windows, run:

```bat
build_exe.bat
```

The executable will be generated at:

```text
dist\AVI_Player.exe
```

Generated folders such as `build/`, `dist/`, and large video files are intentionally ignored by Git.
