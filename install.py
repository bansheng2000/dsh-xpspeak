#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""XPSPeak MCP 一键装：拷文件到技能目录 + 打印可直接导入的 mcpServers JSON。

用法（在仓库根目录，路径用正斜杠照样能跑）：
    python install.py                     # 自动找 XPSPEAK41.exe，装到 ~/.agents/skills/xpspeak-automation
    python install.py --exe "D:/tools/XPSPEAK41/XPSPEAK41.exe" --dest "D:/tools/xpspeak-automation"
    python install.py --json-only         # 只打印 JSON（方便管道）
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "skills", "xpspeak-automation")
FILES = ["SKILL.md", "xpspeak_mcp.py", "xpspeak_drive.py"]
SKIP_DIRS = {"windows", "$recycle.bin", "system volume information", "node_modules", ".git"}


def find_exe(hint=None):
    """在常见位置按深度<=3 找 XPSPEAK41.exe；找不到返回 ''。"""
    if hint:
        return hint
    env = os.environ.get("XPSPEAK_EXE")
    if env and os.path.exists(env):
        return env
    roots = [os.path.expanduser(r"~\Downloads"), os.path.expanduser(r"~\Desktop"), os.path.expanduser("~")]
    for d in "CDEFGH":
        p = d + ":\\"
        if os.path.isdir(p):
            roots.append(p)
    seen = 0
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth >= 3:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith("$")]
            seen += 1
            if seen > 30000:
                return ""
            if "XPSPEAK41.exe" in filenames:
                return os.path.join(dirpath, "XPSPEAK41.exe")
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", help="本机 XPSPEAK41.exe 的绝对路径（不给就自动找）")
    ap.add_argument("--dest", default=os.path.join(os.path.expanduser("~"), ".agents", "skills", "xpspeak-automation"))
    ap.add_argument("--json-only", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(SRC):
        sys.exit("找不到源目录：%s（请在仓库根目录运行本脚本）" % SRC)
    os.makedirs(a.dest, exist_ok=True)
    for f in FILES:
        shutil.copy2(os.path.join(SRC, f), os.path.join(a.dest, f))
    py = sys.executable or "python.exe"
    exe = find_exe(a.exe)

    cfg = {"mcpServers": {"xpspeak": {
        "command": py,
        "args": [os.path.join(a.dest, "xpspeak_mcp.py")],
        "env": {"XPSPEAK_EXE": exe or "<请填本机 XPSPEAK41.exe 的绝对路径>"},
    }}}
    text = json.dumps(cfg, ensure_ascii=False)
    with open(os.path.join(a.dest, "mcpServers.json"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")

    if a.json_only:
        print(text)
        return
    print("已装到: %s" % a.dest)
    print("python: %s" % py)
    print("xpspeak: %s" % (exe or "!! 没找到 XPSPEAK41.exe，装好后请手工填下面 JSON 里的 XPSPEAK_EXE"))
    print("\n把它交给 DSH 的 mcp_connector_import_json（或写进别的客户端的 mcpServers）：\n")
    print(text)
    print("\n自检（会真开一次 XPSPeak，约 12 秒）: \"%s\" \"%s\" --selftest" % (py, os.path.join(a.dest, "xpspeak_mcp.py")))


if __name__ == "__main__":
    main()
