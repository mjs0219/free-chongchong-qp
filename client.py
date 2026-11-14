#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""虫虫钢琴曲谱桌面客户端。

粘贴虫虫钢琴曲谱网址，自动下载 .ccmz，再导出 MIDI / MusicXML / PNG / PDF。

运行：
    py -3.10 client.py
"""

from __future__ import annotations

import io
import math
import os
import queue
import re
import subprocess
import threading
import time
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests

import gangqinpu
from ccmz import LibCCMZ
from main import export_ccmz, safe_base_name

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

    HAS_PIL = True
except ImportError:  # 没有 Pillow 也能用，只是没有极光背景动画
    HAS_PIL = False

BASE_DIR = Path(__file__).resolve().parent

BG_TOP = "#05070f"
BG_BOTTOM = "#0a1226"
CARD_BG = "#141d31"
CARD_BORDER = "#2b3b5c"
TEXT = "#e8efff"
MUTED = "#8fa4c9"
ACCENT = "#5b8cff"
ACCENT_ACTIVE = "#7aa2ff"
ENTRY_BG = "#0d1526"

FONT = "Microsoft YaHei UI"
LOG_FONT = "Consolas"

FORMAT_LABELS = {
    "midi": "MIDI",
    "musicxml": "MusicXML",
    "png": "PNG 谱面",
    "pdf": "PDF",
}


class _QueueWriter(io.TextIOBase):
    """把 print 输出转进 GUI 日志队列。"""

    def __init__(self, client):
        self.client = client

    def write(self, text):
        for line in (text or "").splitlines():
            line = line.rstrip()
            if line:
                self.client.queue.put(("log", (line, "info")))
        return len(text or "")

    def flush(self):
        pass


class ChongchongClient:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.busy = False
        self.closed = False
        self._bg_photo = None
        self._bg_item = None
        self._base_cache = None
        self._base_size = (0, 0)
        self._status_item = None
        self._layout_job = None
        self._anim_job = None

        self.root.title("虫虫钢琴曲谱客户端")
        self.root.minsize(880, 720)
        self._center(980, 780)
        self.root.configure(bg=BG_TOP)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.url_var = tk.StringVar()
        self.out_dir_var = tk.StringVar(value=str(BASE_DIR / "output"))
        self.name_var = tk.StringVar()
        self.type_vars = {
            "midi": tk.BooleanVar(value=True),
            "musicxml": tk.BooleanVar(value=True),
            "png": tk.BooleanVar(value=False),
            "pdf": tk.BooleanVar(value=False),
        }
        self.status_var = tk.StringVar(value="就绪")

        self._build_styles()
        self._build_widgets()
        self._append_log("粘贴虫虫钢琴曲谱网址，选择导出格式后点击开始。", "info")

        self.canvas.bind("<Configure>", self._on_configure)
        self.root.after(0, self._layout)
        self.root.after(60, self._animate)
        self.root.after(70, self._poll_queue)

    # ------------------------------------------------------------------
    # 窗口与样式
    # ------------------------------------------------------------------
    def _center(self, width, height):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Aurora.Horizontal.TProgressbar",
            troughcolor="#0c1424",
            background=ACCENT,
            bordercolor="#0c1424",
            lightcolor=ACCENT,
            darkcolor="#4a6fe0",
        )
        style.configure(
            "Aurora.Vertical.TScrollbar",
            background="#23324d",
            troughcolor="#0c1424",
            bordercolor="#0c1424",
            arrowcolor="#8fa4c9",
            lightcolor="#23324d",
            darkcolor="#23324d",
        )
        style.map(
            "Aurora.Vertical.TScrollbar",
            background=[("active", "#324566")],
        )

    def _label(self, parent, text, size=10, bold=False, fg=TEXT, **kwargs):
        return tk.Label(
            parent,
            text=text,
            bg=CARD_BG,
            fg=fg,
            font=(FONT, size, "bold" if bold else "normal"),
            **kwargs,
        )

    def _entry(self, parent, textvariable, **kwargs):
        return tk.Entry(
            parent,
            textvariable=textvariable,
            bg=ENTRY_BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#2a3a58",
            highlightcolor=ACCENT,
            font=(FONT, 11),
            **kwargs,
        )

    def _button(self, parent, text, command, primary=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=ACCENT if primary else "#1d2a43",
            fg="#ffffff" if primary else TEXT,
            activebackground=ACCENT_ACTIVE if primary else "#2a3a58",
            activeforeground="#ffffff" if primary else TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=(FONT, 10, "bold" if primary else "normal"),
            padx=18,
            pady=8,
        )

    # ------------------------------------------------------------------
    # 控件
    # ------------------------------------------------------------------
    def _build_widgets(self):
        self.canvas = tk.Canvas(
            self.root, bg=BG_TOP, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)
        self._bg_item = self.canvas.create_image(0, 0, anchor="nw")

        # 网址卡片
        self.url_frame = tk.Frame(self.canvas, bg=CARD_BG)
        self._label(self.url_frame, "曲谱网址", size=10, bold=True).pack(
            anchor="w"
        )
        self.url_entry = self._entry(self.url_frame, self.url_var)
        self.url_entry.pack(fill="x", pady=(8, 5), ipady=7)
        self.url_entry.bind("<Return>", lambda _event: self.start())
        self._label(
            self.url_frame,
            "例如：https://www.gangqinpu.com/cchtml/1054183.htm",
            size=9,
            fg=MUTED,
        ).pack(anchor="w")

        # 导出设置卡片
        self.settings_frame = tk.Frame(self.canvas, bg=CARD_BG)

        row = tk.Frame(self.settings_frame, bg=CARD_BG)
        row.pack(fill="x", pady=(0, 10))
        self._label(row, "输出目录", size=10, bold=True, width=7, anchor="w").pack(
            side="left"
        )
        self.browse_btn = self._button(row, "选择…", self.browse_output)
        self.browse_btn.pack(side="right", padx=(8, 0))
        self._entry(row, self.out_dir_var).pack(
            side="left", fill="x", expand=True, ipady=6
        )

        row = tk.Frame(self.settings_frame, bg=CARD_BG)
        row.pack(fill="x", pady=(0, 10))
        self._label(row, "文件名", size=10, bold=True, width=7, anchor="w").pack(
            side="left"
        )
        self._entry(row, self.name_var).pack(
            side="left", fill="x", expand=True, ipady=6
        )
        self._label(row, "留空自动使用曲谱名", size=9, fg=MUTED).pack(
            side="left", padx=(10, 0)
        )

        row = tk.Frame(self.settings_frame, bg=CARD_BG)
        row.pack(fill="x", pady=(0, 6))
        self._label(row, "导出格式", size=10, bold=True, width=7, anchor="w").pack(
            side="left"
        )
        for key in ("midi", "musicxml", "png", "pdf"):
            tk.Checkbutton(
                row,
                text=FORMAT_LABELS[key],
                variable=self.type_vars[key],
                bg=CARD_BG,
                fg=TEXT,
                activebackground=CARD_BG,
                activeforeground=TEXT,
                selectcolor=ENTRY_BG,
                highlightthickness=0,
                bd=0,
                font=(FONT, 10),
                cursor="hand2",
            ).pack(side="left", padx=(0, 14))

        self._label(
            self.settings_frame,
            "PNG / PDF 需要本机已安装 LilyPond；MIDI / MusicXML 不依赖。",
            size=9,
            fg=MUTED,
        ).pack(anchor="w", pady=(0, 10))

        row = tk.Frame(self.settings_frame, bg=CARD_BG)
        row.pack(fill="x")
        self.open_btn = self._button(row, "打开输出目录", self.open_output)
        self.open_btn.pack(side="right")
        self.start_btn = self._button(
            row, "开始下载并导出", self.start, primary=True
        )
        self.start_btn.pack(side="right", padx=(0, 10))

        # 日志卡片
        self.log_frame = tk.Frame(self.canvas, bg=CARD_BG)
        self.log_frame.grid_rowconfigure(1, weight=1)
        self.log_frame.grid_columnconfigure(0, weight=1)
        self._label(self.log_frame, "运行日志", size=10, bold=True).grid(
            row=0, column=0, sticky="w"
        )

        text_row = tk.Frame(self.log_frame, bg=CARD_BG)
        text_row.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        self.log_text = tk.Text(
            text_row,
            bg="#0c1424",
            fg=MUTED,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#24344f",
            wrap="word",
            font=(LOG_FONT, 10),
            height=5,
            padx=10,
            pady=8,
            state="disabled",
            spacing1=2,
            spacing3=2,
        )
        scroll = ttk.Scrollbar(
            text_row,
            command=self.log_text.yview,
            style="Aurora.Vertical.TScrollbar",
        )
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        self.log_text.tag_configure("info", foreground=MUTED)
        self.log_text.tag_configure("ok", foreground="#7ee2a8")
        self.log_text.tag_configure("warn", foreground="#ffd479")
        self.log_text.tag_configure("err", foreground="#ff8a9b")

        bottom = tk.Frame(self.log_frame, bg=CARD_BG)
        bottom.grid(row=2, column=0, sticky="ew", pady=(0, 2))
        self.progress = ttk.Progressbar(
            bottom,
            style="Aurora.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            value=0,
            length=220,
        )
        self.progress.pack(side="left", fill="x", expand=True)

    # ------------------------------------------------------------------
    # 布局与背景
    # ------------------------------------------------------------------
    def _round_rect(self, x1, y1, x2, y2, radius, fill):
        self.canvas.create_rectangle(
            x1 + radius, y1, x2 - radius, y2, fill=fill, outline=fill, tags="card"
        )
        self.canvas.create_rectangle(
            x1, y1 + radius, x2, y2 - radius, fill=fill, outline=fill, tags="card"
        )
        for cx, cy in (
            (x1, y1),
            (x2 - 2 * radius, y1),
            (x1, y2 - 2 * radius),
            (x2 - 2 * radius, y2 - 2 * radius),
        ):
            self.canvas.create_oval(
                cx, cy, cx + 2 * radius, cy + 2 * radius,
                fill=fill, outline=fill, tags="card",
            )

    def _draw_card(self, x1, y1, x2, y2):
        self._round_rect(x1, y1, x2, y2, 16, CARD_BORDER)
        self._round_rect(x1 + 1, y1 + 1, x2 - 1, y2 - 1, 15, CARD_BG)
        self.canvas.create_line(
            x1 + 18, y1 + 1, x2 - 18, y1 + 1,
            fill="#3a4f75", tags="card",
        )

    def _on_configure(self, event):
        if event.widget is self.canvas:
            if self._layout_job:
                self.root.after_cancel(self._layout_job)
            self._layout_job = self.root.after(60, self._layout)

    def _layout(self):
        self._layout_job = None
        if self.closed:
            return
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 20 or height < 20:
            return

        pad = 32
        header_h = 112
        url_top = header_h
        url_h = 128
        settings_top = url_top + url_h + 16
        settings_h = 224
        log_top = settings_top + settings_h + 16
        log_bottom = height - 56

        self.canvas.delete("card")
        self.canvas.delete("decor")

        self.canvas.create_text(
            pad + 4, 46, text="虫虫钢琴曲谱客户端", anchor="w",
            fill=TEXT, font=(FONT, 24, "bold"), tags="decor",
        )
        self.canvas.create_text(
            pad + 5, 82,
            text="粘贴曲谱网址 · 自动下载 ccmz · 导出 MIDI / MusicXML / 谱面",
            anchor="w", fill=MUTED, font=(FONT, 10), tags="decor",
        )

        self._draw_card(pad, url_top, width - pad, url_top + url_h)
        self._draw_card(pad, settings_top, width - pad, settings_top + settings_h)
        self._draw_card(pad, log_top, width - pad, log_bottom)

        inner_x = pad + 22
        inner_w = width - 2 * pad - 44
        self.url_frame.place(
            x=inner_x, y=url_top + 18, width=inner_w, height=url_h - 36
        )
        self.settings_frame.place(
            x=inner_x, y=settings_top + 18,
            width=inner_w, height=settings_h - 36,
        )
        log_inner_h = max(80, log_bottom - log_top - 36)
        self.log_frame.place(
            x=inner_x, y=log_top + 18, width=inner_w, height=log_inner_h
        )

        self.canvas.delete("status_text")
        self._status_item = self.canvas.create_text(
            pad + 4, height - 32,
            text=self.status_var.get(), anchor="w",
            fill=MUTED, font=(FONT, 10), tags="status_text",
        )

    def _build_base(self, width, height):
        gradient = Image.new("RGB", (1, height))
        pixels = gradient.load()
        top = (5, 7, 15)
        mid = (8, 16, 38)
        bottom = (10, 18, 38)
        for y in range(height):
            ratio = y / max(1, height - 1)
            if ratio < 0.5:
                local = ratio * 2
                color = tuple(
                    int(top[i] + (mid[i] - top[i]) * local) for i in range(3)
                )
            else:
                local = (ratio - 0.5) * 2
                color = tuple(
                    int(mid[i] + (bottom[i] - mid[i]) * local) for i in range(3)
                )
            pixels[0, y] = color
        return gradient.resize((width, height))

    def _aurora_frame(self, base, width, height, moment):
        glow = Image.new("RGB", (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(glow)
        blobs = (
            (0.18, 0.22, 0.34, 0.16, (14, 56, 96), 0.0),
            (0.72, 0.30, 0.38, 0.15, (28, 22, 76), 1.9),
            (0.48, 0.82, 0.44, 0.17, (10, 48, 72), 3.6),
            (0.94, 0.72, 0.26, 0.12, (18, 34, 88), 5.2),
        )
        for cx, cy, rx, ry, color, phase in blobs:
            x = (cx + 0.05 * math.sin(moment * 0.32 + phase)) * width
            y = (cy + 0.045 * math.cos(moment * 0.27 + phase)) * height
            radius_x = rx * width * 1.35
            radius_y = ry * height * 1.35
            draw.ellipse(
                (x - radius_x, y - radius_y, x + radius_x, y + radius_y),
                fill=color,
            )
        glow = glow.filter(ImageFilter.GaussianBlur(radius=max(6, width // 18)))
        return ImageChops.add(base, glow)

    def _animate(self):
        self._anim_job = None
        if self.closed:
            return
        if HAS_PIL:
            try:
                width = self.canvas.winfo_width()
                height = self.canvas.winfo_height()
                if width > 20 and height > 20:
                    low_w = max(120, min(340, width // 3))
                    low_h = max(90, int(low_w * height / width))
                    if (low_w, low_h) != self._base_size:
                        self._base_size = (low_w, low_h)
                        self._base_cache = self._build_base(low_w, low_h)
                    frame = self._aurora_frame(
                        self._base_cache, low_w, low_h, time.monotonic()
                    )
                    frame = frame.resize((width, height), Image.BICUBIC)
                    self._bg_photo = ImageTk.PhotoImage(frame)
                    self.canvas.itemconfig(self._bg_item, image=self._bg_photo)
            except Exception:
                pass
        self._anim_job = self.root.after(60, self._animate)

    # ------------------------------------------------------------------
    # 日志与状态
    # ------------------------------------------------------------------
    def _append_log(self, message, level="info"):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp}  {message}\n", level)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, text, color=MUTED):
        self.status_var.set(text)
        if self._status_item:
            self.canvas.itemconfig(self._status_item, text=text, fill=color)

    def _poll_queue(self):
        if self.closed:
            return
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    message, level = payload
                    self._append_log(message, level)
                elif kind == "status":
                    self._set_status(payload, ACCENT)
                elif kind == "name":
                    if not self.name_var.get().strip():
                        self.name_var.set(payload)
                elif kind == "error":
                    self._finish(False, payload)
                elif kind == "done":
                    self._finish(True, "")
        except queue.Empty:
            pass
        self.root.after(70, self._poll_queue)

    def _finish(self, ok, error):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.start_btn.configure(state="normal", text="开始下载并导出")
        self.browse_btn.configure(state="normal")
        if error:
            self._set_status("失败", "#ff8a9b")
            self._append_log(error, "err")
            messagebox.showerror("导出失败", error, parent=self.root)
        elif ok:
            self._set_status("完成", "#7ee2a8")
            self._append_log("全部完成。", "ok")
        else:
            self._set_status("部分完成", "#ffd479")
            self._append_log("导出结束，但有部分格式未生成，详见上方日志。", "warn")

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def browse_output(self):
        initial = self.out_dir_var.get().strip() or str(BASE_DIR)
        chosen = filedialog.askdirectory(parent=self.root, initialdir=initial)
        if chosen:
            self.out_dir_var.set(chosen)

    def open_output(self):
        path = Path(self.out_dir_var.get().strip()).expanduser()
        if not path.exists():
            messagebox.showwarning("目录不存在", "输出目录还不存在。", parent=self.root)
            return
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def start(self):
        if self.busy:
            return

        page_url = self.url_var.get().strip()
        if not page_url:
            messagebox.showwarning("缺少网址", "请先粘贴虫虫钢琴曲谱网址。", parent=self.root)
            return

        types = {key for key, var in self.type_vars.items() if var.get()}
        if not types:
            messagebox.showwarning("未选择格式", "请至少勾选一种导出格式。", parent=self.root)
            return

        out_text = self.out_dir_var.get().strip()
        if not out_text:
            messagebox.showwarning("缺少目录", "请先填写输出目录。", parent=self.root)
            return
        output_dir = Path(out_text).expanduser()
        name = self.name_var.get().strip()

        self.busy = True
        self.start_btn.configure(state="disabled", text="处理中…")
        self.browse_btn.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self._set_status("正在获取曲谱页面…", ACCENT)
        self._append_log("=" * 46, "info")
        self._append_log(f"开始处理: {page_url}", "info")

        thread = threading.Thread(
            target=self._worker,
            args=(page_url, types, output_dir, name),
            daemon=True,
        )
        thread.start()

    def _worker(self, page_url, types, output_dir, name):
        def log(message, level="info"):
            self.queue.put(("log", (message, level)))

        def status(message):
            self.queue.put(("status", message))

        try:
            status("正在获取曲谱页面…")
            ccmz_url, title = gangqinpu.fetch_score_page(page_url)
            log(f"已找到 ccmz 地址: {ccmz_url}", "ok")

            base_name = safe_base_name(name) if name else ""
            if not base_name:
                candidate = safe_base_name(title[:80]) if title else ""
                if not candidate or candidate == "score":
                    match = re.search(r"/cchtml/(\d+)\.htm", page_url)
                    candidate = f"曲谱-{match.group(1)}" if match else "score"
                base_name = candidate
                self.queue.put(("name", base_name))

            status("正在下载 ccmz…")
            data = gangqinpu.download_ccmz(ccmz_url, referer=page_url)
            output_dir.mkdir(parents=True, exist_ok=True)
            ccmz_path = output_dir / f"{base_name}.ccmz"
            ccmz_path.write_bytes(data)
            log(f"ccmz 已保存: {ccmz_path}（{len(data)} 字节）", "ok")

            status("正在解析 ccmz…")
            info = LibCCMZ.read_ccmz(data)
            log(f"解析成功，版本 v{info.version}", "ok")

            status("正在导出所选格式…")
            with redirect_stdout(_QueueWriter(self)):
                ok = export_ccmz(info, types, output_dir, base_name)
            self.queue.put(("done", ok))
        except requests.Timeout:
            self.queue.put(("error", "请求超时，请检查网络后重试。"))
        except requests.ConnectionError:
            self.queue.put(("error", "网络连接失败，请检查网络或代理设置。"))
        except requests.HTTPError as exc:
            self.queue.put(("error", f"请求失败（HTTP {exc.response.status_code}）。"))
        except (OSError, ValueError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # 兜底，避免线程静默退出
            self.queue.put(("error", f"处理失败: {exc}"))

    def _on_close(self):
        self.closed = True
        if self._anim_job:
            self.root.after_cancel(self._anim_job)
        if self._layout_job:
            self.root.after_cancel(self._layout_job)
        self.root.destroy()


def main():
    if os.name == "nt":
        try:  # 高 DPI 下避免界面发糊
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    ChongchongClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
