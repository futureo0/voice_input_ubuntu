# Ubuntu Alt Voice Input

按一下并松开 `Alt` 开始录音，再按一下并松开 `Alt` 结束录音。结束后把豆包流式语音识别的最终文本写入 CopyQ 最新历史，并默认通过 CopyQ 触发粘贴。

## 依赖

Ubuntu 24：

```bash
sudo apt update
sudo apt install -y python3-venv alsa-utils copyq libnotify-bin wireplumber
```

自动粘贴依赖 CopyQ。`wl-clipboard` 或 `xclip` 只作为“复制到系统剪贴板”的后备，不负责自动粘贴：

```bash
sudo apt install -y wl-clipboard xclip
```

Python 依赖：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
VOLC_ASR_APP_KEY=你的 App Key
VOLC_ASR_ACCESS_KEY=你的 Access Key
```

默认使用官方文档中的流式输入模式 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream` 和 `volc.bigasr.sauc.duration`。这个模式会在发送最后一包音频后返回最终结果，更适合“按 Alt 开始、再按 Alt 结束并复制”的用法。

`VOLC_ASR_RESOURCE_ID` 必须是当前 App 已授权的资源 ID；如果控制台给你的 Resource ID 或接入地址不同，直接改 `.env`。

常见流式大模型资源 ID：

```bash
VOLC_ASR_RESOURCE_ID=volc.bigasr.sauc.duration
VOLC_ASR_RESOURCE_ID=volc.bigasr.sauc.concurrent
VOLC_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
VOLC_ASR_RESOURCE_ID=volc.seedasr.sauc.concurrent
```

## 输入权限

脚本用 `evdev` 读取键盘和 M585/M590 水平滚轮事件，在 Wayland/X11 都能工作，但当前用户必须能读 `/dev/input/event*`。

常见做法是把用户加入 `input` 组，然后重新登录：

```bash
sudo usermod -aG input "$USER"
```

如果要让 M585/M590 滚轮左拨不再传给系统，脚本会尝试通过 `/dev/uinput` 创建一个虚拟输入设备：独占物理设备、转发其他事件、只丢弃左拨水平滚轮事件。若 `/dev/uinput` 不可用或权限不足，会自动退回到被动监听，此时左拨仍会触发录音切换，但原本的水平滚动也会继续生效。

给 `/dev/uinput` 授权的一种常见做法：

```bash
sudo modprobe uinput
printf 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"\n' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules
sudo chgrp input /dev/uinput
sudo chmod 660 /dev/uinput
```

然后确认当前用户在 `input` 组里；如果刚加入过 `input` 组，需要重新登录。

## 运行

先确认 CopyQ 已启动，然后运行：

```bash
. .venv/bin/activate
python voice_input.py
```

终端中出现 `Ready. Tap Alt or M585 wheel-left to start/stop recording.` 后：

1. 单独按下并松开 `Alt`：开始录音。
2. 再次单独按下并松开 `Alt`：结束录音。
3. 或者把 M585/M590 鼠标滚轮向左拨动，也会执行同样的开始/结束切换。
4. 最终识别文本会写入 CopyQ 最新历史，并通过 CopyQ 粘贴到当前焦点。

`Alt+Tab`、`Alt+F4` 这类组合键不会触发录音切换；脚本只响应“单独轻按 Alt”。

## 开机自启

本项目提供 `run_voice_input.sh` 和 `voice-input-assistant.desktop`。已安装到当前用户的自启动目录后，登录桌面时会自动运行：

```bash
~/.config/autostart/voice-input-assistant.desktop
```

自启动日志写入：

```bash
~/.local/state/voice-input/voice_input.log
```

如果只是想临时关掉当前正在运行的助手：

```bash
pkill -f '/home/futureoo/Desktop/voice_input/voice_input.py'
```

如果想永久关闭开机自启，可以打开 Ubuntu 的“启动应用程序”并禁用 `Voice Input Assistant`，也可以删除自启动文件：

```bash
rm ~/.config/autostart/voice-input-assistant.desktop
```

## 默认行为

- 开始录音前用 `wpctl` 检查默认麦克风；如果静音或音量接近 0，会自动解除静音并设置为 30%，同时弹出系统通知。
- 默认播放开始/结束录音音效，可用 `VOICE_INPUT_SOUNDS` 关闭，用 `VOICE_INPUT_SOUND_VOLUME=0..200` 调整相对音量。
- 录音期间用 Ubuntu 系统通知显示 `录音中 00:00` 计时，停止录音后更新为识别/完成状态。
- 录音每满 60 秒会以 `VOICE_INPUT_SOUND_VOLUME` 的 50% 播放一次提示音，避免误触后无感长时间录制。
- 如果连续 20 秒没有检测到语音活动，会自动停止本次录音并继续完成识别/复制/粘贴流程。
- 识别完成后优先执行 `copyq add -`，把识别文本写入 CopyQ 历史第 0 条。
- 然后执行 `copyq select 0`，把 CopyQ 最新历史项明确推到当前系统剪贴板。
- 自动粘贴时再次执行 `copyq select 0`，再执行 `copyq paste`。
- `copyq paste` 粘贴的是当前系统剪贴板内容，不是直接从 CopyQ 历史读取；所以 `select 0` 是防止粘出旧 `Ctrl+C` 内容的关键步骤。
- 这个方案不模拟 `Ctrl+V`，由 CopyQ 自己向当前焦点发起粘贴；如果有可接收粘贴的焦点，通常会直接注入；如果没有焦点，通常不会影响其他按钮或输入位置。

相关开关可以放进 `.env`：
> 在 .env.example 中可查看详细变量介绍

```bash
VOICE_INPUT_MIC_AUTO_FIX=1
VOICE_INPUT_MIC_TARGET_VOLUME=30
VOICE_INPUT_NOTIFICATIONS=1
VOICE_INPUT_SOUNDS=1
VOICE_INPUT_SOUND_VOLUME=100
VOICE_INPUT_RECORDING_REMINDER_SECONDS=60
VOICE_INPUT_SILENCE_TIMEOUT_SECONDS=20
VOICE_INPUT_VAD_AGGRESSIVENESS=2
VOICE_INPUT_VAD_MIN_RMS=160
VOICE_INPUT_COPYQ_HISTORY=1
VOICE_INPUT_AUTO_PASTE=1
VOICE_INPUT_PASTE_DELAY_MS=0
VOICE_INPUT_M585_WHEEL=1
VOICE_INPUT_M585_DEVICE_NAMES=M585,M590
VOICE_INPUT_M585_LEFT_SIGN=-1
VOICE_INPUT_M585_INTERCEPT=1
VOICE_INPUT_M585_GESTURE_MS=1200
```

如果只想复制和记录历史，不自动粘贴：

```bash
VOICE_INPUT_AUTO_PASTE=0
```

## 重要限制

当前脚本是“被动监听 Alt”。Alt 事件仍然会传给当前应用，所以某些应用可能会把单按 Alt 当成激活菜单栏，导致输入框失焦。

M585/M590 左拨滚轮默认会尝试“截胡”。如果启动日志里显示 `intercepting left wheel`，左拨事件已经被拦截，其他鼠标事件会通过虚拟输入设备转发；如果显示 `passive; intercept unavailable`，通常是 `/dev/uinput` 不存在或权限不足，此时可继续用左拨触发，但原始水平滚动不会被拦截。

不同内核/接收器上，水平滚轮左右方向可能相反。如果你发现右拨触发而左拨不触发，把 `.env` 里的 `VOICE_INPUT_M585_LEFT_SIGN` 改成 `1`。

M585/M590 的一次左拨可能会连续产生多个水平滚轮事件。脚本默认用 `VOICE_INPUT_M585_GESTURE_MS=1200` 把这一串事件视为同一次手势；如果仍出现一次左拨连续开始/停止/再开始，把这个值调大到 `1600` 或 `2000`。

M585/M590 进入休眠、接收器重连、蓝牙/USB 重新枚举时，`/dev/input/event*` 编号可能变化。脚本会每 5 秒重扫输入设备并自动重新监听；日志中若看到 `Input device removed` 后又出现 `Listening M585 wheel`，说明已经完成重连。

CopyQ 粘贴依赖当前桌面确实存在可接收粘贴的焦点。如果焦点已经丢失，它不会像逐字键盘输入那样乱发字符，也不会触发额外按键，但不会把文本写进目标输入框。

如果看到粘贴出来的是之前手动 `Ctrl+C` 的内容，说明粘贴前当前系统剪贴板没有被更新为 CopyQ 第 0 条。确认 CopyQ 正在运行，并保持默认流程里的 `copyq select 0` 再 `copyq paste`。

## 常见问题

如果提示不能读取键盘设备，检查用户是否在 `input` 组，或临时用 `sudo` 运行。

如果录音失败，先测试麦克风：

```bash
arecord -f S16_LE -r 16000 -c 1 -d 3 /tmp/test.wav
aplay /tmp/test.wav
```

如果启动后看到 `requested resource not granted`，说明 App Key/Access Key 所属应用没有开通当前 `VOLC_ASR_RESOURCE_ID`。这不是本地录音问题，需要在火山引擎控制台开通对应资源，或把 `.env` 改成已经授权的资源 ID。

如果录音结束后显示 `No recognized text`，先打开调试输出：

```bash
VOICE_INPUT_DEBUG=1 python voice_input.py
```

结束一次录音后看 `DEBUG session`：

- `sent_bytes=0`：`arecord` 没有读到音频数据，优先检查麦克风设备。
- `received_payloads=0`：服务端没有返回识别 payload，优先检查协议参数、资源类型和网络。
- 有 `DEBUG ASR payload` 但没有文本：把 payload 结构贴出来，通常是响应字段和当前解析逻辑不一致。
