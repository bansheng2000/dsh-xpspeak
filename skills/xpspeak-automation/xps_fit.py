# -*- coding: utf-8 -*-
"""XPS 分峰拟合（纯标准库，不依赖 numpy/scipy）。

为什么需要它：XPSPeak 4.1 的 Optimise 是**无约束**优化 —— 实测（2026-10-22 MoS2/S 2p）
会把 2p1/2 伴线压到主峰面积的 1/6、FWHM 比主峰更窄，物理上错误；而且它把曲线数组存在
.xps 里，改参数不会重算曲线。要「带约束、可发表」的结果只能自己算；一次拟合是毫秒级，
比让 GUI 探针一步一步走（每步一次模型往返）快 3~4 个数量级。

模型：N 条自旋轨道双峰（2p/d 型）
  每条 = 主峰 + 伴峰（高结合能方向 +ds，面积 ratio 倍，同一 FWHM）
  线型 = 伪 Voigt（eta = %Lorentzian）；约束内建：Δ 固定、面积比固定、双峰等宽。
  自由参数只有每条的 (center, fwhm, height) —— 这就是可发表版本；放开约束的拟合
  可作为系统误差上界（--free 见 SKILL.md 的做法）。

用法：
  python xps_fit.py spec.dat --centers 162.09 --bg file
  python xps_fit.py spec.dat --centers 162.09,167.6 --scan 2 --csv curves.csv
  python xps_fit.py spec.dat --centers 162.09 --bg linear --csv out/curves.csv
自检（不需要谱图文件，合成数据回归）：
  python xps_fit.py --selftest
"""
import json, math, sys

LOG2 = math.log(2.0)


def load(path, bg_col=4):
    """读谱图：空白/逗号分隔的数值表。列序 = BE, Raw, [PeakSum], [Background], ...
    bg_col=4 表示第 4 列（1 起）是背景（XPSPeak 导出格式）。返回 (E, raw, bg|None)，
    E 按结合能**降序**排列（高 BE -> 低 BE，Shirley 累加方向）。"""
    rows = []
    for ln in open(path, encoding="utf-8", errors="replace"):
        t = ln.replace(",", " ").split()
        if not t:
            continue
        try:
            v = [float(x) for x in t]
        except ValueError:
            continue                      # 表头 / 文本行
        if len(v) >= 2:
            rows.append(v)
    if len(rows) < 10:
        raise ValueError("可用数据点太少: %d" % len(rows))
    ncol = min(len(r) for r in rows)
    rows.sort(key=lambda r: -r[0])
    E = [r[0] for r in rows]
    raw = [r[1] for r in rows]
    bg = [r[bg_col - 1] for r in rows] if (bg_col and ncol >= bg_col) else None
    return E, raw, bg, ncol


def linear_bg(E, raw):
    """两端点连线的线性背景（无 XPSPeak 背景列时的兜底）。"""
    y0, y1 = raw[0], raw[-1]
    span = (E[-1] - E[0]) or 1.0
    return [y0 + (y1 - y0) * (e - E[0]) / span for e in E]


def shirley_bg(E, raw, iters=12):
    """迭代 Shirley 背景（自高 BE 向低 BE 累加剩余峰面积）。只用于没有文件自带背景时。"""
    lo, hi = min(raw), max(raw)
    bg = [min(hi, max(lo, y)) for y in raw]        # 初值 = 数据本身
    n = len(raw)
    for _ in range(iters):
        sig = [raw[i] - bg[i] for i in range(n)]
        acc, S = 0.0, []
        for i in range(n - 1, -1, -1):             # 高 BE -> 低 BE
            acc += max(0.0, sig[i])
            S.append(acc)
        S.reverse()
        tot = S[0] or 1.0
        a, b = raw[-1], raw[0]                     # 低 BE 端 / 高 BE 端强度
        bg = [a + (b - a) * (S[i] / tot) for i in range(n)]
    return bg


def gl(x, c, w, h, eta):
    """伪 Voigt（按高度归一）：h=峰高，w=FWHM，eta=Lorentzian 分数。"""
    t = (x - c) / w
    g = math.exp(-4.0 * LOG2 * t * t)
    l = 1.0 / (1.0 + 4.0 * t * t)
    return h * (eta * l + (1.0 - eta) * g)


def area_factor(eta):
    """高度 -> 面积（单位 FWHM*高度）的换算系数。"""
    return eta * math.pi / 2.0 + (1.0 - eta) * math.sqrt(math.pi / (4.0 * LOG2))


def model_at(x, p, nc, ds, ratio, eta):
    y = 0.0
    for k in range(nc):
        c, w, h = p[3 * k], p[3 * k + 1], p[3 * k + 2]
        y += gl(x, c, w, h, eta) + gl(x, c + ds, w, h * ratio, eta)
    return y


def _solve(A, b):
    """高斯消元（部分选主元），A 为小方阵。"""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-300:
            return None
        M[col], M[piv] = M[piv], M[col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            if f:
                for k in range(col, n + 1):
                    M[r][k] -= f * M[col][k]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / M[i][i]
    return x


def _inv(A):
    n = len(A)
    M = [list(A[i]) + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-300:
            return None
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col]
        M[col] = [v / d for v in M[col]]
        for r in range(n):
            if r != col and M[r][col]:
                f = M[r][col]
                M[r] = [a - f * b for a, b in zip(M[r], M[col])]
    return [row[n:] for row in M]


def fit(E, raw, bg, centers, ds=1.18, ratio=0.5, eta=0.3,
        ctol=1.0, wlo=0.6, whi=4.0, hmax=120000.0, iters=400):
    """带约束的 LM 拟合。返回 dict（含每组分参数±σ、rms、R²、AIC、残差）。"""
    nc = len(centers)
    target = [raw[i] - bg[i] for i in range(len(raw))]
    p = []
    for c in centers:
        p += [c, 1.0, 45000.0 / nc]
    lo, hi = [], []
    for c in centers:
        lo += [c - ctol, wlo, 0.0]
        hi += [c + ctol, whi, hmax]
    clamp = lambda q: [min(max(v, lo[i]), hi[i]) for i, v in enumerate(q)]

    def resid(q):
        return [target[i] - model_at(E[i], q, nc, ds, ratio, eta) for i in range(len(E))]

    def jac(q):
        J = []
        cols = []
        for j in range(len(q)):
            d = max(1e-5, 1e-5 * abs(q[j]))
            qp = q[:]; qp[j] += d
            qm = q[:]; qm[j] -= d
            cols.append([(model_at(E[i], qp, nc, ds, ratio, eta) - model_at(E[i], qm, nc, ds, ratio, eta)) / (2 * d)
                         for i in range(len(E))])
        for i in range(len(E)):
            J.append([cols[j][i] for j in range(len(q))])
        return J

    r = resid(p)
    sse = sum(v * v for v in r)
    lam = 1e-3
    for _ in range(iters):
        J = jac(p)
        k = len(p)
        JtJ = [[sum(J[i][a] * J[i][b] for i in range(len(J))) for b in range(k)] for a in range(k)]
        Jtr = [sum(J[i][a] * r[i] for i in range(len(J))) for a in range(k)]
        damped = [[JtJ[a][b] + (lam * JtJ[a][a] if a == b and JtJ[a][a] > 0 else (lam if a == b else 0.0))
                   for b in range(k)] for a in range(k)]
        step = _solve(damped, Jtr)
        if step is None:
            break
        cand = clamp([p[i] + step[i] for i in range(k)])
        rc = resid(cand)
        sse_c = sum(v * v for v in rc)
        if sse_c < sse:
            moved = max(abs(cand[i] - p[i]) for i in range(k))
            prev = sse
            p, r, sse = cand, rc, sse_c
            lam = max(lam * 0.3, 1e-12)
            # 注意：判据必须用 prev，不能用已被覆盖的 sse（否则第一次迭代就误判"已收敛"）
            if moved < 1e-10 or (prev - sse) <= 1e-12 * prev:
                break
        else:
            lam *= 5.0
            if lam > 1e12:
                break

    n, k = len(E), len(p)
    J = jac(p)
    JtJ = [[sum(J[i][a] * J[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    inv = _inv(JtJ)
    s2 = sse / max(1, n - k)
    sig = [math.sqrt(abs(inv[j][j] * s2)) if inv else float("nan") for j in range(k)]
    mean_t = sum(target) / n
    sst = sum((t - mean_t) ** 2 for t in target)
    af = area_factor(eta) * (1.0 + ratio)
    comps = []
    for i in range(nc):
        c, w, h = p[3 * i], p[3 * i + 1], p[3 * i + 2]
        area = h * w * af
        s_area = area * math.hypot(sig[3 * i] / abs(c) if c else 0.0, sig[3 * i + 1] / w if w else 0.0)
        comps.append(dict(center=round(c, 4), center_err=round(sig[3 * i], 4),
                          partner=round(c + ds, 4), fwhm=round(w, 4), fwhm_err=round(sig[3 * i + 1], 4),
                          height=round(h, 1), area=round(area, 1), area_err=round(s_area, 1)))
    tot = sum(c["area"] for c in comps) or 1.0
    for c in comps:
        c["pct"] = round(100.0 * c["area"] / tot, 2)
        c["ratio"] = round(c["area"] / comps[0]["area"], 4) if comps[0]["area"] else None
    par = [c["center"] + ds for c in comps]
    for c, pv in zip(comps, par):
        c["partner"] = round(pv, 4)
    return dict(nc=nc, n=n, k=k, ds=ds, ratio=ratio, eta=eta,
                rms=round(math.sqrt(sse / n), 2), r2=round(1.0 - sse / sst, 6) if sst else None,
                aic=round(n * math.log(sse / n) + 2 * k, 2), sse=round(sse, 2),
                comps=comps, params=[float(v) for v in p],
                curves=dict(E=E, target=target, fit=[target[i] - r[i] for i in range(n)], resid=r))


def report(res, head=""):
    L = [head] if head else []
    L.append("模型: %d 条双峰（Δ=%.3f eV, 面积比 1:%.2f, 等宽, eta=%.2f）  n=%d  k=%d"
             % (res["nc"], res["ds"], res["ratio"], res["eta"], res["n"], res["k"]))
    L.append("拟合优度: rms=%.2f cps   R²=%s   AIC=%.2f" % (res["rms"], res["r2"], res["aic"]))
    for i, c in enumerate(res["comps"], 1):
        L.append("  组分%d: %.3f±%.3f eV (伴线 %.3f)  FWHM %.3f±%.3f  area %.0f±%.0f (%.1f%%)  h=%.0f"
                 % (i, c["center"], c["center_err"], c["partner"], c["fwhm"], c["fwhm_err"],
                    c["area"], c["area_err"], c["pct"], c["height"]))
    rs = res["curves"]["resid"]
    L.append("残差: 均值 %.1f  sd %.1f  最大 |%.0f|" % (sum(rs) / len(rs), math.sqrt(sum(v * v for v in rs) / len(rs)),
                                                     max(abs(v) for v in rs)))
    return "\n".join(L)


def write_csv(path, res):
    E, t, f, r = res["curves"]["E"], res["curves"]["target"], res["curves"]["fit"], res["curves"]["resid"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("BE_eV,Signal,Fit,Resid\n")
        for i in range(len(E)):
            fh.write("%.4f,%.2f,%.2f,%.2f\n" % (E[i], t[i], f[i], r[i]))
    return path


def run(path, centers, bg_kind="file", bg_col=4, ds=1.18, ratio=0.5, eta=0.3, csv=None, scan=0):
    E, raw, bgcol, ncol = load(path, bg_col)
    if bg_kind == "file":
        if bgcol is None:
            raise ValueError("文件只有 %d 列，没有背景列；请用 --bg linear|shirley|none" % ncol)
        bg = bgcol
    elif bg_kind == "linear":
        bg = linear_bg(E, raw)
    elif bg_kind == "shirley":
        bg = shirley_bg(E, raw)
    elif bg_kind == "none":
        bg = [0.0] * len(raw)
    else:
        raise ValueError("bg 只支持 file / linear / shirley / none")
    res = fit(E, raw, bg, centers, ds, ratio, eta)
    if scan:
        for nc in range(1, min(scan, len(centers)) + 1):
            res["scan%d" % nc] = {k: v for k, v in fit(E, raw, bg, centers[:nc], ds, ratio, eta).items()
                                  if k in ("nc", "n", "k", "rms", "r2", "aic")}
    if csv:
        write_csv(csv, res)
    return res      # 保留 curves（report/写图要用）；序列化前自行剔除


def selftest():
    """合成一条双峰谱（真值 162.00 / FWHM 0.95 / 面积 80000），验证能拟合回来。"""
    ds, ratio, eta = 1.18, 0.5, 0.3
    E = [175.9 - 0.1 * i for i in range(181)]
    truth = [162.0, cz := 0.95, 54000.0]
    raw, bg = [], []
    for e in E:
        b = 5900.0 - 3.0 * (e - 158.0)
        bg.append(b)
        raw.append(b + model_at(e, truth, 1, ds, ratio, eta))
    res = fit(E, raw, bg, [162.05], ds, ratio, eta)
    c = res["comps"][0]
    area_truth = 54000.0 * 0.95 * area_factor(eta) * (1 + ratio)
    assert abs(c["center"] - 162.0) < 0.01, c
    assert abs(c["fwhm"] - 0.95) < 0.02, c
    assert abs(c["area"] - area_truth) / area_truth < 0.03, (c["area"], area_truth)
    assert res["r2"] > 0.9999, res["r2"]
    print("selftest OK: center %.3f (真值 162.000)  FWHM %.3f (0.950)  area %.0f (%.0f)  R² %.6f"
          % (c["center"], c["fwhm"], c["area"], area_truth, res["r2"]))


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # cp936 控制台打不出 R²/真值
    except Exception:
        pass
    if "--selftest" in argv:
        return selftest()
    a = argv[1:]
    if not a:
        return print(__doc__)
    path, opt = a[0], {}
    i = 1
    while i < len(a):
        k = a[i].lstrip("-")
        v = a[i + 1] if i + 1 < len(a) and not a[i + 1].startswith("--") else "1"
        opt[k] = v
        i += 2 if v != "1" or (i + 1 < len(a) and not a[i + 1].startswith("--")) else 1
    centers = [float(x) for x in opt.get("centers", "162.0").replace(",", " ").split()]
    res = run(path, centers, opt.get("bg", "file"), int(opt.get("bg-col", 4)),
              float(opt.get("ds", 1.18)), float(opt.get("ratio", 0.5)),
              float(opt.get("eta", 0.3)), opt.get("csv"), int(opt.get("scan", 0)))
    print(report(res, head="文件: %s" % path))
    for key in sorted(k for k in res if k.startswith("scan")):
        s = res[key]
        print("  参考 %s 条: rms=%.1f  AIC=%.1f  R²=%.6f" % (s["nc"], s["rms"], s["aic"], s["r2"]))


if __name__ == "__main__":
    main(sys.argv)
