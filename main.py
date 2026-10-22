# -*- coding: utf-8 -*-
import os
import zlib
import threading
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.progressbar import ProgressBar
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.popup import Popup
from kivy.utils import platform

Window.clearcolor = (0.08, 0.1, 0.15, 1)
PART_SIZE = 1 << 30  # 1GB

# خوارزمية OPL CRC
_M32 = 0xFFFFFFFF
def _build_opl_table():
    tab = [0] * 256
    crc = 0
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

def request_android_permissions():
    if platform == 'android':
        try:
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
        except Exception: pass

class Btn(Button):
    def __init__(self, text="", bg=(0.2, 0.6, 0.9, 1), **kw):
        super().__init__(**kw)
        self.text = text
        self.font_size = sp(16)
        self.bold = True
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.bg = bg
        with self.canvas.before:
            self._c = Color(*bg)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
        self.bind(pos=self._upd, size=self._upd, state=self._upd)

    def _upd(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size
        r, g, b = self.bg[:3]
        self._c.rgba = (r*0.7, g*0.7, b*0.7, 1) if self.state == "down" else (r, g, b, 1)

class ISOApp(App):
    def build(self):
        request_android_permissions()
        root = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(15))

        root.add_widget(Label(text="مُقسّم ألعاب PS2", font_size=sp(24), bold=True, color=(1,1,1,1), size_hint_y=None, height=dp(50)))

        # حقل مسار ISO
        self.iso_in = TextInput(hint_text="مسار ملف ISO", multiline=False, size_hint_y=None, height=dp(48))
        btn_sel_iso = Btn("تحديد ملف ISO", bg=(0.2, 0.5, 0.3, 1), size_hint_y=None, height=dp(45))
        btn_sel_iso.bind(on_release=lambda *_: self.show_picker(self.iso_in, True))
        root.add_widget(self.iso_in)
        root.add_widget(btn_sel_iso)

        # حقل مجلد الحفظ
        self.out_in = TextInput(hint_text="مجلد الحفظ (الفلاشة)", multiline=False, size_hint_y=None, height=dp(48))
        btn_sel_out = Btn("تحديد مجلد الحفظ", bg=(0.2, 0.5, 0.3, 1), size_hint_y=None, height=dp(45))
        btn_sel_out.bind(on_release=lambda *_: self.show_picker(self.out_in, False))
        root.add_widget(self.out_in)
        root.add_widget(btn_sel_out)

        # حقل الاسم والـ ID
        self.name_in = TextInput(hint_text="اسم اللعبة", multiline=False, size_hint_y=None, height=dp(48))
        self.id_in = TextInput(hint_text="كود اللعبة (مثال: SLUS_123.45)", multiline=False, size_hint_y=None, height=dp(48))
        root.add_widget(self.name_in)
        root.add_widget(self.id_in)

        # زر البدء
        self.start_btn = Btn("ابدأ التقسيم", bg=(0.8, 0.3, 0.3, 1), size_hint_y=None, height=dp(55))
        self.start_btn.bind(on_release=self.start_split)
        root.add_widget(self.start_btn)

        self.bar = ProgressBar(max=100)
        root.add_widget(self.bar)

        self.msg = Label(text="", color=(0.8, 0.8, 0.8, 1))
        root.add_widget(self.msg)

        return root

    def show_picker(self, target_input, is_file):
        content = BoxLayout(orientation="vertical")
        fc = FileChooserListView(path="/storage/emulated/0/", dirselect=not is_file)
        content.add_widget(fc)
        btn = Btn("تأكيد", size_hint_y=None, height=dp(50))
        content.add_widget(btn)
        popup = Popup(title="اختر المسار", content=content, size_hint=(0.95, 0.9))
        btn.bind(on_release=lambda *_: self.set_path(popup, fc, target_input, is_file))
        popup.open()

    def set_path(self, popup, fc, target, is_file):
        if fc.selection:
            target.text = fc.selection[0]
        elif not is_file:
            target.text = fc.path
        popup.dismiss()

    def start_split(self, *a):
        iso = self.iso_in.text.strip()
        out = self.out_in.text.strip()
        name = self.name_in.text.strip()
        gid = self.id_in.text.strip().upper()

        if not os.path.isfile(iso):
            self.msg.text = "خطأ: ملف ISO غير موجود"
            return
        if not os.path.isdir(out):
            self.msg.text = "خطأ: مجلد الحفظ غير موجود"
            return
        if not name or not gid:
            self.msg.text = "خطأ: أدخل الاسم والكود"
            return

        self.start_btn.disabled = True
        self.msg.text = "جارٍ التقسيم... يرجى الانتظار"
        threading.Thread(target=self._split, args=(iso, name, gid, out), daemon=True).start()

    def _split(self, iso, name, gid, out):
        try:
            size = os.path.getsize(iso)
            parts = (size + PART_SIZE - 1) // PART_SIZE
            crc = opl_crc(name)
            done = 0

            with open(iso, "rb") as src:
                for i in range(parts):
                    part_path = os.path.join(out, "ul.%08X.%s.%02d" % (crc, gid, i))
                    remaining = min(PART_SIZE, size - i * PART_SIZE)
                    with open(part_path, "wb") as dst:
                        while remaining > 0:
                            chunk = src.read(min(4 * 1024 * 1024, remaining))
                            dst.write(chunk)
                            remaining -= len(chunk)
                            done += len(chunk)
                            pct = int(done * 100 / size)
                            Clock.schedule_once(lambda dt, p=pct: setattr(self.bar, 'value', p))

            # كتابة ملف ul.cfg
            media = 0x14 if size > 700 * 1024 * 1024 else 0x12
            record = (name.encode("ascii", "ignore")[:32].ljust(32, b"\x00") +
                      ("ul." + gid).encode("ascii")[:15].ljust(15, b"\x00") +
                      bytes([parts, media]) + b"\x00" * 15)
            
            cfg_path = os.path.join(out, "ul.cfg")
            with open(cfg_path, "ab") as f:
                f.write(record)

            Clock.schedule_once(lambda dt: self.done("تم التقسيم بنجاح!\nتم إنشاء ملف ul.cfg"))
        except Exception as e:
            Clock.schedule_once(lambda dt: self.done("فشل: " + str(e)))

    def done(self, msg):
        self.msg.text = msg
        self.start_btn.disabled = False
        self.bar.value = 100

if __name__ == "__main__":
    ISOApp().run()
