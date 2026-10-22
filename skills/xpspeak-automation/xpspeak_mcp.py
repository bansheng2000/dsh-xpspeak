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
import contextlib, ctypes, ctypes.wintypes, json, math, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xpspeak_drive as D
import xps_fit as F          # 带约束拟合（纯 Python，不需要 GUI，见 xps_fit.py 顶部说明）

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


def do_fit(a):
    """一次调用完成「拟合 -> 参数±σ -> 优度 -> 可选曲线 CSV」，不启动 GUI。

    XPSPeak 的 Optimise 是无约束优化（实测会把 2p1/2 压成面积 1/6，物理上错误），
    所以「要可发表的结果」必须走这里；GUI 只用于人工看/交付 .xps。"""
    res = F.run(a["path"], [float(c) for c in a["centers"]], a.get("bg", "file"),
                int(a.get("bg_col", 4)), float(a.get("ds", 1.18)),
                float(a.get("ratio", 0.5)), float(a.get("eta", 0.3)),
                a.get("csv"), int(a.get("scan", 0)))
    txt = F.report(res, head="文件: %s" % a["path"])
    for k in sorted(k for k in res if k.startswith("scan")):
        s = res[k]
        txt += "\n  对照 %s 条双线: rms=%.1f  AIC=%.1f  R²=%.6f" % (s["nc"], s["rms"], s["aic"], s["r2"])
    if a.get("csv"):
        txt += "\n曲线已写入: %s" % a["csv"]
    return txt


def _spec_areas(path):
    """从导出的谱图里量出每条分峰的面积（梯形积分 (Peak_i - Background)）。
    纯标准库：这份文件就是 .xps 里存的那条曲线，量它等于量交付物本身。"""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        f.readline()
        for ln in f:
            t = ln.split()
            if len(t) >= 5:
                try:
                    rows.append([float(v) for v in t])
                except ValueError:
                    pass
    if len(rows) < 3:
        return []
    rows.sort(key=lambda r: -r[0])
    out = []
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        step = abs(a[0] - b[0])
        for j in range(4, len(a)):
            if j >= len(b):
                break
            if len(out) <= j - 4:
                out.append(0.0)
            out[j - 4] += (a[j] - a[3] + b[j] - b[3]) / 2.0 * step
    return out


def do_build(a):
    """一次调用把一组峰参数落成可交付的 .xps（不需要人点 GUI）。

    与「先造 .rpa 再 Load Region Parameters」的区别：那条路首峰 Area 会被软件按 height 反算忽略，
    生成曲线比参数小约 25%；走 Add Peak 对话框则 Area 被真正采用（实测面积吻合）。"""
    peaks = a["peaks"]
    if not peaks:
        raise ValueError("peaks 不能为空")
    out = os.path.normpath(a.get("out") or os.path.join(D.WORKDIR, "built.xps"))
    spec = os.path.normpath(a["spec"]) if a.get("spec") else None
    par = os.path.normpath(a["par"]) if a.get("par") else None
    r = D.build(EXE, a["path"], peaks, out, spec, par, a.get("bg"))
    txt = "已生成 %s（%d 字节）" % (r["xps"], r["xps_bytes"])
    if spec:
        txt += "\n谱图 %s" % spec
    if par:
        txt += "\n参数 %s" % par
    if spec:
        got = _spec_areas(spec)
        for i, pk in enumerate(r["peaks"]):
            want = pk["area"]
            if i < len(got):
                txt += "\n  峰%d  %8.3f eV  FWHM %.3f  面积 要求 %.0f / 实测 %.0f  (%+.1f%%)" % (
                    i + 1, pk["pos"], pk["fwhm"], want, got[i], 100.0 * (got[i] / want - 1.0))
            else:
                txt += "\n  峰%d  %8.3f eV  FWHM %.3f  面积 %.0f（谱图里没找到对应峰列）" % (
                    i + 1, pk["pos"], pk["fwhm"], want)
    return txt


def do_call(name, a):
    if name == "xpspeak_fit":
        return do_fit(a)
    if name == "xpspeak_build":
        return do_build(a)
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
    ("xpspeak_fit", "带约束的多峰双线拟合（纯 Python，毫秒级，不启动 GUI）。输入 XPSPeak 导出的谱图（BE/Raw/.../Background）或两列 ASCII，返回每条组分的 center/FWHM/面积±σ、rms、R²、AIC，可一次扫 1..N 条模型做对比。约束内建：自旋轨道劈裂 Δ 固定、面积比固定、双峰等宽 —— 这就是可发表的版本；XPSPeak 自带的 Optimise 是无约束的，会把伴线压到物理上错误的宽度，别用它出结论。",
     {"path": dict(S, description="谱图文件绝对路径（XPSPeak 导出的 .dat 或两列 BE+Intensity）"),
      "centers": {"type": "array", "items": {"type": "number"},
                  "description": "各组分主峰初值（eV，低结合能那条），如 [162.09]；多条如 [162.09, 167.6]"},
      "bg": {"type": "string", "enum": ["file", "linear", "shirley", "none"],
             "description": "背景来源：file=用文件自带背景列（默认，最稳）；linear/shirley=自己算；none=不扣"},
      "bg_col": {"type": "number", "description": "背景列序号（1 起），默认 4"},
      "ds": {"type": "number", "description": "自旋轨道劈裂 ΔBE（eV）：S 2p=1.18，其他元素按文献值，默认 1.18"},
      "ratio": {"type": "number", "description": "伴线/主峰面积比，2p=0.5，d 轨道=2/3，默认 0.5"},
      "eta": {"type": "number", "description": "%Lorentzian（0-1），默认 0.3"},
      "scan": {"type": "number", "description": "同时扫 1..N 条双线做 AIC 对比（N ≤ centers 个数）"},
      "csv": dict(S, description="可选：把 BE/Signal/Fit/Resid 曲线写到这个 CSV")}, ["path", "centers"]),
    ("xpspeak_build", "一次调用生成可交付的 .xps：载谱 -> 扣背景 -> 按给定峰参数逐个 Add Peak -> 导出谱图/参数 -> Save XPS，全程不用人点界面，并回读谱图复核每峰面积。典型用法：先 xpspeak_fit 出可发表参数，再用本工具把同一组参数落成 XPSPeak 原生文件（给人工看/存档/交付）。注意别用「造 .rpa 再 Load Region Parameters」那条路：实测首峰 Area 会被软件按 height 反算忽略，曲线比参数小约 25%。",
     {"path": dict(S, description="源谱图绝对路径（两列 ASCII 或 .xps）"),
      "peaks": {"type": "array", "description": "分峰参数，按顺序 Add Peak",
                "items": {"type": "object", "properties": {
                    "pos": {"type": "number", "description": "峰位 eV"},
                    "fwhm": {"type": "number", "description": "半高宽 eV"},
                    "area": {"type": "number", "description": "面积（会被真正采用）"},
                    "gl": {"type": "number", "description": "%Lorentzian，默认 30"}},
                    "required": ["pos", "fwhm", "area"]}},
      "out": dict(S, description="输出 .xps 绝对路径；省略则写 ~/xpspeak_auto/built.xps"),
      "spec": dict(S, description="可选：同时导出谱图 ASCII 到这个路径（用于复核面积）"),
      "par": dict(S, description="可选：同时导出峰参数表到这个路径"),
      "bg": {"type": "boolean", "description": "是否先扣背景建区域；ASCII 源默认 true，.xps 源默认 false"}},
     ["path", "peaks"]),
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
            "serverInfo": {"name": "xpspeak", "version": "0.3.0"}}}
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
    assert len(names) == 9 and "xpspeak_build" in names and "xpspeak_save" in names, names
    # 不依赖 GUI 的拟合自检：合成一条双峰谱，验证 xpspeak_fit 能把峰位/半宽拟合回来
    syn = os.path.join(tempfile.gettempdir(), "xpspeak_fit_check.dat")
    with open(syn, "w", encoding="utf-8") as fh:
        fh.write("BE Raw PeakSum Background\n")
        for i in range(121):
            e = 175.0 - 0.1 * i
            fh.write("%.4f %.2f %.2f %.2f\n" % (e, 3000 + F.model_at(e, [163.0, 1.0, 200.0], 1, 0.0, 0.0, 0.3),
                                                 3000.0, 3000.0))
    fr = rpc("tools/call", {"name": "xpspeak_fit",
                            "arguments": {"path": syn, "centers": [163.05], "bg": "file",
                                          "ds": 0.0, "ratio": 0.0, "eta": 0.3}})
    assert not fr["result"].get("isError"), fr
    txt = fr["result"]["content"][0]["text"]
    assert "162.9" in txt or "163.0" in txt, txt
    os.remove(syn)
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
    bspec = os.path.join(tempfile.gettempdir(), "xpspeak_build_check.dat")
    bxps = os.path.join(tempfile.gettempdir(), "xpspeak_build_check.xps")
    br = rpc("tools/call", {"name": "xpspeak_build", "arguments": {
        "path": sample, "peaks": [{"pos": 40.0, "fwhm": 1.2, "area": 5000.0}],
        "out": bxps, "spec": bspec}})
    assert not br["result"].get("isError"), br
    btxt = br["result"]["content"][0]["text"]
    assert "实测" in btxt, btxt
    built = os.path.getsize(bxps) if os.path.exists(bxps) else 0
    for f in (bspec, bxps):
        if os.path.exists(f):
            os.remove(f)
    rpc("tools/call", {"name": "xpspeak_close", "arguments": {}})
    pr.stdin.close()
    pr.wait(timeout=10)
    assert size > 1000, "导出不到 1KB: %d" % size
    assert saved > 1000 and head.startswith(b"XPSPEAK"), "存 .xps 异常: %d %r" % (saved, head)
    assert built > 1000, "xpspeak_build 产物异常: %d" % built
    print("selftest OK: %d 个工具，initialize/tools-list/fit/status/open/save/export/build/close 全通，"
          "导出 %d 字节、存 .xps %d 字节、生成 .xps %d 字节" % (len(names), size, saved, built))


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else serve()
