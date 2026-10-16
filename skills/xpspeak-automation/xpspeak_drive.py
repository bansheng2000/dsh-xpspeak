# -*- coding: utf-8 -*-
"""XPSPeak 4.1 控件盘点 + Win32 消息级自动化（纯标准库，无第三方依赖）。

用法:
  python xpspeak_drive.py inventory                      # 打印窗口/菜单/控件清单
  python xpspeak_drive.py export <in.xps> <out.dat>      # 打开谱图并导出 ASCII（已实测）
  python xpspeak_drive.py importascii <in.txt> <out.dat> # Data>Import(ASCII) 后导出（同机制）

实测要点（2026-10-15 于 XPSPEAK41.exe / gaas.xps）:
  * VB6 程序：表单 ThunderRT6FormDC，控件 ThunderRT6CommandButton/TextBox，主窗口 ThunderRT6Main
  * 菜单是真 Win32 菜单 -> 按文字取 id 后 PostMessage WM_COMMAND，不需要鼠标坐标/前台焦点
  * 文件对话框统一这样走：WM_SETTEXT 填 Edit(0x47C=1148 或 1001) -> PostMessage WM_COMMAND IDOK；
    不要去点按钮 HWND（Open 对话框的按钮在 DirectUI 里根本没有 HWND，Save 对话框才有 Button id=1）
  * 必须用 PostMessage：BM_CLICK / IDOK 用 SendMessage 会同步等到模态框关闭，遇异常弹窗直接死锁
  * 导出成功后会弹 "XPSPEAK41 / Data file exported" 模态框，要顺手关掉才能继续
  * Import (ASCII) 只认 CRLF 行尾，LF-only 文件会被静默读错（点数减半、B.E. 列变成强度值）-> ascii_crlf()
  * 传给对话框的路径必须是反斜杠形式（"C:\a\b.dat"）：正斜杠会被 Save 对话框拒绝 -> 对话框不关、文件写不出
"""
import ctypes, os, subprocess, sys, time, shutil
from ctypes import wintypes as w

u = ctypes.windll.user32
EXE_DEFAULT = r"E:\下载\XPS分峰软件XPSpeak41\XPS分峰软件XPSpeak41\XPSPEAK41.exe"
WORKDIR = os.path.join(os.path.expanduser("~"), "xpspeak_auto")
FORM_PROC, FORM_FIT = "XPS Peak Processing", "XPS Peak Fit"
DLG_OPEN, DLG_SAVE, DLG_IMPORT = "Open file", "Export Spectral", "Import Ascii format"   # 对话框标题按子串匹配
DLG_SAVE_RPA = "Save region parameters"
# 坑：Processing 窗的 "Save XPS" 按钮弹出的保存框标题仍是 "Open file"（VB6 CommonDialog 的 caption 没改），
# 所以保存 .xps 复用的就是 DLG_OPEN 这个匹配串；Parameters > Save Region Parameters 的框标题是 DLG_SAVE_RPA。
BM_CLICK, WM_SETTEXT, WM_COMMAND, WM_KEYDOWN = 0x00F5, 0x000C, 0x0111, 0x0100
MF_BYPOSITION, IDOK = 0x400, 1
u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
u.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, ctypes.c_void_p]
u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, ctypes.c_void_p]
CB = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


def text(h, n=512):
    b = ctypes.create_unicode_buffer(n); u.GetWindowTextW(h, b, n); return b.value


def cls(h):
    b = ctypes.create_unicode_buffer(256); u.GetClassNameW(h, b, 256); return b.value


def tops(pid):
    o = []
    def cb(h, l):
        p = w.DWORD(); u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid: o.append(h)
        return True
    u.EnumWindows(CB(cb), 0); return o


def kids(h):
    o = []
    def cb(c, l):
        o.append(c); return True
    u.EnumChildWindows(h, CB(cb), 0); return o


def form(pid, title):
    for h in tops(pid):
        if cls(h) == "ThunderRT6FormDC" and text(h) == title: return h


def button(hwnd, caption):
    for c in kids(hwnd):
        if cls(c).startswith("ThunderRT6CommandButton") and text(c) == caption: return c


def dialogs(pid):
    return [h for h in tops(pid) if cls(h) == "#32770"]


def menu_id(f, top, sub):
    """按菜单文字取命令 id（比下标稳）"""
    m = u.GetMenu(f)
    for i in range(u.GetMenuItemCount(m)):
        b = ctypes.create_unicode_buffer(128); u.GetMenuStringW(m, i, b, 128, MF_BYPOSITION)
        if b.value.replace("&", "") != top: continue
        sm = u.GetSubMenu(m, i)
        if not sm:                      # 顶层项本身就是命令（Background / Add Peak / Optimise Region / ReDisplay）
            return u.GetMenuItemID(m, i)
        for j in range(u.GetMenuItemCount(sm)):
            b2 = ctypes.create_unicode_buffer(128); u.GetMenuStringW(sm, j, b2, 128, MF_BYPOSITION)
            if sub is None or b2.value.replace("&", "") == sub:
                return u.GetMenuItemID(sm, j)
    raise KeyError("%s > %s" % (top, sub))


def click(hwnd, settle=1.5):
    u.PostMessageW(hwnd, BM_CLICK, 0, 0); time.sleep(settle)


def menu_click(f, top, sub, settle=2.0):
    u.PostMessageW(f, WM_COMMAND, menu_id(f, top, sub), 0); time.sleep(settle)


def _kill_strays(pid, keep=()):
    """关掉模态消息框（"Data file exported" 等）。
    实测坑：这类框的确定按钮 CtrlID=2、WM_COMMAND IDOK(1) 打不掉，必须直接 BM_CLICK 真按钮。"""
    for h in dialogs(pid):
        if h in keep:
            continue
        btn = next((c for c in kids(h) if cls(c) == "Button"), None)
        if btn:
            u.PostMessageW(btn, BM_CLICK, 0, 0)
        else:
            u.PostMessageW(h, WM_COMMAND, IDOK, 0)


def _set_window_text(h, s):
    u.SendMessageW(h, WM_SETTEXT, 0, ctypes.cast(ctypes.c_wchar_p(s), ctypes.c_void_p))


def file_dialog(pid, title, path, timeout=15, keep_open_ok=()):
    """Vista 风格文件对话框：填文件名 -> IDOK -> 等它关闭（顺手关掉覆盖确认/信息框）"""
    t0, d = time.time(), None
    while time.time() - t0 < timeout:
        # title=None 时自动挑“带 Edit 的文件对话框”，避开 "Data file exported" 之类的消息框
        hits = [h for h in dialogs(pid)
                if (title in text(h) if title else any(cls(c) == "Edit" for c in kids(h)))]
        if hits: d = hits[0]; break
        _kill_strays(pid)
        time.sleep(0.3)
    if d is None:
        raise TimeoutError("dialog not found: %r (strays: %r)" % (title or "<auto>", [text(h) for h in dialogs(pid)]))
    eds = [c for c in kids(d) if cls(c) == "Edit"]
    ed = ([e for e in eds if u.GetDlgCtrlID(e) in (0x47C, 1001)] or eds)[0]
    _set_window_text(ed, path)
    time.sleep(0.3)
    u.PostMessageW(d, WM_COMMAND, IDOK, 0)
    t0 = time.time()
    while time.time() - t0 < timeout:
        cur = dialogs(pid)
        if d not in cur: return True
        for h in cur:
            if h != d: u.PostMessageW(h, WM_COMMAND, IDOK, 0)   # 覆盖确认等
        time.sleep(0.3)
    raise TimeoutError("dialog stuck: %r" % title)


def ascii_crlf(path):
    """Import (ASCII) 只认 CRLF 行尾：LF-only 文件会被静默读错（点数减半、X/Y 轴值错位），
    实测 181 点的谱读成 91 点且 B.E. 列变成强度值。原文件不动，需要时返回一份 CRLF 副本路径。"""
    with open(path, "rb") as f:
        b = f.read()
    if b"\r\n" in b:
        return path
    os.makedirs(WORKDIR, exist_ok=True)
    out = os.path.join(WORKDIR, "crlf_" + os.path.basename(path))
    with open(out, "wb") as f:
        f.write(b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    return out


def launch(exe=EXE_DEFAULT):
    if not os.path.exists(exe):
        raise FileNotFoundError(
            "找不到 XPSPEAK41.exe: %s\n请设环境变量 XPSPEAK_EXE 指向本机真实的 XPSPEAK41.exe" % exe)
    p = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    time.sleep(4)
    return p


def inventory(pid):
    for h in tops(pid):
        if not u.IsWindowVisible(h): continue
        print("TOP", hex(h), cls(h), repr(text(h)))
        m = u.GetMenu(h)
        for i in range(u.GetMenuItemCount(m) if m else 0):
            b = ctypes.create_unicode_buffer(128); u.GetMenuStringW(m, i, b, 128, MF_BYPOSITION)
            sm = u.GetSubMenu(m, i); items = []
            if sm:
                for j in range(u.GetMenuItemCount(sm)):
                    b2 = ctypes.create_unicode_buffer(128); u.GetMenuStringW(sm, j, b2, 128, MF_BYPOSITION)
                    items.append(b2.value.replace("&", ""))
            print("   MENU", i, b.value.replace("&", ""), items)
        seen = {}
        for c in kids(h):
            k = (cls(c), text(c)); seen[k] = seen.get(k, 0) + 1
        for (k, t), n in seen.items():
            print("   CH", k, repr(t), ("x%d" % n) if n > 1 else "")


def export(exe, src, out, import_ascii=False):
    """打开(或导入)谱图 -> Data>Export(Spectrum) 导出 ASCII。返回写出的字节数。

    注意：传给文件对话框的路径必须是反斜杠形式，正斜杠会被 Save 对话框拒绝（对话框不关、也写不出文件）。
    """
    src, out = os.path.normpath(src), os.path.normpath(out)
    os.makedirs(WORKDIR, exist_ok=True)
    inp = os.path.join(WORKDIR, "in" + os.path.splitext(src)[1])
    shutil.copy(src, inp)
    if os.path.exists(out): os.remove(out)
    p = launch(exe)
    try:
        pid = p.pid
        proc, fit = form(pid, FORM_PROC), form(pid, FORM_FIT)
        if not (proc and fit): raise RuntimeError("forms not found")
        if import_ascii:
            menu_click(fit, "Data", "Import (ASCII)")
            file_dialog(pid, DLG_IMPORT, ascii_crlf(inp))   # 标题实为 "Import Ascii format (BE  Intensity)"
        else:
            click(button(proc, "Open XPS"))
            file_dialog(pid, DLG_OPEN, inp)
        menu_click(fit, "Data", "Export (Spectrum)")
        file_dialog(pid, DLG_SAVE, out)
        time.sleep(0.5); _kill_strays(pid)          # "Data file exported"
        return os.path.getsize(out) if os.path.exists(out) else 0
    finally:
        p.terminate()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "inventory"
    exe = os.environ.get("XPSPEAK_EXE", EXE_DEFAULT)
    if mode == "inventory":
        p = launch(exe)
        try: inventory(p.pid)
        finally: p.terminate()
    elif mode in ("export", "importascii"):
        n = export(exe, sys.argv[2], sys.argv[3], import_ascii=(mode == "importascii"))
        print("bytes:", n)
        if n: print(open(sys.argv[3], encoding="utf-8", errors="replace").read()[:300])
    else:
        raise SystemExit(__doc__)