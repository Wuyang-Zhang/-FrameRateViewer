#!/usr/bin/env python3
"""Simple AVI review player with corrected FPS playback.

This tool is intentionally self-contained: Tkinter handles the desktop UI,
PyAV decodes video frames, and Pillow draws them. It is designed for AVI files
whose container FPS is wrong, for example files saved as 30 FPS while the real
capture rate was around 3 FPS.
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from tkinter import (
    BOTH,
    BOTTOM,
    DISABLED,
    END,
    HORIZONTAL,
    LEFT,
    NORMAL,
    RIGHT,
    TOP,
    X,
    Y,
    Button,
    Canvas,
    DoubleVar,
    Entry,
    Frame,
    Label,
    Listbox,
    Scale,
    StringVar,
    TclError,
    Tk,
    filedialog,
    messagebox,
)

try:
    import av
except ImportError as exc:  # pragma: no cover - user-facing startup guard
    raise SystemExit("Missing dependency: pip install av pillow") from exc

try:
    from PIL import Image, ImageTk
except ImportError as exc:  # pragma: no cover - user-facing startup guard
    raise SystemExit("Missing dependency: pip install pillow") from exc


APP_DIR = Path(__file__).resolve().parent
DEFAULT_REAL_FPS = 3.0
MIN_DELAY_MS = 1


@dataclass
class VideoInfo:
    path: Path
    width: int
    height: int
    nominal_fps: float
    frames: int
    duration_sec: float


class AviPlayer:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("AVI FPS Review Player")
        self.root.geometry("1180x760")
        self.root.minsize(900, 580)

        self.container: av.container.InputContainer | None = None
        self.stream: av.video.stream.VideoStream | None = None
        self.frame_iter = None
        self.video_info: VideoInfo | None = None

        self.current_image: Image.Image | None = None
        self.tk_image: ImageTk.PhotoImage | None = None
        self.current_frame_index = 0
        self.playing = False
        self.after_id: str | None = None
        self.dragging_timeline = False
        self.pan_start: tuple[int, int] | None = None
        self.image_id: int | None = None
        self.fit_scale = 1.0
        self.user_scale = DoubleVar(value=1.0)
        self.speed = DoubleVar(value=1.0)
        self.real_fps = StringVar(value=f"{DEFAULT_REAL_FPS:g}")
        self.status = StringVar(value="Open an AVI file or select one from this folder.")

        self._build_ui()
        self._load_folder(APP_DIR)

    def _build_ui(self) -> None:
        main = Frame(self.root)
        main.pack(fill=BOTH, expand=True)

        sidebar = Frame(main, width=285)
        sidebar.pack(side=LEFT, fill=Y)
        sidebar.pack_propagate(False)

        Label(sidebar, text="AVI files").pack(anchor="w", padx=10, pady=(10, 4))
        self.file_list = Listbox(sidebar, exportselection=False)
        self.file_list.pack(fill=BOTH, expand=True, padx=10)
        self.file_list.bind("<<ListboxSelect>>", self._on_file_select)

        open_buttons = Frame(sidebar)
        open_buttons.pack(fill=X, padx=10, pady=10)
        Button(open_buttons, text="Open file", command=self.open_file).pack(side=LEFT, fill=X, expand=True)
        Button(open_buttons, text="Open folder", command=self.open_folder).pack(side=LEFT, fill=X, expand=True, padx=(8, 0))

        controls = Frame(sidebar)
        controls.pack(fill=X, padx=10, pady=(0, 10))

        Label(controls, text="Real FPS").pack(anchor="w")
        Entry(controls, textvariable=self.real_fps, width=10).pack(fill=X)

        Label(controls, text="Speed").pack(anchor="w", pady=(10, 0))
        Scale(
            controls,
            from_=0.05,
            to=20.0,
            resolution=0.05,
            orient=HORIZONTAL,
            variable=self.speed,
            command=lambda _value: self._update_status(),
        ).pack(fill=X)

        speed_buttons = Frame(controls)
        speed_buttons.pack(fill=X, pady=(4, 0))
        for label, value in [("0.1x", 0.1), ("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("5x", 5.0), ("10x", 10.0)]:
            Button(speed_buttons, text=label, command=lambda v=value: self.speed.set(v)).pack(
                side=LEFT, fill=X, expand=True
            )

        Label(controls, text="Zoom").pack(anchor="w", pady=(10, 0))
        Scale(
            controls,
            from_=0.25,
            to=8.0,
            resolution=0.05,
            orient=HORIZONTAL,
            variable=self.user_scale,
            command=lambda _value: self._redraw_current_frame(),
        ).pack(fill=X)

        self.info_label = Label(sidebar, text="", justify=LEFT, anchor="w")
        self.info_label.pack(fill=X, padx=10, pady=(0, 10))

        right = Frame(main)
        right.pack(side=RIGHT, fill=BOTH, expand=True)

        self.canvas = Canvas(right, bg="#111111", highlightthickness=0)
        self.canvas.pack(side=TOP, fill=BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._redraw_current_frame())
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._pan)

        bottom = Frame(right)
        bottom.pack(side=BOTTOM, fill=X)

        transport = Frame(bottom)
        transport.pack(fill=X, padx=10, pady=(8, 2))
        self.play_button = Button(transport, text="Play", command=self.toggle_play, width=10)
        self.play_button.pack(side=LEFT)
        Button(transport, text="Prev frame", command=lambda: self.step_frame(-1)).pack(side=LEFT, padx=(8, 0))
        Button(transport, text="Next frame", command=lambda: self.step_frame(1)).pack(side=LEFT, padx=(8, 0))
        Button(transport, text="Fit", command=self.fit_view).pack(side=LEFT, padx=(8, 0))
        Button(transport, text="100%", command=lambda: self._set_zoom(1.0)).pack(side=LEFT, padx=(8, 0))

        self.position_text = Label(transport, text="Frame 0 / 0")
        self.position_text.pack(side=RIGHT)

        self.timeline = Scale(
            bottom,
            from_=0,
            to=1,
            resolution=1,
            orient=HORIZONTAL,
            showvalue=False,
            command=self._on_timeline_drag,
        )
        self.timeline.pack(fill=X, padx=10)
        self.timeline.bind("<ButtonPress-1>", lambda _event: setattr(self, "dragging_timeline", True))
        self.timeline.bind("<ButtonRelease-1>", self._on_timeline_release)

        Label(bottom, textvariable=self.status, anchor="w").pack(fill=X, padx=10, pady=(0, 8))

    def _load_folder(self, folder: Path) -> None:
        self.folder = folder
        self.files = sorted(folder.glob("*.avi")) + sorted(folder.glob("*.AVI"))
        self.file_list.delete(0, END)
        for path in self.files:
            self.file_list.insert(END, path.name)
        if self.files:
            self.file_list.selection_clear(0, END)
            self.file_list.selection_set(0)
            self.file_list.activate(0)
            self.load_video(self.files[0])

    def open_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=str(self.folder))
        if selected:
            self._load_folder(Path(selected))

    def open_file(self) -> None:
        selected = filedialog.askopenfilename(
            initialdir=str(self.folder),
            filetypes=[("AVI video", "*.avi *.AVI"), ("All files", "*.*")],
        )
        if selected:
            self.load_video(Path(selected))

    def _on_file_select(self, _event=None) -> None:
        selected = self.file_list.curselection()
        if selected:
            self.load_video(self.files[selected[0]])

    def load_video(self, path: Path) -> None:
        self.stop()
        self._close_video()
        try:
            self.container = av.open(str(path))
            self.stream = self.container.streams.video[0]
            self.stream.thread_type = "AUTO"
        except Exception as exc:
            messagebox.showerror("Cannot open video", f"{path}\n\n{exc}")
            return

        nominal_fps = self._rate_to_float(self.stream.average_rate) or self._rate_to_float(self.stream.base_rate) or 30.0
        frames = int(self.stream.frames or 0)
        duration_sec = self._duration_seconds()
        self.video_info = VideoInfo(path, self.stream.width, self.stream.height, nominal_fps, frames, duration_sec)
        self.current_frame_index = 0
        self.frame_iter = self.container.decode(self.stream)
        self._configure_timeline()
        self._read_and_show_next_frame()
        self.fit_view()
        self._update_info()
        self._update_status()

    def _close_video(self) -> None:
        if self.container is not None:
            self.container.close()
        self.container = None
        self.stream = None
        self.frame_iter = None

    def _rate_to_float(self, rate) -> float:
        if not rate:
            return 0.0
        try:
            return float(rate)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    def _duration_seconds(self) -> float:
        if self.container and self.container.duration:
            return float(self.container.duration) / 1_000_000.0
        if self.stream and self.stream.duration and self.stream.time_base:
            return float(self.stream.duration * self.stream.time_base)
        return 0.0

    def _configure_timeline(self) -> None:
        total = self._total_frames_for_ui()
        self.timeline.configure(to=max(total - 1, 1), state=NORMAL if total > 1 else DISABLED)

    def _total_frames_for_ui(self) -> int:
        if not self.video_info:
            return 0
        if self.video_info.frames > 0:
            return self.video_info.frames
        if self.video_info.duration_sec > 0 and self.video_info.nominal_fps > 0:
            return max(1, int(round(self.video_info.duration_sec * self.video_info.nominal_fps)))
        return max(1, self.current_frame_index + 1)

    def _read_and_show_next_frame(self) -> bool:
        if self.frame_iter is None:
            return False
        try:
            frame = next(self.frame_iter)
        except StopIteration:
            self.stop()
            self.status.set("End of video.")
            return False
        except Exception as exc:
            self.stop()
            self.status.set(f"Decode error: {exc}")
            return False

        self.current_frame_index += 1
        self.current_image = frame.to_image()
        self._redraw_current_frame()
        self._update_position()
        return True

    def _redraw_current_frame(self) -> None:
        if self.current_image is None:
            return
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        img_w, img_h = self.current_image.size
        self.fit_scale = min(canvas_w / img_w, canvas_h / img_h)
        scale = max(0.01, self.fit_scale * float(self.user_scale.get()))
        target_w = max(1, int(img_w * scale))
        target_h = max(1, int(img_h * scale))
        resized = self.current_image.resize((target_w, target_h), Image.Resampling.BILINEAR)
        self.tk_image = ImageTk.PhotoImage(resized)

        if self.image_id is None:
            self.image_id = self.canvas.create_image(canvas_w // 2, canvas_h // 2, image=self.tk_image, anchor="center")
        else:
            self.canvas.itemconfigure(self.image_id, image=self.tk_image)
            if target_w <= canvas_w and target_h <= canvas_h:
                self.canvas.coords(self.image_id, canvas_w // 2, canvas_h // 2)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def fit_view(self) -> None:
        self.user_scale.set(1.0)
        self._redraw_current_frame()

    def _set_zoom(self, scale: float) -> None:
        if self.fit_scale <= 0:
            self.user_scale.set(scale)
        else:
            self.user_scale.set(max(0.25, min(8.0, scale / self.fit_scale)))
        self._redraw_current_frame()

    def _start_pan(self, event) -> None:
        self.pan_start = (event.x, event.y)

    def _pan(self, event) -> None:
        if self.pan_start is None or self.image_id is None:
            return
        old_x, old_y = self.pan_start
        dx = event.x - old_x
        dy = event.y - old_y
        self.canvas.move(self.image_id, dx, dy)
        self.pan_start = (event.x, event.y)

    def toggle_play(self) -> None:
        if self.playing:
            self.stop()
        else:
            if self.video_info is None:
                return
            self.playing = True
            self.play_button.configure(text="Pause")
            self._schedule_next_frame()

    def stop(self) -> None:
        self.playing = False
        self.play_button.configure(text="Play")
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    def _schedule_next_frame(self) -> None:
        if not self.playing:
            return
        delay_ms = self._frame_delay_ms()
        self.after_id = self.root.after(delay_ms, self._play_tick)

    def _play_tick(self) -> None:
        self.after_id = None
        started = time.perf_counter()
        if self._read_and_show_next_frame():
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            delay_ms = max(MIN_DELAY_MS, self._frame_delay_ms() - int(elapsed_ms))
            self.after_id = self.root.after(delay_ms, self._play_tick)

    def _frame_delay_ms(self) -> int:
        fps = self._safe_real_fps()
        speed = max(0.01, float(self.speed.get()))
        return max(MIN_DELAY_MS, int(round(1000.0 / (fps * speed))))

    def _safe_real_fps(self) -> float:
        try:
            fps = float(self.real_fps.get())
        except ValueError:
            fps = DEFAULT_REAL_FPS
        if not math.isfinite(fps) or fps <= 0:
            fps = DEFAULT_REAL_FPS
        return fps

    def step_frame(self, direction: int) -> None:
        if self.video_info is None:
            return
        self.stop()
        if direction > 0:
            self._read_and_show_next_frame()
            return
        target = max(0, self.current_frame_index - 2)
        self._seek_to_frame(target)

    def _on_timeline_drag(self, value: str) -> None:
        if self.dragging_timeline:
            self.position_text.configure(text=f"Frame {int(float(value)) + 1} / {self._total_frames_for_ui()}")

    def _on_timeline_release(self, _event) -> None:
        if not self.dragging_timeline:
            return
        self.dragging_timeline = False
        self.stop()
        self._seek_to_frame(int(float(self.timeline.get())))

    def _seek_to_frame(self, frame_index: int) -> None:
        if not self.container or not self.stream or not self.video_info:
            return
        frame_index = max(0, min(frame_index, self._total_frames_for_ui() - 1))
        try:
            if self.video_info.nominal_fps > 0:
                seconds = frame_index / self.video_info.nominal_fps
                timestamp = int(seconds / float(self.stream.time_base))
                self.container.seek(timestamp, stream=self.stream, backward=True, any_frame=False)
            else:
                self.container.seek(0)
                frame_index = 0
        except Exception:
            self.container.seek(0)
            frame_index = 0

        self.frame_iter = self.container.decode(self.stream)
        self.current_frame_index = max(0, frame_index)
        for _ in range(12):
            if not self._read_and_show_next_frame():
                break
            if self.current_frame_index >= frame_index + 1:
                break

    def _update_position(self) -> None:
        total = self._total_frames_for_ui()
        if not self.dragging_timeline:
            self.timeline.set(max(0, self.current_frame_index - 1))
        real_fps = self._safe_real_fps()
        real_time = self.current_frame_index / real_fps if real_fps > 0 else 0
        self.position_text.configure(
            text=f"Frame {self.current_frame_index} / {total}   Real time {self._format_time(real_time)}"
        )

    def _update_info(self) -> None:
        if not self.video_info:
            self.info_label.configure(text="")
            return
        info = self.video_info
        corrected_duration = info.frames / self._safe_real_fps() if info.frames else 0.0
        self.info_label.configure(
            text=(
                f"{info.path.name}\n"
                f"{info.width} x {info.height}\n"
                f"File FPS: {info.nominal_fps:.3g}\n"
                f"Frames: {info.frames or 'unknown'}\n"
                f"File duration: {self._format_time(info.duration_sec)}\n"
                f"At real FPS: {self._format_time(corrected_duration)}"
            )
        )

    def _update_status(self) -> None:
        if not self.video_info:
            return
        playback_fps = self._safe_real_fps() * max(0.01, float(self.speed.get()))
        self.status.set(
            f"Playback uses real FPS x speed = {self._safe_real_fps():.3g} x {self.speed.get():.2f} "
            f"= {playback_fps:.3g} shown frames/sec."
        )
        self._update_info()

    def _format_time(self, seconds: float) -> str:
        if seconds <= 0 or not math.isfinite(seconds):
            return "00:00"
        seconds_i = int(round(seconds))
        h, rem = divmod(seconds_i, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def on_close(self) -> None:
        self.stop()
        self._close_video()
        self.root.destroy()


def main() -> int:
    try:
        root = Tk()
    except TclError as exc:
        print(f"Cannot open desktop window: {exc}", file=sys.stderr)
        print("Run this script from a graphical desktop session.", file=sys.stderr)
        return 1
    app = AviPlayer(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
