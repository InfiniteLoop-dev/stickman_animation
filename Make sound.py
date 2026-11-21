"""
给火柴人投篮视频配音效（全部用代码合成，不需要任何音频素材）
运行： python make_sound.py
会生成 stickman_basketball.wav；电脑装了 ffmpeg 的话，还会合成 stickman_basketball_sound.mp4

原理：声音也是"时间的函数"。每个音效就是一串数字（每秒 44100 个采样点），
把它们按正确的时间点加到一条长长的音轨上，再保存成 wav 文件。
"""
import os
import shutil
import subprocess
import wave

import numpy as np

# 直接复用动画代码里的时间点，保证声音和画面严格对得上
import stickman_basketball as sb

SR = 44100
rng = np.random.default_rng(42)


# ====================================================================
# 1. 小工具
# ====================================================================
def lowpass(x, fc):
    """最简单的低通滤波：把高频"削掉"，声音变闷"""
    a = 1 - np.exp(-2 * np.pi * fc / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += a * (v - acc)
        y[i] = acc
    return y


def normalize(s):
    return s / (np.max(np.abs(s)) + 1e-9)


# ====================================================================
# 2. 篮球落地声：低频"咚" + 球壳共鸣 + 撞击瞬间的"啪"
# ====================================================================
def bounce_sound(pitch=1.0):
    n = int(0.5 * SR)
    t = np.arange(n) / SR
    # 低频闷响：音高从约 190Hz 快速滑到 110Hz，像球被压扁又弹开
    phase = 2 * np.pi * (110 * pitch * t + 80 * pitch * 0.02 * (1 - np.exp(-t / 0.02)))
    thump = np.sin(phase) * np.exp(-t / 0.07)
    # 空心球壳的共鸣
    shell = (0.35 * np.sin(2 * np.pi * 260 * pitch * t) * np.exp(-t / 0.10)
             + 0.15 * np.sin(2 * np.pi * 410 * pitch * t) * np.exp(-t / 0.06))
    # 球皮拍在地面上的瞬间"啪"声：很短的一小段噪音
    m = int(0.05 * SR)
    click = np.zeros(n)
    click[:m] = lowpass(rng.normal(size=m), 3500) * np.exp(-t[:m] / 0.006)
    s = (thump + shell + 1.0 * click) * (1 - np.exp(-t / 0.0007))   # 极短的起音，避免"咔"一声
    return normalize(s)


# ====================================================================
# 3. 入网声："唰"：高频沙沙 + 中频摩擦，由亮变暗
# ====================================================================
def swish_sound():
    n = int(0.8 * SR)
    t = np.arange(n) / SR
    noise = rng.normal(size=n)
    low = lowpass(noise, 3500)
    bright = lowpass(noise - low, 9000)            # 高频部分
    mid = low - lowpass(noise, 700)                # 中频部分（尼龙绳摩擦）
    env_b = (1 - np.exp(-t / 0.008)) * np.exp(-t / 0.09)
    env_m = (1 - np.exp(-t / 0.02)) * np.exp(-t / 0.20)
    ripple = 1 + 0.25 * np.sin(2 * np.pi * 22 * t)  # 网的轻微抖动
    return normalize(0.9 * normalize(bright) * env_b + 1.1 * normalize(mid) * env_m * ripple)


# ====================================================================
# 4. 胜利音效：电子游戏风格（方波 + 三角波），类似"过关"的上行琶音 + 和弦
# ====================================================================
def pulse(f, dur, duty=0.5, vib=0.0, sustain=0.55):
    n = int(dur * SR)
    t = np.arange(n) / SR
    ph = f * t + (f * vib / (2 * np.pi * 6)) * (1 - np.cos(2 * np.pi * 6 * t))   # vib 是颤音深度
    wave_ = np.where((ph % 1.0) < duty, 1.0, -1.0)
    env = np.minimum(1, t / 0.003) * (sustain + (1 - sustain) * np.exp(-t / 0.08))
    env *= np.clip((dur - t) / 0.02, 0, 1)
    return wave_ * env


def triangle(f, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    w = 2 * np.abs(2 * ((f * t) % 1.0) - 1) - 1
    env = np.minimum(1, t / 0.003) * np.clip((dur - t) / 0.03, 0, 1)
    return w * env


def victory_jingle():
    step = 0.085
    notes = [523.25, 659.25, 783.99, 1046.50, 783.99, 1046.50, 1318.51]   # C5 E5 G5 C6 G5 C6 E6
    total = 7 * step + 1.3
    out = np.zeros(int(total * SR))

    def put(sig, at):
        i = int(at * SR)
        out[i:i + len(sig)] += sig[:len(out) - i]

    for k, f in enumerate(notes):                      # 快速上行琶音
        put(0.55 * pulse(f, step * 0.95, duty=0.25, sustain=0.8), k * step)
    put(0.5 * triangle(130.81, 0.35), 0)               # 低音 C3
    t_chord = 7 * step
    for f in (1046.50, 1318.51, 1567.98):              # 最后的长和弦 C6 E6 G6，带一点颤音
        put(0.32 * pulse(f, 0.9, duty=0.5, vib=0.006), t_chord)
    put(0.5 * triangle(130.81, 0.9), t_chord)
    for k, f in enumerate((2093.0, 2637.0, 3136.0)):   # 最后的闪光小音符
        put(0.16 * pulse(f, 0.06, duty=0.125, sustain=0.5), t_chord + 0.38 + k * 0.06)
    return out


# ====================================================================
# 5. 把所有声音按时间点放到音轨上
# ====================================================================
track = np.zeros(int((sb.DURATION + 0.5) * SR))


def place(sound, at, gain):
    i = int(at * SR)
    if i < 0 or i >= len(track):
        return
    seg = sound[:len(track) - i]
    track[i:i + len(seg)] += gain * seg


for c in sb.DRIB_CONTACTS:                              # 运球：每次球砸地
    place(bounce_sound(pitch=rng.uniform(0.96, 1.04)), c, 0.75 * rng.uniform(0.95, 1.05))

place(bounce_sound(pitch=0.7), sb.T_LAND, 0.3)          # 落地的"噔"

place(swish_sound(), sb.T_RIM - 0.05, 0.55)             # 球入网

jingle = victory_jingle()
place(jingle, sb.T_RIM + 0.1, 0.30)                     # 胜利音效（和 SCORE! 同时出现）

for gain, c in zip((0.9, 0.5, 0.28, 0.15), sb.BOUNCES): # 球入网后在地上弹几下，越弹越轻
    place(bounce_sound(pitch=rng.uniform(0.98, 1.06)), c, gain)

for k in (1, 2, 3):                                     # 庆祝小跳的落地声
    place(bounce_sound(pitch=0.75), sb.T_RIM + 0.5 * k, 0.1)

peak = np.max(np.abs(track))
if peak > 0.92:                                         # 防止爆音
    track *= 0.92 / peak

# ====================================================================
# 6. 保存 wav，并合成进视频
# ====================================================================
with wave.open("stickman_basketball.wav", "wb") as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(SR)
    f.writeframes((track * 32767).astype(np.int16).tobytes())
print("done: stickman_basketball.wav")

if shutil.which("ffmpeg") and os.path.exists("stickman_basketball.mp4"):
    tmp = "tmp_with_sound.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", "stickman_basketball.mp4",
                    "-i", "stickman_basketball.wav", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", tmp], check=True)
    os.replace(tmp, "stickman_basketball_sound.mp4")      # ffmpeg 成功结束后才改名
    print("done: stickman_basketball_sound.mp4")
else:
    print("没找到 ffmpeg 或 mp4，只生成了 wav；可以用剪辑软件把它和视频合在一起")