# ⚡ SVN Fast Search & Spotlight (SVN 极速搜索与全局悬浮工具套件)

[![Python Version](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PyQt5-green.svg)](https://riverbankcomputing.com/software/pyqt/)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)](https://www.microsoft.com/)
[![License](https://img.shields.io/badge/License-MIT-orange.svg)](#许可证)

专为汽车零部件研发与大型工程团队设计的局域网 SVN 极速文件索引、检索与直达套件。包含类似 **Everything** 的全功能管理面板与类似 **macOS Spotlight** 的全局快捷悬浮搜索条。

---

## 🌟 核心特性

- ⚡ **Everything 级内存秒搜**：全量索引常驻内存，40 万+ 工程文件实现 10~30ms 毫秒级极速响应，支持拼音首字母、多关键词、全词匹配与语法糖。
- 🎯 **Spotlight 全局悬浮窗 (`Alt + Q`)**：
  - 任何工作界面下均可一键呼出，光标自动聚焦；
  - 键盘上下键平滑穿透浏览，按回车或双击直接调起 TortoiseSVN 定位；
  - 失焦或按 `Esc` 自动极简收起，常驻系统托盘，内存占用极低。
- 🔗 **TortoiseSVN 深度无缝联动**：
  - 双击结果行直接调起 TortoiseSVN **版本库浏览器 (Repository Browser)** 并定位至父级目录；
  - 遵循 RFC 3986 标准自动转义 URL 空格及特殊字符，杜绝单文件定位报错；
  - 同步将标准化 SVN URL 复制至系统剪贴板。
- 🔄 **版本感知与增量补丁**：
  - 启动时后台毫秒级探测远程 Revision 版本号；
  - 仅拉取增量差异（`svn diff`），告别全量拉取耗时。
- 🛠️ **工业设计与工程格式支持**：
  - 针对工程场景收录：EDA 电路设计 (`.SchDoc`/`.Sch`/`.PcbDoc`/`.pcb`/`.PrjPcb`)、CAD 图纸 (`.dwg`/`.dxf`)、思维导图 (`.xmind`)、项目计划 (`.mpp`)、带宏表格 (`.xlsm`/`.csv`) 等。
- 📊 **专业报告导出**：
  - 一键将检索结果导出为排版优美的 Word (`.docx`)、带筛选器的 Excel (`.xlsx`)、CSV 或 TXT 报告。
- 📥 **独立 MPP 项目计划拉取工具 (`pull_mpp.py`)**：
  - 提供独立 CLI 与 Python API，无需 Checkout 整个仓库即可秒级导出并调用 Project 打开特定 `.mpp` 文件。

---

## 📁 目录结构

```text
Tool_test/
├── svn_search.py            # 主程序：Everything 风格全功能索引与搜索面板
├── svn_spotlight.py         # 极简版：macOS Spotlight 全局悬浮胶囊搜索
├── pull_mpp.py              # 工具：.mpp 计划文件独立快速提取与管理
├── app_icon.ico             # 应用程序图标
├── requirements.txt         # 核心依赖清单
├── build.bat                # 一键 PyInstaller 打包脚本
├── SVN_Search.spec          # SVN 极速搜索工具打包规范 (相对路径版)
├── SVN_Spotlight.spec       # Spotlight 悬浮版打包规范 (相对路径版)
├── .gitignore               # Git 忽略配置 (含敏感缓存隔离)
├── docs/                    # 详细使用文档
│   ├── search_guide.md      # 全功能版详细使用手册
│   └── spotlight_guide.md   # Spotlight 悬浮版详细使用手册
└── bundled_cache/           # 预置/打包索引缓存目录 (大文件自动被 Git 忽略)
    └── .gitkeep
```

---

## 🚀 快速上手

### 1. 环境准备
确保系统安装了 **Python 3.8+**，建议使用虚拟环境：

```bash
git clone <your-repo-url>
cd Tool_test

# 安装依赖
pip install -r requirements.txt
```

> **系统建议**：建议安装 [TortoiseSVN](https://tortoisesvn.net/) 并在安装时勾选 `Command Line Client Tools` 以支持增量同步与浏览器联动。

### 2. 运行程序

#### 运行全功能搜索面板：
```bash
python svn_search.py
```

#### 运行全局悬浮 Spotlight 搜索：
```bash
python svn_spotlight.py
```
- 启动后程序进入托盘待命，在任意软件下按下 **`Alt + Q`** 即可呼出。

#### 运行 MPP 独立提取工具：
```bash
python pull_mpp.py "项目计划.mpp" --open
```

---

## 📦 打包为免安装 EXE

本项目已配置纯净相对路径的 PyInstaller `.spec` 规范，根目录提供了一键交互式打包批处理：

```bash
# 双击运行或在终端执行
.\build.bat
```
打包完成后将在 `dist/` 目录下生成：
- `SVN极速搜索工具.exe`
- `SVN极速搜索_Spotlight版.exe`

---

## ⚙️ 仓库配置与自定义

默认预设仓库可以在 `svn_search.py` 与 `svn_spotlight.py` 中的 `DEFAULT_PRESET_REPOS` 与 `DEPARTMENTS` 进行配置：

```python
DEFAULT_PRESET_REPOS = [
    ("💻 电子中心", "https://your-svn-server/svn/YourRepo1"),
    ("📐 结构设计", "https://your-svn-server/svn/YourRepo2"),
    ("🛠 工艺设计", "https://your-svn-server/svn/YourRepo3"),
]
```

> 💡 **安全提示**：
> 本工具自动在 `~/.svn_search_cache` 生成本地索引。
> 为防止企业局域网文件树及敏感项目信息泄露，`.gitignore` 默认排除了 `bundled_cache/*.txt` 等超大缓存文件（60MB+），确保代码仓库轻量安全。

---

## 📄 许可证

本项目遵循 [MIT License](LICENSE) 开源协议。
