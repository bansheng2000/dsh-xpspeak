# dsh-xpspeak

用 AI/脚本驱动 **XPSPeak 4.1**（XPSPEAK41.exe，XPS 分峰软件）。

- 纯标准库 Python，**零依赖**：不需要 pywin32 / pywinauto / UIAutomation
- 走 **Win32 消息级自动化**（按菜单文字取 id → `PostMessage WM_COMMAND`），不抢鼠标、不抢前台焦点、和 DPI/分辨率无关
- 自带 MCP server（stdio，9 个工具），DSH / Claude Code / Cursor 等任何 MCP 客户端都能用；也可以不装 MCP 直接命令行跑

## 两档模式：教你 / 替你做

- **教学模式**（用户说「教我 / 带我走一遍 / 我想学」）：一次只讲一小步（位置 → 目的 → 判据），讲完就停等 `done`；**服务端硬拦所有会改界面的调用** —— `open / export / save / menu / click / close / build` 直接返回拒绝 +「这一步请你操作」，只读的 `xpspeak_status` 与纯计算的 `xpspeak_fit` 照常可用。
- **代执行**（用户说「你来做 / 帮我处理完」）：在授权范围内一次做完；范围（哪些文件、哪些谱、能不能覆盖原工程）先说清，中途扩大范围先问。档位没说清就先问一句。
- 切换：`xpspeak_status(mode="teach"|"exec")`；用户说「你来吧」才切代执行，切换时先复述授权范围。
- 凡给结论都按五段：**当前证据 → 改什么 → 为什么 → 依据 → 如何判定**；缺一段即降级为「未验证」。

## 文件

    install.py                                   一键装：拷文件 + 自动找 XPSPEAK41.exe + 打印 mcpServers JSON
    skills/xpspeak-automation/SKILL.md           安装手册 + 控制方式选型 + 13 条实测坑
    skills/xpspeak-automation/xpspeak_mcp.py     MCP server（stdio）
    skills/xpspeak-automation/xpspeak_drive.py   Win32 消息驱动（也可独立当 CLI）
    skills/xpspeak-automation/xps_fit.py         带约束的分峰拟合（纯 Python，毫秒级，不碰 GUI）

## 安装

**0. 一条命令**（推荐；在仓库根目录，只要有 Python 就行）：

    "<PYTHON.exe>" install.py

它会：把 `SKILL.md` + 三个 .py 拷到 `%USERPROFILE%\.agents\skills\xpspeak-automation\\`、在常见位置自动搜 `XPSPEAK41.exe`、打印一段**可直接导入的 mcpServers JSON**（同目录还会落一份 `mcpServers.json`）。
参数：`--exe <路径>` 指定 XPSPEAK41.exe、`--dest <目录>` 换安装位置、`--json-only` 只打印 JSON。

> 为什么不能"给个 GitHub 链接就直接装进 MCP"：这个 MCP 驱动的是**本机 GUI**，它必须是本机 stdio 进程，装不了连接器市场里那种托管服务（DSH 市场 109 条全是 `transport: streamable-http`）。**GitHub 给代码，本机这一条命令负责落地 + 注册** —— 这就是最短的路。

**1. 手工拷目录**（整个 `xpspeak-automation/` 一起，三个 .py 必须同目录）：

    %USERPROFILE%\.agents\skills\xpspeak-automation\

**2. 注册 MCP**（JSON 里反斜杠写两个）：

    {"mcpServers":{"xpspeak":{"command":"<PYTHON.exe>","args":["<DIR>\\xpspeak_mcp.py"],"env":{"XPSPEAK_EXE":"<XPSPEAK41.exe>"}}}}

DSH 用 `mcp_connector_import_json` 粘贴这段即可；其它客户端写进各自的 mcpServers。

**3. 自检**（会真开一次 XPSPeak，约 12 秒）：

    "<PYTHON.exe>" "<DIR>\xpspeak_mcp.py" --selftest

**4. 重启客户端**，先调 `xpspeak_status`。

不用 MCP 也能跑：

    "<PYTHON.exe>" xpspeak_drive.py inventory
    "<PYTHON.exe>" xpspeak_drive.py export <in.xps> <out.dat>
    "<PYTHON.exe>" xpspeak_drive.py importascii <in.txt> <out.dat>

## 要求

- Windows + 已安装 XPSPeak 4.1（`XPSPEAK41.exe`）
- Python 3.8+，标准库即可。**命令里的 `<PYTHON.exe>` 不是随便写的 `python`**：本机裸 `python` 可能是 WindowsApps 存根（退出码 49、零输出、什么都不做），一律写真实 `python.exe` 的绝对路径；DSH 自带的在 `C:\Users\12576\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`
- `XPSPEAK_EXE` 指向本机的 XPSPEAK41.exe（不同机器路径不同，这是唯一需要改的旋钮）

## 分峰：用 `xpspeak_fit`，别用 GUI 的 Optimise

XPSPeak 4.1 的 Optimise 是**无约束**优化：实测把 MoS₂/S 2p 跑成 2p₁/₂ 面积只有主峰的 1/6、FWHM 比主峰还窄（物理上不可能），而且它把曲线数组存在 `.xps` 里，改参数不会重算曲线。

`xpspeak_fit` 用参数化内建约束（自旋轨道劈裂 Δ 固定、面积比固定、双峰等宽），毫秒级出 center/FWHM/面积±σ + rms/R²/AIC，还能一次扫 1..N 条模型做 AIC 对比：

    "<PYTHON.exe>" skills/xpspeak-automation/xps_fit.py out/mos_s2p_export.dat --centers 162.09 --bg file
    "<PYTHON.exe>" skills/xpspeak-automation/xps_fit.py out/mos_s2p_export.dat --centers 162.09,167.6,169.2 --bg file --scan 3

回归基准（MoS₂/S 2p，181 点）：162.059±0.003 eV、FWHM 0.952±0.006、area 80944±698、rms 577.4、R² 0.996822、AIC 2307.8。
（area 的 σ 从旧记录的 ±518 变成 ±698：旧公式漏了 height 项、σ 系统性偏小，现已按 `area=h·w·af` 传播两项；中心值 80944 与其余指标一位没变。）


## 生成 .xps：用 `xpspeak_build`

分峰结论由 `xpspeak_fit` 出，要交一份能在 XPSPeak 里打开、参数和曲线自洽的 `.xps` 时用 `xpspeak_build`：一次调用完成「载谱 → 扣背景 → 逐个 Add Peak → 导出谱图/参数 → Save XPS」，并回读谱图复核每峰面积（实测与要求值差 1.8%，是 0.1 eV 粗网格的梯形积分误差）。

    xpspeak_build  path=out/mos_s2p.dat  peaks=[{pos:162.059,fwhm:0.952,area:53963},{pos:163.239,fwhm:0.952,area:26981}]  out=out/mos_s2p.xps  spec=out/check.dat

> 别用「程序化造 `.rpa` 再 Load Region Parameters」那条路：实测首峰 Area 会被软件按 height 反算忽略（写 53963 读回 40401.88），主峰曲线画小约 25%。Add Peak 对话框的 Area 才是真正生效的。

## 为什么不用 pywinauto / 键鼠脚本

XPSPeak 是 VB6 程序，控件属性暴露极少，pywinauto/UIA 反而更绕；键鼠模拟要求前台窗口和固定坐标，用户碰一下鼠标就崩。菜单是**真 Win32 菜单**，按文字取 id 后 `PostMessage WM_COMMAND` 是最稳的一条路。

详细坑表见 `skills/xpspeak-automation/SKILL.md`。
