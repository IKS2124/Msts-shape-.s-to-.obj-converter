"""
app.py - MSTS/Open Rails .s -> Wavefront .obj converter (desktop GUI)

Run directly with Python (3.8+, needs numpy):
    pip install numpy
    python app.py

Or build a standalone Windows .exe - see BUILD_EXE.txt in this folder.
"""

import os
import sys
import threading
import queue
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import s_converter


APP_TITLE = "MSTS .s -> OBJ Converter"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("640x420")
        self.minsize(560, 360)

        self.s_path = None
        self.worker_thread = None
        self.cancel_event = threading.Event()
        self.msg_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()

        self.protocol("WM_DELETE_WINDOW", self.on_exit)

    # ---------------------------------------------------------- UI layout
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        top = tk.Frame(self)
        top.pack(fill="x", **pad)

        self.btn_open = tk.Button(top, text="Open .s File...", width=18, command=self.on_open)
        self.btn_open.pack(side="left")

        self.btn_export = tk.Button(top, text="Export as .obj...", width=18,
                                     command=self.on_export, state="disabled")
        self.btn_export.pack(side="left", padx=(10, 0))

        self.btn_exit = tk.Button(top, text="Exit", width=10, command=self.on_exit)
        self.btn_exit.pack(side="right")

        file_frame = tk.Frame(self)
        file_frame.pack(fill="x", **pad)
        tk.Label(file_frame, text="Selected file:").pack(side="left")
        self.lbl_file = tk.Label(file_frame, text="(none)", anchor="w", fg="#444")
        self.lbl_file.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=10, pady=(0, 6))

        self.lbl_status = tk.Label(self, text="Open an uncompressed .s file to begin.", anchor="w")
        self.lbl_status.pack(fill="x", padx=10)

        log_frame = tk.Frame(self)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.log = tk.Text(log_frame, wrap="word", state="disabled", height=12)
        scrollbar = tk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    # ---------------------------------------------------------- helpers
    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.btn_open.configure(state=state)
        self.btn_export.configure(state=state if self.s_path else "disabled")

    # ---------------------------------------------------------- button: Open
    def on_open(self):
        path = filedialog.askopenfilename(
            title="Open MSTS/Open Rails .s shape file",
            filetypes=[("Shape files", "*.s"), ("All files", "*.*")],
        )
        if not path:
            return
        self.s_path = path
        self.lbl_file.configure(text=path)
        self.btn_export.configure(state="normal")
        self.lbl_status.configure(text="Ready to export.")
        size_mb = os.path.getsize(path) / (1024 * 1024)
        self._log(f"Opened: {path}  ({size_mb:.1f} MB)")

    # ---------------------------------------------------------- button: Export
    def on_export(self):
        if not self.s_path:
            return
        default_name = os.path.splitext(os.path.basename(self.s_path))[0] + ".obj"
        out_path = filedialog.asksaveasfilename(
            title="Export as Wavefront OBJ",
            defaultextension=".obj",
            initialfile=default_name,
            filetypes=[("Wavefront OBJ", "*.obj")],
        )
        if not out_path:
            return

        self.cancel_event = threading.Event()
        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.lbl_status.configure(text="Converting...")
        self._log(f"\nExporting to: {out_path}")

        self.worker_thread = threading.Thread(
            target=self._run_conversion, args=(self.s_path, out_path), daemon=True
        )
        self.worker_thread.start()

    def _run_conversion(self, s_path, out_path):
        def progress_cb(stage, cur, total):
            self.msg_queue.put(("progress", stage, cur, total))

        try:
            stats = s_converter.convert(
                s_path, out_path, progress=progress_cb, cancel_event=self.cancel_event
            )
            self.msg_queue.put(("done", stats))
        except s_converter.Cancelled:
            self.msg_queue.put(("cancelled", None))
        except s_converter.ConversionError as e:
            self.msg_queue.put(("error", str(e)))
        except Exception:
            self.msg_queue.put(("error", "Unexpected error:\n" + traceback.format_exc()))

    # ---------------------------------------------------------- background queue polling
    def _poll_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                kind = item[0]

                if kind == "progress":
                    _, stage, cur, total = item
                    if total:
                        self.progress.configure(mode="determinate", maximum=total)
                        self.progress["value"] = cur
                        self.lbl_status.configure(text=f"{stage} ({cur}/{total})")
                    else:
                        self.lbl_status.configure(text=stage)

                elif kind == "done":
                    stats = item[1]
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=100, maximum=100)
                    self._set_busy(False)
                    self.lbl_status.configure(text="Done.")
                    self._log(
                        "Finished:\n"
                        f"  Points:        {stats['points']:,}\n"
                        f"  Normals:       {stats['normals']:,}\n"
                        f"  UV points:     {stats['uv_points']:,}\n"
                        f"  Sub-objects:   {stats['sub_objects']:,}\n"
                        f"  Vertices out:  {stats['vertices_written']:,}\n"
                        f"  Faces out:     {stats['faces_written']:,}\n"
                        f"  Materials:     {stats['materials']:,}\n"
                        f"  OBJ: {stats['obj_path']}\n"
                        f"  MTL: {stats['mtl_path']}"
                    )
                    messagebox.showinfo(
                        APP_TITLE,
                        f"Export complete.\n\n"
                        f"{stats['vertices_written']:,} vertices, "
                        f"{stats['faces_written']:,} faces, "
                        f"{stats['materials']:,} materials.\n\n"
                        f"Saved to:\n{stats['obj_path']}"
                    )

                elif kind == "cancelled":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self._set_busy(False)
                    self.lbl_status.configure(text="Cancelled.")
                    self._log("Export cancelled.")

                elif kind == "error":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self._set_busy(False)
                    self.lbl_status.configure(text="Failed - see log.")
                    self._log("ERROR: " + item[1])
                    messagebox.showerror(APP_TITLE, "Conversion failed:\n\n" + item[1])

        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    # ---------------------------------------------------------- button: Exit
    def on_exit(self):
        if self.worker_thread is not None and self.worker_thread.is_alive():
            if not messagebox.askyesno(
                APP_TITLE, "A conversion is still running. Cancel it and exit?"
            ):
                return
            self.cancel_event.set()
            self.worker_thread.join(timeout=2.0)
        self.destroy()
        sys.exit(0)


if __name__ == "__main__":
    App().mainloop()
