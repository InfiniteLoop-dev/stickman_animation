"""
火柴人投篮动画（夜晚球场）
运行：python stickman_basketball.py
装了 ffmpeg 就导出 mp4；没装就自动导出 gif。

整体思路：
  1. 把一切都写成"时间 t 的函数"：给定 t，就能算出火柴人每个关节、篮球在哪里
  2. 每一帧只做一件事：算出这一刻的位置 -> 画出来
  3. matplotlib 把 300 帧拼成视频
"""
import math
import shutil

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.animation import FuncAnimation, FFMpegWriter, PillowWriter
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Circle, Ellipse, Polygon, Rectangle

# ====================================================================
# 1. 全局设置（单位：画面宽 16、高 9，不是像素）
# ====================================================================
FPS = 30
DURATION = 10.0
N_FRAMES = int(FPS * DURATION)

W, H = 16.0, 9.0
G = 1.2          # 地面的高度
BX = 3.5         # 火柴人所在的横坐标
R = 0.25         # 篮球半径
GRAV = 12.0      # 篮球的重力加速度

# 篮筐
RIM_Y = 5.8
RIM_L, RIM_R = 12.15, 13.05
RIM_X = (RIM_L + RIM_R) / 2

# 时间线（秒）
T_DRIB0 = 0.8                      # 开始运球
PERIOD = 0.8                       # 每次运球用时
N_DRIB = 4                         # 运球次数
T_DRIB1 = T_DRIB0 + N_DRIB * PERIOD  # 运球结束 = 4.0
T_SET = 5.2                        # 蹲到最低、球举过头顶
T_TO = 5.5                         # 起跳
AIR = 0.62                         # 滞空时间
T_LAND = T_TO + AIR                # 落地
T_REL = 5.85                       # 出手
FLIGHT = 1.15                      # 球飞行时间
T_RIM = T_REL + FLIGHT             # 球入网 = 7.0
T_CELE = 7.45                      # 开始庆祝挥拳

BODY_C = "#f4f6ff"   # 近侧手脚颜色
FAR_C = "#98a4cc"    # 远侧手脚颜色（暗一点，有层次）
BALL_C = "#f08a3c"
SEAM_C = "#4a210d"
RIM_C = "#ff5a36"


# ====================================================================
# 2. 小工具函数
# ====================================================================
def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def smooth(u):
    """0~1 之间的缓入缓出：开头慢、中间快、结尾慢，动作更自然"""
    u = clamp(u)
    return u * u * (3 - 2 * u)


def lerp(a, b, u):
    return a + (b - a) * u


def lerp2(p, q, u):
    return (lerp(p[0], q[0], u), lerp(p[1], q[1], u))


def track(t, keys, ease=True):
    """关键帧插值：keys = [(时间, 值), ...]，值可以是数字或 (x, y) 元组"""
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            u = (t - t0) / (t1 - t0)
            if ease:
                u = smooth(u)
            if isinstance(v0, (tuple, list)):
                return tuple(lerp(a, b, u) for a, b in zip(v0, v1))
            return lerp(v0, v1, u)


def two_link(p0, target, l1, l2, bend):
    """
    两段式肢体（上臂+前臂 / 大腿+小腿）：
    已知起点和想让末端到达的位置，算出中间关节（肘/膝）在哪。
    bend=+1 关节偏向左侧，-1 偏向右侧。
    """
    dx, dy = target[0] - p0[0], target[1] - p0[1]
    d = math.hypot(dx, dy)
    d = clamp(d, abs(l1 - l2) + 1e-3, l1 + l2 - 1e-3)
    ang = math.atan2(dy, dx)
    cos_a = (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d)
    a = math.acos(clamp(cos_a, -1, 1))
    a1 = ang + bend * a
    joint = (p0[0] + l1 * math.cos(a1), p0[1] + l1 * math.sin(a1))
    end = (p0[0] + d * math.cos(ang), p0[1] + d * math.sin(ang))
    return joint, end


def polar(origin, length, angle_deg):
    a = math.radians(angle_deg)
    return (origin[0] + length * math.cos(a), origin[1] + length * math.sin(a))


# ====================================================================
# 3. 运球：每个周期分四段（压球 -> 下落 -> 反弹上升 -> 接住）
# ====================================================================
HH, HL = 1.65, 1.35        # 手的最高、最低位置（相对地面）
DRIB_CONTACTS = [T_DRIB0 + (k + 0.45) * PERIOD for k in range(N_DRIB)]  # 球砸地的时刻


def dribble(t):
    """返回 (手的高度, 球心高度, 周期内进度)"""
    if t < T_DRIB0:
        return HH, HH - 0.2, 0.9
    k = min(int((t - T_DRIB0) // PERIOD), N_DRIB - 1)
    tau = clamp((t - T_DRIB0) / PERIOD - k)
    if tau < 0.15:                      # 手向下压球
        u = smooth(tau / 0.15)
        hand = lerp(HH, HL, u)
        ball = hand - 0.2
    elif tau < 0.45:                    # 球离手，加速落向地面
        u = (tau - 0.15) / 0.30
        top = HL - 0.2
        ball = top + (R - top) * (0.3 * u + 0.7 * u * u)
        hand = HL
    elif tau < 0.85:                   # 反弹，减速上升
        u = (tau - 0.45) / 0.40
        ball = R + ((HH - 0.2) - R) * (1 - (1 - u) ** 2)
        hand = max(lerp(HL, HH, smooth(u)), ball + 0.2)
    else:                              # 手接住球
        hand, ball = HH, HH - 0.2
    return hand, ball, tau


# ====================================================================
# 4. 投篮：起跳、球的飞行轨迹
# ====================================================================
def jump_offset(t):
    """身体离地的高度：起跳是一条抛物线，进球后是几个小跳庆祝"""
    if T_TO <= t <= T_LAND:
        s = (t - T_TO) / AIR
        return 0.75 * 4 * s * (1 - s)
    if t >= T_RIM:
        k = (t - T_RIM) / 0.5
        env = 1 - smooth((t - T_RIM - 1.0) / 0.6)
        return 0.2 * abs(math.sin(math.pi * k)) * env
    return 0.0


HIP_KEYS = [(0, 1.25), (T_DRIB1, 1.25), (4.6, 1.05), (T_SET, 0.85), (T_TO, 1.27),
            (T_LAND, 1.2), (6.25, 1.0), (6.7, 1.25), (DURATION, 1.25)]
LEAN_KEYS = [(T_DRIB1, 6), (T_SET, 10), (T_TO, 8), (T_REL, -2), (T_LAND, 2),
             (6.6, 5), (7.0, 5), (7.3, -5), (DURATION, -4)]

# 球在身体坐标系里的位置（相对脚下地面、相对火柴人 x）
REL_REL = (0.52, 2.95)   # 出手瞬间球的位置


def ball_rel(t):
    if t < T_SET:   # 从腰间举到头顶
        return track(t, [(T_DRIB1, (0.55, 1.45)), (T_SET, (0.55, 2.45))])
    return track(t, [(T_SET, (0.55, 2.45)), (T_TO, (0.50, 2.72)), (T_REL, REL_REL)], ease=False)


# 出手后：已知起点、终点（篮筐中心）和飞行时间，反推初速度
X0 = BX + REL_REL[0]
Y0 = G + jump_offset(T_REL) + REL_REL[1]
VX = (RIM_X - X0) / FLIGHT
VY = (RIM_Y - Y0 + 0.5 * GRAV * FLIGHT ** 2) / FLIGHT
VY_RIM = VY - GRAV * FLIGHT
SPIN = 9.0   # 球的旋转速度（弧度/秒）


def flight_pos(t):
    dt = t - T_REL
    return (X0 + VX * dt, Y0 + VY * dt - 0.5 * GRAV * dt * dt)


# 球穿过篮网后：慢慢下落、落地反弹。这里提前用小步长把整段运动"模拟"出来
SIM_DT = 0.002


def simulate_after_rim():
    x, y = RIM_X, RIM_Y
    vx, vy = 0.15, VY_RIM * 0.45          # 被篮网减速
    xs, ys, bounces = [x], [y], []
    t = T_RIM
    for _ in range(int((DURATION - T_RIM) / SIM_DT) + 2):
        vy -= GRAV * SIM_DT
        x += vx * SIM_DT
        y += vy * SIM_DT
        t += SIM_DT
        if y <= G + R and vy < 0:          # 碰到地面
            y = G + R
            if abs(vy) > 1.5:
                vy = -vy * 0.55            # 每次弹起保留 55% 的速度
                bounces.append(t)
            else:
                vy = 0.0
        if vy == 0.0:
            vx *= 0.995                    # 滚动时的摩擦
        xs.append(x)
        ys.append(y)
    return xs, ys, bounces


SIM_X, SIM_Y, BOUNCES = simulate_after_rim()
CONTACT_TIMES = DRIB_CONTACTS + BOUNCES


def sim_pos(t):
    i = clamp(int((t - T_RIM) / SIM_DT), 0, len(SIM_X) - 1)
    return (SIM_X[i], SIM_Y[i])


def squash_at(t):
    """球砸地瞬间被压扁的程度 0~0.25"""
    s = 0.0
    for c in CONTACT_TIMES:
        dt = abs(t - c)
        if dt < 0.04:
            s = max(s, 0.25 * (1 - dt / 0.04))
    return s


# 出手后手臂的动作（相对肩膀的 长度, 角度）：随手、收回、庆祝举起
NEAR_POL = [(T_REL, (0.90, 62)), (6.0, (0.95, 40)), (6.4, (0.75, -40)), (6.9, (0.70, -65)),
            (7.15, (0.70, -65)), (T_CELE, (0.98, 75)), (DURATION, (0.98, 75))]
FAR_POL = [(T_REL, (0.90, 40)), (6.1, (0.75, -20)), (6.6, (0.70, -60)), (7.15, (0.70, -60)),
           (T_CELE, (0.98, 112)), (DURATION, (0.98, 112))]


def net_bulge(tt):
    """球穿网之后，篮网被撑开再晃动回去"""
    if tt < 0:
        return 0.0
    return (1 - math.exp(-tt / 0.04)) * math.exp(-2.2 * tt) * (0.5 + 0.5 * math.cos(14 * tt))


# ====================================================================
# 5. 火柴人的躯干：由 "髋部高度、离地高度、前倾角" 算出各关节
# ====================================================================
def torso(hip_h, J, lean_deg):
    a = math.radians(lean_deg)
    d = (math.sin(a), math.cos(a))             # 躯干朝向
    hip = (BX, G + J + hip_h)
    neck = (hip[0] + 0.8 * d[0], hip[1] + 0.8 * d[1])
    shoulder = (neck[0] - 0.07 * d[0], neck[1] - 0.07 * d[1])
    head = (neck[0] + 0.30 * d[0], neck[1] + 0.30 * d[1])
    return hip, neck, shoulder, head, d


# ====================================================================
# 6. 搭建静态背景（只画一次）：夜空、月亮、城市、路灯、地面、篮架
# ====================================================================
fig = plt.figure(figsize=(12.8, 7.2), dpi=100)   # 1280 x 720
fig.patch.set_facecolor("#070b1d")
ax = fig.add_axes([0, 0, 1, 1])
ax.axis("off")

sky = LinearSegmentedColormap.from_list("sky", ["#2b3a78", "#070b1d"])
ax.imshow(np.linspace(0, 1, 256).reshape(-1, 1), extent=(0, W, G, H), origin="lower",
          aspect="auto", cmap=sky, zorder=0)
ax.set_xlim(0, W)
ax.set_ylim(0, H)

rng = np.random.default_rng(3)
ax.scatter(rng.uniform(0, W, 110), rng.uniform(4.2, H, 110), s=rng.uniform(0.5, 5, 110),
           color="white", alpha=0.7, zorder=0.5, linewidths=0)

# 月亮
for r, a in ((1.5, 0.04), (1.1, 0.06), (0.8, 0.09)):
    ax.add_patch(Circle((1.9, 7.8), r, fc="#fff3c4", alpha=a, ec="none", zorder=0.6))
ax.add_patch(Circle((1.9, 7.8), 0.5, fc="#f5efd2", ec="none", zorder=0.7))
for dx, dy, rr in ((-0.15, 0.1, 0.1), (0.18, -0.12, 0.07), (0.05, 0.22, 0.05)):
    ax.add_patch(Circle((1.9 + dx, 7.8 + dy), rr, fc="#d9d2b0", ec="none", zorder=0.8))

# 远处的城市天际线 + 亮着灯的窗户
rng = np.random.default_rng(7)
wins_x, wins_y = [], []
x = 0.0
while x < W:
    bw, bh = rng.uniform(0.5, 1.1), rng.uniform(0.6, 2.6)
    ax.add_patch(Rectangle((x, G), bw, bh, fc="#0c1230", ec="none", zorder=1))
    for wx in np.arange(x + 0.12, x + bw - 0.1, 0.22):
        for wy in np.arange(G + 0.2, G + bh - 0.1, 0.32):
            if rng.random() < 0.35:
                wins_x.append(wx)
                wins_y.append(wy)
    x += bw + rng.uniform(0, 0.15)
ax.scatter(wins_x, wins_y, s=9, marker="s", color="#ffd98a", alpha=0.6, zorder=1.2, linewidths=0)

# 路灯：一束光照在火柴人所在的区域
ax.add_patch(Polygon([(1.0, 6.45), (-0.9, G), (5.9, G)], closed=True, fc="#ffe7a3",
                     alpha=0.06, ec="none", zorder=1.5))
ax.add_patch(Rectangle((0.96, G), 0.08, 5.3, fc="#2b3254", ec="none", zorder=2))
ax.add_patch(Rectangle((0.8, 6.45), 0.4, 0.08, fc="#2b3254", ec="none", zorder=2))
for r, a in ((0.5, 0.06), (0.3, 0.12), (0.14, 0.9)):
    ax.add_patch(Circle((1.0, 6.4), r, fc="#ffe7a3", alpha=a, ec="none", zorder=2.1))

# 地面
ax.add_patch(Rectangle((0, 0), W, G, fc="#141a33", ec="none", zorder=2))
ax.plot([0, W], [G, G], color="#4a5580", lw=3, zorder=2.5)
for yy in (0.8, 0.42):
    ax.plot([0, W], [yy, yy], color="#232a4d", lw=1.5, zorder=2.5)
ax.add_patch(Ellipse((4.2, G - 0.02), 8, 0.6, fc="#ffe7a3", alpha=0.07, ec="none", zorder=2.2))

# 篮架：立柱、横臂、篮板、篮筐
ax.add_patch(Rectangle((13.9, G), 0.14, 5.3, fc="#2b3254", ec="none", zorder=2.4))
ax.plot([13.24, 13.9], [6.2, 6.2], color="#2b3254", lw=7, zorder=2.4)
ax.add_patch(Rectangle((13.12, 5.5), 0.12, 1.7, fc="#d8def5", ec="white", lw=1, zorder=3))
ax.plot([RIM_R, 13.12], [RIM_Y, RIM_Y], color=RIM_C, lw=5, zorder=9)
ax.plot([RIM_L, RIM_R], [RIM_Y, RIM_Y], color=RIM_C, lw=6, solid_capstyle="round", zorder=9)

dyn = []   # 每一帧都会变的图形放这里，下一帧开始前统一删掉


def add(artist):
    dyn.append(artist)
    return artist


def limb(points, color, z, lw=7):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    (ln,) = ax.plot(xs, ys, color=color, lw=lw, solid_capstyle="round",
                    solid_joinstyle="round", zorder=z)
    add(ln)


def draw_ball(x, y, theta, sq):
    """画篮球：椭圆（砸地时压扁）+ 随旋转角度转动的纹路"""
    sx, sy = 1 + 0.9 * sq, 1 - sq
    add(ax.add_patch(Ellipse((x, y), 2 * R * sx, 2 * R * sy, fc=BALL_C, ec=SEAM_C, lw=2, zorder=7)))

    def tr(px, py):
        return x + (px - x) * sx, y + (py - y) * sy

    u = (math.cos(theta), math.sin(theta))
    v = (-math.sin(theta), math.cos(theta))
    for dirv in (u, v):                                   # 十字纹路
        p1 = tr(x - 0.98 * R * dirv[0], y - 0.98 * R * dirv[1])
        p2 = tr(x + 0.98 * R * dirv[0], y + 0.98 * R * dirv[1])
        add(ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=SEAM_C, lw=1.6, zorder=7.1)[0])
    for sgn in (1, -1):                                   # 两侧的弧线
        cx, cy = x + sgn * 1.45 * R * u[0], y + sgn * 1.45 * R * u[1]
        phi0 = math.atan2(y - cy, x - cx)
        phis = np.linspace(phi0 - 1.2, phi0 + 1.2, 30)
        px, py = cx + 0.95 * R * np.cos(phis), cy + 0.95 * R * np.sin(phis)
        keep = np.hypot(px - x, py - y) < 0.985 * R
        qx, qy = tr(px[keep], py[keep])
        add(ax.plot(qx, qy, color=SEAM_C, lw=1.6, zorder=7.1)[0])


def net_segments(bulge):
    n = 5
    top_x = np.linspace(RIM_L + 0.03, RIM_R - 0.03, n)
    half = 0.20 + 0.10 * bulge
    bot_x = np.linspace(RIM_X - half, RIM_X + half, n)
    bot_y = RIM_Y - 0.75 - 0.12 * bulge

    def P(i, r):
        return (top_x[i] + (bot_x[i] - top_x[i]) * r, RIM_Y + (bot_y - RIM_Y) * r)

    segs = []
    for i in range(n - 1):
        for r0, r1 in ((0, 0.5), (0.5, 1.0)):
            segs.append((P(i, r0), P(i + 1, r1)))
            segs.append((P(i + 1, r0), P(i, r1)))
    return segs


# ====================================================================
# 7. 每一帧的绘制：给定时间 t，算出所有东西的位置并画出来
# ====================================================================
def draw_scene(t):
    # ---------- 身体参数 ----------
    if t < T_DRIB1:
        hand_y, ball_h, tau = dribble(t)
        push = (HH - hand_y) / (HH - HL)          # 手压得越低，身体越下沉
        hip_h = 1.25 - 0.14 * push
        if t < T_DRIB0:
            hip_h += 0.01 * math.sin(2 * math.pi * t / 1.6)   # 待机时轻微起伏
        lean = 6 + 4 * push
    else:
        hip_h = track(t, HIP_KEYS)
        lean = track(t, LEAN_KEYS)
    J = jump_offset(t)
    hip, neck, shoulder, head, d = torso(hip_h, J, lean)

    # ---------- 篮球位置 ----------
    if t < T_DRIB1:
        bpos, theta = (BX + 0.55, G + ball_h), 0.4
    elif t < T_REL:
        rel = ball_rel(t)
        bpos, theta = (BX + rel[0], G + J + rel[1]), 0.4
    elif t < T_RIM:
        bpos, theta = flight_pos(t), 0.4 + SPIN * (t - T_REL)
    else:
        bpos = sim_pos(t)
        theta = 0.4 + SPIN * FLIGHT + 3.0 * (1 - math.exp(-2 * (t - T_RIM)))
    sq = squash_at(t)
    by = bpos[1] - R * sq

    # ---------- 双手的目标位置 ----------
    if t < T_DRIB1:
        near = (BX + 0.55, G + hand_y)
        far = (shoulder[0] + 0.62, shoulder[1] - 0.22)
    else:
        rel = ball_rel(min(t, T_REL))
        held = (BX + rel[0], G + J + rel[1])              # 球还在手里时的位置
        w = smooth((t - T_DRIB1) / 0.5)
        phi = math.radians(lerp(90, 236, w))               # 近侧手从球上方绕到球下方
        near = (held[0] + 0.2 * math.cos(phi), held[1] + 0.2 * math.sin(phi))
        far = lerp2((shoulder[0] + 0.62, shoulder[1] - 0.22), (held[0] + 0.15, held[1] - 0.12), w)
        if t > T_REL:                                      # 出手之后：随手 -> 庆祝
            w2 = smooth((t - T_REL) / 0.15)
            n_len, n_ang = track(t, NEAR_POL)
            f_len, f_ang = track(t, FAR_POL)
            if t > T_CELE:                                 # 庆祝时双臂挥舞
                ph = 2 * math.pi * 2.5 * (t - T_CELE)
                env = 1 - smooth((t - 9.0) / 0.8)
                n_len += 0.05 * math.sin(ph) * env
                n_ang += 10 * math.sin(ph) * env
                f_len += 0.05 * math.sin(ph + 1) * env
                f_ang -= 10 * math.sin(ph + 1) * env
            near = lerp2(near, polar(shoulder, n_len, n_ang), w2)
            far = lerp2(far, polar(shoulder, f_len, f_ang), w2)

    # ---------- 腿：滞空时收腿 ----------
    if T_TO <= t <= T_LAND:
        tuck = 0.18 * math.sin(math.pi * (t - T_TO) / AIR)
    elif t >= T_RIM:
        tuck = 0.9 * J
    else:
        tuck = 0.0

    # ---------- 阴影 ----------
    add(ax.add_patch(Ellipse((BX, G - 0.12), 1.1 * (1 - 0.25 * min(J, 1)), 0.18,
                             fc="black", alpha=0.35, ec="none", zorder=3)))
    height = max(0.0, by - (G + R))
    if bpos[0] < 13.4:
        add(ax.add_patch(Ellipse((bpos[0], G - 0.12), max(0.25, 0.55 * (1 - 0.12 * min(height, 3))), 0.12,
                                 fc="black", alpha=0.3 * (1 - 0.08 * min(height, 3)), ec="none", zorder=3)))

    # ---------- 画火柴人 ----------
    foot_n = (BX + 0.22, G + J + 0.6 * tuck)
    foot_f = (BX - 0.20, G + J + tuck)
    for foot, col, z in ((foot_f, FAR_C, 4), (foot_n, BODY_C, 5)):
        knee, ankle = two_link(hip, foot, 0.65, 0.65, +1)    # 膝盖朝前弯
        limb([hip, knee, ankle], col, z)
        limb([ankle, (ankle[0] + 0.2, ankle[1])], col, z)
    limb([hip, neck], BODY_C, 6)
    limb([neck, (head[0] - 0.2 * d[0], head[1] - 0.2 * d[1])], BODY_C, 6)
    add(ax.add_patch(Circle(head, 0.2, fc=BODY_C, ec=BODY_C, zorder=6)))
    add(ax.add_patch(Circle((head[0] + 0.09, head[1] + 0.04), 0.03, fc="#0b1026", ec="none", zorder=6.1)))
    for hand, col, z in ((far, FAR_C, 5), (near, BODY_C, 8)):
        elbow, end = two_link(shoulder, hand, 0.5, 0.5, -1)  # 手肘朝下弯
        limb([shoulder, elbow, end], col, z)

    # ---------- 球的飞行轨迹（虚线）----------
    if t > T_REL:
        ts = np.linspace(T_REL, min(t, T_RIM), 40)
        pts = [flight_pos(s) for s in ts]
        alpha = 0.35 * (1 - smooth((t - T_RIM - 0.3) / 1.5))
        if alpha > 0.01:
            add(ax.plot([p[0] for p in pts], [p[1] for p in pts], color="white", lw=2,
                        ls=(0, (3, 4)), alpha=alpha, zorder=6.5)[0])

    draw_ball(bpos[0], by, theta, sq)

    # ---------- 篮网（画在球前面，看起来球从网里穿过）----------
    lc = LineCollection(net_segments(net_bulge(t - T_RIM)), colors="white", linewidths=1.6,
                        alpha=0.85, zorder=10)
    add(ax.add_collection(lc))

    # ---------- 进球特效 + SCORE 文字 ----------
    ts_ = t - T_RIM
    if 0 <= ts_ < 0.6:
        u = ts_ / 0.6
        add(ax.add_patch(Circle((RIM_X, RIM_Y - 0.2), 0.3 + 1.3 * u, fill=False, ec="#ffd54a",
                                lw=3, alpha=0.9 * (1 - u), zorder=11)))
    if ts_ > 0.05:
        u = clamp((ts_ - 0.05) / 0.35)
        scale = (0.4 + 0.6 * u) + 0.25 * math.sin(math.pi * u)
        txt = ax.text(8.0, 7.7, "SCORE!", ha="center", va="center", fontsize=54 * scale,
                      fontweight="bold", color="#ffd54a", zorder=20,
                      path_effects=[pe.withStroke(linewidth=6, foreground="#1a1033")])
        add(txt)


def update(i):
    for a in dyn:
        a.remove()
    dyn.clear()
    draw_scene(i / FPS)


# ====================================================================
# 8. 导出
# ====================================================================
if __name__ == "__main__":
    anim = FuncAnimation(fig, update, frames=N_FRAMES, interval=1000 / FPS, blit=False)
    if shutil.which("ffmpeg"):
        anim.save("stickman_basketball.mp4", writer=FFMpegWriter(fps=FPS, bitrate=5000))
        print("done: stickman_basketball.mp4")
    else:
        anim.save("stickman_basketball.gif", writer=PillowWriter(fps=FPS), dpi=50)
        print("done: stickman_basketball.gif（没找到 ffmpeg，已改为导出 gif）")