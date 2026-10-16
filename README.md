# dsh-xpspeak

用 AI/脚本驱动 **XPSPeak 4.1**（XPSPEAK41.exe，XPS 分峰软件）。

- 纯标准库 Python，**零依赖**：不需要 pywin32 / pywinauto / UIAutomation
- 走 **Win32 消息级自动化**（按菜单文字取 id → \`PostMessage WM_COMMAND\`），不抢鼠标、不抢前台焦点、和 DPI/分辨率无关
- 自带 MCP server（stdio，7 个工具），DSH / Claude Code / Cursor 等任何 MCP 客户端都能用；也可以不装 MCP 直接命令行跑

## 文件

    install.py                                   一键装：拷文件 + 自动找 XPSPEAK41.exe + 打印 mcpServers JSON
    skills/xpspeak-automation/SKILL.md           安装手册 + 控制方式选型 + 10 条实测坑
    skills/xpspeak-automation/xpspeak_mcp.py     MCP server（stdio）
    skills/xpspeak-automation/xpspeak_drive.py   Win32 消息驱动（也可独立当 CLI）

## 安装

**0. 一条命令**（推荐；在仓库根目录，只要有 Python 就行）：

    python install.py

它会：把 \`SKILL.md\` + 两个 .py 拷到 \`%USERPROFILE%\.agents\skills\xpspeak-automation\\\`、在常见位置自动搜 \`XPSPEAK41.exe\`、打印一段**可直接导入的 mcpServers JSON**（同目录还会落一份 \`mcpServers.json\`）。
参数：\`--exe <路径>\` 指定 XPSPEAK41.exe、\`--dest <目录>\` 换安装位置、\`--json-only\` 只打印 JSON。

> 为什么不能"给个 GitHub 链接就直接装进 MCP"：这个 MCP 驱动的是**本机 GUI**，它必须是本机 stdio 进程，装不了连接器市场里那种托管服务（DSH 市场 109 条全是 \`transport: streamable-http\`）。**GitHub 给代码，本机这一条命令负责落地 + 注册** —— 这就是最短的路。

**1. 手工拷目录**（整个 \`xpspeak-automation/\` 一起，两个 .py 必须同目录）：

    %USERPROFILE%\.agents\skills\xpspeak-automation\

**2. 注册 MCP**（JSON 里反斜杠写两个）：

    {"mcpServers":{"xpspeak":{"command":"<PYTHON.exe>","args":["<DIR>\\xpspeak_mcp.py"],"env":{"XPSPEAK_EXE":"<XPSPEAK41.exe>"}}}}

DSH 用 \`mcp_connector_import_json\` 粘贴这段即可；其它客户端写进各自的 mcpServers。

**3. 自检**（会真开一次 XPSPeak，约 12 秒）：

    "<PYTHON.exe>" "<DIR>\xpspeak_mcp.py" --selftest

**4. 重启客户端**，先调 \`xpspeak_status\`。

不用 MCP 也能跑：

    python xpspeak_drive.py inventory
    python xpspeak_drive.py export <in.xps> <out.dat>
    python xpspeak_drive.py importascii <in.txt> <out.dat>

## 要求

- Windows + 已安装 XPSPeak 4.1（\`XPSPEAK41.exe\`）
- Python 3.8+，标准库即可
- \`XPSPEAK_EXE\` 指向本机的 XPSPEAK41.exe（不同机器路径不同，这是唯一需要改的旋钮）

## 为什么不用 pywinauto / 键鼠脚本

XPSPeak 是 VB6 程序，控件属性暴露极少，pywinauto/UIA 反而更绕；键鼠模拟要求前台窗口和固定坐标，用户碰一下鼠标就崩。菜单是**真 Win32 菜单**，按文字取 id 后 \`PostMessage WM_COMMAND\` 是最稳的一条路。

详细坑表见 \`skills/xpspeak-automation/SKILL.md\`。
