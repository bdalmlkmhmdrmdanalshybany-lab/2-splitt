# -*- coding: utf-8 -*-
"""
Super File Plus 1.2 - النسخة الشاملة والنهائية
ميزات: PS2 (تقسيم، ZSO، BIN)، إدارة ul.cfg، فك ضغط (7z, zip, tar)، مكررات، AI
"""
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tarfile
import textwrap
import threading
import time
import zipfile

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import Screen, ScreenManager, SlideTransition
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import escape_markup, platform

try:
    import lz4.block
    HAVE_LZ4 = True
except ImportError:
    HAVE_LZ4 = False

# ==================================================================
# 1) ثوابت عامة وإعدادات
# ==================================================================
APP_VERSION = "1.3"
DEFAULT_ROOT = "/storage/emulated/0/"
DEFAULT_FOLDER = DEFAULT_ROOT + "Download"
PART_SIZE = 1 << 30
CHUNK = 4 << 20
OPL_DIRS = ("DVD", "CD", "VMC", "ART", "CFG")
DUP_DIR = "_Duplicates"

C_BG = (0.06, 0.08, 0.12, 1)
C_NAV = (0.08, 0.10, 0.15, 1)
C_CARD = (0.10, 0.13, 0.19, 1)
C_CARD2 = (0.14, 0.18, 0.26, 1)
C_INPUT = (0.15, 0.18, 0.26, 1)
C_TEXT = (0.93, 0.95, 0.98, 1)
C_MUTED = (0.62, 0.68, 0.78, 1)
C_ACCENT = (0.38, 0.62, 0.97, 1)
C_TEAL = (0.26, 0.72, 0.62, 1)
C_PURPLE = (0.62, 0.52, 0.96, 1)
C_AMBER = (0.95, 0.68, 0.30, 1)
C_RED = (0.88, 0.38, 0.40, 1)
C_GRAY = (0.24, 0.28, 0.37, 1)

Window.clearcolor = C_BG
Window.softinput_mode = "below_target"

# ==================================================================
# 2) دعم العربية
# ==================================================================
try:
    import arabic_reshaper
    HAVE_RESHAPER = True
except Exception:
    HAVE_RESHAPER = False

try:
    from bidi.algorithm import get_display
    HAVE_BIDI = True
except Exception:
    HAVE_BIDI = False

DEFAULT_AR_MODE = 2 if HAVE_BIDI else 5
AR_MODE = DEFAULT_AR_MODE
USE_MARKUP = True

_LTR_T = r"[A-Za-z0-9_./\\%+@#~\-]+"
_LTR_RUN = re.compile(_LTR_T + r"(?:[ ]+" + _LTR_T + r"|(?<=[0-9]):" + _LTR_T + r")*")
_MIRROR = str.maketrans("()[]<>{}", ")(][><}{")

def own_bidi(s):
    r = s[::-1].translate(_MIRROR)
    return _LTR_RUN.sub(lambda m: m.group(0)[::-1], r)

def shape_line(s, mode):
    if mode == 3: return s
    if HAVE_RESHAPER and mode in (1, 2, 5): s = arabic_reshaper.reshape(s)
    if mode == 1: return s
    if mode == 2 and HAVE_BIDI: return get_display(s)
    return own_bidi(s)

def ar(text, mode=None):
    m = AR_MODE if mode is None else mode
    return "\n".join(shape_line(line, m) if has_ar(line) else line for line in str(text).split("\n"))

def has_ar(text):
    return any("؀" <= c <= "ۿ" for c in str(text))

def _script_dir():
    try: return os.path.dirname(os.path.abspath(__file__))
    except Exception: return os.getcwd()

def _find_arabic_font():
    for d in (_script_dir(), os.getcwd()):
        f = os.path.join(d, "font.ttf")
        if os.path.isfile(f): return f
    for n in ("NotoNaskhArabic-Regular.ttf", "NotoSansArabic-Regular.ttf", "NotoSansArabicUI-Regular.ttf", "DroidSansArabic.ttf"):
        p = os.path.join("/system/fonts", n)
        if os.path.exists(p): return p
    found = glob.glob("/system/fonts/*Arabic*.ttf")
    return found[0] if found else None

AR_FONT = _find_arabic_font()

def _is_latin(ch): return ch.isascii() and (ch.isalpha() or ch == "_")

def mk(text, mode=None):
    s = ar(text, mode)
    if not AR_FONT or not has_ar(text): return escape_markup(s)
    runs, cur, cur_lat = [], "", None
    for ch in s:
        lat = _is_latin(ch)
        if cur_lat is None or lat == cur_lat:
            cur += ch; cur_lat = lat
        else:
            runs.append((cur, cur_lat)); cur, cur_lat = ch, lat
    if cur: runs.append((cur, cur_lat))
    return "".join("[font=%s]%s[/font]" % ("Roboto" if lat else AR_FONT, escape_markup(r)) for r, lat in runs)

def prep(w, text):
    if USE_MARKUP:
        w.markup = True; w.text = mk(text)
    else:
        w.markup = False
        w.font_name = AR_FONT if (AR_FONT and has_ar(text)) else "Roboto"
        w.text = ar(text)

def set_ar_display(mode, markup):
    global AR_MODE, USE_MARKUP
    AR_MODE = mode if mode in (1, 2, 3, 5) else DEFAULT_AR_MODE
    USE_MARKUP = bool(markup)

# ==================================================================
# 3) أدوات عامة (بما فيها محرك 7z)
# ==================================================================
def ui(fn, *args): Clock.schedule_once(lambda dt: fn(*args), 0)

def hsize(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024: return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f TB" % n

def get_7z_binary():
    app = App.get_running_app()
    bin_path = os.path.join(app.user_data_dir, "7z_bin")
    
    # إذا كان الملف المنسوخ سابقاً صغيراً (خطأ)، احذفه
    if os.path.exists(bin_path) and os.path.getsize(bin_path) < 1000000:
        try: os.remove(bin_path)
        except: pass

    if not os.path.exists(bin_path):
        # 1. محاولة القراءة من assets (عند العمل كـ APK)
        if platform == 'android':
            try:
                from android import mActivity
                with mActivity.getAssets().open("7z") as f_in, open(bin_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.chmod(bin_path, 0o755)
                return bin_path, None
            except Exception:
                pass
        
        # 2. البحث في عدة مسارات في الهاتف
        search_paths = [
            _script_dir(),
            os.getcwd(),
            "/storage/emulated/0/Download",
            "/storage/emulated/0/super file"
        ]
        
        found = False
        for p in search_paths:
            local_path = os.path.join(p, "7z")
            if os.path.exists(local_path) and os.path.isfile(local_path):
                if os.path.getsize(local_path) < 1000000:
                    return None, "الملف 7z صغير جداً. تأكد من اختيار libp7zbin.so (1.72M)."
                try:
                    shutil.copy2(local_path, bin_path)
                    os.chmod(bin_path, 0o755)
                    found = True
                    break
                except Exception:
                    pass
        
        if not found:
            return None, "ملف 7z غير موجود. تأكد من وضعه في مجلد download أو super file."
            
    return bin_path, None

def extract_7z_binary(archive_path, out_dir):
    bin_path, err = get_7z_binary()
    if err: return False, err
    os.makedirs(out_dir, exist_ok=True)
    try:
        cmd = [bin_path, "x", archive_path, f"-o{out_dir}", "-y"]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode == 0: return True, "تم بنجاح"
        return False, stderr.decode('utf-8', errors='ignore')
    except Exception as e: return False, str(e)

def file_md5(path):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(1024 * 1024)
                if not b: break
                h.update(b)
        return h.hexdigest()
    except OSError: return None

def quick_key(path, size):
    try:
        with open(path, "rb") as f:
            head = f.read(1024 * 1024)
            if size > 2 * 1024 * 1024:
                f.seek(size - 1024 * 1024); tail = f.read(1024 * 1024)
            else: tail = b""
        return hashlib.md5(head + tail).hexdigest()
    except OSError: return None

# --- OPL CRC ---
_M32 = 0xFFFFFFFF
def _build_opl_table():
    tab = [0] * 256; crc = 0
    for t in range(256):
        crc = (t << 24) & _M32
        for _ in range(8):
            if crc & 0x80000000: crc = (crc << 1) & _M32
            else: crc = ((crc << 1) ^ 0x04C11DB7) & _M32
        tab[255 - t] = crc
    return tab, crc

_OPL_TAB, _OPL_INIT = _build_opl_table()

def opl_crc(name):
    crc = _OPL_INIT
    for b in name.encode("ascii", "ignore")[:32] + b"\x00":
        crc = _OPL_TAB[(b ^ (crc >> 24)) & 0xFF] ^ ((crc << 8) & 0xFFFFFF00)
    return crc & _M32

def write_ul_cfg(folder, name, record):
    path = os.path.join(folder, "ul.cfg"); records = []
    if os.path.isfile(path):
        with open(path, "rb") as f: data = f.read()
        for i in range(0, len(data) - 63, 64):
            r = data[i:i + 64]
            if r[:32].split(b"\x00")[0].decode("ascii", "ignore") != name: records.append(r)
    records.append(record)
    with open(path, "wb") as f: f.write(b"".join(records))

def analyze_folder(folder):
    now = time.time(); total = count = old = apk = arch = iso = 0
    apk_size = arch_size = old_size = 0; big = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != DUP_DIR]
        for f in files:
            p = os.path.join(root, f)
            try:
                if os.path.islink(p): continue
                st = os.stat(p)
            except OSError: continue
            count += 1; total += st.st_size; big.append((st.st_size, f))
            ext = os.path.splitext(f)[1].lower()
            if ext == ".apk": apk += 1; apk_size += st.st_size
            elif ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"): arch += 1; arch_size += st.st_size
            elif ext in (".iso", ".bin", ".cue"): iso += 1
            if now - st.st_mtime > 180 * 86400: old += 1; old_size += st.st_size
    big.sort(reverse=True)
    return {"count": count, "total": total, "apk": apk, "apk_size": apk_size, "arch": arch, "arch_size": arch_size, "iso": iso, "old": old, "old_size": old_size, "big": big[:3]}

# --- ZSO & BIN ---
def zso_compress(iso_path, zso_path, progress_cb):
    if not HAVE_LZ4: raise ImportError("مكتبة lz4 غير مثبتة. اكتب pip install lz4")
    block_size = 2048
    with open(iso_path, "rb") as fin, open(zso_path, "wb") as fout:
        total = os.path.getsize(iso_path); blocks = total // block_size
        fout.write(struct.pack("<4sIIII", b"ZISO", 1, total, block_size, 0, blocks))
        for i in range(blocks):
            chunk = fin.read(block_size); comp = lz4.block.compress(chunk, store_size=False)
            if len(comp) < block_size: fout.write(struct.pack("<II", i, len(comp)) + comp)
            else: fout.write(struct.pack("<II", i, block_size) + chunk)
            if i % 1000 == 0: progress_cb(int(i * 100 / blocks))
    progress_cb(100)

def zso_decompress(zso_path, iso_path, progress_cb):
    if not HAVE_LZ4: raise ImportError("مكتبة lz4 غير مثبتة.")
    with open(zso_path, "rb") as fin, open(iso_path, "wb") as fout:
        magic, ver, total, block_size, align, blocks = struct.unpack("<4sIIII", fin.read(20))
        if magic != b"ZISO": raise ValueError("ملف ZSO غير صالح")
        for i in range(blocks):
            hdr = fin.read(8)
            if not hdr: break
            idx, csize = struct.unpack("<II", hdr)
            if csize == block_size: fout.write(fin.read(block_size))
            else: fout.write(lz4.block.decompress(fin.read(csize), uncompressed_size=block_size))
            if idx % 1000 == 0: progress_cb(int(idx * 100 / blocks))
    progress_cb(100)

def bin_to_iso(bin_path, iso_path, progress_cb):
    total = os.path.getsize(bin_path); sectors = total // 2352
    with open(bin_path, "rb") as fin, open(iso_path, "wb") as fout:
        for i in range(sectors):
            sector = fin.read(2352)
            if sector[0] not in (0x00, 0x01, 0x02): fout.write(sector[16:2064])
            else: fout.write(sector[24:2072])
            if i % 2000 == 0: progress_cb(int(i * 100 / sectors))
    progress_cb(100)

# ==================================================================
# 4) عناصر الواجهة
# ==================================================================
class Btn(Button):
    def __init__(self, text="", bg=C_ACCENT, size=16, radius=14, **kw):
        super().__init__(**kw); prep(self, text); self.font_size = sp(size); self.bold = True
        self.background_normal = ""; self.background_down = ""; self.background_color = (0, 0, 0, 0); self.bg = bg
        with self.canvas.before:
            self._c = Color(*bg); self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._upd, size=self._upd, state=self._upd, disabled=self._upd)

    def _upd(self, *a):
        self._r.pos = self.pos; self._r.size = self.size; r, g, b = self.bg[:3]
        if self.disabled: self._c.rgba = (r, g, b, 0.35)
        elif self.state == "down": self._c.rgba = (r * 0.7, g * 0.7, b * 0.7, 1)
        else: self._c.rgba = (r, g, b, 1)
    def set(self, text): prep(self, text)
    def set_bg(self, color): self.bg = color; self._upd()

class Lbl(Label):
    def __init__(self, text="", size=15, color=C_TEXT, halign="center", auto=False, **kw):
        super().__init__(font_size=sp(size), color=color, halign=halign, valign="middle", **kw)
        self._raw = str(text); self.bind(width=self._on_width)
        if auto:
            self.size_hint_y = None; self.height = dp(24)
            self.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(6)))
        self._render()
    def _on_width(self, inst, w): self.text_size = (w, None); self._render()
    def _render(self):
        raw = self._raw
        if self.width > dp(40) and has_ar(raw):
            cpl = max(6, int(self.width / (self.font_size * 0.6))); lines = []
            for para in raw.split("\n"): lines.extend(textwrap.wrap(para, cpl)) if len(para) > cpl and has_ar(para) else lines.append(para)
            raw = "\n".join(lines)
        prep(self, raw)
    def set(self, text): self._raw = str(text); self._render()

class Card(BoxLayout):
    def __init__(self, bg=C_CARD, radius=18, **kw):
        kw.setdefault("orientation", "vertical"); kw.setdefault("padding", dp(14))
        kw.setdefault("spacing", dp(8)); kw.setdefault("size_hint_y", None)
        super().__init__(**kw); self.bg = bg
        with self.canvas.before:
            self._col = Color(*bg); self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._sync, size=self._sync)
        if self.size_hint_y is None: self.bind(minimum_height=self.setter("height"))
    def _sync(self, *a): self._rect.pos = self.pos; self._rect.size = self.size

class CardButton(ButtonBehavior, Card):
    def __init__(self, title, desc, tint, **kw):
        super().__init__(bg=C_CARD, **kw); self.tint = tint
        self.add_widget(Widget()); self.add_widget(Lbl(title, size=18, bold=True, color=tint, auto=True))
        self.add_widget(Lbl(desc, size=12, color=C_MUTED, auto=True)); self.add_widget(Widget())
        self.bind(state=self._press)
    def _press(self, *a): self._col.rgba = C_CARD2 if self.state == "down" else self.bg

class Bar(BoxLayout):
    def __init__(self, bg=C_NAV, **kw):
        super().__init__(**kw)
        with self.canvas.before: Color(*bg); self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)
    def _sync(self, *a): self._rect.pos = self.pos; self._rect.size = self.size

def make_input(hint="", text="", arabic=False):
    return TextInput(text=text, hint_text=hint, multiline=False, size_hint_y=None, height=dp(48),
        font_name=(AR_FONT if (arabic and AR_FONT) else "Roboto"), font_size=sp(15),
        background_normal="", background_active="", background_color=C_INPUT,
        foreground_color=C_TEXT, hint_text_color=(1, 1, 1, 0.38), cursor_color=C_ACCENT,
        padding=[dp(12), dp(13), dp(12), dp(10)], write_tab=False)

def input_row(inp, file_mode=True):
    row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4))
    btn = Btn("تحديد", bg=C_CARD2, size=12, size_hint_x=None, width=dp(60))
    btn.bind(on_release=lambda *_: show_filechooser(inp, file_mode))
    row.add_widget(inp); row.add_widget(btn); return row

def card_title(text, color=C_ACCENT): return Lbl(text, size=17, bold=True, color=color, halign="right", auto=True)
def card_note(text): return Lbl(text, size=13, color=C_MUTED, halign="right", auto=True)

def show_filechooser(target_input, file_mode=True):
    content = BoxLayout(orientation="vertical", spacing=dp(4))
    fc = FileChooserListView(path=DEFAULT_ROOT, dirselect=not file_mode)
    content.add_widget(fc)
    btn_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
    cancel = Btn("إلغاء", bg=C_RED, size=14); select = Btn("اختيار", bg=C_TEAL, size=14)
    btn_row.add_widget(cancel); btn_row.add_widget(select); content.add_widget(btn_row)
    popup = Popup(title="اختر المسار", content=content, size_hint=(0.95, 0.9))
    def on_select(*a): target_input.text = fc.selection[0] if fc.selection else fc.path; popup.dismiss()
    select.bind(on_release=on_select); cancel.bind(on_release=popup.dismiss); popup.open()

class Page(Screen):
    def __init__(self, app, title, subtitle="", scroll=True, align="right", **kw):
        super().__init__(**kw); self.app = app
        root = BoxLayout(orientation="vertical")
        head = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(90), padding=[dp(14), dp(8), dp(14), dp(2)], spacing=dp(2))
        title_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(8))
        menu_btn = Btn("≡", bg=C_CARD2, size=25, radius=12, size_hint_x=None, width=dp(48))
        menu_btn.bind(on_release=lambda *_: app.open_menu())
        title_label = Lbl(title, size=22, bold=True, halign=align)
        title_row.add_widget(menu_btn)
        title_row.add_widget(title_label)
        head.add_widget(title_row)
        self.sub = Lbl(subtitle, size=12, color=C_MUTED, halign=align, size_hint_y=None, height=dp(24))
        head.add_widget(self.sub); root.add_widget(head)
        if scroll:
            sv = ScrollView(do_scroll_x=False, bar_width=dp(3))
            col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(12), padding=[dp(14), dp(6), dp(14), dp(14)])
            col.bind(minimum_height=col.setter("height")); sv.add_widget(col); root.add_widget(sv)
        else:
            col = BoxLayout(orientation="vertical", spacing=dp(8), padding=[dp(14), dp(4), dp(14), dp(10)])
            root.add_widget(col)
        self.col = col; self.add_widget(root)

# ==================================================================
# 5) الشاشة الرئيسية
# ==================================================================
class HomeScreen(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "Super File Plus", "", align="center", **kw)
        c = self.col
        self.disk_card = Card(); self.disk_card.add_widget(card_title("مساحة التخزين"))
        self.disk_bar = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(10))
        self.disk_card.add_widget(self.disk_bar); self.disk_msg = card_note(""); self.disk_card.add_widget(self.disk_msg); c.add_widget(self.disk_card)
        grid = GridLayout(cols=2, spacing=dp(12), size_hint_y=None, height=dp(300))
        for title, desc, tint, target in (("قسم PS2 والفلاشة", "تقسيم، ZSO، إدارة", C_ACCENT, "ps2"), ("التنظيف والأرشيف", "مكررات وفك ضغط", C_TEAL, "clean"), ("صديقك الذكي", "مساعدة فورية", C_PURPLE, "ai"), ("الإعدادات", "الاسم والمجلد", C_AMBER, "settings")):
            card = CardButton(title, desc, tint, size_hint_y=1); card.bind(on_release=lambda _c, t=target: app.go(t)); grid.add_widget(card)
        c.add_widget(grid); c.add_widget(Lbl("الإصدار %s" % APP_VERSION, size=12, color=C_MUTED, auto=True))
    def on_pre_enter(self, *a):
        name = self.app.cfg.get("user_name", "").strip()
        self.sub.set(("أهلاً %s، كل شيء جاهز" % name) if name else "أهلاً بك، كل شيء جاهز")
        try:
            total, used, free = shutil.disk_usage(DEFAULT_ROOT); self.disk_bar.value = used * 100.0 / total
            self.disk_msg.set("المتاح: %s من %s" % (hsize(free), hsize(total)))
        except Exception: self.disk_msg.set("تعذر قراءة مساحة التخزين")

# ==================================================================
# 6) قسم PS2
# ==================================================================
class _Cancelled(Exception): pass

class PS2Screen(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "قسم PS2 والفلاشة", "أدوات احترافية لألعاب OPL", **kw)
        c = self.col; self.busy = False; self.cancel = threading.Event()
        # 1. التقسيم
        card = Card(); card.add_widget(card_title("تقسيم ISO إلى صيغة UL"))
        card.add_widget(card_note("للفلاشات FAT32. اضغط 'تحديد' لاختيار الملف."))
        self.iso_in = make_input("ISO path"); self.iso_in.bind(focus=self._autofill); card.add_widget(input_row(self.iso_in, True))
        self.name_in = make_input("Game name (max 32 chars)"); self.id_in = make_input("Game ID e.g. SLUS_123.45"); self.out_in = make_input("Output folder")
        card.add_widget(self.name_in); card.add_widget(self.id_in); card.add_widget(input_row(self.out_in, False))
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.split_btn = Btn("ابدأ التقسيم", bg=C_ACCENT, size_hint_x=0.65); self.split_btn.bind(on_release=self.start_split)
        self.cancel_btn = Btn("إلغاء", bg=C_GRAY, size=14, size_hint_x=0.35, disabled=True); self.cancel_btn.bind(on_release=lambda *_: self.cancel.set())
        row.add_widget(self.split_btn); row.add_widget(self.cancel_btn); card.add_widget(row)
        self.bar = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(10)); card.add_widget(self.bar)
        self.split_msg = Lbl("", size=14, halign="right", auto=True); card.add_widget(self.split_msg); c.add_widget(card)
        # 2. أدوات ZSO و BIN
        card2 = Card(); card2.add_widget(card_title("أدوات الضغط والتحويل", C_PURPLE))
        self.zso_in = make_input("Source file (.iso or .zso)"); card2.add_widget(input_row(self.zso_in, True))
        row2 = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(6))
        b_zso = Btn("ISO > ZSO", bg=C_PURPLE, size=14); b_zso.bind(on_release=lambda *_: self.start_convert("zso"))
        b_iso = Btn("ZSO > ISO", bg=C_TEAL, size=14); b_iso.bind(on_release=lambda *_: self.start_convert("iso"))
        b_bin = Btn("BIN > ISO", bg=C_AMBER, size=14); b_bin.bind(on_release=lambda *_: self.start_convert("bin"))
        row2.add_widget(b_zso); row2.add_widget(b_iso); row2.add_widget(b_bin); card2.add_widget(row2)
        self.conv_msg = Lbl("", size=14, halign="right", auto=True); card2.add_widget(self.conv_msg); c.add_widget(card2)
        # 3. إدارة ul.cfg
        card3 = Card(); card3.add_widget(card_title("إدارة الألعاب (ul.cfg)", C_TEAL))
        self.flash_in = make_input("Flash root folder"); card3.add_widget(input_row(self.flash_in, False))
        row3 = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(6))
        b_check = Btn("فحص", bg=C_TEAL, size=14); b_check.bind(on_release=self.check_flash)
        b_manage = Btn("إدارة الألعاب", bg=C_ACCENT, size=14); b_manage.bind(on_release=self.manage_ul)
        row3.add_widget(b_check); row3.add_widget(b_manage); card3.add_widget(row3)
        self.flash_msg = Lbl("", size=14, halign="right", auto=True); card3.add_widget(self.flash_msg); c.add_widget(card3)

    def _autofill(self, inp, focused):
        if focused: return
        iso = self.iso_in.text.strip()
        if not iso: return
        base = os.path.splitext(os.path.basename(iso))[0]
        if not self.name_in.text.strip(): self.name_in.text = re.sub(r"[^\w .\-]", "", base)[:32]
        if not self.id_in.text.strip():
            m = re.search(r"([A-Z]{4})[_-](\d{3})[._]?(\d{2})", base.upper())
            if m: self.id_in.text = "%s_%s.%s" % m.groups()

    def start_split(self, *a):
        if self.busy: return
        iso = self.iso_in.text.strip(); name = self.name_in.text.strip(); gid = self.id_in.text.strip().upper(); out = self.out_in.text.strip()
        if not os.path.isfile(iso): self.split_msg.set("ملف ISO غير موجود"); return
        if not name: self.split_msg.set("اكتب اسم اللعبة"); return
        if not re.match(r"^[A-Z]{4}_\d{3}\.\d{2}$", gid): self.split_msg.set("صيغة ID غير صحيحة"); return
        if not out: self.split_msg.set("حدد مجلد الإخراج"); return
        self.busy = True; self.cancel.clear(); self.split_btn.disabled = True; self.cancel_btn.disabled = False; self.bar.value = 0; self.split_msg.set("جارٍ التقسيم...")
        threading.Thread(target=self._split, args=(iso, name[:32], gid, out), daemon=True).start()

    def _split(self, iso, name, gid, out):
        created = []
        try:
            size = os.path.getsize(iso)
            if size <= 0: raise IOError("الملف فارغ")
            parts = (size + PART_SIZE - 1) // PART_SIZE
            if parts > 255: raise IOError("الملف كبير جداً")
            os.makedirs(out, exist_ok=True)
            if shutil.disk_usage(out).free < size + 50 * 1024 * 1024: raise IOError("المساحة غير كافية")
            crc = opl_crc(name); done = 0; last_pct = -1
            with open(iso, "rb") as src:
                for i in range(parts):
                    path = os.path.join(out, "ul.%08X.%s.%02d" % (crc, gid, i)); created.append(path)
                    remaining = min(PART_SIZE, size - i * PART_SIZE)
                    with open(path, "wb") as dst:
                        while remaining > 0:
                            if self.cancel.is_set(): raise _Cancelled()
                            chunk = src.read(min(CHUNK, remaining))
                            if not chunk: raise IOError("انتهى الملف قبل الأوان")
                            dst.write(chunk); remaining -= len(chunk); done += len(chunk)
                            pct = int(done * 100 / size)
                            if pct != last_pct: last_pct = pct; ui(self._progress, pct)
            media = 0x14 if size > 700 * 1024 * 1024 else 0x12
            record = (name.encode("ascii", "ignore")[:32].ljust(32, b"\x00") + ("ul." + gid).encode("ascii")[:15].ljust(15, b"\x00") + bytes([parts, media]) + b"\x00" * 15)
            write_ul_cfg(out, name, record); ui(self._split_done, True, "تم بنجاح!\nالأجزاء: %d" % parts)
        except _Cancelled: self._cleanup(created); ui(self._split_done, False, "تم الإلغاء")
        except Exception as e: self._cleanup(created); ui(self._split_done, False, "فشل: %s" % e)

    @staticmethod
    def _cleanup(paths):
        for p in paths:
            try: os.remove(p)
            except OSError: pass
    def _progress(self, pct): self.bar.value = pct; self.split_msg.set("... %d%%" % pct)
    def _split_done(self, ok, msg):
        self.busy = False; self.split_btn.disabled = False; self.cancel_btn.disabled = True
        if ok: self.bar.value = 100
        self.split_msg.set(msg)

    def start_convert(self, mode):
        src = self.zso_in.text.strip()
        if not os.path.isfile(src): self.conv_msg.set("الملف غير موجود"); return
        self.conv_msg.set("جارٍ التحويل..."); threading.Thread(target=self._convert, args=(src, mode), daemon=True).start()

    def _convert(self, src, mode):
        try:
            base = os.path.splitext(src)[0]
            if mode == "zso": out = base + ".zso"; zso_compress(src, out, lambda p: ui(self._conv_prog, p))
            elif mode == "iso": out = base + "_extracted.iso"; zso_decompress(src, out, lambda p: ui(self._conv_prog, p))
            elif mode == "bin": out = base + "_converted.iso"; bin_to_iso(src, out, lambda p: ui(self._conv_prog, p))
            ui(self.conv_msg.set, "تم بنجاح:\n" + os.path.basename(out))
        except Exception as e: ui(self.conv_msg.set, "فشل: %s" % e)
    def _conv_prog(self, pct): self.conv_msg.set("... %d%%" % pct)

    def check_flash(self, *a):
        p = self.flash_in.text.strip()
        if not os.path.isdir(p): self.flash_msg.set("المسار غير موجود"); return
        lines = []; missing = [d for d in OPL_DIRS if not os.path.isdir(os.path.join(p, d))]
        if missing: lines.append("مجلدات ناقصة: " + ", ".join(missing))
        else: lines.append("كل مجلدات OPL موجودة")
        if os.path.isfile(os.path.join(p, "ul.cfg")): lines.append("ملف ul.cfg موجود")
        self.flash_msg.set("\n".join(lines))

    def manage_ul(self, *a):
        p = self.flash_in.text.strip(); cfg_path = os.path.join(p, "ul.cfg")
        if not os.path.isfile(cfg_path): self.flash_msg.set("ملف ul.cfg غير موجود"); return
        with open(cfg_path, "rb") as f: data = f.read()
        games = []
        for i in range(0, len(data) - 63, 64):
            r = data[i:i + 64]; name = r[:32].split(b"\x00")[0].decode("ascii", "ignore"); gid = r[32:47].split(b"\x00")[0].decode("ascii", "ignore")
            games.append((name, gid))
        if not games: self.flash_msg.set("لا توجد ألعاب"); return
        content = BoxLayout(orientation="vertical", spacing=dp(4)); scroll = ScrollView()
        listbox = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2)); listbox.bind(minimum_height=listbox.setter("height"))
        popup = Popup(title="الألعاب", content=content, size_hint=(0.95, 0.9))
        def delete_game(name, gid):
            new_data = b""
            for i in range(0, len(data) - 63, 64):
                r = data[i:i + 64]
                if r[:32].split(b"\x00")[0].decode("ascii", "ignore") != name: new_data += r
            with open(cfg_path, "wb") as f: f.write(new_data)
            for f in os.listdir(p):
                if f.startswith(gid):
                    try: os.remove(os.path.join(p, f))
                    except OSError: pass
            popup.dismiss(); self.flash_msg.set("تم الحذف: %s" % name)
        for name, gid in games:
            row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4))
            lbl = Lbl("%s (%s)" % (name, gid), size=14, halign="left")
            del_btn = Btn("حذف", bg=C_RED, size=12, size_hint_x=None, width=dp(60))
            del_btn.bind(on_release=lambda _b, n=name, g=gid: delete_game(n, g))
            row.add_widget(del_btn); row.add_widget(lbl); listbox.add_widget(row)
        scroll.add_widget(listbox); content.add_widget(scroll)
        close_btn = Btn("إغلاق", bg=C_GRAY, size=14, size_hint_y=None, height=dp(44)); close_btn.bind(on_release=popup.dismiss); content.add_widget(close_btn); popup.open()

# ==================================================================
# 7) التنظيف والأرشيف (مع محرك 7z الجديد)
# ==================================================================
class CleanScreen(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "التنظيف والأرشيف", "يدعم 7z, ZIP, TAR", **kw)
        c = self.col; self.groups = []; self.root_dir = ""; self.scan_busy = False; self.ex_busy = False
        card = Card(); card.add_widget(card_title("الملفات المكررة", C_TEAL))
        card.add_widget(card_note("نقارن المحتوى. نبقي الأقدم وننقل الباقي إلى _Duplicates."))
        self.dir_in = make_input("Folder to scan"); card.add_widget(input_row(self.dir_in, False))
        self.scan_btn = Btn("ابدأ الفحص", bg=C_TEAL, size_hint_y=None, height=dp(52)); self.scan_btn.bind(on_release=self.start_scan); card.add_widget(self.scan_btn)
        self.move_btn = Btn("نقل المكررات", bg=C_AMBER, size=15, size_hint_y=None, height=dp(50), disabled=True); self.move_btn.bind(on_release=self.move_dups); card.add_widget(self.move_btn)
        self.scan_msg = Lbl("", size=13, halign="left", auto=True); card.add_widget(self.scan_msg); c.add_widget(card)

        card2 = Card(); card2.add_widget(card_title("فك الضغط", C_ACCENT))
        self.arc_in = make_input("Archive path (.zip, .tar, .7z)"); card2.add_widget(input_row(self.arc_in, True))
        self.ex_btn = Btn("فك الضغط", bg=C_ACCENT, size_hint_y=None, height=dp(52)); self.ex_btn.bind(on_release=self.start_extract); card2.add_widget(self.ex_btn)
        self.ex_msg = Lbl("", size=13, halign="right", auto=True); card2.add_widget(self.ex_msg); c.add_widget(card2)

    def start_scan(self, *a):
        if self.scan_busy: return
        folder = self.dir_in.text.strip()
        if not os.path.isdir(folder): self.scan_msg.set("المجلد غير موجود"); return
        self.scan_busy = True; self.scan_btn.disabled = True; self.move_btn.disabled = True
        self.groups = []; self.root_dir = folder; self.scan_msg.set("جارٍ الفحص...")
        threading.Thread(target=self._scan, args=(folder,), daemon=True).start()

    def _scan(self, folder):
        try:
            by_size = {}; total_files = 0
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != DUP_DIR]
                for f in files:
                    p = os.path.join(root, f)
                    try:
                        if os.path.islink(p): continue
                        s = os.path.getsize(p)
                    except OSError: continue
                    if s < 1024: continue
                    total_files += 1; by_size.setdefault(s, []).append(p)
                    if total_files % 200 == 0: ui(self.scan_msg.set, "جارٍ الفحص... %d ملف" % total_files)
            groups = []
            for s, paths in by_size.items():
                if len(paths) < 2: continue
                by_quick = {}
                for p in paths:
                    k = quick_key(p, s)
                    if k: by_quick.setdefault(k, []).append(p)
                for cand in by_quick.values():
                    if len(cand) < 2: continue
                    by_hash = {}
                    for p in cand:
                        h = file_md5(p)
                        if h: by_hash.setdefault(h, []).append(p)
                    for ps in by_hash.values():
                        if len(ps) > 1: ps.sort(key=lambda x: os.path.getmtime(x)); groups.append((s, ps))
            groups.sort(key=lambda g: g[0] * (len(g[1]) - 1), reverse=True)
            wasted = sum(s * (len(ps) - 1) for s, ps in groups)
            lines = ["فُحص %d ملف" % total_files]
            if not groups: lines.append("لا توجد مكررات!")
            else:
                lines.append("مجموعات: %d | يمكن توفير: %s" % (len(groups), hsize(wasted)))
                for s, ps in groups[:15]: lines.append("%s x%d" % (hsize(s), len(ps)))
            ui(self._scan_done, groups, "\n".join(lines))
        except Exception as e: ui(self._scan_done, [], "فشل: %s" % e)

    def _scan_done(self, groups, msg):
        self.scan_busy = False; self.groups = groups; self.scan_btn.disabled = False; self.move_btn.disabled = not groups; self.scan_msg.set(msg)

    def move_dups(self, *a):
        if not self.groups: return
        target = os.path.join(self.root_dir, DUP_DIR)
        try: os.makedirs(target, exist_ok=True)
        except OSError as e: self.scan_msg.set("تعذر: %s" % e); return
        moved = errors = 0
        for s, ps in self.groups:
            for p in ps[1:]:
                dest = os.path.join(target, os.path.basename(p))
                stem, ext = os.path.splitext(dest); n = 1
                while os.path.exists(dest): dest = "%s_%d%s" % (stem, n, ext); n += 1
                try: shutil.move(p, dest); moved += 1
                except OSError: errors += 1
        self.groups = []; self.move_btn.disabled = True; self.scan_msg.set("تم النقل: %d | أخطاء: %d" % (moved, errors))

    def start_extract(self, *a):
        if self.ex_busy: return
        path = self.arc_in.text.strip()
        if not os.path.isfile(path): self.ex_msg.set("الملف غير موجود"); return
        self.ex_busy = True; self.ex_btn.disabled = True; self.ex_msg.set("جارٍ الفك...")
        threading.Thread(target=self._extract, args=(path,), daemon=True).start()

    def _extract(self, path):
        try:
            out = os.path.splitext(path)[0] + "_extracted"
            low_path = path.lower()
            if low_path.endswith(".7z"):
                ok, msg = extract_7z_binary(path, out)
                if not ok: raise ValueError(msg)
            elif zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as z: z.extractall(out)
            elif tarfile.is_tarfile(path):
                with tarfile.open(path) as t: t.extractall(out)
            else: raise ValueError("الصيغة غير مدعومة")
            ui(self._extract_done, "تم في:\n" + out)
        except Exception as e: ui(self._extract_done, "فشل: %s" % e)
def _extract_done(self, msg):
    self.ex_busy = False
    self.ex_btn.disabled = False
    # فصل النص العربي عن مسار الملف لمنع تداخل الاتجاهات
    if "تم في:\n" in msg:
        prefix, path = msg.split("تم في:\n", 1)
        self.ex_msg.set("تم فك الضغط بنجاح في المسار:\n" + path)
    else:
        self.ex_msg.set(msg)

# ==================================================================
# 8) الذكاء الاصطناعي
# ==================================================================
class AIScreen(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "صديقك الذكي", "مساعد خبير في الملفات والـ PS2", scroll=False, **kw)
        self.history = []; self.hist_key = None; self.sv = None; self.chat_col = None; self.msg_in = None; self.analyzing = False

    def on_pre_enter(self, *a): self.refresh()

    def refresh(self):
        self.col.clear_widgets()
        name = self.app.cfg.get("ai_name", "").strip()
        if not name: self._build_setup()
        else: self._build_chat(name)

    def _build_setup(self):
        c = self.col; c.add_widget(Widget()); card = Card()
        card.add_widget(card_title("لنتعرّف أولاً", C_PURPLE)); card.add_widget(card_note("اختر اسماً لصديقك الذكي."))
        self.user_in = make_input("Your name", text=self.app.cfg.get("user_name", ""), arabic=True)
        self.name_in = make_input("Friend name", arabic=True)
        card.add_widget(self.user_in); card.add_widget(self.name_in)
        b = Btn("ابدأ", bg=C_PURPLE, size_hint_y=None, height=dp(52)); b.bind(on_release=self._save_names)
        card.add_widget(b); c.add_widget(card); c.add_widget(Widget())

    def _save_names(self, *a):
        name = self.name_in.text.strip()
        if not name: return
        self.app.cfg["ai_name"] = name[:20]; self.app.cfg["user_name"] = self.user_in.text.strip()[:20]
        self.app.save_cfg(); self.refresh()

    def _build_chat(self, name):
        key = (name, self.app.cfg.get("user_name", ""))
        if self.hist_key != key: self.history = []; self.hist_key = key
        c = self.col
        self.sv = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self.chat_col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=[0, dp(4)])
        self.chat_col.bind(minimum_height=self.chat_col.setter("height")); self.sv.add_widget(self.chat_col); c.add_widget(self.sv)
        chips = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        for label, text in (("افحص التنزيلات", "افحص التنزيلات"), ("تقسيم ISO", "كيف أقسم ISO"), ("الفلاشة", "جهز الفلاشة")):
            b = Btn(label, bg=C_CARD2, size=12, radius=12); b.bind(on_release=lambda _b, t=text: self._send_text(t)); chips.add_widget(b)
        c.add_widget(chips)
        row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        self.msg_in = make_input("Type here...", arabic=True); self.msg_in.bind(on_text_validate=lambda *_: self._send_text())
        send = Btn("إرسال", bg=C_PURPLE, size=15, size_hint_x=None, width=dp(90)); send.bind(on_release=lambda *_: self._send_text())
        row.add_widget(self.msg_in); row.add_widget(send); c.add_widget(row)
        if not self.history:
            user = self.app.cfg.get("user_name", "").strip(); hello = ("أهلاً %s! " % user) if user else "أهلاً بك! "
            self.history.append((name, hello + "أنا %s. اطلب مني فحص مجلد التنزيلات أو اشرح لي مشكلة PS2." % name, False))
        for who, text, mine in self.history: self._add_bubble(who, text, mine, store=False)

    def _add_bubble(self, who, text, mine, store=True, action_btn=None):
        if store: self.history.append((who, text, mine))
        row = BoxLayout(size_hint_y=None, spacing=dp(6))
        card = Card(bg=(C_CARD2 if mine else C_CARD), radius=16, size_hint_x=0.86, padding=dp(10), spacing=dp(2))
        card.add_widget(Lbl(who, size=11, color=(C_MUTED if mine else C_PURPLE), halign="right", auto=True))
        card.add_widget(Lbl(text, size=15, halign="right", auto=True))
        if action_btn:
            btn = Btn(action_btn[0], bg=C_TEAL, size=14, size_hint_y=None, height=dp(40)); btn.bind(on_release=lambda *_: action_btn[1]()); card.add_widget(btn)
        spacer = Widget(size_hint_x=0.14)
        if mine: row.add_widget(spacer); row.add_widget(card)
        else: row.add_widget(card); row.add_widget(spacer)
        card.bind(height=lambda i, h: setattr(row, "height", h)); row.height = card.height
        self.chat_col.add_widget(row); Clock.schedule_once(lambda dt: setattr(self.sv, "scroll_y", 0), 0.15)

    def _send_text(self, text=None):
        if text is None: text = self.msg_in.text.strip(); self.msg_in.text = ""
        if not text: return
        name = self.app.cfg.get("ai_name", "AI"); self._add_bubble("أنت", text, True)
        answer, action = self._reply(text)
        if answer == "__ANALYZE__": self._start_analysis(name)
        else: self._add_bubble(name, answer, False, action_btn=action)

    def _start_analysis(self, name):
        folder = self.app.cfg.get("folder", DEFAULT_FOLDER)
        if self.analyzing: return
        if not os.path.isdir(folder): self._add_bubble(name, "لم أجد المجلد. غيّره من الإعدادات.", False); return
        self.analyzing = True; self._add_bubble(name, "حسناً، أفحص المجلد الآن...", False)
        threading.Thread(target=self._analyze, args=(folder, name), daemon=True).start()

    def _analyze(self, folder, name):
        try:
            r = analyze_folder(folder)
            if r["count"] == 0: msg = "المجلد فارغ، لا شيء لتنظيفه."
            else:
                lines = ["وجدت %d ملف بحجم %s." % (r["count"], hsize(r["total"]))]
                if r["apk"]: lines.append("- %d ملف APK (%s): غالباً تُحذف بعد التثبيت." % (r["apk"], hsize(r["apk_size"])))
                if r["arch"]: lines.append("- %d ملف مضغوط (%s): فكّها ثم احذف الأصل." % (r["arch"], hsize(r["arch_size"])))
                if r["old"]: lines.append("- %d ملف أقدم من 6 أشهر (%s)." % (r["old"], hsize(r["old_size"])))
                if r["iso"]: lines.append("- %d ملف ISO/BIN: جاهزة للتقسيم." % r["iso"])
                if r["big"]: lines.append("أكبر الملفات:\n" + "\n".join("- %s (%s)" % (n, hsize(s)) for s, n in r["big"]))
                msg = "\n".join(lines)
        except Exception as e: msg = "تعذر الفحص: %s" % e
        ui(self._analysis_done, name, msg)

    def _analysis_done(self, name, msg):
        self.analyzing = False; self._add_bubble(name, msg, False, action_btn=("اذهب لقسم التنظيف", lambda: self.app.go("clean")))

    @staticmethod
    def _reply(text):
        t = text.lower()
        rules = (
            (("افحص", "اقتراح", "نظف", "scan", "downloads"), ("__ANALYZE__", None)),
            (("iso", "تقسيم", "opl", "ul"), ("لتقسيم لعبة: ادخل قسم PS2، اختر ملف ISO بزر 'تحديد'، واكتب ID بصيغة SLUS_123.45.", ("اذهب لـ PS2", lambda: App.get_running_app().go("ps2")))),
            (("فلاش", "zso", "ضغط"), ("الفلاشة يجب أن تكون FAT32. يمكنك استخدام زر ISO > ZSO لتوفير مساحة 30%.", None)),
            (("مكرر", "duplicate"), ("قسم التنظيف يبحث عن المكررات الحقيقية وينقلها لمجلد _Duplicates بأمان.", ("اذهب للتنظيف", lambda: App.get_running_app().go("clean")))),
            (("شكرا", "thanks"), ("العفو! أنا هنا متى احتجت.", None)),
            (("من انت", "اسمك"), ("أنا مساعدك الذكي، أعمل بدون إنترنت.", None)),
        )
        for keys, ans in rules:
            if any(k in t for k in keys): return ans
        return ("لم أفهم تماماً. جرّب: افحص التنزيلات، كيف أقسم ISO، أو المكررات.", None)

# ==================================================================
# 9) الإعدادات والتطبيق الرئيسي
# ==================================================================
class SettingsScreen(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "الإعدادات", "تُحفظ تلقائياً", **kw)
        c = self.col
        card = Card(); card.add_widget(card_title("الأسماء", C_AMBER))
        self.user_in = make_input("Your name", text=app.cfg.get("user_name", ""), arabic=True)
        self.ai_in = make_input("Friend name", text=app.cfg.get("ai_name", ""), arabic=True)
        card.add_widget(self.user_in); card.add_widget(self.ai_in); c.add_widget(card)

        card2 = Card(); card2.add_widget(card_title("المجلد الافتراضي", C_TEAL))
        self.dir_in = make_input("Folder", text=app.cfg.get("folder", DEFAULT_FOLDER)); card2.add_widget(input_row(self.dir_in, False)); c.add_widget(card2)

        for inp in (self.user_in, self.ai_in, self.dir_in): inp.bind(focus=lambda i, f: (not f) and self.save(silent=True))

        save = Btn("حفظ", bg=C_TEAL, size_hint_y=None, height=dp(52)); save.bind(on_release=lambda *_: self.save()); c.add_widget(save)
        reset = Btn("مسح محادثة الذكاء الاصطناعي", bg=C_GRAY, size=14, size_hint_y=None, height=dp(48)); reset.bind(on_release=self.reset_chat); c.add_widget(reset)
        self.msg = Lbl("", size=14, color=C_TEAL, auto=True); c.add_widget(self.msg)

    def save(self, silent=False):
        self.app.cfg["user_name"] = self.user_in.text.strip()[:20]; self.app.cfg["ai_name"] = self.ai_in.text.strip()[:20]
        self.app.cfg["folder"] = self.dir_in.text.strip() or DEFAULT_FOLDER; self.app.save_cfg(); self.app.apply_cfg()
        if not silent: self.msg.set("تم الحفظ")
    def reset_chat(self, *a): self.app.pages["ai"].history = []; self.msg.set("تم المسح")

class CalibScreen(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "Arabic Fix", "", align="center", **kw)
        c = self.col; cur = "%d%s" % (AR_MODE, "B" if USE_MARKUP else "A")
        c.add_widget(Lbl("Tap the correct text.\nCurrent: %s" % cur, size=13, auto=True))
        sample = "الإعدادات 123"; font = AR_FONT or "Roboto"
        for mode in (2, 5, 1, 3):
            row = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(6))
            for use_mk in (False, True):
                cell = BoxLayout(spacing=dp(4))
                b = Btn("%d%s" % (mode, "B" if use_mk else "A"), bg=(C_PURPLE if use_mk else C_ACCENT), size=15, size_hint_x=None, width=dp(58))
                b.bind(on_release=lambda _b, m=mode, u=use_mk: self.choose(m, u))
                if use_mk: lab = Label(text=mk(sample, mode), markup=True, font_size=sp(17), halign="center", valign="middle")
                else: lab = Label(text=shape_line(sample, mode), font_name=font, font_size=sp(17), halign="center", valign="middle")
                lab.bind(size=lambda i, v: setattr(i, "text_size", v)); cell.add_widget(b); cell.add_widget(lab); row.add_widget(cell)
            c.add_widget(row)
        self.msg = Lbl("", size=14, auto=True); c.add_widget(self.msg)
    def choose(self, mode, use_mk):
        self.app.cfg["ar_mode"] = mode; self.app.cfg["ar_markup"] = bool(use_mk); self.app.save_cfg()
        self.msg.set("Saved. Close and reopen.")

class SuperFilePlus(App):
    title = "Super File Plus"
    NAV = (("home", "الرئيسية"), ("ps2", "PS2"), ("clean", "التنظيف"), ("ai", "الذكي"), ("settings", "الإعدادات"))
    ORDER = ("home", "ps2", "clean", "ai", "settings", "calib")

    def build(self):
        self.cfg = self.load_cfg(); set_ar_display(self.cfg.get("ar_mode", DEFAULT_AR_MODE), self.cfg.get("ar_markup", True))
        root = BoxLayout(orientation="vertical"); self.sm = ScreenManager(transition=SlideTransition(duration=0.2)); self.pages = {}
        for key, cls in (("home", HomeScreen), ("ps2", PS2Screen), ("clean", CleanScreen), ("ai", AIScreen), ("settings", SettingsScreen), ("calib", CalibScreen)):
            page = cls(self, name=key); self.pages[key] = page; self.sm.add_widget(page)
        root.add_widget(self.sm)
        nav = Bar(size_hint_y=None, height=dp(58), padding=[dp(6), dp(6)], spacing=dp(4)); self.nav_btns = {}
        for key, label in self.NAV:
            b = Btn(label, bg=C_NAV, size=12, radius=12); b.bind(on_release=lambda _b, k=key: self.go(k)); self.nav_btns[key] = b; nav.add_widget(b)
        root.add_widget(nav)
        self.sm.bind(current=self._on_screen); self._on_screen(self.sm, self.sm.current); self.apply_cfg()
        Window.bind(on_keyboard=self.on_key); return root

    def open_menu(self, *args):
        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(14), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))
        title = Lbl("القائمة السريعة", size=19, bold=True, color=C_ACCENT, auto=True)
        content.add_widget(title)
        choices = (
            ("الرئيسية", "home", C_ACCENT),
            ("أدوات PS2 والفلاشة", "ps2", C_PURPLE),
            ("التنظيف والأرشيف", "clean", C_TEAL),
            ("المساعد الذكي", "ai", C_PURPLE),
            ("الإعدادات", "settings", C_AMBER),
            ("إصلاح عرض العربية", "calib", C_GRAY),
        )
        popup = Popup(title="Super File Plus", content=content, size_hint=(0.86, 0.78), auto_dismiss=True)
        for label, target, tint in choices:
            b = Btn(label, bg=tint, size=15, size_hint_y=None, height=dp(46))
            b.bind(on_release=lambda _b, t=target: (popup.dismiss(), self.go(t)))
            content.add_widget(b)
        close = Btn("إغلاق", bg=C_CARD2, size=14, size_hint_y=None, height=dp(42))
        close.bind(on_release=popup.dismiss)
        content.add_widget(close)
        popup.open()

    def go(self, name, direction=None):
        if name == self.sm.current: return
        if direction is None:
            a, b = self.ORDER.index(self.sm.current), self.ORDER.index(name); direction = "left" if b > a else "right"
        self.sm.transition.direction = direction; self.sm.current = name

    def _on_screen(self, sm, current):
        for key, b in self.nav_btns.items(): b.set_bg(C_CARD2 if key == current else C_NAV)

    def on_key(self, window, key, *args):
        if key == 27 and self.sm.current != "home": self.go("settings" if self.sm.current == "calib" else "home", "right"); return True
        return False

    def _cfg_path(self): return os.path.join(self.user_data_dir, "settings.json")
    def load_cfg(self):
        try:
            with open(self._cfg_path(), "r", encoding="utf-8") as f: data = json.load(f); return data if isinstance(data, dict) else {}
        except Exception: return {}
    def save_cfg(self):
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            with open(self._cfg_path(), "w", encoding="utf-8") as f: json.dump(self.cfg, f, ensure_ascii=False)
        except Exception: pass
    def apply_cfg(self):
        folder = self.cfg.get("folder")
        if folder: self.pages["clean"].dir_in.text = folder; self.pages["ps2"].out_in.text = folder

if __name__ == "__main__":
    SuperFilePlus().run()