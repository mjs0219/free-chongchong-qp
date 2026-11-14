# 虫虫钢琴曲谱客户端（chongchong-free-python）

一个本地桌面工具：粘贴虫虫钢琴（gangqinpu.com）曲谱网址，自动下载 `.ccmz` 文件，
再解析并导出为 MIDI、MusicXML、谱面 PNG / PDF。

也可以继续用命令行直接处理本地已有的 `.ccmz` 文件。

## 功能

- **桌面客户端**：粘贴曲谱网址 → 自动提取 `.ccmz` 直链 → 下载 → 解析 → 导出，全程有进度和日志
- **网址解析**：从曲谱页源码的 `ai-score` iframe 中提取 `url=` 后面的 `.ccmz` 地址，并自动读取曲谱名作为默认文件名
- **多格式导出**：可同时勾选 `midi`、`musicxml`、`png`、`pdf`
- **直接读取本地 `.ccmz`**（v1 / v2）
- **MIDI 导出**：保留通道、音色、全部速度变化、踏板等控制器；修复弯音、同音高重叠音符、PPQ 识别等问题
- **MusicXML 导出**：支持和弦、附点、多声部 / 双谱表、按 tick 定位、歌词
- **谱面渲染**：调用 LilyPond 生成 PNG，并用 reportlab 合成 A4 PDF
- **界面**：Tkinter 极光渐变背景 + 卡片式布局，下载在后台线程执行，不卡界面

## 系统要求

- Python 3.8 及以上（推荐 3.10+）
- Windows / macOS / Linux，需带 Tk 支持的 Python 官方安装包
- 仅 `png` / `pdf` 导出需要额外安装 LilyPond

## 安装

```bash
# 建议先创建虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

依赖用途：

| 依赖 | 用途 | 是否必需 |
| --- | --- | --- |
| `requests` | 请求曲谱页、下载 `.ccmz` | 客户端必需 |
| `midiutil` | 生成标准 MIDI | 必需 |
| `pillow` | 谱面 PNG 合成、客户端背景动画 | `png`/`pdf` 需要；客户端未安装时仅无背景动画 |
| `reportlab` | 把 PNG 页面合成 A4 PDF | `pdf` 需要 |

> `tkinter` 是 Python 标准库，随官方安装包自带，无需 pip 安装。

### LilyPond（仅 `png` / `pdf` 需要）

`midi` 和 `musicxml` 不需要 LilyPond。要导出谱面图片 / PDF，请先安装：

- Windows：<https://lilypond.org/download.html>
- Linux：`sudo apt-get install lilypond`
- macOS：`brew install lilypond`

安装后检查环境：

```bash
python main.py --check-env
```

程序会自动查找 `PATH`、`%LOCALAPPDATA%\lilyponddist`、`C:\Program Files\LilyPond`
等常见位置，并兼容 `bin` / `usr\bin` 两种目录结构。

> Windows 上如果 `python` 指向应用商店别名，把下面的 `python` 换成 `py -3.10`。

## 使用客户端

```bash
python client.py
```

Windows 也可以直接双击 `run.bat`。

使用步骤：

1. 粘贴虫虫钢琴曲谱网址，例如
   `https://www.gangqinpu.com/cchtml/1054183.htm`
2. 选择输出目录（默认项目下的 `output`，不存在时自动创建）
3. 文件名留空时自动使用曲谱名；也可以手动指定
4. 勾选导出格式，点击「开始下载并导出」
5. 完成后可用「打开输出目录」查看结果

界面说明：

- **曲谱网址**：支持任意 `gangqinpu.com` 曲谱详情页；按回车可直接开始
- **运行日志**：实时显示下载地址、文件大小、ccmz 版本、各格式输出路径与错误原因
- **进度条 / 状态**：下载和导出全程在后台线程执行，可继续操作窗口
- 下载的 `<曲谱名>.ccmz` 会和导出文件一起保存在输出目录，便于二次导出

## 命令行用法

处理本地 `.ccmz`：

```bash
# 默认只导出 MIDI
python main.py song.ccmz

# 同时导出 MIDI 和 MusicXML
python main.py song.ccmz -t midi,musicxml

# 导出谱面 PNG（需要 LilyPond）
python main.py song.ccmz -t png

# 导出 PDF（需要 LilyPond）
python main.py song.ccmz -t pdf

# 全部导出
python main.py song.ccmz -t all

# 指定输出目录 / 文件名
python main.py song.ccmz -t all -o output --name 我的谱子

# 指定 PPQ、只输出错误信息
python main.py song.ccmz --ppq 480 -q

# 只检查 LilyPond 环境
python main.py --check-env
```

### main.py 参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `source` | 本地 `.ccmz` 文件路径 | — |
| `-t, --type` | `midi` / `musicxml` / `png` / `pdf` / `all`，可逗号组合 | `midi` |
| `-o, --output` | 输出目录 | 与 `.ccmz` 同目录 |
| `--name` | 输出文件名（不含扩展名） | 与 `.ccmz` 同名 |
| `--ppq` | 覆盖 MIDI 的 PPQ（默认从文件识别，回退 480） | 自动 |
| `--check-env` | 检查 LilyPond 环境后退出 | — |
| `-q, --quiet` | 只输出错误信息 | — |

### 导出类型

| 类型 | 输出 | 是否需要 LilyPond |
| --- | --- | --- |
| `midi` | `<名字>.mid` | 否 |
| `musicxml` | `<名字>.musicxml` | 否 |
| `png` | `<名字>-page1.png`、`<名字>-page2.png`… | 是 |
| `pdf` | `<名字>.pdf`（A4，多页） | 是 |
| `all` | 以上全部 | PNG/PDF 部分需要 |

### 只想要 MIDI

也可以直接使用 `ccmz.py`：

```bash
python ccmz.py song.ccmz
python ccmz.py song.ccmz -o out.mid
python ccmz.py song.ccmz --score --json   # 顺便导出 score.json / midi.json
python ccmz.py song.ccmz --ppq 480 -q
```

| 参数 | 说明 |
| --- | --- |
| `-o, --output` | 输出 `.mid` 路径或目录 |
| `--score` | 同时导出谱面（v1 为 `data.xml`，v2 为 `score.json`） |
| `--json` | 导出解包后的 `midi.json`（仅 v2） |
| `--ppq` | 覆盖 PPQ / division |
| `-q, --quiet` | 只输出错误信息 |

## 输出说明

- `ccmz` v1：只有 `data.xml` + `data.mid`，只能导出原始 MIDI。
- `ccmz` v2：包含 `score.json` + `midi.json`，可导出 MIDI、MusicXML；
  `png` / `pdf` 需要 LilyPond。
- 中文歌词 / 标题需要系统里有中文字体，否则渲染时可能缺字。
- 谱面 PNG 按 300 DPI 渲染，PDF 为 A4 多页。

## 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| 提示「没有在该页面中找到 ccmz 地址」 | 不是曲谱详情页，或该曲谱不提供下载；请确认网址形如 `/cchtml/数字.htm` |
| 请求超时 / 连接失败 | 检查网络或代理；稍后重试 |
| 勾选 PNG/PDF 但提示未找到 LilyPond | 安装 LilyPond，或运行 `python main.py --check-env` 排查 |
| v1 文件无法导出谱面 | v1 不含 `score.json`，只能导出 MIDI，属正常限制 |
| 界面没有极光背景动画 | 未安装 `pillow`，`pip install pillow` 即可，不影响导出 |
| 导出的谱面中文缺字 | 系统缺少中文字体，安装中文字体后重试 |

## 项目结构

```
.
├── client.py               # 桌面客户端：网址 -> ccmz -> 导出
├── gangqinpu.py            # 虫虫钢琴页面解析 + ccmz 下载
├── main.py                 # 可复用导出逻辑 export_ccmz + 本地 .ccmz 命令行入口
├── ccmz.py                 # .ccmz 解析 + MIDI 重建（也可单独运行）
├── ccxml_to_musicxml.py    # CCXML -> MusicXML
├── score_generator.py      # LilyPond 渲染 PNG / PDF
├── requirements.txt
├── run.bat                 # Windows 启动客户端
└── README.md
```

## 说明

- CCXML -> MusicXML 的转换思路参考自
  [bszapp/ccmz-to-midi](https://github.com/bszapp/ccmz-to-midi) 的
  `ccxml-to-musicxml` 分支，本仓库重写了小节时间轴、和弦、附点和多声部处理。
- 原项目：[yfishyon/chongchong-free-python](https://github.com/yfishyon/chongchong-free-python)
- 本项目基于原项目的 `test` 分支修改。
- 本工具仅供个人学习与研究使用，请勿用于商业用途或大批量抓取；曲谱版权归原作者及虫虫钢琴所有。

## 许可证

继承原项目许可证（GPL-3.0），详见 [LICENSE](LICENSE)。
