"""RASAD (رصد) - میانگین درصد آخرین معامله‌ی شرکت‌های منتخب بورس ایران."""
import datetime as dt
import json
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import arabic_reshaper
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from bidi.algorithm import get_display
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from market import DOWNLOAD_DIR, MarketError, fetch_market, norm
from watchlist import load_names, match_companies

APP_NAME = "RASAD"
APP_VERSION = "2.0.0"
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RASAD")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
WATCH_FILE = os.path.join(DATA_DIR, "namad.txt")      # فهرست شرکت‌ها (قابل ویرایش)
SNAP_PREFIX = "snap3"
FONT = "Tahoma"
GREEN, RED, GRAY = "#0a8a0a", "#c62828", "#9e9e9e"
C_AVG = "#00008b"
PALETTE = ["#e65100", "#00897b", "#c2185b", "#6d4c41", "#546e7a",
           "#7cb342", "#f9a825", "#0277bd", "#8e24aa", "#d84315"]

# هیچ متن فارسی‌ای داخل matplotlib رسم نمی‌شود؛ فقط ویجت‌های Tk فارسی دارند.
matplotlib.rcParams["font.family"] = [FONT, "DejaVu Sans"]


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)


def fa(text: str) -> str:
    """آماده‌سازی متن فارسی برای Tk. در متن فارسی از / و = و رقم فارسی استفاده نکنید."""
    shaped = arabic_reshaper.reshape(text)
    try:
        return get_display(shaped, base_dir="R")
    except TypeError:
        return get_display(shaped)


def key_of(s: str) -> str:
    return norm(s).replace(" ", "")


class RasadApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"رصد - {APP_NAME} {APP_VERSION}")
        self.geometry("1040x720")
        self.minsize(800, 540)
        try:
            self.iconbitmap(resource_path("assets/rasad.ico"))
        except Exception:
            pass
        self.q = queue.Queue()
        self.snaps = []       # (datetime, میانگین، تعداد، {نماد: درصد آخرین})
        self.hist = []        # (datetime, میانگین)
        self.symbols = []     # نمادهای اضافه‌شده به نمودار
        self.rows = []        # آخرین نتیجه‌ی تطبیق فهرست
        self.last_file = None
        self.row_symbol = {}
        self.busy = False
        self.auto = True
        self.next_run = 0.0
        self.interval = tk.IntVar(value=2)
        self._ensure_watch_file()
        self._load_settings()
        self._build_ui()
        self._cleanup_old()
        self._load_history()
        self._rebuild_hist()
        self._rebuild_legend()
        self._draw_chart()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(300, self._poll)
        self.after(500, self._tick)
        self.refresh()

    # ---------------- فایل‌ها و تنظیمات ----------------
    def _ensure_watch_file(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            if not os.path.exists(WATCH_FILE):
                shutil.copyfile(resource_path("assets/namad.txt"), WATCH_FILE)
        except OSError:
            pass

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s = json.load(f)
            self.symbols = [x for x in s.get("symbols", []) if isinstance(x, str)]
            self.interval.set(int(s.get("interval", 2)))
        except (OSError, ValueError):
            pass

    def _save_settings(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({"symbols": self.symbols, "interval": self._minutes()},
                          f, ensure_ascii=False)
        except OSError:
            pass

    def _close(self):
        self._save_settings()
        self.destroy()

    def _minutes(self):
        try:
            return min(30, max(1, int(self.interval.get())))
        except Exception:
            return 2

    # ---------------- رابط کاربری ----------------
    def _build_ui(self):
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        self.btn_toggle = ttk.Button(top, text=fa("توقف"), command=self.toggle)
        self.btn_toggle.pack(side="left")
        ttk.Button(top, text=fa("به‌روزرسانی اکنون"), command=self.refresh).pack(side="left", padx=6)
        ttk.Label(top, text=fa("فاصله (دقیقه):")).pack(side="left", padx=(12, 2))
        ttk.Spinbox(top, from_=1, to=30, width=4, textvariable=self.interval).pack(side="left")
        ttk.Button(top, text=fa("ویرایش فهرست شرکت‌ها"),
                   command=self.edit_list).pack(side="left", padx=(12, 0))
        ttk.Button(top, text=fa("باز کردن فایل اکسل"),
                   command=self.open_file).pack(side="left", padx=6)
        self.lbl_status = ttk.Label(top, text="")
        self.lbl_status.pack(side="right")

        head = ttk.Frame(self, padding=(10, 4))
        head.pack(fill="x")
        try:
            self.logo_img = tk.PhotoImage(file=resource_path("assets/logo_small.png"))
            tk.Label(head, image=self.logo_img).place(x=0, y=0)
        except Exception:
            pass
        ttk.Label(head, text=fa("درصد قیمت آخرین معامله شرکت‌های منتخب"),
                  font=(FONT, 12, "bold")).pack(anchor="e")
        self.lbl_avg = ttk.Label(head, text=fa("میانگین درصد آخرین معامله: -"),
                                 font=(FONT, 14, "bold"))
        self.lbl_avg.pack(anchor="e", pady=2)
        self.lbl_info = ttk.Label(head, text="", font=(FONT, 9))
        self.lbl_info.pack(anchor="e")
        self.lbl_src = ttk.Label(head, text="", font=(FONT, 9))
        self.lbl_src.pack(anchor="e")
        self.lbl_err = ttk.Label(head, text="", font=(FONT, 9, "bold"), foreground=RED,
                                 justify="right")
        self.lbl_err.pack(anchor="e")
        self.lbl_hint = ttk.Label(head, text="", font=(FONT, 9), foreground="#b36b00",
                                  justify="right")
        self.lbl_hint.pack(anchor="e")
        self.lbl_detail = ttk.Label(head, text="", font=("Consolas", 8), foreground=RED,
                                    wraplength=950, justify="left")
        self.lbl_detail.pack(anchor="e")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        tab_chart = ttk.Frame(nb)
        tab_table = ttk.Frame(nb)
        nb.add(tab_chart, text=fa("نمودار روند"))
        nb.add(tab_table, text=fa("شرکت‌های منتخب"))

        bar = ttk.Frame(tab_chart, padding=(4, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text=fa("افزودن"), command=self.add_from_entry).pack(side="left")
        self.sym_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.sym_var, width=16, font=(FONT, 10))
        ent.pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: self.add_from_entry())
        ttk.Label(bar, text=fa("افزودن نماد به نمودار:")).pack(side="left", padx=(4, 12))

        self.legend = ttk.Frame(tab_chart, padding=(6, 2))
        self.legend.pack(side="bottom", fill="x")

        holder = ttk.Frame(tab_chart)
        holder.pack(fill="both", expand=True)
        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=holder)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.lbl_wait = tk.Label(holder, text=fa("در انتظار دریافت اولین داده..."),
                                 font=(FONT, 11), bg="white")

        tbar = ttk.Frame(tab_table, padding=(4, 4))
        tbar.pack(fill="x")
        ttk.Button(tbar, text=fa("افزودن موارد انتخاب‌شده به نمودار"),
                   command=self.add_selected).pack(side="right")
        ttk.Label(tbar, text=fa("با دابل‌کلیک روی هر ردیف، نماد آن به نمودار اضافه می‌شود"),
                  font=(FONT, 9)).pack(side="left")

        cols = ("name", "symbol", "yest", "last", "last_pct", "status")
        titles = ("نام شرکت", "نماد", "دیروز", "آخرین معامله", "درصد آخرین", "وضعیت")
        widths = (280, 100, 90, 100, 100, 90)
        self.tree = ttk.Treeview(tab_table, columns=cols, show="headings", selectmode="extended")
        for c, t, w in zip(cols, titles, widths):
            self.tree.heading(c, text=fa(t), command=lambda c=c: self._sort(c))
            self.tree.column(c, width=w, anchor="center")
        sb = ttk.Scrollbar(tab_table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("up", foreground=GREEN)
        self.tree.tag_configure("down", foreground=RED)
        self.tree.tag_configure("none", foreground=GRAY)
        self.tree.bind("<Double-1>", lambda e: self.add_selected())
        self._sort_rev = {}

    def _sort(self, col):
        rev = self._sort_rev.get(col, False)

        def key(v):
            try:
                return (0, float(v.replace(",", "")))
            except ValueError:
                return (1, v)

        items = sorted(((self.tree.set(i, col), i) for i in self.tree.get_children("")),
                       key=lambda x: key(x[0]), reverse=rev)
        for idx, (_, iid) in enumerate(items):
            self.tree.move(iid, "", idx)
        self._sort_rev[col] = not rev

    # ---------------- نمادهای نمودار ----------------
    def _color(self, i):
        return PALETTE[i % len(PALETTE)]

    def add_symbol(self, sym):
        if sym and sym not in self.symbols:
            self.symbols.append(sym)
            self._save_settings()
            self._rebuild_legend()
            self._draw_chart()

    def remove_symbol(self, sym):
        if sym in self.symbols:
            self.symbols.remove(sym)
            self._save_settings()
            self._rebuild_legend()
            self._draw_chart()

    def add_selected(self):
        for iid in self.tree.selection():
            self.add_symbol(self.row_symbol.get(iid))

    def add_from_entry(self):
        txt = key_of(self.sym_var.get())
        if not txt:
            return
        rows = [r for r in self.rows if r["symbol"]]
        if not rows:
            self.lbl_err.config(text=fa("هنوز داده‌ای دریافت نشده؛ کمی صبر کنید."))
            return
        found = [r for r in rows if key_of(r["symbol"]) == txt] or \
                [r for r in rows if txt in key_of(r["symbol"]) or txt in key_of(r["name"])]
        if len(found) == 1:
            self.lbl_err.config(text="")
            self.add_symbol(found[0]["symbol"])
            self.sym_var.set("")
        elif not found:
            self.lbl_err.config(text=fa("در شرکت‌های منتخب چنین نماد یا نامی پیدا نشد."))
        else:
            self.lbl_err.config(text=fa(f"{len(found)} مورد مطابقت دارد؛ نام دقیق‌تری وارد کنید یا از تب شرکت‌های منتخب انتخاب کنید."))

    def _rebuild_legend(self):
        for w in self.legend.winfo_children():
            w.destroy()
        items = [(C_AVG, "میانگین شرکت‌های منتخب", None)]
        for i, s in enumerate(self.symbols):
            items.append((self._color(i), s, s))
        for n, (color, text, sym) in enumerate(items):
            cell = ttk.Frame(self.legend)
            cell.grid(row=n // 4, column=n % 4, padx=8, pady=2, sticky="w")
            tk.Label(cell, bg=color, width=2).pack(side="left")
            ttk.Label(cell, text=fa(text)).pack(side="left", padx=3)
            if sym:
                x = tk.Label(cell, text="×", fg=RED, cursor="hand2", font=(FONT, 11, "bold"))
                x.pack(side="left")
                x.bind("<Button-1>", lambda e, s=sym: self.remove_symbol(s))

    # ---------------- کنترل ----------------
    def toggle(self):
        self.auto = not self.auto
        self.btn_toggle.config(text=fa("توقف") if self.auto else fa("شروع"))
        if self.auto:
            self.refresh()
        else:
            self.lbl_status.config(text=fa("متوقف شد"))

    def open_file(self):
        if self.last_file and os.path.exists(self.last_file):
            os.startfile(self.last_file)  # فقط ویندوز

    def edit_list(self):
        """فهرست شرکت‌ها را در Notepad باز می‌کند؛ هر خط یک نام شرکت. تغییرات در به‌روزرسانی بعدی اعمال می‌شود."""
        self._ensure_watch_file()
        os.startfile(WATCH_FILE)

    def _clear_error(self):
        self.lbl_err.config(text="")
        self.lbl_hint.config(text="")
        self.lbl_detail.config(text="")

    def _show_error(self, msg, detail=""):
        self.lbl_err.config(text=fa(msg))
        hint = ""
        if any(k in detail for k in ("SSL", "Connection", "Max retries", "timed out",
                                     "Timeout", "resolve", "Proxy", "refused", "reset")):
            hint = (fa("احتمالاً سایت بورس از IP فعلی شما در دسترس نیست.") + "\n" +
                    fa("از IP ایران یا VPN ایران استفاده کنید و دوباره امتحان کنید."))
        self.lbl_hint.config(text=hint)
        self.lbl_detail.config(text=detail[:700])

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self._save_settings()
        self.lbl_status.config(text=fa("در حال دریافت اطلاعات..."))
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            df, info = fetch_market(lambda m: self.q.put(("stage", m)))
            self.q.put(("stage", "در حال تطبیق فهرست شرکت‌ها..."))
            rows = match_companies(df, load_names(WATCH_FILE))
            self.q.put(("ok", (rows, info)))
        except MarketError as e:
            self.q.put(("err", (e.msg, e.detail)))
        except OSError as e:
            self.q.put(("err", ("فهرست شرکت‌ها خوانده نشد.", str(e))))
        except Exception as e:  # noqa
            self.q.put(("err", ("خطای غیرمنتظره در برنامه", f"{type(e).__name__}: {e}")))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "stage":
                    self.lbl_status.config(text=fa(payload))
                    continue
                self.busy = False
                self.next_run = time.time() + self._minutes() * 60
                if kind == "ok":
                    self._clear_error()
                    self._on_data(payload)
                else:
                    self._show_error(*payload)
        except queue.Empty:
            pass
        self.after(300, self._poll)

    def _tick(self):
        if self.auto and not self.busy and self.next_run:
            left = int(self.next_run - time.time())
            if left <= 0:
                self.refresh()
            else:
                self.lbl_status.config(text=fa(f"به‌روزرسانی بعدی: {left // 60}:{left % 60:02d}"))
        self.after(1000, self._tick)

    # ---------------- داده ----------------
    def _on_data(self, payload):
        rows, info = payload
        self.rows = rows
        self.last_file = info["path"]
        self.lbl_src.config(text=fa(f"فایل اکسل دریافت‌شده: {os.path.basename(info['path'])} "
                                    f"({info['size'] // 1024} KB)"))
        now = dt.datetime.now()
        matched = [r for r in rows if r["symbol"]]
        self._fill_table(rows)
        if not matched:
            self.lbl_avg.config(text=fa("میانگین درصد آخرین معامله: -"), foreground="")
            self._show_error("هیچ‌کدام از شرکت‌های فهرست در فایل بازار پیدا نشد.",
                             "Check names in namad.txt or the Name column of the downloaded file")
            return
        avg = sum(r["last_pct"] for r in matched) / len(matched)
        up = sum(1 for r in matched if r["last_pct"] > 0)
        down = sum(1 for r in matched if r["last_pct"] < 0)
        self.lbl_avg.config(text=fa("میانگین درصد آخرین معامله: ") + f"{avg:+.2f}%",
                            foreground=GREEN if avg >= 0 else RED)
        self.lbl_info.config(text=fa(
            f"{len(matched)} شرکت از {len(rows)} شرکت فهرست پیدا شد، مثبت: {up}، منفی: {down}، "
            f"بدون تغییر: {len(matched) - up - down}، آخرین به‌روزرسانی: {now:%H:%M:%S}"))
        if self.snaps and self.snaps[0][0].date() != now.date():
            self.snaps.clear()
        snap = {r["symbol"]: float(r["last_pct"]) for r in matched}
        self.snaps.append((now, avg, len(matched), snap))
        self._save_snap(now, avg, len(matched), snap)
        self._rebuild_hist()
        self._draw_chart()

    def _fill_table(self, rows):
        self.tree.delete(*self.tree.get_children())
        self.row_symbol.clear()
        for i, r in enumerate(rows):
            iid = str(i)
            if r["symbol"]:
                self.row_symbol[iid] = r["symbol"]
                tag = "up" if r["last_pct"] > 0 else ("down" if r["last_pct"] < 0 else "")
                vals = (fa(norm(r["name"])), fa(r["symbol"]), f"{r['yest']:,.0f}",
                        f"{r['last']:,.0f}", f"{r['last_pct']:+.2f}", fa(r["status"]))
            else:
                tag = "none"
                vals = (fa(norm(r["name"])), "-", "-", "-", "-", fa(r["status"]))
            self.tree.insert("", "end", iid=iid, tags=(tag,), values=vals)

    # ---------------- تاریخچه ----------------
    def _snap_path(self):
        return os.path.join(DATA_DIR, f"{SNAP_PREFIX}_{dt.date.today():%Y-%m-%d}.jsonl")

    def _save_snap(self, t, avg, n, snap):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(self._snap_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": t.isoformat(timespec="seconds"), "avg": round(avg, 4),
                                    "n": n, "d": {s: round(v, 3) for s, v in snap.items()}},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _load_history(self):
        try:
            with open(self._snap_path(), encoding="utf-8") as f:
                for line in f:
                    o = json.loads(line)
                    self.snaps.append((dt.datetime.fromisoformat(o["t"]), float(o["avg"]),
                                       int(o["n"]), {s: float(v) for s, v in o["d"].items()}))
        except (OSError, ValueError, KeyError):
            pass

    def _rebuild_hist(self):
        self.hist = [(t, avg) for t, avg, _, _ in self.snaps]

    def _cleanup_old(self):
        try:
            limit = dt.date.today() - dt.timedelta(days=10)
            for n in os.listdir(DATA_DIR):
                if n.startswith(("history_", "snap_", "snap2_", f"{SNAP_PREFIX}_")):
                    d = dt.date.fromisoformat(n.split("_", 1)[1].split(".")[0])
                    if d < limit:
                        os.remove(os.path.join(DATA_DIR, n))
        except (OSError, ValueError):
            pass

    # ---------------- نمودار ----------------
    def _draw_chart(self):
        ax = self.ax
        ax.clear()
        if self.hist:
            self.lbl_wait.place_forget()
            xs = [h[0] for h in self.hist]
            ys = [h[1] for h in self.hist]
            ax.plot(xs, ys, color=C_AVG, lw=2.2)
            allv = list(ys)
            for i, sym in enumerate(self.symbols):
                pts = [(t, d[sym]) for t, _, _, d in self.snaps if sym in d]
                if pts:
                    px, py = zip(*pts)
                    ax.plot(px, py, color=self._color(i), lw=1.5, ls="--", marker="o", ms=2.5)
                    allv += list(py)
            lo, hi = min(0, min(allv)), max(0.25, max(allv))
            pad = (hi - lo) * 0.08
            ax.set_ylim(lo - pad, hi + pad)
            if len(xs) > 1:
                ax.set_xlim(xs[0], xs[-1] + dt.timedelta(minutes=1))
            else:
                ax.set_xlim(xs[0] - dt.timedelta(minutes=5), xs[0] + dt.timedelta(minutes=5))
            span = (xs[-1] - xs[0]).total_seconds() / 60
            step = 20 if span > 60 else (10 if span > 20 else 2)
            ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=range(0, 60, step)))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        else:
            self.lbl_wait.place(relx=0.5, rely=0.5, anchor="center")
        ax.axhline(0, color="#007070", ls=":", lw=1)
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        self.fig.tight_layout()
        self.canvas.draw_idle()


if __name__ == "__main__":
    RasadApp().mainloop()
