# -*- coding: utf-8 -*-
"""XPSPeak 4.1 的 MCP server（stdio，换行分隔 JSON-RPC，纯标准库）。

把 xpspeak_drive.py 的消息级驱动包成 MCP 工具，任何 MCP 客户端（DSH / Claude Code / ...）
都能像调用别的 MCP 工具一样驱动 XPSPeak 分峰。

自检（会真的开一次 XPSPeak、导出一次 ASCII，约 20s）:
  python xpspeak_mcp.py --selftest

手工联调:
  echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | python xpspeak_mcp.py

注意: 本进程只许往 stdout 写协议行，工具里的任何 print 都重定向到 stderr。
"""
import contextlib, ctypes, ctypes.wintypes, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xpspeak_drive as D

EXE = os.environ.get("XPSPEAK_EXE", D.EXE_DEFAULT)
PROTO = "2024-11-05"
S = {"type": "string"}


def _pid_of(h):
    p = ctypes.wintypes.DWORD()
    D.u.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def _menu_label(m, i):
    b = ctypes.create_unicode_buffer(128)
    D.u.GetMenuStringW(m, i, b, 128, D.MF_BYPOSITION)
    return b.value.replace("&", "")


def _menu_form(pid):
    """带菜单的那个窗体 = 分峰窗。它的标题会随区域改名（载入后从 "XPS Peak Fit" 变成 "Region 4"），
    所以只能靠「有菜单」来认，不能靠标题 —— 这是实测踩到的坑。"""
    for h in D.tops(pid):
        if D.cls(h) == "ThunderRT6FormDC" and D.u.GetMenu(h):
            return h


def find_pid():
    """找已在运行的 XPSPeak（不管是本 server 还是用户手动启动的）；没有返回 None"""
    seen = []

    def cb(h, l):
        if D.cls(h) == "ThunderRT6FormDC":
            seen.append(_pid_of(h))
        return True

    D.u.EnumWindows(D.CB(cb), 0)
    return seen[0] if seen else None


def app(launch=True):
    """确保程序在跑，返回 (pid, proc_form, fit_form)"""
    pid = find_pid()
    if pid is None:
        if not launch:
            raise RuntimeError("XPSPeak 未运行")
        D.launch(EXE)
        for _ in range(20):
            pid = find_pid()
            if pid:
                break
            time.sleep(0.5)
        if pid is None:
            raise RuntimeError("启动后仍找不到 XPSPeak 窗体")
    return pid, D.form(pid, D.FORM_PROC), _menu_form(pid)


def do_status():
    pid, proc, fit = app()
    out = ["pid=%s" % pid,
           "XPS Peak Processing = %s" % (hex(proc) if proc else "无"),
           "XPS Peak Fit        = %s" % (hex(fit) if fit else "无")]
    if proc:
        btns = [D.text(c) for c in D.kids(proc)
                if D.cls(c).startswith("ThunderRT6CommandButton") and D.text(c)]
        out.append("按钮: " + " | ".join(dict.fromkeys(btns)))
    if fit:
        m = D.u.GetMenu(fit)
        for i in range(D.u.GetMenuItemCount(m)):
            sm = D.u.GetSubMenu(m, i)
            items = [_menu_label(sm, j) for j in range(D.u.GetMenuItemCount(sm) if sm else 0)]
            out.append("菜单 %s: %s" % (_menu_label(m, i), " / ".join(items) or "(无子项)"))
    return "\n".join(out)


def do_open(path, source="auto"):
    path = os.path.normpath(path)          # 必须反斜杠：正斜杠会被文件对话框拒绝
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    kind = source if source != "auto" else ("xps" if path.lower().endswith((".xps", ".rpa")) else "ascii")
    pid, proc, fit = app()
    note = ""
    if kind == "ascii":
        fixed = D.ascii_crlf(path)      # LF-only 会被 XPSPeak 静默读错（点数减半、轴值错位）
        if fixed != path:
            note = "（原文是 LF 换行，已自动改用 CRLF 副本 %s）" % fixed
        D.menu_click(fit, "Data", "Import (ASCII)")
        D.file_dialog(pid, D.DLG_IMPORT, fixed)
    else:
        D.click(D.button(proc, "Open XPS"))
        D.file_dialog(pid, D.DLG_OPEN, path)
    D._kill_strays(pid)
    return "已载入 %s（%s）%s" % (path, kind, note)


def do_export(path, what="spectrum"):
    path = os.path.normpath(path)
    pid, proc, fit = app()
    if os.path.exists(path):
        os.remove(path)
    D.menu_click(fit, "Data", "Export (Spectrum)" if what == "spectrum" else "Export (Peak Parameters)")
    # Spectrum 的对话框标题已知；Peak Parameters 的标题未知 -> 传 None 自动挑带 Edit 的对话框
    D.file_dialog(pid, D.DLG_SAVE if what == "spectrum" else None, path)
    time.sleep(0.5)
    D._kill_strays(pid)                    # "Data file exported" 模态框
    if not os.path.exists(path):
        raise RuntimeError("对话框已关闭但没写出文件: %s" % path)
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(400)
    return "已导出 %s（%d 字节）\n--- 前几行 ---\n%s" % (path, os.path.getsize(path), head)


def do_save(path, what="xps"):
    """保存当前状态。what=xps = Processing 窗 Save XPS（谱图+背景+已定峰参数，能被 xpspeak_open 载回）；
    what=rpa = Parameters > Save Region Parameters（只存区域峰参数，可 Load 到别的谱上套用）。"""
    path = os.path.normpath(path)          # 必须反斜杠，同 do_export
    pid, proc, fit = app()
    if os.path.exists(path):
        os.remove(path)
    if what == "xps":
        btn = D.button(proc, "Save XPS")
        if not btn:
            raise RuntimeError("找不到 Save XPS 按钮")
        D.click(btn)
        D.file_dialog(pid, D.DLG_OPEN, path)     # 坑：这个保存框标题就是 "Open file"
    elif what == "rpa":
        D.menu_click(fit, "Parameters", "Save Region Parameters")
        D.file_dialog(pid, D.DLG_SAVE_RPA, path)
    else:
        raise ValueError("what 只支持 xps / rpa")
    time.sleep(0.3)
    D._kill_strays(pid)
    if not os.path.exists(path):
        raise RuntimeError("对话框已关闭但没写出文件: %s" % path)
    with open(path, "rb") as f:
        head = f.read(24)
    return "已保存 %s（%d 字节，文件头 %r）" % (path, os.path.getsize(path), head)


def do_call(name, a):
    if name == "xpspeak_status":
        return do_status()
    if name == "xpspeak_open":
        return do_open(a["path"], a.get("source", "auto"))
    if name == "xpspeak_export":
        return do_export(a["path"], a.get("what", "spectrum"))
    if name == "xpspeak_save":
        return do_save(a["path"], a.get("what", "xps"))
    if name == "xpspeak_menu":
        pid, proc, fit = app()
        D.menu_click(fit, a["top"], a.get("sub"), settle=float(a.get("settle", 2)))
        return "已执行菜单: %s%s" % (a["top"], (" > " + a["sub"]) if a.get("sub") else "")
    if name == "xpspeak_click":
        pid, proc, fit = app()
        h = proc if a.get("form", "processing") == "processing" else fit
        b = D.button(h, a["caption"])
        if not b:
            raise KeyError("找不到按钮: %r" % a["caption"])
        D.click(b)
        return "已点击: %s" % a["caption"]
    if name == "xpspeak_close":
        pid = find_pid()
        if pid is None:
            return "XPSPeak 未在运行"
        os.system("taskkill /PID %d /F >NUL 2>&1" % pid)
        return "已关闭 pid=%d" % pid
    raise KeyError("unknown tool: " + name)


TOOLS = [
    ("xpspeak_status", "启动（若未运行）并返回 XPSPeak 进程号、窗体、按钮和全部菜单项。任何操作前先调它。", {}, []),
    ("xpspeak_open", "载入谱图。source=auto 按扩展名判断（.xps/.rpa 走 Open XPS，其余走 Data>Import (ASCII)，ASCII 若是 LF 换行会自动改用 CRLF 副本）。",
     {"path": dict(S, description="谱图文件绝对路径（.xps / .rpa / 两列 BE+Intensity 的 ASCII）"),
      "source": {"type": "string", "enum": ["auto", "xps", "ascii"], "description": "默认 auto"}}, ["path"]),
    ("xpspeak_export", "导出数据到文件。what=spectrum 导出 B.E./Raw Intensity/Peak Sum/Background/各峰/difference；what=peaks 导出峰参数表。",
     {"path": dict(S, description="输出的绝对路径"),
      "what": {"type": "string", "enum": ["spectrum", "peaks"], "description": "默认 spectrum"}}, ["path"]),
    ("xpspeak_save", "保存当前状态为 XPSPeak 原生文件。what=xps 走 Processing 窗 Save XPS（谱图+背景+已定峰参数，xpspeak_open 可原样载回）；what=rpa 走 Parameters>Save Region Parameters（只存区域峰参数，可套用到别的谱上）。",
     {"path": dict(S, description="输出绝对路径（.xps / .rpa）"),
      "what": {"type": "string", "enum": ["xps", "rpa"], "description": "默认 xps"}}, ["path"]),
    ("xpspeak_menu", "执行任意菜单命令：Add Peak / Background / Optimise Region / Load|Save Region Parameters / Clear Parameters / Delete Region / Region Shift / ReDisplay / Import (Kratos|Leybold|Phi|VAMAS)。",
     {"top": dict(S, description="顶层菜单名，如 Data、Parameters、Add Peak、Background"),
      "sub": dict(S, description="子项名；顶层项本身是命令时省略"),
      "settle": {"type": "number", "description": "命令后等待秒数，默认 2"}}, ["top"]),
    ("xpspeak_click", "按标题点击窗体按钮：Open XPS / Save XPS / Optimise All / Close / Options / 区域号 1..10。",
     {"caption": dict(S, description="按钮标题，精确匹配"),
      "form": {"type": "string", "enum": ["processing", "fit"], "description": "默认 processing"}}, ["caption"]),
    ("xpspeak_close", "关闭 XPSPeak 进程。", {}, []),
]


def handle(req):
    m, rid = req.get("method"), req.get("id")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": req.get("params", {}).get("protocolVersion") or PROTO,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "xpspeak", "version": "0.1.0"}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": p, "required": r}}
            for n, d, p, r in TOOLS]}}
    if m == "tools/call":
        p = req.get("params") or {}
        try:
            with contextlib.redirect_stdout(sys.stderr):
                txt = do_call(p.get("name"), p.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": str(txt)}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}], "isError": True}}
    if m == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found: %s" % m}}


def serve():
    # MCP 规定 UTF-8；Windows 管道默认 cp936，必须显式改，否则中文描述会解码失败
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32700, "message": "parse error: %s" % e}}) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def selftest():
    """端到端自检：协议握手 -> tools/list -> 启动 -> 载入样例 -> 导出 -> 校验字节数 -> 关闭"""
    import subprocess, tempfile
    pr = subprocess.Popen([sys.executable, os.path.abspath(__file__)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding="utf-8", bufsize=1)
    n = [0]

    def rpc(method, params=None):
        n[0] += 1
        pr.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": method, "params": params}) + "\n")
        pr.stdin.flush()
        return json.loads(pr.stdout.readline())

    ini = rpc("initialize", {"protocolVersion": PROTO})
    assert ini["result"]["serverInfo"]["name"] == "xpspeak", ini
    names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
    assert len(names) == 7 and "xpspeak_export" in names and "xpspeak_save" in names, names
    st = rpc("tools/call", {"name": "xpspeak_status", "arguments": {}})
    assert "pid=" in st["result"]["content"][0]["text"], st
    sample = os.path.join(os.path.dirname(EXE), "gaas.xps")
    out = os.path.join(tempfile.gettempdir(), "xpspeak_mcp_check.dat")
    for method, args in (("xpspeak_open", {"path": sample}), ("xpspeak_export", {"path": out})):
        r = rpc("tools/call", {"name": method, "arguments": args})
        assert not r["result"].get("isError"), r
    size = os.path.getsize(out) if os.path.exists(out) else 0
    if size:
        os.remove(out)
    xps = os.path.join(tempfile.gettempdir(), "xpspeak_mcp_save.xps")
    r = rpc("tools/call", {"name": "xpspeak_save", "arguments": {"path": xps}})
    assert not r["result"].get("isError"), r
    saved = os.path.getsize(xps) if os.path.exists(xps) else 0
    head = open(xps, "rb").read(12) if saved else b""
    if saved:
        os.remove(xps)
    rpc("tools/call", {"name": "xpspeak_close", "arguments": {}})
    pr.stdin.close()
    pr.wait(timeout=10)
    assert size > 1000, "导出不到 1KB: %d" % size
    assert saved > 1000 and head.startswith(b"XPSPEAK"), "存 .xps 异常: %d %r" % (saved, head)
    print("selftest OK: %d 个工具，initialize/tools-list/status/open/save/export/close 全通，导出 %d 字节、存 .xps %d 字节"
          % (len(names), size, saved))


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else serve()
