# ClubDeck CLI：本机实时转写

本仓库只保留本机 Clubhouse 登录、选择并加入活动房间、房间保活和实时语音转写。转写内容只显示在当前终端界面中，不写入转写档案，也不生成用户侧写或声音指纹。

## 环境要求

- Windows 和 Python 3。
- Clubhouse 登录凭证：可以从本机 Clubdeck 登录资料读取，也可以在程序的“设置 → 敏感参数”中填写 Token 和用户 ID。
- Agora 音频桥：Electron 12（ABI 87）和 `agora-electron-sdk` 原生模块。
- `faster-whisper` 转写依赖。Whisper 模型通常会在首次使用时下载。

## 安装与启动

在仓库目录打开 PowerShell，安装 Python 依赖并创建仓库虚拟环境：

```powershell
.\setup_venv.cmd
```

或者直接运行 PowerShell 安装脚本：

```powershell
.\scripts\setup_venv.ps1
```

启动界面：

```powershell
.\start_clubdeck.cmd
```

也可以直接运行 `python chcli_tui.py`。若启动检查提示音频桥缺失，可运行 `scripts/setup_standalone_bridge.ps1` 配置 Electron 和 Agora SDK。启动前检查依赖与音频桥：

```powershell
python runtime_bootstrap.py --check
```

## 使用

1. 主菜单选择“活动房间”，再选择房间加入。
2. 默认会自动开始实时转写；在房间界面按 `T` 可暂停或恢复。
3. 离开房间或退出后，界面中的转写缓存会清空。

加入房间期间，程序会把单段房间音频暂存在系统临时目录，转写完成后删除 WAV 文件。运行中断时，操作系统的临时目录可能留下未清理的片段；仓库不会把这些片段作为转写历史保存。

## 转写设置

在“设置 → 转写参数”中调整选项。配置保存在本机忽略文件 `clubdeck.config.json` 的 `tui` 段：

| 设置 | 说明 |
| --- | --- |
| `auto_transcribe` | 加入房间后是否自动转写 |
| `whisper_model` | `auto`、`tiny`、`base`、`small` 或 `medium` |
| `transcribe_seconds` | 每段识别时长；可填 `auto` 或秒数 |
| `whisper_vad` | 是否启用 Whisper VAD |
| `transcribe_max_lines` | 界面最多保留的转写行数 |
| `agora_playback_device` | 房间音频使用的本机播放设备；默认系统设备 |

音频桥路径也可在 `tui` 配置段中设置 `bridge_runtime` 和 `agora_sdk_dir`。登录凭证单独保存在本机忽略文件 `clubdeck.secrets.json`，不要提交到 Git。

## 旧版本地数据

旧版本可能留下被 Git 忽略的 `transcripts/` 目录，以及 `data/` 下的 `transcripts.jsonl`、`profiles.json`、`user_profiles.json`、`user_memory.json`、`voiceprints.json` 和 `voiceprint_stats.jsonl`。当前程序不读取、更新或自动删除这些旧文件；如不再需要，请在本机手动清理。`data/` 也可能包含账户和最近房间等本机运行数据。
