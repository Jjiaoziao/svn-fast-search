# -*- coding: utf-8 -*-
"""
SVN 极速索引与搜索工具 (Everything 架构增强版)
核心特性：
1. Everything 级体验：全量拉取构建索引 + 内存常驻零延迟秒搜 + svn diff 增量更新。
2. 轻量版本感知：利用 svn info 毫秒级探测远程最新 Revision，感知仓库是否有新提交。
3. 现代卡片 UI：Fluent/Slate 科技蓝主题，多列数据表格展示，支持排序、双击复制、右键菜单。
4. 多格式专业导出：Word (.docx)、Excel (.xlsx)、文本 (.txt)、表格 (.csv)，导出后支持一键打开。
5. 实时百分比进度条：全量拉取历史预估百分比 + 速度与耗时看板 + 本地秒搜毫秒响应。
"""

import sys
import os
import time
import json
import hashlib
import locale
import subprocess
import csv
import urllib.parse
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QRadioButton,
    QButtonGroup, QProgressBar, QCheckBox, QGroupBox, QComboBox,
    QMenu, QAction, QAbstractItemView, QFrame, QSplitter
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QSettings, QPoint, QTimer
from PyQt5.QtGui import QFont, QColor, QIcon, QCursor, QClipboard

# 常见无意义的编译输出、临时缓存和底层库目录，开启过滤时跳过
DEFAULT_IGNORE_PATTERNS = [
    ".si4project", "/debug/", "/release/", "/drivers/",
    "/cmsis/", ".git", ".vscode", "/obj/", "/list/",
    ".dep", ".pbi", ".o", ".idx", ".sisc", ".map",
    "thumbs.db", ".ds_store", "__pycache__"
]

BATCH_SIZE = 100         # 批量 UI 刷新步长
BATCH_INTERVAL = 0.12    # 批量 UI 刷新间隔（秒）
MAX_URL_HISTORY = 10     # 历史记录最大保留数

DEFAULT_SVN_TARGET = "https://dell740-svn/svn/ElectronicCentreRepository/Electronic trunk"
DEFAULT_SVN_PREFIX = "https://dell740-svn/svn/ElectronicCentreRepository/Electronic trunk/"

DEFAULT_PRESET_REPOS = [
    ("💻 电子中心 (Electronic trunk)", "https://dell740-svn/svn/ElectronicCentreRepository/Electronic trunk"),
    ("📐 结构设计 (PhysicalDesign)", "https://dell740-svn/svn/PhysicalDesignRepositories"),
    ("🛠 工艺设计 (ProcessDesign)", "https://dell740-svn/svn/ProcessDesignRepositories"),
]


def build_full_url(target: str, rel_path: str) -> str:
    """构建完整的文件访问 URL / 路径，默认补全 SVN 仓库前缀"""
    if not rel_path:
        return ""
    if rel_path.startswith(("http://", "https://", "svn://")):
        return rel_path

    t = (target or "").strip()
    if t.startswith(("http://", "https://", "svn://")):
        base = t.rstrip("/") + "/"
    elif t and os.path.exists(t):
        return os.path.normpath(os.path.join(t, rel_path)).replace("\\", "/")
    else:
        base = DEFAULT_SVN_PREFIX

    return base + rel_path.lstrip("/\\")


def build_dir_url(target: str, rel_dir: str) -> str:
    """构建完整目录 URL / 路径，默认补全 SVN 仓库前缀"""
    if not rel_dir or rel_dir == "/":
        t = (target or "").strip()
        if t.startswith(("http://", "https://", "svn://")):
            return t.rstrip("/") + "/"
        return DEFAULT_SVN_PREFIX
    if rel_dir.startswith(("http://", "https://", "svn://")):
        return rel_dir.rstrip("/") + "/"

    t = (target or "").strip()
    if t.startswith(("http://", "https://", "svn://")):
        base = t.rstrip("/") + "/"
    elif t and os.path.exists(t):
        return os.path.normpath(os.path.join(t, rel_dir)).replace("\\", "/") + "/"
    else:
        base = DEFAULT_SVN_PREFIX

    return base + rel_dir.strip("/\\") + "/"


def canonicalize_svn_url(url: str) -> str:
    """将 SVN URL 转为符合 RFC3986 规范的标准 URL (避免二次转义，安全处理空格与中文)"""
    if not url:
        return ""
    if not url.startswith(("http://", "https://", "svn://")):
        return url
    import urllib.parse
    # 先 unquote 避免已经是 %20 的字符串被重复编码为 %2520
    url_unquoted = urllib.parse.unquote(url)
    parsed = urllib.parse.urlsplit(url_unquoted)
    quoted_path = urllib.parse.quote(parsed.path, safe="/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, quoted_path, parsed.query, parsed.fragment))


def find_svn_exe() -> str:
    """查找本地 svn 命令行工具绝对路径，优先环境变量，其次 TortoiseSVN 安装目录"""
    import shutil
    p = shutil.which("svn.exe") or shutil.which("svn")
    if p and os.path.exists(p):
        return p
    candidates = [
        r"C:\Program Files\TortoiseSVN\bin\svn.exe",
        r"C:\Program Files (x86)\TortoiseSVN\bin\svn.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "svn"


def find_tortoiseproc() -> str:
    """查找本地 TortoiseSVN 的主程序 TortoiseProc.exe 绝对路径"""
    import shutil
    # 1. 环境变量 PATH
    proc = shutil.which("TortoiseProc.exe") or shutil.which("TortoiseProc")
    if proc and os.path.exists(proc):
        return proc

    # 2. 常用绝对安装路径
    candidates = [
        r"C:\Program Files\TortoiseSVN\bin\TortoiseProc.exe",
        r"C:\Program Files (x86)\TortoiseSVN\bin\TortoiseProc.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    # 3. Windows 注册表查找
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subkey in (r"SOFTWARE\TortoiseSVN", r"SOFTWARE\WOW6432Node\TortoiseSVN"):
                try:
                    with winreg.OpenKey(root, subkey) as k:
                        val, _ = winreg.QueryValueEx(k, "Directory")
                        if val:
                            exe_path = os.path.join(val, "bin", "TortoiseProc.exe")
                            if os.path.exists(exe_path):
                                return exe_path
                except Exception:
                    pass
    except Exception:
        pass

    return None


def detect_system_svn_username() -> str:
    """尝试从系统 %APPDATA%/Subversion/auth/svn.simple 探测已缓存的默认 SVN 用户名"""
    try:
        auth_dir = os.path.expandvars(r"%APPDATA%\Subversion\auth\svn.simple")
        if not os.path.exists(auth_dir):
            return ""
        for fname in os.listdir(auth_dir):
            p = os.path.join(auth_dir, fname)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.read().splitlines()
                    for i, line in enumerate(lines):
                        if line.strip() == "username" and i + 2 < len(lines):
                            u = lines[i + 2].strip()
                            if u:
                                return u
            except Exception:
                pass
    except Exception:
        pass
    return ""


def verify_and_cache_svn_credentials(target_url: str, username: str, password: str = "") -> tuple:
    """
    通过 svn 命令行工具对目标仓库执行一次带凭据的静默验证。
    如果验证成功，Subversion 会自动将凭据安全加密持久化至 %APPDATA%/Subversion/auth/svn.simple/
    这样无论是本工具的后台更新，还是 TortoiseSVN 的 Repo-Browser，都会直接免密通过，跳过确认弹窗！
    返回: (bool_success, message)
    """
    if not username:
        return False, "用户名不能为空"

    clean_target = canonicalize_svn_url(target_url) if target_url.startswith(("http://", "https://", "svn://")) else target_url
    svn_exe = find_svn_exe()

    cmd = [svn_exe, "info", clean_target, "--username", username, "--non-interactive"]
    if password:
        cmd.extend(["--password", password])

    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        p = subprocess.Popen(cmd, **kwargs)
        out_bytes, err_bytes = p.communicate(timeout=10)
        if p.returncode == 0:
            return True, "凭据验证通过！已写入系统 SVN 凭据库，后续双击将完全免密直达。"
        else:
            err = decode_line(err_bytes)
            if "Authentication failed" in err or "E215004" in err or "E170001" in err:
                return False, "认证失败：SVN 账号或密码不正确，请重新核对输入"
            return False, f"SVN 验证返回: {err.strip()[:150]}"
    except subprocess.TimeoutExpired:
        return False, "连接 SVN 服务器超时（超过 10 秒），请检查网络连接"
    except Exception as e:
        return False, f"执行验证异常: {str(e)}"


def open_in_repo_browser(url: str) -> bool:
    """调用 TortoiseSVN 直接调出 Repository Browser 界面并定位到指定目录"""
    if not url:
        return False
    proc = find_tortoiseproc()
    if not proc:
        return False

    clean_url = canonicalize_svn_url(url)
    cmd = [proc, "/command:repobrowser", f"/path:{clean_url}"]
    try:
        subprocess.Popen(cmd)
        return True
    except Exception:
        return False




# =====================================================================
# 1. 编码与文本解码辅助
# =====================================================================

def get_candidate_encodings():
    """获取 Windows SVN 管道输出的备选解码列表，优先本地系统代码页 (GBK/CP936)"""
    encs = []
    try:
        pe = locale.getpreferredencoding(False)
        if pe:
            encs.append(pe)
    except Exception:
        pass
    for e in ("utf-8", "gbk", "gb18030", "cp936"):
        if e.lower() not in [x.lower() for x in encs]:
            encs.append(e)
    return encs


CANDIDATE_ENCODINGS = get_candidate_encodings()


def decode_line(raw_bytes):
    """鲁棒解码字节数据"""
    for enc in CANDIDATE_ENCODINGS:
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode(CANDIDATE_ENCODINGS[-1], errors="replace")


# =====================================================================
# 2. 文件类型辅助类
# =====================================================================

class FileTypeHelper:
    EXT_MAP = {
        # 文档类
        ".doc": "📄 文档", ".docx": "📄 文档", ".pdf": "📄 PDF",
        ".wps": "📄 文档", ".txt": "📄 文本", ".md": "📄 Markdown",
        ".rtf": "📄 文档",
        # 表格类与项目管理
        ".xls": "📊 表格", ".xlsx": "📊 表格", ".xlsm": "📊 表格", ".csv": "📊 表格",
        ".et": "📊 表格",
        ".mpp": "📊 项目计划",
        # 演示文稿
        ".ppt": "📽 幻灯片", ".pptx": "📽 幻灯片", ".dps": "📽 幻灯片",
        # 图表与工程设计 / 3D模型 / CAD
        ".drawio": "📐 架构图", ".vsdx": "📐 流程图", ".vsd": "📐 流程图",
        ".dwg": "📐 CAD图纸", ".dxf": "📐 CAD图纸",
        ".step": "📐 3D模型", ".stp": "📐 3D模型", ".iges": "📐 3D模型",
        ".igs": "📐 3D模型", ".prt": "📐 3D模型", ".asm": "📐 3D模型",
        ".sldprt": "📐 3D模型", ".sldasm": "📐 3D模型",
        # 原理图与PCB设计 (EDA)
        ".schdoc": "🔌 原理图", ".sch": "🔌 原理图", ".schlib": "🔌 原理图库",
        ".pcb": "⚡ PCB版图", ".pcbdoc": "⚡ PCB版图", ".pcblib": "⚡ PCB封装库",
        ".prjpcb": "🛠 PCB工程", ".prjpcbstructure": "🛠 PCB工程",
        # 思维导图
        ".xmind": "🧠 思维导图",
        # 代码类
        ".c": "💻 代码", ".h": "💻 代码", ".cpp": "💻 代码",
        ".hpp": "💻 代码", ".py": "💻 代码", ".java": "💻 代码",
        ".js": "💻 代码", ".ts": "💻 代码", ".html": "💻 网页",
        ".css": "💻 样式", ".sh": "💻 脚本", ".bat": "💻 脚本",
        ".go": "💻 代码", ".rs": "💻 代码", ".sql": "💻 数据库",
        # 图片类
        ".png": "🖼 图片", ".jpg": "🖼 图片", ".jpeg": "🖼 图片",
        ".bmp": "🖼 图片", ".gif": "🖼 图片", ".svg": "🖼 矢量图",
        ".ico": "🖼 图标",
        # 压缩包
        ".zip": "📦 压缩包", ".rar": "📦 压缩包", ".7z": "📦 压缩包",
        ".tar": "📦 压缩包", ".gz": "📦 压缩包",
        # 可执行 / 固件
        ".exe": "⚙ 程序", ".dll": "⚙ 库文件", ".bin": "⚙ 固件",
        ".hex": "⚙ 固件", ".lib": "⚙ 库文件", ".so": "⚙ 库文件",
    }

    @classmethod
    def get_info(cls, path_str: str):
        """解析返回: (类型描述, 纯文件名, 所在目录, 扩展名)"""
        path_str = path_str.replace("\\", "/")
        is_dir = path_str.endswith("/")
        cleaned = path_str.rstrip("/")
        parts = cleaned.split("/")
        name = parts[-1] if parts else ""
        parent = "/".join(parts[:-1]) if len(parts) > 1 else "/"

        if is_dir:
            return "📁 目录", name + "/", parent, "-"

        _, ext = os.path.splitext(name)
        ext_lower = ext.lower()
        badge = cls.EXT_MAP.get(ext_lower, "📎 文件")
        return badge, name, parent, ext_lower or "-"


import re

DEFAULT_DOC_EXTENSIONS = [
    ".docx", ".doc", ".pdf", ".xlsx", ".xls", ".xlsm", ".pptx", ".ppt",
    ".drawio", ".vsdx", ".vsd", ".txt", ".md", ".csv", ".mpp",
    ".dwg", ".schdoc", ".sch", ".schlib", ".pcb", ".pcbdoc", ".xmind", ".pcblib", ".prjpcb"
]

def normalize_extensions(ext_input: str) -> list:
    """
    将用户输入的后缀文本（支持 'word, pdf, ppt, eda, pcb, mpp' 或 '.docx, *.PCB'）
    清洗规范为标准的 ['.docx', '.doc', '.pdf', ...] 列表。
    完全不区分大小写，无论带点不带点（如 pcb, .pcb, .PCB, PCB）均自动归一化并解析别名。
    """
    ALIAS_MAP = {
        "word": [".docx", ".doc"],
        "excel": [".xlsx", ".xls", ".xlsm", ".csv"],
        "excle": [".xlsx", ".xls", ".xlsm", ".csv"],
        "xlsm": [".xlsm"],
        "ppt": [".pptx", ".ppt"],
        "powerpoint": [".pptx", ".ppt"],
        "pdf": [".pdf"],
        "drawio": [".drawio"],
        "visio": [".vsdx", ".vsd"],
        "mpp": [".mpp"],
        "project": [".mpp"],
        "xmind": [".xmind"],
        "导图": [".xmind"],
        "思维导图": [".xmind"],
        "脑图": [".xmind"],
        "cad": [".dwg", ".dxf", ".vsd", ".vsdx", ".step", ".stp"],
        "dwg": [".dwg", ".dxf"],
        "图纸": [".dwg", ".dxf", ".step", ".stp"],
        "eda": [".schdoc", ".sch", ".schlib", ".pcb", ".pcbdoc", ".pcblib", ".prjpcb"],
        "sch": [".schdoc", ".sch", ".schlib"],
        "schdoc": [".schdoc"],
        "schlib": [".schlib"],
        "原理图": [".schdoc", ".sch", ".schlib"],
        "pcb": [".pcb", ".pcbdoc", ".pcblib", ".prjpcb"],
        "pcbdoc": [".pcbdoc"],
        "pcblib": [".pcblib"],
        "prjpcb": [".prjpcb"],
        "3d": [".step", ".stp", ".iges", ".igs", ".prt", ".asm", ".sldprt", ".sldasm"],
        "model": [".step", ".stp", ".iges", ".igs", ".prt", ".asm", ".sldprt", ".sldasm"],
        "markdown": [".md"],
        "text": [".txt"],
        "txt": [".txt"],
        "code": [".c", ".h", ".cpp", ".py", ".java"],
    }
    raw_tokens = re.split(r"[,;|\s]+", ext_input.strip().lower())
    result = []
    for tok in raw_tokens:
        tok = tok.strip()
        if not tok:
            continue
        clean_key = tok.lstrip(".*")
        if clean_key in ALIAS_MAP:
            for e in ALIAS_MAP[clean_key]:
                if e not in result:
                    result.append(e)
            continue
        if tok in ALIAS_MAP:
            for e in ALIAS_MAP[tok]:
                if e not in result:
                    result.append(e)
            continue
        tok = tok.lstrip("*")
        if "." in tok:
            tok = "." + tok.split(".")[-1]
        elif not tok.startswith("."):
            tok = "." + tok
        if tok not in result and len(tok) > 1:
            result.append(tok)
    return result


def parse_search_ext_filter(ext_text: str) -> list:
    """
    解析检索过滤区【后缀过滤】文本（支持下拉选择或手动输入）
    例如: "全部后缀" -> [] (不限)
          ".mpp (项目计划)" -> [".mpp"]
          ".xlsx / .xls (表格)" -> [".xlsx", ".xls"]
          "mpp" -> [".mpp"]
          "dwg, dxf" -> [".dwg", ".dxf"]
    """
    if not ext_text:
        return []
    s = ext_text.strip()
    if not s or "全部" in s or s.lower() in ("all", "*", "*.*"):
        return []
    # 去除可能包含的中文或英文圆括号说明，如 (项目计划)
    s = re.sub(r'\(.*?\)|（.*?）', ' ', s)
    s = s.replace("/", " ")
    return normalize_extensions(s)


# =====================================================================
# 3. 独立仓库索引与缓存管理器
# =====================================================================

class SvnIndexManager:
    """管理每个 SVN 仓库的独立本地缓存与版本元数据"""

    def __init__(self):
        self.base_dir = os.path.join(os.path.expanduser("~"), ".svn_search_cache")
        os.makedirs(self.base_dir, exist_ok=True)
        self._import_bundled_cache_if_needed()

    def _get_bundled_dir(self):
        """获取随软件一同打包内置的预设缓存目录"""
        # 1. PyInstaller 打包后的运行临时目录
        if hasattr(sys, "_MEIPASS"):
            p1 = os.path.join(sys._MEIPASS, "bundled_cache")
            if os.path.exists(p1):
                return p1
        # 2. 与可执行程序或脚本同级目录下的 bundled_cache 或 cache 目录
        exe_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)
        for sub in ("bundled_cache", "cache"):
            p2 = os.path.join(exe_dir, sub)
            if os.path.exists(p2):
                return p2
        # 3. 源码同级目录
        p3 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bundled_cache")
        if os.path.exists(p3):
            return p3
        return None

    def _import_bundled_cache_if_needed(self):
        """若同事电脑上首次打开尚无本地索引，或内置捆绑包索引条目更新/更大，自动从内置捆绑包复制导入"""
        bundled_dir = self._get_bundled_dir()
        if not bundled_dir or not os.path.exists(bundled_dir):
            return

        try:
            import shutil
            for fname in os.listdir(bundled_dir):
                src_file = os.path.join(bundled_dir, fname)
                dst_file = os.path.join(self.base_dir, fname)
                need_copy = False
                if not os.path.exists(dst_file) or os.path.getsize(dst_file) == 0:
                    need_copy = True
                elif fname.startswith("cache_") and fname.endswith(".txt"):
                    # 如果内置包明显比本地已有缓存更大（说明内置了最新扩充的全量索引），自动覆盖升级
                    if os.path.getsize(src_file) > os.path.getsize(dst_file):
                        need_copy = True
                elif fname.startswith("meta_") and fname.endswith(".json"):
                    # 同步更新配套的 meta 元数据
                    cache_name = fname.replace("meta_", "cache_").replace(".json", ".txt")
                    src_c = os.path.join(bundled_dir, cache_name)
                    dst_c = os.path.join(self.base_dir, cache_name)
                    if os.path.exists(src_c) and (not os.path.exists(dst_c) or os.path.getsize(src_c) > os.path.getsize(dst_c)):
                        need_copy = True

                if need_copy:
                    shutil.copy2(src_file, dst_file)
        except Exception:
            pass

    def _get_hash(self, target_path: str) -> str:
        clean = target_path.strip().rstrip("/\\").lower()
        return hashlib.md5(clean.encode("utf-8")).hexdigest()[:12]

    def get_paths(self, target_path: str):
        h = self._get_hash(target_path)
        cache_file = os.path.join(self.base_dir, f"cache_{h}.txt")
        meta_file = os.path.join(self.base_dir, f"meta_{h}.json")
        return cache_file, meta_file

    def get_meta(self, target_path: str):
        _, meta_file = self.get_paths(target_path)
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def save_meta(self, target_path: str, rev, count: int, exts: list = None):
        _, meta_file = self.get_paths(target_path)
        data = {
            "target": target_path,
            "rev": str(rev) if rev is not None else "",
            "count": count,
            "extensions": exts or [],
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_cache_lines(self, target_path: str):
        cache_file, _ = self.get_paths(target_path)
        if not os.path.exists(cache_file):
            return []
        try:
            with open(cache_file, "r", encoding="utf-8", errors="ignore") as f:
                return [line.strip() for line in f if line.strip()]
        except Exception:
            return []

    def save_cache_lines(self, target_path: str, lines: list, rev=None, exts: list = None):
        cache_file, _ = self.get_paths(target_path)
        try:
            with open(cache_file, "w", encoding="utf-8", buffering=1024 * 1024) as f:
                for line in lines:
                    f.write(line + "\n")
            self.save_meta(target_path, rev, len(lines), exts=exts)
            return True
        except Exception:
            return False

    def clear_cache(self, target_path: str):
        cache_file, meta_file = self.get_paths(target_path)
        for p in (cache_file, meta_file):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


# =====================================================================
# 4. 后台工作线程
# =====================================================================

class AuthVerifyWorker(QThread):
    """异步执行 SVN 凭据验证与持久化写入"""
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, target_path: str, username: str, password: str = "", parent=None):
        super().__init__(parent)
        self.target_path = target_path
        self.username = username
        self.password = password

    def run(self):
        ok, msg = verify_and_cache_svn_credentials(self.target_path, self.username, self.password)
        self.finished_signal.emit(ok, msg)


class SvnInfoWorker(QThread):
    """轻量异步查询 SVN 当前最新 Revision 号"""
    finished_signal = pyqtSignal(bool, str, str)  # success, rev, message

    def __init__(self, target_path, username: str = "", password: str = ""):
        super().__init__()
        self.target_path = target_path
        self.username = username
        self.password = password

    def run(self):
        clean_target = canonicalize_svn_url(self.target_path) if self.target_path.startswith(("http://", "https://", "svn://")) else self.target_path
        svn_exe = find_svn_exe()
        cmd = [svn_exe, "info", "--non-interactive", "--show-item", "revision", clean_target]
        if self.username:
            cmd.extend(["--username", self.username])
        if self.password:
            cmd.extend(["--password", self.password])

        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1)
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        p = None
        try:
            p = subprocess.Popen(cmd, **kwargs)
            out_bytes, err_bytes = p.communicate(timeout=30)
            if p.returncode == 0:
                rev = decode_line(out_bytes).strip()
                self.finished_signal.emit(True, rev, "获取版本成功")
            else:
                err = decode_line(err_bytes).strip()
                self.finished_signal.emit(False, "", err or "获取版本失败")
        except subprocess.TimeoutExpired:
            if p:
                try:
                    p.kill()
                    p.communicate()
                except Exception:
                    pass
            self.finished_signal.emit(
                False, "",
                "连接远程 SVN 仓库超时（等待超过 30 秒）。\n\n"
                "可能原因及排查方案：\n"
                "1. 公司内网或 VPN 未连接或不稳定；\n"
                "2. 远程主机 dell740-svn 正在繁忙处理请求或主机名解析延迟；\n"
                "3. 当前暂无网络连接（您仍可使用已有的本地缓存进行秒搜）。"
            )
        except FileNotFoundError:
            self.finished_signal.emit(
                False, "",
                "未检测到系统的 SVN 命令行工具 (svn.exe)。\n\n"
                "提示：\n"
                "1. 本地秒搜（离线索引）功能完全不受影响，已内置全部 3.9 万余条数据；\n"
                "2. 若需同步远程最新变更，请安装 TortoiseSVN 并勾选【Command Line Tools】。"
            )
        except Exception as e:
            if p:
                try:
                    p.kill()
                except Exception:
                    pass
            self.finished_signal.emit(False, "", str(e))


class SvnFullScanWorker(QThread):
    """全量拉取线程：运行 svn list -R，按后缀白名单流式过滤写入缓存，收集常驻内存列表"""
    progress_signal = pyqtSignal(int, int, int, float, str)  # scanned_total, kept_count, estimated_total, speed, path
    finished_signal = pyqtSignal(bool, list, str, str)        # success, all_lines, rev, message

    def __init__(self, target_path, allowed_exts=None, filter_noise=True, estimated_total=0,
                 username: str = "", password: str = ""):
        super().__init__()
        self.target_path = target_path
        self.allowed_exts = [e.lower() for e in allowed_exts] if allowed_exts else []
        self.filter_noise = filter_noise
        self.estimated_total = estimated_total
        self.username = username
        self.password = password
        self.is_running = True
        self.process = None

    def stop(self):
        self.is_running = False
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

    def run(self):
        clean_target = canonicalize_svn_url(self.target_path) if self.target_path.startswith(("http://", "https://", "svn://")) else self.target_path
        svn_exe = find_svn_exe()

        # 1. 尝试先探测当前仓库的 HEAD Revision
        head_rev = ""
        try:
            info_cmd = [svn_exe, "info", "--show-item", "revision", clean_target]
            if self.username:
                info_cmd.extend(["--username", self.username])
            if self.password:
                info_cmd.extend(["--password", self.password])
            ikwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if sys.platform.startswith("win"):
                ikwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ip = subprocess.Popen(info_cmd, **ikwargs)
            iout, _ = ip.communicate(timeout=5)
            if ip.returncode == 0:
                head_rev = decode_line(iout).strip()
        except Exception:
            pass

        # 2. 执行 svn list -R
        cmd = [svn_exe, "list", "-R"]
        if clean_target:
            cmd.append(clean_target)
        if self.username:
            cmd.extend(["--username", self.username])
        if self.password:
            cmd.extend(["--password", self.password])

        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1)
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        all_lines = []
        start_time = time.time()

        try:
            self.process = subprocess.Popen(cmd, **kwargs)
            scanned_total = 0

            while self.is_running:
                raw_line = self.process.stdout.readline()
                if not raw_line:
                    break

                line = decode_line(raw_line).strip()
                if not line:
                    continue

                scanned_total += 1

                # 开启后缀白名单时跳过纯目录
                if self.allowed_exts and line.endswith("/"):
                    continue

                line_lower = line.lower().replace("\\", "/")

                # 编译与临时噪音过滤
                if self.filter_noise and any(pat in line_lower for pat in DEFAULT_IGNORE_PATTERNS):
                    continue

                # 后缀白名单匹配 (严格大小写不敏感)
                if self.allowed_exts:
                    allowed_tuple = tuple(ext.lower() for ext in self.allowed_exts)
                    if not line_lower.endswith(allowed_tuple):
                        continue

                all_lines.append(line)

                if scanned_total % 100 == 0:
                    elapsed = max(0.001, time.time() - start_time)
                    speed = scanned_total / elapsed
                    self.progress_signal.emit(scanned_total, len(all_lines), self.estimated_total, speed, line)

            self.process.stdout.close()
            err_bytes = self.process.stderr.read()
            err_out = decode_line(err_bytes) if err_bytes else ""
            self.process.wait()

            if not self.is_running:
                self.finished_signal.emit(False, [], "", "用户中断全量拉取。")
            elif self.process.returncode != 0:
                self.finished_signal.emit(False, [], "", f"SVN 执行失败:\n{err_out}")
            else:
                self.finished_signal.emit(
                    True, all_lines, head_rev,
                    f"全量拉取完成：已扫描 {scanned_total:,} 个对象，收录目标文件 {len(all_lines):,} 项。"
                )

        except FileNotFoundError:
            self.finished_signal.emit(
                False, [], "",
                "未检测到系统的 SVN 命令行工具 (svn.exe)，无法执行远程全量拉取。\n"
                "本地已内置索引数据，您可直接使用【⚡ 本地秒搜】。"
            )
        except Exception as e:
            self.finished_signal.emit(False, [], "", f"异常报错: {str(e)}")


class SvnDiffWorker(QThread):
    """增量同步线程：基于 svn diff --summarize -r old_rev:new_rev 提取变动"""
    progress_signal = pyqtSignal(int, str)             # count, status_text
    finished_signal = pyqtSignal(bool, list, list, str, str)  # success, added, deleted, new_rev, message

    def __init__(self, target_path, old_rev, new_rev, allowed_exts=None,
                 filter_noise=True, username: str = "", password: str = ""):
        super().__init__()
        self.target_path = target_path
        self.old_rev = old_rev
        self.new_rev = new_rev
        self.allowed_exts = [e.lower() for e in allowed_exts] if allowed_exts else []
        self.filter_noise = filter_noise
        self.username = username
        self.password = password
        self.is_running = True
        self.process = None

    def stop(self):
        self.is_running = False
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

    def run(self):
        clean_target = canonicalize_svn_url(self.target_path) if self.target_path.startswith(("http://", "https://", "svn://")) else self.target_path
        svn_exe = find_svn_exe()
        cmd = [svn_exe, "diff", "--summarize", "-r", f"{self.old_rev}:{self.new_rev}", clean_target]
        if self.username:
            cmd.extend(["--username", self.username])
        if self.password:
            cmd.extend(["--password", self.password])

        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1)
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        added = []
        deleted = []
        target_norm = self.target_path.rstrip("/\\")

        try:
            self.process = subprocess.Popen(cmd, **kwargs)
            count = 0

            while self.is_running:
                raw_line = self.process.stdout.readline()
                if not raw_line:
                    break

                line = decode_line(raw_line).strip()
                if not line or len(line) < 2:
                    continue

                action = line[0]  # 'A', 'D', 'M'
                raw_path = line[1:].strip()

                # 1. 彻底进行 URL 规范化反转义（将 %20、%E8 等中文字符及特殊符号还原为可读中文字符）
                unquoted_path = urllib.parse.unquote(raw_path).replace("\\", "/").strip()
                unquoted_target = urllib.parse.unquote(self.target_path).replace("\\", "/").rstrip("/")

                # 2. 剥离目标仓库 URL 前缀，提取为纯净的标准相对路径
                if unquoted_path.startswith(unquoted_target):
                    rel = unquoted_path[len(unquoted_target):].lstrip("/")
                else:
                    target_name = unquoted_target.split("/")[-1] if unquoted_target else ""
                    if target_name and ("/" + target_name + "/") in unquoted_path:
                        rel = unquoted_path.split("/" + target_name + "/", 1)[1].lstrip("/")
                    else:
                        rel = unquoted_path

                if not rel:
                    continue

                if action == 'D':
                    # 删除操作记录所有删除的相对路径（包含文件和目录），用于精准裁剪索引
                    deleted.append(rel)
                    count += 1
                elif action in ('A', 'M'):
                    rel_lower = rel.lower().replace("\\", "/")
                    # 噪点过滤（临时文件、编译中间文件等）
                    if self.filter_noise and any(pat in rel_lower for pat in DEFAULT_IGNORE_PATTERNS):
                        continue
                    # 后缀过滤检查 (涵盖该仓库全部历史检索/收录过的扩展名，严格大小写不敏感)
                    if self.allowed_exts:
                        allowed_tuple = tuple(ext.lower() for ext in self.allowed_exts)
                        if not rel_lower.endswith(allowed_tuple):
                            continue
                    added.append(rel)
                    count += 1

                if count % 20 == 0:
                    self.progress_signal.emit(count, f"已解析增量变更: {count} 条")

            self.process.stdout.close()
            err_bytes = self.process.stderr.read()
            err_out = decode_line(err_bytes) if err_bytes else ""
            self.process.wait()

            if not self.is_running:
                self.finished_signal.emit(False, [], [], "", "用户中断增量同步。")
            elif self.process.returncode != 0:
                self.finished_signal.emit(False, [], [], "", f"增量 Diff 失败:\n{err_out}")
            else:
                msg = f"增量更新解析成功: 新增 {len(added)} 项, 删除 {len(deleted)} 项。"
                self.finished_signal.emit(True, added, deleted, self.new_rev, msg)

        except FileNotFoundError:
            self.finished_signal.emit(
                False, [], [], "",
                "未检测到系统的 SVN 命令行工具 (svn.exe)，无法执行远程增量同步。\n"
                "本地已内置索引数据，您可直接使用【⚡ 本地秒搜】。"
            )
        except Exception as e:
            self.finished_signal.emit(False, [], [], "", f"增量执行异常: {str(e)}")


class SvnRemoteSearchWorker(QThread):
    """远程实时检索线程（临时不走缓存时的流式直查）"""
    items_matched = pyqtSignal(list)
    progress_signal = pyqtSignal(int, int, float, str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, target_path, keywords, match_mode, scope="filename",
                 word_boundary=False, case_sensitive=False, filter_noise=True,
                 type_filter="全部类型", ext_filters=None, estimated_total=0):
        super().__init__()
        self.target_path = target_path
        self.raw_keywords = [k.strip() for k in keywords if k.strip()]
        self.keywords = [k if case_sensitive else k.lower() for k in self.raw_keywords]
        self.match_mode = match_mode
        self.scope = scope
        self.word_boundary = word_boundary
        self.case_sensitive = case_sensitive
        self.filter_noise = filter_noise
        self.type_filter = type_filter
        self.ext_filters = ext_filters or []
        self.estimated_total = estimated_total
        self.is_running = True
        self.process = None

    def stop(self):
        self.is_running = False
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

    def run(self):
        cmd = ["svn", "list", "-R"]
        if self.target_path:
            cmd.append(self.target_path)

        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1)
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        start_time = time.time()
        scanned_count = 0
        pending_batch = []
        last_flush = time.time()

        try:
            self.process = subprocess.Popen(cmd, **kwargs)

            while self.is_running:
                raw_line = self.process.stdout.readline()
                if not raw_line:
                    break

                line = decode_line(raw_line).strip()
                if not line:
                    continue

                scanned_count += 1
                line_lower = line.lower()

                if self.filter_noise and any(pat in line_lower for pat in DEFAULT_IGNORE_PATTERNS):
                    continue

                badge, name, parent, ext = FileTypeHelper.get_info(line)

                # 后缀过滤
                if self.ext_filters:
                    if ext == "-":
                        continue
                    name_low = name.lower()
                    if not any(name_low.endswith(ef) for ef in self.ext_filters):
                        continue

                # 类型过滤
                if self.type_filter != "全部类型":
                    if self.type_filter == "文档":
                        if not ("文档" in badge or "项目计划" in badge or "PDF" in badge or "文本" in badge or "Markdown" in badge):
                            continue
                    elif self.type_filter == "原理图/PCB":
                        if not any(k in badge for k in ["原理图", "PCB"]):
                            continue
                    elif self.type_filter == "CAD工程图":
                        if not any(k in badge for k in ["CAD", "工程图", "3D模型"]):
                            continue
                    elif self.type_filter == "思维导图":
                        if not ("思维导图" in badge or "导图" in badge):
                            continue
                    elif self.type_filter not in badge:
                        continue

                # 匹配范围确定
                if self.scope == "filename":
                    target_str = name
                elif self.scope == "directory":
                    target_str = parent
                else:  # fullpath
                    target_str = line

                if not self.case_sensitive:
                    target_str = target_str.lower()

                # 关键词匹配 (支持独立词匹配)
                if not self.keywords:
                    is_hit = True
                elif self.word_boundary:
                    if self.match_mode == 'all':
                        is_hit = all(re.search(r'(?<![a-zA-Z0-9])' + re.escape(k) + r'(?![a-zA-Z0-9])', target_str) for k in self.keywords)
                    elif self.match_mode == 'exact':
                        exact_kw = " ".join(self.keywords)
                        is_hit = bool(re.search(r'(?<![a-zA-Z0-9])' + re.escape(exact_kw) + r'(?![a-zA-Z0-9])', target_str))
                    else:  # any
                        is_hit = any(re.search(r'(?<![a-zA-Z0-9])' + re.escape(k) + r'(?![a-zA-Z0-9])', target_str) for k in self.keywords)
                else:
                    if self.match_mode == 'all':
                        is_hit = all(k in target_str for k in self.keywords)
                    elif self.match_mode == 'exact':
                        exact_kw = " ".join(self.keywords)
                        is_hit = exact_kw in target_str
                    else:  # any
                        is_hit = any(k in target_str for k in self.keywords)

                if is_hit:
                    pending_batch.append(line)

                now = time.time()
                if scanned_count % 100 == 0 or (now - last_flush) >= BATCH_INTERVAL:
                    elapsed = max(0.001, now - start_time)
                    speed = scanned_count / elapsed
                    self.progress_signal.emit(scanned_count, self.estimated_total, speed, line)
                    if pending_batch:
                        self.items_matched.emit(pending_batch)
                        pending_batch = []
                    last_flush = now

            if pending_batch:
                self.items_matched.emit(pending_batch)

            self.process.stdout.close()
            err_bytes = self.process.stderr.read()
            err_out = decode_line(err_bytes) if err_bytes else ""
            self.process.wait()

            if not self.is_running:
                self.finished_signal.emit(False, "用户手动中断检索。")
            elif self.process.returncode != 0:
                self.finished_signal.emit(False, f"SVN 执行失败:\n{err_out}")
            else:
                self.finished_signal.emit(True, f"远程检索完毕，遍历 {scanned_count} 个对象。")

        except FileNotFoundError:
            self.finished_signal.emit(
                False,
                "未检测到系统的 SVN 命令行工具 (svn.exe)，无法执行远程实时检索。\n"
                "本地已内置索引数据，请直接使用【⚡ 本地秒搜】。"
            )
        except Exception as e:
            self.finished_signal.emit(False, f"远程检索异常: {str(e)}")


class SvnExportWorker(QThread):
    """单独拉取/导出文件线程：使用 svn export 将远程指定文件快速拉取至本地"""
    progress_signal = pyqtSignal(str)                  # 状态文本
    finished_signal = pyqtSignal(bool, str, str, bool) # success, local_path, error_msg, open_after

    def __init__(self, file_url: str, save_path: str, open_after: bool = False,
                 username: str = "", password: str = ""):
        super().__init__()
        self.file_url = file_url
        self.save_path = save_path
        self.open_after = open_after
        self.username = username
        self.password = password
        self.is_running = True
        self.process = None

    def stop(self):
        self.is_running = False
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

    def run(self):
        clean_url = canonicalize_svn_url(self.file_url) if self.file_url.startswith(("http://", "https://", "svn://")) else self.file_url
        svn_exe = find_svn_exe()

        os.makedirs(os.path.dirname(os.path.abspath(self.save_path)), exist_ok=True)
        cmd = [svn_exe, "export", clean_url, self.save_path, "--force", "--non-interactive"]
        if self.username:
            cmd.extend(["--username", self.username])
        if self.password:
            cmd.extend(["--password", self.password])

        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self.progress_signal.emit(f"正在从 SVN 拉取文件: {os.path.basename(self.save_path)}...")
        try:
            self.process = subprocess.Popen(cmd, **kwargs)
            out_bytes, err_bytes = self.process.communicate()

            if not self.is_running:
                self.finished_signal.emit(False, self.save_path, "用户取消了拉取操作。", self.open_after)
                return

            if self.process.returncode != 0:
                err_out = decode_line(err_bytes)
                self.finished_signal.emit(False, self.save_path, f"SVN 导出失败:\n{err_out}", self.open_after)
            else:
                if self.open_after and os.path.exists(self.save_path):
                    try:
                        os.startfile(self.save_path)
                    except Exception:
                        pass
                self.finished_signal.emit(True, self.save_path, "", self.open_after)
        except Exception as e:
            self.finished_signal.emit(False, self.save_path, f"拉取异常: {str(e)}", self.open_after)


class SvnBatchExportWorker(QThread):
    """批量拉取指定后缀所有文件到本地文件夹线程"""
    progress_signal = pyqtSignal(int, int, str)        # current_idx, total_count, current_filename
    finished_signal = pyqtSignal(bool, int, str, str)  # success, success_count, dest_dir, error_msg

    def __init__(self, target_repo: str, rel_file_list: list, dest_dir: str,
                 username: str = "", password: str = ""):
        super().__init__()
        self.target_repo = target_repo
        self.rel_file_list = rel_file_list
        self.dest_dir = dest_dir
        self.username = username
        self.password = password
        self.is_running = True
        self.process = None

    def stop(self):
        self.is_running = False
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

    def run(self):
        svn_exe = find_svn_exe()
        os.makedirs(self.dest_dir, exist_ok=True)
        total = len(self.rel_file_list)
        success_count = 0

        for i, rel_path in enumerate(self.rel_file_list, 1):
            if not self.is_running:
                break
            file_name = rel_path.replace("\\", "/").rstrip("/").split("/")[-1]
            self.progress_signal.emit(i, total, file_name)

            full_url = build_full_url(self.target_repo, rel_path)
            clean_url = canonicalize_svn_url(full_url)
            save_path = os.path.join(self.dest_dir, file_name)

            cmd = [svn_exe, "export", clean_url, save_path, "--force", "--non-interactive"]
            if self.username:
                cmd.extend(["--username", self.username])
            if self.password:
                cmd.extend(["--password", self.password])

            kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            try:
                self.process = subprocess.Popen(cmd, **kwargs)
                out, err = self.process.communicate(timeout=60)
                if self.process.returncode == 0:
                    success_count += 1
            except Exception:
                pass

        if not self.is_running:
            self.finished_signal.emit(False, success_count, self.dest_dir, "用户取消了批量拉取。")
        else:
            self.finished_signal.emit(True, success_count, self.dest_dir, "")


class SvnAppendExtWorker(QThread):
    """追加拉取指定后缀到当前索引库线程：遍历远程并只提取指定后缀，保留现有索引并合并"""
    progress_signal = pyqtSignal(int, int, str)        # scanned_total, hit_count, current_path
    finished_signal = pyqtSignal(bool, list, str, str) # success, hit_lines, head_rev, message

    def __init__(self, target_path: str, target_exts: list, existing_paths: list = None,
                 username: str = "", password: str = ""):
        super().__init__()
        self.target_path = target_path
        self.target_exts = [e.lower() for e in target_exts] if target_exts else []
        self.existing_set = set(existing_paths) if existing_paths else set()
        self.username = username
        self.password = password
        self.is_running = True
        self.process = None

    def stop(self):
        self.is_running = False
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

    def run(self):
        clean_target = canonicalize_svn_url(self.target_path) if self.target_path.startswith(("http://", "https://", "svn://")) else self.target_path
        svn_exe = find_svn_exe()

        head_rev = ""
        try:
            info_cmd = [svn_exe, "info", "--show-item", "revision", clean_target]
            if self.username:
                info_cmd.extend(["--username", self.username])
            if self.password:
                info_cmd.extend(["--password", self.password])
            ikwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if sys.platform.startswith("win"):
                ikwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ip = subprocess.Popen(info_cmd, **ikwargs)
            iout, _ = ip.communicate(timeout=5)
            if ip.returncode == 0:
                head_rev = decode_line(iout).strip()
        except Exception:
            pass

        cmd = [svn_exe, "list", "-R", clean_target]
        if self.username:
            cmd.extend(["--username", self.username])
        if self.password:
            cmd.extend(["--password", self.password])

        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1)
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        new_hits = []
        scanned_total = 0
        target_tuple = tuple(ext.lower() for ext in self.target_exts)
        existing_lower_set = set(p.lower().replace("\\", "/") for p in self.existing_set)
        try:
            self.process = subprocess.Popen(cmd, **kwargs)
            while self.is_running:
                raw_line = self.process.stdout.readline()
                if not raw_line:
                    break
                line = decode_line(raw_line).strip()
                if not line or line.endswith("/"):
                    continue
                scanned_total += 1
                clean_norm = line.replace("\\", "/")
                clean_lower = clean_norm.lower()

                # 严格大小写不敏感匹配后缀 (例如 .PCB, .pcb, .PrjPCB, .PrjPcb, .PcbDoc 全部精准命中)
                if clean_lower.endswith(target_tuple):
                    if clean_lower not in existing_lower_set:
                        new_hits.append(clean_norm)
                        existing_lower_set.add(clean_lower)

                if scanned_total % 100 == 0:
                    self.progress_signal.emit(scanned_total, len(new_hits), line)

            self.process.stdout.close()
            self.process.wait()
            if not self.is_running:
                self.finished_signal.emit(False, [], "", "用户中断了追加拉取。")
            else:
                ext_str = ", ".join(self.target_exts)
                msg = f"追加拉取完成：已检索 {scanned_total:,} 个对象，新增收录 {len(new_hits):,} 个【{ext_str}】文件！"
                self.finished_signal.emit(True, new_hits, head_rev, msg)
        except Exception as e:
            self.finished_signal.emit(False, [], "", f"追加拉取异常: {str(e)}")


# =====================================================================
# 5. 多格式导出模块
# =====================================================================

class ExportHelper:
    """支持 Word (.docx), Excel (.xlsx), 文本 (.txt), CSV (.csv) 导出"""

    @staticmethod
    def export(parent_widget, results: list, meta_info: dict):
        if not results:
            QMessageBox.information(parent_widget, "提示", "当前没有可导出的检索结果。")
            return

        filters = (
            "Word 文档 (*.docx);;"
            "Excel 工作表 (*.xlsx);;"
            "文本文件 (*.txt);;"
            "CSV 逗号分隔表格 (*.csv)"
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"SVN_Search_Result_{timestamp}.docx"

        file_path, selected_filter = QFileDialog.getSaveFileName(
            parent_widget, "导出检索结果报告", default_name, filters
        )
        if not file_path:
            return

        ext = os.path.splitext(file_path)[1].lower()
        if not ext:
            if "xlsx" in selected_filter:
                file_path += ".xlsx"; ext = ".xlsx"
            elif "txt" in selected_filter:
                file_path += ".txt"; ext = ".txt"
            elif "csv" in selected_filter:
                file_path += ".csv"; ext = ".csv"
            else:
                file_path += ".docx"; ext = ".docx"

        try:
            if ext == ".docx":
                ExportHelper._save_docx(file_path, results, meta_info)
            elif ext == ".xlsx":
                ExportHelper._save_xlsx(file_path, results, meta_info)
            elif ext == ".csv":
                ExportHelper._save_csv(file_path, results, meta_info)
            else:
                ExportHelper._save_txt(file_path, results, meta_info)

            # 导出成功后的便民操作
            msg_box = QMessageBox(parent_widget)
            msg_box.setWindowTitle("导出成功")
            msg_box.setText(f"检索结果已成功导出至:\n{file_path}\n\n是否立即打开文件？")
            btn_open = msg_box.addButton("打开文件", QMessageBox.ActionRole)
            btn_folder = msg_box.addButton("定位文件夹", QMessageBox.ActionRole)
            btn_close = msg_box.addButton("关闭", QMessageBox.RejectRole)
            msg_box.exec_()

            if msg_box.clickedButton() == btn_open:
                os.startfile(file_path)
            elif msg_box.clickedButton() == btn_folder:
                subprocess.Popen(f'explorer /select,"{os.path.normpath(file_path)}"')

        except Exception as e:
            QMessageBox.critical(parent_widget, "导出失败", f"写入文件时出错:\n{str(e)}")

    @staticmethod
    def _save_docx(file_path, results, meta):
        from docx import Document
        from docx.shared import Pt, Inches, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_TABLE_ALIGNMENT

        doc = Document()
        title_p = doc.add_heading("SVN 仓库文件检索报告", level=1)
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # 元数据表格
        meta_table = doc.add_table(rows=5, cols=2)
        meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        meta_table.style = "Table Grid"
        labels = [
            ("目标仓库 / 路径", meta.get("target", "-")),
            ("搜索关键词", meta.get("keywords", "(未指定)")),
            ("匹配模式与条件", meta.get("mode", "-")),
            ("导出生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("命中记录总数", f"{len(results)} 条"),
        ]
        for i, (k, v) in enumerate(labels):
            r = meta_table.rows[i].cells
            r[0].text = k
            r[1].text = str(v)
            r[0].paragraphs[0].runs[0].font.bold = True
            r[0].width = Inches(1.8)
            r[1].width = Inches(4.7)

        doc.add_paragraph()

        target = meta.get("target", "")

        # 结果数据表格
        headers = ["序号", "类型", "文件名", "所在路径 (URL)", "扩展名"]
        col_widths = [Inches(0.6), Inches(0.9), Inches(1.8), Inches(2.7), Inches(0.8)]
        res_table = doc.add_table(rows=1, cols=len(headers))
        res_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        res_table.style = "Light Shading Accent 1"

        hdr_cells = res_table.rows[0].cells
        for i, title in enumerate(headers):
            hdr_cells[i].text = title
            hdr_cells[i].width = col_widths[i]
            p = hdr_cells[i].paragraphs[0]
            if p.runs:
                p.runs[0].font.bold = True
                p.runs[0].font.color.rgb = RGBColor(255, 255, 255)

        for idx, path in enumerate(results, 1):
            badge, name, parent, ext = FileTypeHelper.get_info(path)
            dir_url = build_dir_url(target, parent)
            row_cells = res_table.add_row().cells
            data = [str(idx), badge, name, dir_url, ext]
            for i, val in enumerate(data):
                row_cells[i].text = val
                row_cells[i].width = col_widths[i]
                for p in row_cells[i].paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(9)

        doc.save(file_path)

    @staticmethod
    def _save_xlsx(file_path, results, meta):
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "检索结果"

        header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
        header_font = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
        cell_font = Font(name="Microsoft YaHei", size=10)
        thin_border = Border(
            left=Side(style="thin", color="E2E8F0"),
            right=Side(style="thin", color="E2E8F0"),
            top=Side(style="thin", color="E2E8F0"),
            bottom=Side(style="thin", color="E2E8F0"),
        )

        headers = ["序号", "类型", "文件名称", "所在路径 (URL)", "扩展名", "完整路径 (URL)"]
        ws.append(headers)
        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        target = meta.get("target", "")
        for idx, path in enumerate(results, 1):
            badge, name, parent, ext = FileTypeHelper.get_info(path)
            dir_url = build_dir_url(target, parent)
            full_url = build_full_url(target, path)
            ws.append([idx, badge, name, dir_url, ext, full_url])
            current_row = idx + 1
            for col_num in range(1, len(headers) + 1):
                c = ws.cell(row=current_row, column=col_num)
                c.font = cell_font
                c.border = thin_border
                if col_num in (1, 2, 5):
                    c.alignment = Alignment(horizontal="center")

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(60, max(max_len + 3, 10))

        wb.save(file_path)

    @staticmethod
    def _save_csv(file_path, results, meta):
        target = meta.get("target", "")
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["序号", "类型", "文件名称", "所在路径 (URL)", "扩展名", "完整路径 (URL)"])
            for idx, path in enumerate(results, 1):
                badge, name, parent, ext = FileTypeHelper.get_info(path)
                dir_url = build_dir_url(target, parent)
                full_url = build_full_url(target, path)
                writer.writerow([idx, badge, name, dir_url, ext, full_url])

    @staticmethod
    def _save_txt(file_path, results, meta):
        target = meta.get("target", "")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write("SVN 仓库文件检索报告\n")
            f.write("=" * 70 + "\n")
            f.write(f"目标仓库: {meta.get('target', '-')}\n")
            f.write(f"关键词: {meta.get('keywords', '(未指定)')}\n")
            f.write(f"匹配模式: {meta.get('mode', '-')}\n")
            f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"命中总数: {len(results)} 条\n")
            f.write("=" * 70 + "\n\n")
            for idx, path in enumerate(results, 1):
                full_url = build_full_url(target, path)
                f.write(f"[{idx:04d}] {full_url}\n")


# =====================================================================
# 6. 现代 UI 样式表 (Fluent / Slate 主题)
# =====================================================================

MODERN_STYLE = """
QWidget {
    background-color: #F8FAFC;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 12px;
    color: #1E293B;
}

QGroupBox {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
    color: #334155;
}

QGroupBox#repoGroup {
    margin-top: 4px;
    padding: 6px 10px 6px 10px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #2563EB;
}

QLineEdit, QComboBox {
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 5px 8px;
    background: #FFFFFF;
    color: #0F172A;
    min-height: 20px;
    selection-background-color: #2563EB;
}

QLineEdit:focus, QComboBox:focus {
    border: 1.5px solid #2563EB;
}

QPushButton {
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 14px;
    background-color: #2563EB;
    color: #FFFFFF;
    font-weight: 600;
    min-height: 20px;
}

QPushButton:hover {
    background-color: #1D4ED8;
}

QPushButton:pressed {
    background-color: #1E40AF;
}

QPushButton:disabled {
    background-color: #E2E8F0;
    color: #94A3B8;
    border: 1px solid #CBD5E1;
}

QPushButton#accentBtn {
    background-color: #059669;
    color: #FFFFFF;
}
QPushButton#accentBtn:hover {
    background-color: #047857;
}
QPushButton#accentBtn:disabled {
    background-color: #E2E8F0;
    color: #94A3B8;
    border: 1px solid #CBD5E1;
}

QPushButton#secondaryBtn {
    background-color: #F1F5F9;
    color: #334155;
    border: 1px solid #CBD5E1;
}
QPushButton#secondaryBtn:hover {
    background-color: #E2E8F0;
    border-color: #94A3B8;
}

QPushButton#dangerBtn {
    background-color: #EF4444;
    color: #FFFFFF;
}
QPushButton#dangerBtn:hover {
    background-color: #DC2626;
}
QPushButton#dangerBtn:disabled {
    background-color: #E2E8F0;
    color: #94A3B8;
    border: 1px solid #CBD5E1;
}

QFrame#statusCard {
    background-color: #F1F5F9;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    padding: 4px 8px;
}

QProgressBar {
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    background-color: #EDF2F7;
    text-align: center;
    height: 18px;
    font-size: 11px;
    font-weight: 600;
    color: #1E293B;
}

QProgressBar::chunk {
    border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B82F6, stop:1 #2563EB);
}

QTableWidget {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    gridline-color: #F1F5F9;
    selection-background-color: #DBEAFE;
    selection-color: #1E3A8A;
    alternate-background-color: #F8FAFC;
}

QHeaderView::section {
    background-color: #F1F5F9;
    color: #475569;
    font-weight: 600;
    padding: 6px;
    border: none;
    border-bottom: 1px solid #CBD5E1;
    border-right: 1px solid #E2E8F0;
}

QTableWidget::item {
    padding: 3px 6px;
}
"""


# =====================================================================
# 7. 主窗口程序
# =====================================================================

class ModernSvnSearchApp(QWidget):
    def __init__(self):
        super().__init__()
        self.index_mgr = SvnIndexManager()
        self.settings = QSettings("LocalTools", "SvnModernSearch")

        # 内存常驻索引：当前所选仓库的所有路径列表
        self.memory_paths = []
        self.current_cached_rev = ""
        self.remote_head_rev = ""

        # 当前呈现结果列表（用于过滤与导出）
        self.matched_paths = []

        # 线程句柄
        self.active_worker = None
        self.search_start_time = 0

        self.init_ui()
        self.load_settings()

        # 启动后延迟 300ms 自动加载初始仓库的缓存到内存
        QTimer.singleShot(300, self.on_target_changed)
        # 启动后延迟 600ms 自动检测远程版本并进行增量同步 (若有更新静默自动同步)
        QTimer.singleShot(600, self.auto_check_on_startup)

    def init_ui(self):
        self.setWindowTitle("⚡ SVN 极速索引与搜索工具 (Everything 架构版)")
        self.resize(1020, 760)
        self.setStyleSheet(MODERN_STYLE)

        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
        if hasattr(sys, "_MEIPASS"):
            icon_path_meipass = os.path.join(sys._MEIPASS, "app_icon.ico")
            if os.path.exists(icon_path_meipass):
                icon_path = icon_path_meipass
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        root = QVBoxLayout()
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # -------------------------------------------------------------
        # 卡片 1: 仓库同步中心 (Everything 索引层 - 默认折叠，点击展开)
        # -------------------------------------------------------------
        repo_group = QGroupBox()
        repo_group.setObjectName("repoGroup")
        repo_group_layout = QVBoxLayout()
        repo_group_layout.setContentsMargins(10, 8, 10, 8)
        repo_group_layout.setSpacing(6)

        # 顶部折叠栏 (常驻显示，展示标题、状态摘要与折叠按钮)
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self.toggle_repo_btn = QPushButton("▶ ① 仓库连接与 Everything 索引配置 (点击展开)")
        self.toggle_repo_btn.setStyleSheet("""
            QPushButton {
                font-weight: bold;
                font-size: 13px;
                color: #1E293B;
                border: none;
                background: transparent;
                text-align: left;
                padding: 2px 4px;
            }
            QPushButton:hover {
                color: #2563EB;
            }
        """)
        self.toggle_repo_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_repo_btn.clicked.connect(self.toggle_repo_card)
        header_row.addWidget(self.toggle_repo_btn)

        # 折叠状态下的精简摘要徽标 (让用户在折叠状态下一目了然当前仓库与索引状态)
        self.repo_summary_label = QLabel("正在检测本地索引...")
        self.repo_summary_label.setStyleSheet("""
            QLabel {
                color: #475569;
                background-color: #F1F5F9;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 12px;
            }
        """)
        header_row.addWidget(self.repo_summary_label, 1)

        self.repo_expand_btn = QPushButton("⚙ 展开设置 ▼")
        self.repo_expand_btn.setObjectName("secondaryBtn")
        self.repo_expand_btn.setCursor(Qt.PointingHandCursor)
        self.repo_expand_btn.setToolTip("展开配置目标仓库、文件后缀白名单、全量拉取及增量更新设置")
        self.repo_expand_btn.clicked.connect(self.toggle_repo_card)
        header_row.addWidget(self.repo_expand_btn)

        repo_group_layout.addLayout(header_row)

        # 折叠主体内容容器 (默认隐藏，点击展开时可见)
        self.repo_content_widget = QWidget()
        repo_layout = QVBoxLayout(self.repo_content_widget)
        repo_layout.setContentsMargins(0, 4, 0, 0)
        repo_layout.setSpacing(6)

        # URL 行
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("SVN路径 / 本地:"))
        self.url_combo = QComboBox()
        self.url_combo.setEditable(True)
        self.url_combo.setInsertPolicy(QComboBox.NoInsert)
        self.url_combo.lineEdit().setPlaceholderText("填远程URL（如 https://...）或本地工作副本目录")
        for _, u in DEFAULT_PRESET_REPOS:
            self.url_combo.addItem(u)
        self.url_combo.lineEdit().setText(DEFAULT_PRESET_REPOS[0][1])
        self.url_combo.currentTextChanged.connect(self.on_target_changed)
        url_row.addWidget(self.url_combo, 1)

        self.browse_btn = QPushButton("📁 浏览本地")
        self.browse_btn.setObjectName("secondaryBtn")
        self.browse_btn.clicked.connect(self.browse_local_directory)
        url_row.addWidget(self.browse_btn)

        self.check_rev_btn = QPushButton("🌐 探测远程版本")
        self.check_rev_btn.setObjectName("secondaryBtn")
        self.check_rev_btn.setToolTip("轻量调用 svn info 检查远程是否有新提交 (耗时约0.2秒)")
        self.check_rev_btn.clicked.connect(self.check_remote_revision)
        url_row.addWidget(self.check_rev_btn)

        repo_layout.addLayout(url_row)

        # 常用仓库快捷预设按钮行
        preset_repo_row = QHBoxLayout()
        preset_repo_row.setSpacing(6)
        lbl_preset = QLabel("常用预设仓库:")
        lbl_preset.setStyleSheet("color: #475569; font-weight: bold;")
        preset_repo_row.addWidget(lbl_preset)

        self.btn_preset_elec = QPushButton("💻 电子中心 (Electronic)")
        self.btn_preset_elec.setObjectName("secondaryBtn")
        self.btn_preset_elec.setToolTip("切换到电子中心仓库:\nhttps://dell740-svn/svn/ElectronicCentreRepository/Electronic trunk")
        self.btn_preset_elec.clicked.connect(
            lambda: self.switch_repository("https://dell740-svn/svn/ElectronicCentreRepository/Electronic trunk")
        )
        preset_repo_row.addWidget(self.btn_preset_elec)

        self.btn_preset_phys = QPushButton("📐 结构设计 (Physical)")
        self.btn_preset_phys.setObjectName("secondaryBtn")
        self.btn_preset_phys.setToolTip("切换到结构设计仓库:\nhttps://dell740-svn/svn/PhysicalDesignRepositories")
        self.btn_preset_phys.clicked.connect(
            lambda: self.switch_repository("https://dell740-svn/svn/PhysicalDesignRepositories")
        )
        preset_repo_row.addWidget(self.btn_preset_phys)

        self.btn_preset_proc = QPushButton("🛠 工艺设计 (Process)")
        self.btn_preset_proc.setObjectName("secondaryBtn")
        self.btn_preset_proc.setToolTip("切换到工艺设计仓库:\nhttps://dell740-svn/svn/ProcessDesignRepositories")
        self.btn_preset_proc.clicked.connect(
            lambda: self.switch_repository("https://dell740-svn/svn/ProcessDesignRepositories")
        )
        preset_repo_row.addWidget(self.btn_preset_proc)

        preset_repo_row.addStretch()
        repo_layout.addLayout(preset_repo_row)

        # 后缀白名单配置行 (解决全量拉取过大、只收录常用文档/工程文件的需求)
        ext_row = QHBoxLayout()
        self.chk_ext_filter = QCheckBox("仅拉取常用文档/指定后缀 (推荐):")
        self.chk_ext_filter.setChecked(True)
        self.chk_ext_filter.setToolTip("开启后仅收录指定后缀，跳过源码、编译缓存与临时垃圾，极大提升速度与节省空间")
        self.chk_ext_filter.toggled.connect(self.on_ext_filter_toggled)
        ext_row.addWidget(self.chk_ext_filter)

        self.ext_input = QLineEdit()
        self.ext_input.setPlaceholderText("例如: word, pdf, ppt, eda, dwg, xmind, mpp 或 .docx, .pdf, *.mpp")
        self.ext_input.setText(".docx, .doc, .pdf, .xlsx, .xls, .pptx, .ppt, .drawio, .vsdx, .md, .txt, .mpp, .xmind, .dwg, .schdoc, .sch, .schlib, .pcb, .pcbdoc, .pcblib, .prjpcb")
        ext_row.addWidget(self.ext_input, 1)

        self.preset_doc_btn = QPushButton("📄 常用办公/设计")
        self.preset_doc_btn.setObjectName("secondaryBtn")
        self.preset_doc_btn.setToolTip("包含 Word、PDF、Excel、PPT、DrawIO、Visio、Markdown、TXT、Project(.mpp)")
        self.preset_doc_btn.clicked.connect(self.set_preset_doc)
        ext_row.addWidget(self.preset_doc_btn)

        self.preset_eda_btn = QPushButton("+ ⚡ 原理图/PCB")
        self.preset_eda_btn.setObjectName("secondaryBtn")
        self.preset_eda_btn.setToolTip("追加 .schdoc, .sch, .schlib, .pcb, .pcbdoc, .pcblib, .prjpcb")
        self.preset_eda_btn.clicked.connect(self.append_preset_eda)
        ext_row.addWidget(self.preset_eda_btn)

        self.preset_cad_btn = QPushButton("+ 📐 图纸")
        self.preset_cad_btn.setObjectName("secondaryBtn")
        self.preset_cad_btn.setToolTip("追加 .dwg, .dxf, .vsd")
        self.preset_cad_btn.clicked.connect(self.append_preset_cad)
        ext_row.addWidget(self.preset_cad_btn)

        self.preset_xmind_btn = QPushButton("+ 🧠 导图")
        self.preset_xmind_btn.setObjectName("secondaryBtn")
        self.preset_xmind_btn.setToolTip("追加 .xmind")
        self.preset_xmind_btn.clicked.connect(self.append_preset_xmind)
        ext_row.addWidget(self.preset_xmind_btn)

        self.preset_code_btn = QPushButton("+ 💻 代码")
        self.preset_code_btn.setObjectName("secondaryBtn")
        self.preset_code_btn.setToolTip("追加 .c, .h, .cpp, .py, .java")
        self.preset_code_btn.clicked.connect(self.append_preset_code)
        ext_row.addWidget(self.preset_code_btn)

        self.preset_all_btn = QPushButton("🌐 全部文件")
        self.preset_all_btn.setObjectName("secondaryBtn")
        self.preset_all_btn.setToolTip("取消后缀限制，索引所有文件")
        self.preset_all_btn.clicked.connect(self.set_preset_all)
        ext_row.addWidget(self.preset_all_btn)

        repo_layout.addLayout(ext_row)

        # 状态胶囊条与同步操作按钮
        sync_row = QHBoxLayout()

        self.status_capsule = QLabel("📦 本地索引: 检测中...")
        self.status_capsule.setStyleSheet("color: #475569; font-weight: bold;")
        sync_row.addWidget(self.status_capsule, 1)

        self.chk_auto_sync = QCheckBox("启动自动增量同步")
        self.chk_auto_sync.setToolTip("开启后，每次打开软件自动在后台检测远程版本，若有新提交则自动增量更新，无变更则不更新")
        self.chk_auto_sync.setChecked(True)
        self.chk_auto_sync.toggled.connect(lambda: self.update_repo_summary())
        sync_row.addWidget(self.chk_auto_sync)

        self.append_ext_btn = QPushButton("➕ 追加指定后缀到索引")
        self.append_ext_btn.setObjectName("accentBtn")
        self.append_ext_btn.setToolTip("根据输入框填写的后缀（如 .mpp），在保留现有几万条索引的基础上，增量检索并合并收录到索引库中！")
        self.append_ext_btn.clicked.connect(self.start_append_ext_to_index)
        sync_row.addWidget(self.append_ext_btn)

        self.full_pull_btn = QPushButton("📥 全量拉取 (构建完整索引)")
        self.full_pull_btn.setObjectName("secondaryBtn")
        self.full_pull_btn.setToolTip("首次使用或需要彻底重新遍历所有文件时点击")
        self.full_pull_btn.clicked.connect(self.start_full_pull)
        sync_row.addWidget(self.full_pull_btn)

        self.inc_update_btn = QPushButton("🔄 增量更新 (svn diff 补丁)")
        self.inc_update_btn.setObjectName("secondaryBtn")
        self.inc_update_btn.setToolTip("只拉取自上次缓存以来的变动项，秒级合并更新")
        self.inc_update_btn.clicked.connect(self.start_incremental_update)
        sync_row.addWidget(self.inc_update_btn)

        self.clear_cache_btn = QPushButton("🗑 清除缓存")
        self.clear_cache_btn.setObjectName("secondaryBtn")
        self.clear_cache_btn.clicked.connect(self.clear_current_cache)
        sync_row.addWidget(self.clear_cache_btn)

        self.open_cache_btn = QPushButton("📂 打开缓存目录")
        self.open_cache_btn.setObjectName("secondaryBtn")
        self.open_cache_btn.setToolTip("直接在 Windows 资源管理器中打开当前缓存文件存放目录")
        self.open_cache_btn.clicked.connect(self.open_cache_directory)
        sync_row.addWidget(self.open_cache_btn)

        repo_layout.addLayout(sync_row)
        repo_group_layout.addWidget(self.repo_content_widget)
        repo_group.setLayout(repo_group_layout)
        root.addWidget(repo_group)

        # 默认折叠隐藏配置区域，最大化搜索结果展示空间
        self.repo_content_widget.setVisible(False)

        # -------------------------------------------------------------
        # 卡片 2: 搜索与过滤控制
        # -------------------------------------------------------------
        search_group = QGroupBox("② 极速检索与条件过滤")
        search_layout = QVBoxLayout()
        search_layout.setSpacing(6)

        # 关键词行
        kw_row = QHBoxLayout()
        kw_row.addWidget(QLabel("搜索关键词:"))
        self.kw_input = QLineEdit()
        self.kw_input.setClearButtonEnabled(True)
        self.kw_input.setPlaceholderText("输入关键词（空格分隔多个词，按回车直接触发本地秒搜）")
        self.kw_input.returnPressed.connect(self.start_memory_search)
        kw_row.addWidget(self.kw_input, 1)

        self.radio_all = QRadioButton("全部匹配 (AND)")
        self.radio_any = QRadioButton("任一匹配 (OR)")
        self.radio_exact = QRadioButton("精确短语")
        self.radio_all.setChecked(True)
        self.btn_group = QButtonGroup()
        self.btn_group.addButton(self.radio_all)
        self.btn_group.addButton(self.radio_any)
        self.btn_group.addButton(self.radio_exact)
        kw_row.addWidget(self.radio_all)
        kw_row.addWidget(self.radio_any)
        kw_row.addWidget(self.radio_exact)

        search_layout.addLayout(kw_row)

        # 匹配范围与单词边界行 (解决 TR 命中 Electronic 路径等误匹配痛点)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("匹配范围:"))
        self.scope_filename = QRadioButton("仅匹配文件名 (推荐，不查路径)")
        self.scope_fullpath = QRadioButton("完整路径 (同时匹配文件名与目录)")
        self.scope_directory = QRadioButton("仅所在目录")
        self.scope_filename.setChecked(True)
        self.scope_btn_group = QButtonGroup()
        self.scope_btn_group.addButton(self.scope_filename)
        self.scope_btn_group.addButton(self.scope_fullpath)
        self.scope_btn_group.addButton(self.scope_directory)
        self.scope_filename.toggled.connect(self.on_filter_changed)
        self.scope_fullpath.toggled.connect(self.on_filter_changed)
        self.scope_directory.toggled.connect(self.on_filter_changed)

        scope_row.addWidget(self.scope_filename)
        scope_row.addWidget(self.scope_fullpath)
        scope_row.addWidget(self.scope_directory)

        self.chk_word_boundary = QCheckBox("独立词/全词匹配 (如 TR 不匹配 Electronic)")
        self.chk_word_boundary.setToolTip("开启后要求关键词为独立单词（以空格、下划线、短横线、点分隔），如搜 TR 不会命中 Electronic，但会命中 TR-01 或 TR_Doc")
        self.chk_word_boundary.toggled.connect(self.on_filter_changed)
        scope_row.addWidget(self.chk_word_boundary)
        scope_row.addStretch()

        search_layout.addLayout(scope_row)

        # 过滤与选项行
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("类型过滤:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "全部类型", "文档", "表格", "幻灯片", "项目计划",
            "原理图/PCB", "CAD工程图", "思维导图",
            "代码", "图片", "压缩包", "程序/固件", "目录"
        ])
        self.type_combo.currentIndexChanged.connect(self.on_filter_changed)
        filter_row.addWidget(self.type_combo)

        filter_row.addWidget(QLabel("后缀过滤:"))
        self.search_ext_combo = QComboBox()
        self.search_ext_combo.setEditable(True)
        self.search_ext_combo.setMinimumWidth(165)
        self.search_ext_combo.setToolTip("选择预设或直接输入要限定搜索的后缀名（如 .schdoc、.pcb、.dwg、.xmind、.mpp 等），默认【全部后缀】则不限")
        self.search_ext_combo.addItems([
            "全部后缀",
            ".schdoc / .sch / .schlib (原理图)",
            ".pcb / .pcblib / .prjpcb (PCB设计)",
            ".dwg / .dxf (CAD工程图)",
            ".xmind (思维导图)",
            ".mpp (项目计划)",
            ".xlsx / .xls / .xlsm (表格)",
            ".docx / .doc (文档)",
            ".pptx / .ppt (幻灯片)",
            ".pdf (PDF文档)",
            ".drawio / .vsdx (流程架构图)",
            ".step / .stp / .prt (3D模型)",
            ".zip / .7z / .rar (压缩包)",
            ".c / .h / .cpp (C/C++代码)",
            ".py / .java / .go (脚本/语言)",
            ".txt / .md (文本笔记)"
        ])
        if self.search_ext_combo.lineEdit():
            self.search_ext_combo.lineEdit().setPlaceholderText("全部后缀或输入如 .mpp")
            self.search_ext_combo.lineEdit().returnPressed.connect(self.start_memory_search)
        self.search_ext_combo.currentTextChanged.connect(self.on_filter_changed)
        filter_row.addWidget(self.search_ext_combo)

        self.chk_filter_noise = QCheckBox("过滤编译临时垃圾 (Debug/Release/si4project等)")
        self.chk_filter_noise.setChecked(True)
        self.chk_filter_noise.toggled.connect(self.on_filter_changed)
        filter_row.addWidget(self.chk_filter_noise)

        self.chk_case_sensitive = QCheckBox("区分大小写")
        self.chk_case_sensitive.toggled.connect(self.on_filter_changed)
        filter_row.addWidget(self.chk_case_sensitive)

        filter_row.addStretch()

        search_layout.addLayout(filter_row)

        # 操作大按钮行
        btn_row = QHBoxLayout()

        self.local_search_btn = QPushButton("⚡ 本地秒搜 (纯内存零延迟)")
        self.local_search_btn.setStyleSheet("font-size: 13px; padding: 7px 18px;")
        self.local_search_btn.clicked.connect(self.start_memory_search)
        btn_row.addWidget(self.local_search_btn)

        self.remote_search_btn = QPushButton("🔍 远程实时检索 (备用)")
        self.remote_search_btn.setObjectName("secondaryBtn")
        self.remote_search_btn.setToolTip("不读写本地索引，直接远程实时流式检索")
        self.remote_search_btn.clicked.connect(self.start_remote_search)
        btn_row.addWidget(self.remote_search_btn)

        self.stop_btn = QPushButton("⏹ 终止")
        self.stop_btn.setObjectName("dangerBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_active_task)
        btn_row.addWidget(self.stop_btn)

        self.export_btn = QPushButton("💾 导出结果 (Word/Excel/TXT/CSV)")
        self.export_btn.setObjectName("secondaryBtn")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_results)
        btn_row.addWidget(self.export_btn)

        self.clear_res_btn = QPushButton("🧹 清空列表")
        self.clear_res_btn.setObjectName("secondaryBtn")
        self.clear_res_btn.clicked.connect(self.clear_results)
        btn_row.addWidget(self.clear_res_btn)

        search_layout.addLayout(btn_row)
        search_group.setLayout(search_layout)
        root.addWidget(search_group)

        # -------------------------------------------------------------
        # 卡片 3: 实时进度与性能看板
        # -------------------------------------------------------------
        progress_card = QFrame()
        progress_card.setObjectName("statusCard")
        prog_layout = QVBoxLayout(progress_card)
        prog_layout.setContentsMargins(8, 6, 8, 6)
        prog_layout.setSpacing(4)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("待命就绪")
        prog_layout.addWidget(self.progress_bar)

        metric_row = QHBoxLayout()
        self.metric_count = QLabel("索引库: 0 项")
        self.metric_hit = QLabel("当前命中: 0 条")
        self.metric_time = QLabel("耗时: -")
        self.metric_speed = QLabel("处理速度: -")
        self.status_text = QLabel("正在扫描: 就绪")
        self.status_text.setStyleSheet("color: #64748B;")

        metric_row.addWidget(self.metric_count)
        metric_row.addWidget(self.metric_hit)
        metric_row.addWidget(self.metric_time)
        metric_row.addWidget(self.metric_speed)
        metric_row.addStretch()
        prog_layout.addLayout(metric_row)
        prog_layout.addWidget(self.status_text)

        root.addWidget(progress_card)

        # -------------------------------------------------------------
        # 卡片 4: 结果数据表格展示
        # -------------------------------------------------------------
        result_group = QGroupBox("③ 检索结果 (双击直接调起 SVN Repo-Browser 打开所在目录，支持右键菜单)")
        result_layout = QVBoxLayout()
        result_layout.setSpacing(6)

        subfilter_row = QHBoxLayout()
        subfilter_row.addWidget(QLabel("结果内即时过滤:"))
        self.subfilter_input = QLineEdit()
        self.subfilter_input.setPlaceholderText("在当前已匹配的结果中再次快速过滤...")
        self.subfilter_input.setClearButtonEnabled(True)
        self.subfilter_input.textChanged.connect(self.filter_current_table)
        subfilter_row.addWidget(self.subfilter_input, 1)

        self.table_counter_label = QLabel("显示 0 / 共 0 条")
        self.table_counter_label.setStyleSheet("color: #2563EB; font-weight: bold;")
        subfilter_row.addWidget(self.table_counter_label)
        result_layout.addLayout(subfilter_row)

        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(5)
        self.table_widget.setHorizontalHeaderLabels(["#", "类型", "文件名称", "所在目录", "扩展名"])
        self.table_widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_widget.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table_widget.horizontalHeader().resizeSection(2, 300)
        self.table_widget.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table_widget.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setSortingEnabled(True)
        self.table_widget.itemDoubleClicked.connect(self.on_table_double_clicked)
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self.show_context_menu)

        result_layout.addWidget(self.table_widget)
        result_group.setLayout(result_layout)
        root.addWidget(result_group, 1)

        self.setLayout(root)

    # ---------- 卡片 1 折叠面板控制 ----------

    def toggle_repo_card(self):
        is_visible = not self.repo_content_widget.isVisible()
        self.repo_content_widget.setVisible(is_visible)
        if is_visible:
            self.toggle_repo_btn.setText("▼ ① 仓库连接与 Everything 索引配置 (点击收起)")
            self.repo_expand_btn.setText("🔼 收起设置 ▲")
        else:
            self.toggle_repo_btn.setText("▶ ① 仓库连接与 Everything 索引配置 (点击展开)")
            self.repo_expand_btn.setText("⚙ 展开设置 ▼")

    def switch_repository(self, repo_url: str):
        """快捷切换目标 SVN 仓库"""
        idx = self.url_combo.findText(repo_url)
        if idx >= 0:
            self.url_combo.setCurrentIndex(idx)
        else:
            self.url_combo.insertItem(0, repo_url)
            self.url_combo.setCurrentIndex(0)
        self.url_combo.lineEdit().setText(repo_url)

    def update_repo_summary(self, extra_status: str = ""):
        target = self.url_combo.currentText().strip()
        short_target = target
        if len(target) > 38:
            parts = target.rstrip("/").split("/")
            short_target = ".../" + "/".join(parts[-2:]) if len(parts) >= 2 else target

        count_str = f"{len(self.memory_paths):,}" if self.memory_paths else "0"
        rev_str = f"r{self.current_cached_rev}" if self.current_cached_rev else "未记录"
        sync_str = "自动增量: 开" if self.chk_auto_sync.isChecked() else "自动增量: 关"

        if extra_status:
            self.repo_summary_label.setText(f"📁 {short_target}  |  📦 索引: {count_str} 项 ({rev_str})  |  {extra_status}")
        else:
            self.repo_summary_label.setText(f"📁 {short_target}  |  📦 索引: {count_str} 项 ({rev_str})  |  {sync_str}")

    # ---------- 设置记忆与加载 ----------

    def on_ext_filter_toggled(self, checked):
        self.ext_input.setEnabled(checked)

    def set_preset_doc(self):
        self.chk_ext_filter.setChecked(True)
        self.ext_input.setText(".docx, .doc, .pdf, .xlsx, .xls, .pptx, .ppt, .drawio, .vsdx, .md, .txt, .mpp")

    def append_preset_eda(self):
        self.chk_ext_filter.setChecked(True)
        curr = self.ext_input.text().strip()
        additions = [".schdoc", ".sch", ".schlib", ".pcb", ".pcbdoc", ".pcblib", ".prjpcb"]
        curr_list = [x.strip() for x in curr.split(",") if x.strip()]
        for a in additions:
            if a not in curr_list:
                curr_list.append(a)
        self.ext_input.setText(", ".join(curr_list))

    def append_preset_cad(self):
        self.chk_ext_filter.setChecked(True)
        curr = self.ext_input.text().strip()
        additions = [".dwg", ".dxf", ".vsd"]
        curr_list = [x.strip() for x in curr.split(",") if x.strip()]
        for a in additions:
            if a not in curr_list:
                curr_list.append(a)
        self.ext_input.setText(", ".join(curr_list))

    def append_preset_xmind(self):
        self.chk_ext_filter.setChecked(True)
        curr = self.ext_input.text().strip()
        additions = [".xmind"]
        curr_list = [x.strip() for x in curr.split(",") if x.strip()]
        for a in additions:
            if a not in curr_list:
                curr_list.append(a)
        self.ext_input.setText(", ".join(curr_list))

    def append_preset_code(self):
        self.chk_ext_filter.setChecked(True)
        curr = self.ext_input.text().strip()
        additions = [".c", ".h", ".cpp", ".py", ".java"]
        curr_list = [x.strip() for x in curr.split(",") if x.strip()]
        for a in additions:
            if a not in curr_list:
                curr_list.append(a)
        self.ext_input.setText(", ".join(curr_list))

    def set_preset_all(self):
        self.chk_ext_filter.setChecked(False)

    # ---------- 设置记忆与加载 ----------

    def load_settings(self):
        history = self.settings.value("url_history", [], type=list)
        if history:
            self.url_combo.clear()
            self.url_combo.addItems(history)
            self.url_combo.setCurrentIndex(0)
        self.chk_filter_noise.setChecked(self.settings.value("filter_noise", True, type=bool))
        self.chk_ext_filter.setChecked(self.settings.value("ext_filter_enabled", True, type=bool))
        saved_exts = self.settings.value("ext_filter_list", "", type=str)
        if saved_exts:
            self.ext_input.setText(saved_exts)

        scope = self.settings.value("search_scope", "filename", type=str)
        if scope == "directory":
            self.scope_directory.setChecked(True)
        elif scope == "fullpath":
            self.scope_fullpath.setChecked(True)
        else:
            self.scope_filename.setChecked(True)

        self.chk_word_boundary.setChecked(self.settings.value("word_boundary", False, type=bool))
        self.chk_case_sensitive.setChecked(self.settings.value("case_sensitive", False, type=bool))
        self.chk_auto_sync.setChecked(self.settings.value("auto_sync_on_startup", True, type=bool))

    def save_target_history(self, target: str):
        if not target:
            return
        history = self.settings.value("url_history", [], type=list)
        if target in history:
            history.remove(target)
        history.insert(0, target)
        history = history[:MAX_URL_HISTORY]
        self.settings.setValue("url_history", history)
        self.settings.setValue("filter_noise", self.chk_filter_noise.isChecked())
        self.settings.setValue("ext_filter_enabled", self.chk_ext_filter.isChecked())
        self.settings.setValue("ext_filter_list", self.ext_input.text().strip())

        scope_str = "directory" if self.scope_directory.isChecked() else ("fullpath" if self.scope_fullpath.isChecked() else "filename")
        self.settings.setValue("search_scope", scope_str)
        self.settings.setValue("word_boundary", self.chk_word_boundary.isChecked())
        self.settings.setValue("case_sensitive", self.chk_case_sensitive.isChecked())
        self.settings.setValue("auto_sync_on_startup", self.chk_auto_sync.isChecked())

    # ---------- 仓库切换与常驻内存加载 ----------

    def on_target_changed(self):
        target = self.url_combo.currentText().strip()
        if not target:
            self.status_capsule.setText("📦 未指定仓库路径")
            self.memory_paths = []
            return

        meta = self.index_mgr.get_meta(target)
        if meta and meta.get("count", 0) > 0:
            self.memory_paths = self.index_mgr.load_cache_lines(target)
            self.current_cached_rev = meta.get("rev", "")
            rev_str = f"r{self.current_cached_rev}" if self.current_cached_rev else "未记录"
            count_str = f"{len(self.memory_paths):,}"

            exts = meta.get("extensions", [])
            ext_desc = f"{len(exts)} 种常用后缀" if exts else "全部文件"

            self.status_capsule.setText(
                f"📦 本地已载入内存: {count_str} 项 (范围: {ext_desc} | 版本: {rev_str} | 时间: {meta.get('updated_at', '')})"
            )
            self.status_capsule.setStyleSheet("color: #059669; font-weight: bold;")
            self.metric_count.setText(f"索引库: {count_str} 项")
            self.inc_update_btn.setEnabled(bool(self.current_cached_rev))
            self.update_repo_summary()
        else:
            self.memory_paths = []
            self.current_cached_rev = ""
            self.status_capsule.setText("📦 暂无本地索引缓存，建议先执行【📥 全量拉取】")
            self.status_capsule.setStyleSheet("color: #D97706; font-weight: bold;")
            self.metric_count.setText("索引库: 0 项")
            self.inc_update_btn.setEnabled(False)
            self.update_repo_summary()

    def browse_local_directory(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择本地 SVN 工作副本目录")
        if dir_path:
            self.url_combo.lineEdit().setText(os.path.normpath(dir_path))

    def clear_current_cache(self):
        target = self.url_combo.currentText().strip()
        if not target:
            return
        reply = QMessageBox.question(
            self, "确认清除",
            f"确定要清除当前仓库的本地索引缓存吗？\n{target}",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.index_mgr.clear_cache(target)
            self.on_target_changed()
            QMessageBox.information(self, "已清除", "本地缓存与元数据已清空。")

    def open_cache_directory(self):
        if os.path.exists(self.index_mgr.base_dir):
            os.startfile(self.index_mgr.base_dir)
        else:
            os.makedirs(self.index_mgr.base_dir, exist_ok=True)
            os.startfile(self.index_mgr.base_dir)

    # ---------- 探测远程版本 (svn info) ----------

    def check_remote_revision(self):
        target = self.url_combo.currentText().strip()
        if not target:
            QMessageBox.information(self, "提示", "请先输入或选择 SVN 路径。")
            return

        self.check_rev_btn.setEnabled(False)
        self.status_text.setText("正在轻量探测远程版本 (svn info)...")

        self.info_worker = SvnInfoWorker(target)
        self.info_worker.finished_signal.connect(self.on_check_rev_finished)
        self.info_worker.start()

    def on_check_rev_finished(self, success, rev, msg):
        self.check_rev_btn.setEnabled(True)
        if not success:
            self.status_text.setText(f"探测版本失败: {msg}")
            QMessageBox.warning(self, "探测版本失败", msg)
            return

        self.remote_head_rev = rev
        cached = self.current_cached_rev
        if cached and cached == rev:
            self.status_text.setText(f"✅ 远程最新版本为 r{rev}，本地缓存已是最新，无需同步。")
            QMessageBox.information(self, "版本一致", f"远程版本: r{rev}\n本地缓存: r{cached}\n\n✅ 索引已是最新的，可放心进行本地秒搜！")
        elif cached and cached != rev:
            self.status_text.setText(f"⚡ 远程有新更新: r{cached} → r{rev}，建议点击【🔄 增量更新】")
            self.inc_update_btn.setEnabled(True)
            reply = QMessageBox.question(
                self, "发现新版本",
                f"远程最新: r{rev}\n本地缓存: r{cached}\n\n检测到有新提交，是否立即执行增量更新？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.start_incremental_update(silent=False)
        else:
            self.status_text.setText(f"远程当前最新版本: r{rev} (本地尚无缓存)")

    # ---------- 全量拉取 (构建完整索引) ----------

    def start_full_pull(self):
        target = self.url_combo.currentText().strip()
        if not target:
            QMessageBox.information(self, "提示", "请填写目标 SVN 路径。")
            return

        self.save_target_history(target)
        self.set_ui_busy(True)

        self.active_exts = normalize_extensions(self.ext_input.text()) if self.chk_ext_filter.isChecked() else []
        filter_noise = self.chk_filter_noise.isChecked()

        estimated = len(self.memory_paths)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("全量拉取中: 0%")
        self.status_text.setText("正在连接远程 SVN 仓库执行全量遍历 (svn list -R)...")
        self.search_start_time = time.time()

        self.active_worker = SvnFullScanWorker(
            target, allowed_exts=self.active_exts, filter_noise=filter_noise, estimated_total=estimated
        )
        self.active_worker.progress_signal.connect(self.on_full_pull_progress)
        self.active_worker.finished_signal.connect(self.on_full_pull_finished)
        self.active_worker.start()

    def on_full_pull_progress(self, total_scanned, kept_count, estimated_total, speed, current_path):
        elapsed = max(0.1, time.time() - self.search_start_time)
        if estimated_total > 0:
            percent = min(99, int(total_scanned / estimated_total * 100))
            self.progress_bar.setValue(percent)
            eta = int((estimated_total - total_scanned) / speed) if speed > 0 and estimated_total > total_scanned else None
            eta_str = f"，剩余 ~{eta}s" if eta is not None else ""
            self.progress_bar.setFormat(f"{percent}% (已遍历 {total_scanned:,} / 收录 {kept_count:,}{eta_str})")
        else:
            self.progress_bar.setFormat(f"已遍历 {total_scanned:,} 项 / 收录 {kept_count:,} 项")

        self.metric_count.setText(f"已遍历: {total_scanned:,} 项")
        self.metric_hit.setText(f"已收录目标: {kept_count:,} 项")
        self.metric_time.setText(f"耗时: {elapsed:.1f}s")
        self.metric_speed.setText(f"速度: {speed:.0f} 项/s")

        disp = current_path if len(current_path) < 55 else "..." + current_path[-52:]
        self.status_text.setText(f"正在扫描: {disp}")

    def on_full_pull_finished(self, success, lines, head_rev, msg):
        self.set_ui_busy(False)
        self.progress_bar.setValue(100 if success else 0)
        self.progress_bar.setFormat("100% 全量完成" if success else "拉取中断/失败")

        if success:
            target = self.url_combo.currentText().strip()
            active_exts = getattr(self, "active_exts", [])
            # 汇总收录所有文件的扩展名全集保存进元数据，确保未来增量更新涵盖所有历史后缀
            all_exts = set(e.lower() for e in active_exts)
            for p in lines:
                _, ext = os.path.splitext(p)
                if ext:
                    all_exts.add(ext.lower())
            self.index_mgr.save_cache_lines(target, lines, rev=head_rev, exts=sorted(list(all_exts)))
            self.memory_paths = lines
            self.current_cached_rev = head_rev
            self.on_target_changed()
            elapsed = time.time() - self.search_start_time
            scope_desc = f"{len(active_exts)} 种指定后缀文件" if active_exts else "全部文件"
            QMessageBox.information(
                self, "全量拉取完成",
                f"成功构建本地索引！\n"
                f"收录范围: {scope_desc}\n"
                f"目标对象总计: {len(lines):,} 项\n"
                f"版本记录: r{head_rev}\n"
                f"总耗时: {elapsed:.1f}s\n\n"
                f"现在可以零延迟进行【本地秒搜】了！"
            )
        else:
            if "用户中断" not in msg:
                QMessageBox.critical(self, "全量拉取失败", msg)

    # ---------- 启动自检与增量更新 (svn diff --summarize) ----------

    def auto_check_on_startup(self):
        """程序启动后自动轻量探测 SVN 远程最新版本，有变更则自动增量更新，无变更则静默就绪"""
        if not self.chk_auto_sync.isChecked():
            return
        self.start_incremental_update(silent=True)

    def start_incremental_update(self, silent=False):
        target = self.url_combo.currentText().strip()
        if not target:
            if not silent:
                QMessageBox.information(self, "提示", "请先输入或选择 SVN 路径。")
            return

        if not self.current_cached_rev:
            if not silent:
                QMessageBox.warning(self, "无法增量", "当前仓库未记录基础版本号，请先执行一次【全量拉取】。")
            else:
                self.status_text.setText("📦 提示: 当前仓库尚无本地索引，请先点击【📥 全量拉取】构建首次索引。")
            return

        if self.active_worker and self.active_worker.isRunning():
            return

        if silent:
            self.status_text.setText("🔄 [启动自检] 正在后台探测远程 SVN 最新版本...")
            self.status_capsule.setText(f"📦 本地版本 r{self.current_cached_rev} | 正在自动探测远程是否有新提交...")
            self.status_capsule.setStyleSheet("color: #2563EB; font-weight: bold;")
        else:
            self.set_ui_busy(True)
            self.status_text.setText(f"正在轻量探测远程最新版本...")

        self.info_worker = SvnInfoWorker(target)
        self.info_worker.finished_signal.connect(lambda ok, rev, msg: self._do_inc_diff(ok, rev, msg, silent=silent))
        self.info_worker.start()

    def _do_inc_diff(self, success, new_rev, msg, silent=False):
        if not success or not new_rev:
            if not silent:
                self.set_ui_busy(False)
                QMessageBox.warning(self, "获取最新版本失败", msg)
            else:
                self.status_text.setText(f"⚠️ [自检跳过] 无法连接远程 SVN 仓库 (已跳过自动同步): {msg}")
                self.status_capsule.setText(
                    f"📦 本地已载入内存: {len(self.memory_paths):,} 项 (版本: r{self.current_cached_rev} | 离线/跳过同步)"
                )
                self.status_capsule.setStyleSheet("color: #64748B; font-weight: bold;")
            return

        old_rev = self.current_cached_rev
        if old_rev == new_rev:
            if not silent:
                self.set_ui_busy(False)
                self.progress_bar.setValue(100)
                self.progress_bar.setFormat("100% 已是最新")
                QMessageBox.information(self, "无需更新", f"本地版本为 r{old_rev}，远程也是 r{new_rev}，已经是最新！")
            else:
                self.status_text.setText(f"✅ [自检完成] 远程最新版本为 r{new_rev}，本地索引已是最新，无需更新。")
                self.status_capsule.setText(
                    f"📦 本地已载入内存: {len(self.memory_paths):,} 项 (版本: r{old_rev} 最新 ✅ | 零延迟秒搜就绪)"
                )
                self.status_capsule.setStyleSheet("color: #059669; font-weight: bold;")
                self.update_repo_summary("已是最新 ✅")
            return

        target = self.url_combo.currentText().strip()
        if not silent:
            self.set_ui_busy(True)
        self.progress_bar.setRange(0, 0)
        prefix_tag = "自动增量同步中" if silent else "增量同步中"
        self.progress_bar.setFormat(f"{prefix_tag}: r{old_rev} → r{new_rev}")
        self.status_text.setText(f"正在执行 svn diff --summarize -r {old_rev}:{new_rev} 提取变动补丁...")
        self.status_capsule.setText(f"🔄 发现远程更新 (r{old_rev} → r{new_rev})，正在自动增量同步...")
        self.status_capsule.setStyleSheet("color: #D97706; font-weight: bold;")
        self.search_start_time = time.time()

        # 严格收集该仓库过去拉取过的全部历史扩展名集合（严格按用户要求：仅收录过去拉取过的后缀 + 当前配置的后缀）
        all_tracked_exts = set()

        # 1. 当前内存/缓存中所有已有文件的实际扩展名（真实历史拉取过的后缀全集）
        for p in self.memory_paths:
            _, ext = os.path.splitext(p)
            if ext:
                e_clean = ext.lower().strip()
                if e_clean and not e_clean.endswith("/"):
                    if not e_clean.startswith("."):
                        e_clean = "." + e_clean
                    all_tracked_exts.add(e_clean)

        # 2. meta 元数据中记录的历史拉取扩展名
        meta = self.index_mgr.get_meta(target)
        if meta and isinstance(meta, dict):
            for e in meta.get("extensions", []):
                if e:
                    e_clean = e.lower().strip()
                    if not e_clean.startswith("."):
                        e_clean = "." + e_clean
                    all_tracked_exts.add(e_clean)

        # 3. 界面当前后缀输入框中用户新配置/追加的扩展名（如果有）
        for e in normalize_extensions(self.ext_input.text()):
            if e:
                e_clean = e.lower().strip()
                if not e_clean.startswith("."):
                    e_clean = "." + e_clean
                all_tracked_exts.add(e_clean)

        # 增量更新严格按历史拉取过的后缀进行白名单过滤，绝不拉取无关或未曾收录过的后缀类型
        if all_tracked_exts:
            diff_allowed_exts = sorted(list(all_tracked_exts))
        else:
            diff_allowed_exts = sorted(list(set(e.lower() for e in DEFAULT_DOC_EXTENSIONS)))

        cached_exts = sorted(list(all_tracked_exts))
        filter_noise = self.chk_filter_noise.isChecked() if hasattr(self, "chk_filter_noise") else True

        self.active_worker = SvnDiffWorker(
            target, old_rev, new_rev, allowed_exts=diff_allowed_exts, filter_noise=filter_noise
        )
        self.active_worker.progress_signal.connect(lambda c, s: self.status_text.setText(f"🔄 [增量同步] {s}"))
        self.active_worker.finished_signal.connect(
            lambda ok, added, deleted, nrev, diff_msg: self.on_diff_finished(ok, added, deleted, nrev, diff_msg, silent=silent, cached_exts=cached_exts)
        )
        self.active_worker.start()

    def on_diff_finished(self, success, added, deleted, new_rev, msg, silent=False, cached_exts=None):
        self.set_ui_busy(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if success else 0)
        self.progress_bar.setFormat("100% 增量完成" if success else "增量失败")

        if not success:
            if not silent and "用户中断" not in msg:
                QMessageBox.critical(self, "增量同步失败", msg)
            else:
                self.status_text.setText(f"⚠️ [增量同步中断或失败]: {msg}")
            return

        target = self.url_combo.currentText().strip()
        path_set = set(self.memory_paths)

        if deleted:
            to_remove = set()
            for d in deleted:
                d_clean = d.rstrip("/")
                for p in path_set:
                    if p == d or p == d_clean or p.startswith(d_clean + "/"):
                        to_remove.add(p)
            path_set.difference_update(to_remove)

        for a in added:
            path_set.add(a)

        new_list = sorted(list(path_set))
        self.memory_paths = new_list
        self.current_cached_rev = new_rev

        # 提取更新后所有文件的扩展名全集保存进 meta，保证元数据永不丢失任何历史扩展名
        updated_exts = set()
        for p in new_list:
            _, ext = os.path.splitext(p)
            if ext:
                updated_exts.add(ext.lower())
        meta = self.index_mgr.get_meta(target)
        if meta:
            for e in meta.get("extensions", []):
                if e:
                    updated_exts.add(e.lower())
        if cached_exts:
            for e in cached_exts:
                if e:
                    updated_exts.add(e.lower())

        self.index_mgr.save_cache_lines(target, new_list, rev=new_rev, exts=sorted(list(updated_exts)))
        self.on_target_changed()

        elapsed = time.time() - self.search_start_time
        ext_desc = f"{len(updated_exts)} 种历史扩展名" if updated_exts else "全部文件"
        self.status_capsule.setText(
            f"📦 本地已载入内存: {len(new_list):,} 项 (范围: {ext_desc} | 版本: r{new_rev} 最新 ✅ | 同步耗时: {elapsed:.1f}s)"
        )
        self.status_capsule.setStyleSheet("color: #059669; font-weight: bold;")
        self.status_text.setText(
            f"✅ 增量更新完成！版本已升级至 r{new_rev} (新增 {len(added)} 项, 删除 {len(deleted)} 项)，耗时 {elapsed:.2f}s。"
        )

        # 若界面中已有关键词，重新秒搜刷新视图
        if self.kw_input.text().strip():
            self.start_memory_search()

        if not silent:
            QMessageBox.information(
                self, "增量更新成功",
                f"✅ 增量补丁合并完毕！\n"
                f"新增变动: {len(added)} 项\n"
                f"删除变动: {len(deleted)} 项\n"
                f"版本更新至: r{new_rev}\n"
                f"当前索引总数: {len(new_list):,} 项\n"
                f"耗时: {elapsed:.2f}s"
            )

    # ---------- 纯内存秒搜 (Everything 核心) ----------

    def start_memory_search(self):
        target = self.url_combo.currentText().strip()
        if not self.memory_paths:
            if os.path.exists(self.index_mgr.get_paths(target)[0]):
                self.memory_paths = self.index_mgr.load_cache_lines(target)
            if not self.memory_paths:
                QMessageBox.warning(self, "无本地索引", "当前仓库暂无本地索引缓存！\n请先点击【📥 全量拉取】构建索引。")
                return

        raw_kw = self.kw_input.text().strip()
        selected_ext_text = self.search_ext_combo.currentText().strip() if hasattr(self, 'search_ext_combo') else ""
        type_filter = self.type_combo.currentText()
        ext_filters = parse_search_ext_filter(selected_ext_text)

        if not raw_kw and not ext_filters and type_filter == "全部类型":
            if self.matched_paths:
                self.clear_results()
                self.status_text.setText("已重置条件。请输入搜索关键词，或指定后缀/类型进行检索。")
            else:
                QMessageBox.information(self, "提示", "请输入搜索关键词，或选择特定后缀/类型进行检索！")
            return

        self.save_target_history(target)
        t_start = time.perf_counter()

        case_sensitive = self.chk_case_sensitive.isChecked()
        filter_noise = self.chk_filter_noise.isChecked()

        scope = "directory" if self.scope_directory.isChecked() else ("fullpath" if self.scope_fullpath.isChecked() else "filename")
        word_boundary = self.chk_word_boundary.isChecked()

        keywords = raw_kw.split()
        if not case_sensitive:
            keywords = [k.lower() for k in keywords]

        mode = "all"
        if self.radio_any.isChecked():
            mode = "any"
        elif self.radio_exact.isChecked():
            mode = "exact"

        exact_phrase = raw_kw if case_sensitive else raw_kw.lower()

        matched = []
        for line in self.memory_paths:
            if filter_noise:
                line_low = line.lower()
                if any(pat in line_low for pat in DEFAULT_IGNORE_PATTERNS):
                    continue

            badge, name, parent, ext = FileTypeHelper.get_info(line)

            # 后缀过滤
            if ext_filters:
                if ext == "-":
                    continue
                name_low = name.lower()
                if not any(name_low.endswith(ef) for ef in ext_filters):
                    continue

            if type_filter != "全部类型":
                if type_filter == "文档":
                    if not ("文档" in badge or "项目计划" in badge or "PDF" in badge or "文本" in badge or "Markdown" in badge):
                        continue
                elif type_filter == "原理图/PCB":
                    if not any(k in badge for k in ["原理图", "PCB"]):
                        continue
                elif type_filter == "CAD工程图":
                    if not any(k in badge for k in ["CAD", "工程图", "3D模型"]):
                        continue
                elif type_filter == "思维导图":
                    if not ("思维导图" in badge or "导图" in badge):
                        continue
                elif type_filter not in badge:
                    continue

            # 匹配范围确定：仅文件名 / 仅目录 / 完整路径
            if scope == "filename":
                target_str = name
            elif scope == "directory":
                target_str = parent
            else:  # fullpath
                target_str = line

            if not case_sensitive:
                target_str = target_str.lower()

            # 关键词匹配 (支持独立词/全词匹配)
            if not keywords:
                matched.append(line)
            elif word_boundary:
                if mode == "all":
                    if all(re.search(r'(?<![a-zA-Z0-9])' + re.escape(k) + r'(?![a-zA-Z0-9])', target_str) for k in keywords):
                        matched.append(line)
                elif mode == "exact":
                    if re.search(r'(?<![a-zA-Z0-9])' + re.escape(exact_phrase) + r'(?![a-zA-Z0-9])', target_str):
                        matched.append(line)
                else:  # any
                    if any(re.search(r'(?<![a-zA-Z0-9])' + re.escape(k) + r'(?![a-zA-Z0-9])', target_str) for k in keywords):
                        matched.append(line)
            else:
                if mode == "all":
                    if all(k in target_str for k in keywords):
                        matched.append(line)
                elif mode == "exact":
                    if exact_phrase in target_str:
                        matched.append(line)
                else:  # any
                    if any(k in target_str for k in keywords):
                        matched.append(line)

        cost_ms = (time.perf_counter() - t_start) * 1000

        self.matched_paths = matched
        self.render_table(matched)

        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat(f"100% 秒搜完成 (用时 {cost_ms:.1f}ms)")

        self.metric_hit.setText(f"当前命中: {len(matched):,} 条")
        self.metric_time.setText(f"耗时: {cost_ms:.1f}ms")
        self.metric_speed.setText("速度: 内存极速")
        scope_name = "仅文件名" if scope == "filename" else ("仅所在目录" if scope == "directory" else "完整路径")
        ext_desc = f"，后缀限定: {', '.join(ext_filters)}" if ext_filters else ""
        self.status_text.setText(f"本地秒搜完成 (范围: {scope_name}{ext_desc})，在 {len(self.memory_paths):,} 项中命中 {len(matched):,} 条，耗时 {cost_ms:.1f} 毫秒。")

        self.export_btn.setEnabled(bool(matched))

    # ---------- 远程实时检索 (备用) ----------

    def start_remote_search(self):
        target = self.url_combo.currentText().strip()
        if not target:
            QMessageBox.information(self, "提示", "请填写目标 SVN 路径。")
            return

        raw_kw = self.kw_input.text().strip().split()
        selected_ext_text = self.search_ext_combo.currentText().strip() if hasattr(self, 'search_ext_combo') else ""
        type_filter = self.type_combo.currentText()
        ext_filters = parse_search_ext_filter(selected_ext_text)

        if not raw_kw and not ext_filters and type_filter == "全部类型":
            QMessageBox.information(self, "提示", "请输入搜索关键词，或选择特定后缀/类型！")
            return

        match_mode = "all" if self.radio_all.isChecked() else ("exact" if self.radio_exact.isChecked() else "any")
        scope = "directory" if self.scope_directory.isChecked() else ("fullpath" if self.scope_fullpath.isChecked() else "filename")
        word_boundary = self.chk_word_boundary.isChecked()
        case_sensitive = self.chk_case_sensitive.isChecked()
        filter_noise = self.chk_filter_noise.isChecked()

        self.save_target_history(target)
        self.set_ui_busy(True)

        self.matched_paths = []
        self.table_widget.setRowCount(0)
        self.table_widget.setSortingEnabled(False)

        estimated = len(self.memory_paths)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("远程检索中: 0%")
        self.status_text.setText("正在连接远程 SVN 仓库执行流式检索...")
        self.search_start_time = time.time()

        self.active_worker = SvnRemoteSearchWorker(
            target, raw_kw, match_mode, scope=scope, word_boundary=word_boundary,
            case_sensitive=case_sensitive, filter_noise=filter_noise,
            type_filter=type_filter, ext_filters=ext_filters, estimated_total=estimated
        )
        self.active_worker.items_matched.connect(self.on_remote_batch_matched)
        self.active_worker.progress_signal.connect(self.on_full_pull_progress)
        self.active_worker.finished_signal.connect(self.on_remote_search_finished)
        self.active_worker.start()

    def on_remote_batch_matched(self, batch):
        self.matched_paths.extend(batch)
        self.append_batch_to_table(batch)
        self.metric_hit.setText(f"当前命中: {len(self.matched_paths):,} 条")

    def on_remote_search_finished(self, success, msg):
        self.set_ui_busy(False)
        self.table_widget.setSortingEnabled(True)
        self.progress_bar.setValue(100 if success else 0)
        self.progress_bar.setFormat("100% 检索完成" if success else "检索中断/失败")

        elapsed = time.time() - self.search_start_time
        if success:
            self.status_text.setText(f"远程检索结束！共找到 {len(self.matched_paths):,} 条结果，用时 {elapsed:.1f}s。")
            self.export_btn.setEnabled(bool(self.matched_paths))
        else:
            if "用户中断" not in msg:
                QMessageBox.critical(self, "检索出错", msg)

    # ---------- 表格渲染与快速过滤 ----------

    def render_table(self, paths: list):
        self.table_widget.setSortingEnabled(False)
        self.table_widget.setUpdatesEnabled(False)
        self.table_widget.setRowCount(len(paths))
        target = self.url_combo.currentText().strip()

        for row, p in enumerate(paths):
            badge, name, parent, ext = FileTypeHelper.get_info(p)
            full_url = build_full_url(target, p)
            dir_url = build_dir_url(target, parent)

            item_idx = QTableWidgetItem(str(row + 1))
            item_idx.setTextAlignment(Qt.AlignCenter)

            item_badge = QTableWidgetItem(badge)
            item_badge.setTextAlignment(Qt.AlignCenter)

            item_name = QTableWidgetItem(name)
            font = item_name.font()
            font.setBold(True)
            item_name.setFont(font)
            item_name.setData(Qt.UserRole, full_url)
            item_name.setToolTip(f"完整 URL:\n{full_url}\n\n💡 提示：双击可直接定位所在目录，完整路径已自动复制。")

            # 优化路径显示：精简重复前缀，展示直观的相对目录层级（如 /01_系统需求/02_硬件规格/）
            if not parent or parent == "/":
                display_dir = "/"
            else:
                display_dir = f"/{parent.strip('/')}/"

            item_path = QTableWidgetItem(display_dir)
            item_path.setData(Qt.UserRole, dir_url)
            item_path.setToolTip(
                f"所在目录完整 URL:\n{dir_url}\n\n💡 提示：双击直接在 SVN Repo-Browser 中打开此目录；右键可复制完整路径。"
            )

            item_ext = QTableWidgetItem(ext)
            item_ext.setTextAlignment(Qt.AlignCenter)

            self.table_widget.setItem(row, 0, item_idx)
            self.table_widget.setItem(row, 1, item_badge)
            self.table_widget.setItem(row, 2, item_name)
            self.table_widget.setItem(row, 3, item_path)
            self.table_widget.setItem(row, 4, item_ext)

        self.table_widget.setUpdatesEnabled(True)
        self.table_widget.setSortingEnabled(True)
        self.update_counter_label(len(paths), len(paths))

    def append_batch_to_table(self, batch: list):
        self.table_widget.setUpdatesEnabled(False)
        start_row = self.table_widget.rowCount()
        self.table_widget.setRowCount(start_row + len(batch))
        target = self.url_combo.currentText().strip()

        for idx, p in enumerate(batch):
            row = start_row + idx
            badge, name, parent, ext = FileTypeHelper.get_info(p)
            full_url = build_full_url(target, p)
            dir_url = build_dir_url(target, parent)

            item_idx = QTableWidgetItem(str(row + 1))
            item_idx.setTextAlignment(Qt.AlignCenter)
            item_badge = QTableWidgetItem(badge)
            item_badge.setTextAlignment(Qt.AlignCenter)
            item_name = QTableWidgetItem(name)
            item_name.setData(Qt.UserRole, full_url)
            item_name.setToolTip(f"完整 URL:\n{full_url}\n\n💡 提示：双击可直接定位所在目录，完整路径已自动复制。")

            if not parent or parent == "/":
                display_dir = "/"
            else:
                display_dir = f"/{parent.strip('/')}/"

            item_path = QTableWidgetItem(display_dir)
            item_path.setData(Qt.UserRole, dir_url)
            item_path.setToolTip(
                f"所在目录完整 URL:\n{dir_url}\n\n💡 提示：双击直接在 SVN Repo-Browser 中打开此目录；右键可复制完整路径。"
            )
            item_ext = QTableWidgetItem(ext)
            item_ext.setTextAlignment(Qt.AlignCenter)

            self.table_widget.setItem(row, 0, item_idx)
            self.table_widget.setItem(row, 1, item_badge)
            self.table_widget.setItem(row, 2, item_name)
            self.table_widget.setItem(row, 3, item_path)
            self.table_widget.setItem(row, 4, item_ext)

        self.table_widget.setUpdatesEnabled(True)
        self.update_counter_label(self.table_widget.rowCount(), len(self.matched_paths))

    def filter_current_table(self, text: str):
        kw = text.strip()
        visible_count = 0
        self.table_widget.setUpdatesEnabled(False)
        scope = "directory" if self.scope_directory.isChecked() else ("fullpath" if self.scope_fullpath.isChecked() else "filename")
        word_boundary = self.chk_word_boundary.isChecked()
        case_sensitive = self.chk_case_sensitive.isChecked()

        for r in range(self.table_widget.rowCount()):
            if not kw:
                self.table_widget.setRowHidden(r, False)
                visible_count += 1
            else:
                name_item = self.table_widget.item(r, 2)
                path_item = self.table_widget.item(r, 3)
                full_url = name_item.data(Qt.UserRole) or ""
                dir_url = path_item.data(Qt.UserRole) or ""

                if scope == "filename":
                    check_target = name_item.text()
                elif scope == "directory":
                    check_target = f"{path_item.text()} {dir_url}"
                else:
                    check_target = f"{path_item.text()} {dir_url} {name_item.text()} {full_url}"

                target_str = check_target if case_sensitive else check_target.lower()
                query_str = kw if case_sensitive else kw.lower()

                if word_boundary:
                    hidden = not bool(re.search(r'(?<![a-zA-Z0-9])' + re.escape(query_str) + r'(?![a-zA-Z0-9])', target_str))
                else:
                    hidden = query_str not in target_str

                self.table_widget.setRowHidden(r, hidden)
                if not hidden:
                    visible_count += 1
        self.table_widget.setUpdatesEnabled(True)
        self.update_counter_label(visible_count, len(self.matched_paths))

    def update_counter_label(self, visible_count, total_count):
        self.table_counter_label.setText(f"显示 {visible_count:,} / 共 {total_count:,} 条")

    def on_filter_changed(self):
        if hasattr(self, 'memory_paths') and self.memory_paths:
            has_kw = bool(self.kw_input.text().strip())
            ext_text = self.search_ext_combo.currentText().strip() if hasattr(self, 'search_ext_combo') else ""
            has_ext = bool(parse_search_ext_filter(ext_text))
            has_type = (self.type_combo.currentText() != "全部类型") if hasattr(self, 'type_combo') else False
            if has_kw or has_ext or has_type or self.matched_paths:
                self.start_memory_search()

    # ---------- 表格交互：双击与右键菜单 ----------

    def on_table_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        name_item = self.table_widget.item(row, 2)
        full_url = name_item.data(Qt.UserRole) or name_item.text()
        file_name = name_item.text()
        path_item = self.table_widget.item(row, 3)
        dir_url = path_item.data(Qt.UserRole) or path_item.text()
        badge_item = self.table_widget.item(row, 1)
        is_dir = "目录" in (badge_item.text() if badge_item else "")

        # 1. 始终同步复制文件完整路径到剪贴板，方便用户需要贴给他人
        QApplication.clipboard().setText(full_url)

        # 2. 如果是本地已有物理文件，直接调用默认关联程序打开
        if os.path.exists(full_url):
            try:
                os.startfile(full_url)
                self.status_text.setText(f"🚀 已打开本地文件: {full_url} (路径已同步复制)")
                return
            except Exception:
                pass

        # 3. 核心功能：直接调起 TortoiseSVN 的 Repo-Browser 并定位到所在目录
        if full_url.startswith(("http://", "https://", "svn://")):
            # 双击文件时传入所在父级目录 (dir_url)；双击目录项时传入其自身
            target_browser_url = full_url if is_dir else dir_url
            ok = open_in_repo_browser(target_browser_url)
            if ok:
                if is_dir:
                    self.status_text.setText(f"🚀 已在 SVN Repo-Browser 中打开目录: {dir_url} (路径已同步复制)")
                else:
                    self.status_text.setText(
                        f"🚀 已在 Repo-Browser 打开所在目录！目标文件【{file_name}】位于该目录下 (完整URL已复制)"
                    )
            else:
                # 若未安装 TortoiseSVN，降级为系统默认网页浏览器打开
                clean_url = canonicalize_svn_url(target_browser_url)
                try:
                    os.startfile(clean_url)
                    self.status_text.setText(f"🌐 未检测到 TortoiseSVN，已在网页浏览器中打开: {clean_url} (路径已同步复制)")
                except Exception as e:
                    self.status_text.setText(f"📋 已复制路径到剪贴板: {full_url} (打开失败: {str(e)})")
        else:
            self.status_text.setText(f"📋 已复制路径到剪贴板: {full_url}")

    def show_context_menu(self, pos: QPoint):
        item = self.table_widget.itemAt(pos)
        if not item:
            return
        row = item.row()
        name_item = self.table_widget.item(row, 2)
        full_url = name_item.data(Qt.UserRole) or name_item.text()
        file_name = name_item.text()
        path_item = self.table_widget.item(row, 3)
        dir_url = path_item.data(Qt.UserRole) or path_item.text()
        badge_item = self.table_widget.item(row, 1)
        is_dir = "目录" in (badge_item.text() if badge_item else "")

        menu = QMenu(self)

        # 顶部优先提供单文件拉取与 Repo-Browser 功能
        if full_url.startswith(("http://", "https://", "svn://")):
            if not is_dir:
                act_pull_and_open = QAction(f"⚡ 单独拉取并直接打开此文件 ({file_name})", self)
                font_pull_open = act_pull_and_open.font()
                font_pull_open.setBold(True)
                act_pull_and_open.setFont(font_pull_open)
                act_pull_and_open.triggered.connect(
                    lambda: self.pull_single_file(full_url, save_as=False, open_after=True)
                )
                menu.addAction(act_pull_and_open)

                act_pull_save_as = QAction("📥 单独拉取此文件到本地 (另存为...)", self)
                act_pull_save_as.triggered.connect(
                    lambda: self.pull_single_file(full_url, save_as=True, open_after=False)
                )
                menu.addAction(act_pull_save_as)
                menu.addSeparator()

            act_repobrowser_dir = QAction("🚀 在 SVN Repo-Browser 中打开所在目录 (默认双击)", self)
            font_bold = act_repobrowser_dir.font()
            font_bold.setBold(True)
            act_repobrowser_dir.setFont(font_bold)
            act_repobrowser_dir.triggered.connect(lambda: self.open_url_in_repobrowser(dir_url, file_name=file_name))

            act_open_web_file = QAction("🌐 在系统网页浏览器中直接打开/下载此文件", self)
            act_open_web_file.triggered.connect(lambda: os.startfile(canonicalize_svn_url(full_url)))

            act_open_web_dir = QAction("🌐 在系统网页浏览器中打开所在目录", self)
            act_open_web_dir.triggered.connect(lambda: os.startfile(canonicalize_svn_url(dir_url)))

            menu.addAction(act_repobrowser_dir)
            if not is_dir:
                menu.addAction(act_open_web_file)
            menu.addAction(act_open_web_dir)
            menu.addSeparator()

        act_copy_full = QAction("📋 复制完整文件 URL", self)
        act_copy_full.triggered.connect(lambda: QApplication.clipboard().setText(full_url))

        act_copy_name = QAction("📄 复制文件名称", self)
        act_copy_name.triggered.connect(lambda: QApplication.clipboard().setText(file_name))

        act_copy_parent = QAction("📁 复制所在目录完整 URL", self)
        act_copy_parent.triggered.connect(lambda: QApplication.clipboard().setText(dir_url))

        act_copy_rel = QAction("📂 复制相对目录路径", self)
        act_copy_rel.triggered.connect(lambda: QApplication.clipboard().setText(path_item.text()))

        menu.addAction(act_copy_full)
        menu.addAction(act_copy_name)
        menu.addAction(act_copy_parent)
        menu.addAction(act_copy_rel)
        menu.addSeparator()

        if full_url.startswith(("http://", "https://", "svn://")):
            pass
        elif os.path.exists(full_url):
            act_open_file = QAction("🚀 打开此文件", self)
            act_open_file.triggered.connect(lambda: os.startfile(full_url))
            act_open_folder = QAction("📂 在文件资源管理器中定位", self)
            act_open_folder.triggered.connect(
                lambda: subprocess.Popen(f'explorer /select,"{os.path.normpath(full_url)}"')
            )
            menu.addAction(act_open_file)
            menu.addAction(act_open_folder)

        menu.exec_(self.table_widget.mapToGlobal(pos))

    def open_url_in_repobrowser(self, url: str, file_name: str = ""):
        """辅助方法：调起 Repo-Browser 并同步复制 URL"""
        QApplication.clipboard().setText(url)
        ok = open_in_repo_browser(url)
        if ok:
            if file_name:
                self.status_text.setText(
                    f"🚀 已在 Repo-Browser 打开所在目录！目标文件【{file_name}】位于该目录下 (完整URL已复制)"
                )
            else:
                self.status_text.setText(f"🚀 已调起 TortoiseSVN Repo-Browser 并打开目录: {url} (路径已同步复制)")
        else:
            clean_url = canonicalize_svn_url(url)
            try:
                os.startfile(clean_url)
                self.status_text.setText(f"🌐 未检测到 TortoiseProc.exe，已在网页浏览器中打开: {clean_url}")
            except Exception as e:
                QMessageBox.warning(
                    self, "提示",
                    f"未检测到 TortoiseSVN 客户端 (TortoiseProc.exe)。\n"
                    f"已将目标路径复制到剪贴板：\n{url}"
                )

    def pull_single_file(self, full_url: str, save_as: bool = False, open_after: bool = False):
        """单独拉取远程单个文件到本地（使用 svn export）"""
        if not full_url:
            return

        file_name = full_url.replace("\\", "/").rstrip("/").split("/")[-1]

        if save_as:
            default_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            default_save_path = os.path.join(default_dir, file_name)
            save_path, _ = QFileDialog.getSaveFileName(
                self, f"单独拉取并保存文件: {file_name}", default_save_path, "所有文件 (*.*)"
            )
            if not save_path:
                return
        else:
            # 存放到软件独立缓存的 pulled_files 目录
            cache_dl_dir = os.path.join(self.index_mgr.base_dir, "pulled_files")
            os.makedirs(cache_dl_dir, exist_ok=True)
            save_path = os.path.join(cache_dl_dir, file_name)

        self.status_capsule.setText(f"📥 正在拉取文件: {file_name}...")
        self.status_capsule.setStyleSheet("color: #2563EB; font-weight: bold;")
        self.status_text.setText(f"正在从 SVN 极速导出: {file_name} → {save_path}")

        export_worker = SvnExportWorker(full_url, save_path, open_after=open_after)

        def on_export_finished(ok, local_p, err, opened):
            if ok:
                self.status_capsule.setText(f"✅ 文件拉取成功: {file_name}")
                self.status_capsule.setStyleSheet("color: #059669; font-weight: bold;")
                self.status_text.setText(f"✅ 已成功拉取到: {local_p}")
                if save_as:
                    ret = QMessageBox.information(
                        self, "拉取成功",
                        f"文件已成功拉取并保存到本地：\n{local_p}\n\n是否立即在资源管理器中定位？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                    )
                    if ret == QMessageBox.Yes:
                        subprocess.Popen(f'explorer /select,"{os.path.normpath(local_p)}"')
            else:
                self.status_capsule.setText(f"❌ 文件拉取失败: {file_name}")
                self.status_capsule.setStyleSheet("color: #DC2626; font-weight: bold;")
                self.status_text.setText(f"❌ 拉取失败: {err}")
                QMessageBox.critical(self, "拉取失败", f"无法拉取文件【{file_name}】：\n{err}")

        export_worker.finished_signal.connect(on_export_finished)
        if not hasattr(self, "_active_export_workers"):
            self._active_export_workers = []
        self._active_export_workers.append(export_worker)
        export_worker.finished_signal.connect(
            lambda: self._active_export_workers.remove(export_worker) if export_worker in self._active_export_workers else None
        )
        export_worker.start()

    def start_append_ext_to_index(self):
        """仅拉取指定后缀（如 .mpp）并追加合并到当前索引库，不清除已有缓存"""
        target = self.url_combo.currentText().strip()
        if not target:
            QMessageBox.information(self, "提示", "请先选择或输入目标 SVN 仓库路径。")
            return

        raw_ext = self.ext_input.text().strip()
        target_exts = normalize_extensions(raw_ext)
        if not target_exts:
            QMessageBox.warning(self, "提示", "请在后缀输入框中填写要拉取的后缀，例如: .mpp")
            return

        self.save_target_history(target)
        self.set_ui_busy(True)

        self.progress_bar.setRange(0, 0)
        ext_str = ", ".join(target_exts)
        self.progress_bar.setFormat(f"正在追加拉取指定后缀: {ext_str}")
        self.status_capsule.setText(f"🔄 正在向索引库追加后缀: {ext_str}...")
        self.status_capsule.setStyleSheet("color: #2563EB; font-weight: bold;")
        self.status_text.setText(f"正在遍历远程仓库提取【{ext_str}】文件并合并到当前索引...")
        self.search_start_time = time.time()

        self.active_worker = SvnAppendExtWorker(
            target, target_exts, existing_paths=self.memory_paths
        )

        def on_append_progress(scanned, hits, current_p):
            disp = current_p if len(current_p) < 55 else "..." + current_p[-52:]
            self.status_text.setText(f"已扫描 {scanned:,} 项，发现目标后缀 {hits} 项: {disp}")
            self.progress_bar.setFormat(f"已扫描 {scanned:,} / 命中 {hits} 项")

        def on_append_finished(success, hit_lines, head_rev, msg):
            self.set_ui_busy(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100 if success else 0)
            self.progress_bar.setFormat("100% 追加完成" if success else "追加中断")

            if success:
                merged_set = set(self.memory_paths)
                for line in hit_lines:
                    merged_set.add(line)
                new_all = sorted(list(merged_set))
                self.memory_paths = new_all

                meta = self.index_mgr.get_meta(target) or {}
                all_exts = set(meta.get("extensions", []))
                for p in new_all:
                    _, ext = os.path.splitext(p)
                    if ext:
                        all_exts.add(ext.lower())
                for te in target_exts:
                    if te:
                        all_exts.add(te.lower())

                rev = head_rev or self.current_cached_rev
                self.index_mgr.save_cache_lines(target, new_all, rev=rev, exts=sorted(list(all_exts)))
                self.on_target_changed()

                elapsed = time.time() - self.search_start_time
                self.status_capsule.setText(
                    f"📦 本地已载入内存: {len(new_all):,} 项 (追加了 {len(hit_lines)} 个【{ext_str}】文件 ✅)"
                )
                self.status_capsule.setStyleSheet("color: #059669; font-weight: bold;")
                self.status_text.setText(f"✅ {msg} 用时 {elapsed:.1f}s。")

                if any(te.lstrip(".") in self.kw_input.text().lower() for te in target_exts):
                    self.start_memory_search()

                QMessageBox.information(
                    self, "追加拉取成功",
                    f"✅ 成功完成指定后缀拉取！\n\n"
                    f"目标后缀: {ext_str}\n"
                    f"新收录项: {len(hit_lines)} 项\n"
                    f"当前索引库总计: {len(new_all):,} 项\n"
                    f"耗时: {elapsed:.1f}s\n\n"
                    f"现在可以直接在秒搜中检索该后缀的所有文件了！"
                )
            else:
                if "用户中断" not in msg:
                    QMessageBox.critical(self, "追加拉取失败", msg)

        self.active_worker.progress_signal.connect(on_append_progress)
        self.active_worker.finished_signal.connect(on_append_finished)
        self.active_worker.start()

    def start_batch_export(self, target_repo: str, rel_file_list: list, dest_dir: str, ext_label: str = ""):
        """后台执行批量拉取指定后缀到本地文件夹"""
        total = len(rel_file_list)
        self.set_ui_busy(True)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"批量拉取中: 0/{total}")
        self.status_capsule.setText(f"📥 正在批量拉取 {total} 个【{ext_label}】文件到本地...")
        self.status_capsule.setStyleSheet("color: #2563EB; font-weight: bold;")
        self.status_text.setText(f"目标目录: {dest_dir}")

        worker = SvnBatchExportWorker(target_repo, rel_file_list, dest_dir)

        def on_batch_prog(idx, tot, fname):
            self.progress_bar.setValue(idx)
            self.progress_bar.setFormat(f"批量拉取中: {idx}/{tot} ({fname})")
            self.status_text.setText(f"正在下载 [{idx}/{tot}]: {fname}")

        def on_batch_done(success, count, out_dir, err):
            self.set_ui_busy(False)
            self.progress_bar.setValue(total)
            self.progress_bar.setFormat(f"100% 批量拉取完成 ({count}/{total})")

            if success:
                self.status_capsule.setText(f"✅ 批量拉取完成: 共 {count} 个文件")
                self.status_capsule.setStyleSheet("color: #059669; font-weight: bold;")
                self.status_text.setText(f"✅ 已成功将 {count} 个文件拉取到: {out_dir}")
                ret = QMessageBox.information(
                    self, "批量拉取完成",
                    f"✅ 成功拉取全部 {count} 个【{ext_label}】文件！\n"
                    f"保存目录: {out_dir}\n\n"
                    f"是否立即打开该文件夹？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                if ret == QMessageBox.Yes:
                    try:
                        os.startfile(out_dir)
                    except Exception:
                        pass
            else:
                self.status_capsule.setText(f"⚠️ 批量拉取中断: 已拉取 {count} 个")
                self.status_capsule.setStyleSheet("color: #DC2626; font-weight: bold;")
                self.status_text.setText(f"批量拉取中断: {err}")
                QMessageBox.warning(self, "提示", f"批量拉取中断或失败：\n{err}\n已拉取完成 {count} 个文件。")

        worker.progress_signal.connect(on_batch_prog)
        worker.finished_signal.connect(on_batch_done)

        if not hasattr(self, "_batch_workers"):
            self._batch_workers = []
        self._batch_workers.append(worker)
        worker.finished_signal.connect(lambda: self._batch_workers.remove(worker) if worker in self._batch_workers else None)
        worker.start()

    def show_pull_by_extension_dialog(self):
        """弹出对话框：支持批量拉取指定后缀所有文件到本地文件夹，或单独拉取"""
        from PyQt5.QtWidgets import QDialog, QListWidget, QListWidgetItem, QFileDialog
        target = self.url_combo.currentText().strip()
        dialog = QDialog(self)
        dialog.setWindowTitle("📥 批量拉取指定后缀文件到本地文件夹")
        dialog.resize(680, 430)

        vbox = QVBoxLayout(dialog)
        vbox.setSpacing(10)

        lbl = QLabel("请输入要拉取的扩展名后缀（如 .mpp、.docx、.pdf），可一键批量下载该后缀的所有文件：")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-weight: bold; color: #1E293B; font-size: 13px;")
        vbox.addWidget(lbl)

        # 快速后缀选择按钮行
        quick_ext_layout = QHBoxLayout()
        quick_ext_layout.addWidget(QLabel("常用后缀:"))
        for e_label, e_val in [
            ("⚡ .PrjPcb", ".prjpcb"),
            ("🔌 .SchDoc", ".schdoc"),
            ("⚡ .pcb", ".pcb"),
            ("🧠 .xmind", ".xmind"),
            ("📐 .dwg", ".dwg"),
            ("📊 .mpp", ".mpp"),
            ("📄 .docx", ".docx"),
            ("📊 .xlsx", ".xlsx"),
            ("📄 .pdf", ".pdf")
        ]:
            btn = QPushButton(e_label, dialog)
            btn.setObjectName("secondaryBtn")
            btn.clicked.connect(lambda _, val=e_val: input_edit.setText(val))
            quick_ext_layout.addWidget(btn)
        quick_ext_layout.addStretch()
        vbox.addLayout(quick_ext_layout)

        input_edit = QLineEdit(dialog)
        input_edit.setPlaceholderText("输入后缀名，例如: .mpp 或 mpp")
        input_edit.setText(".mpp")
        input_edit.setClearButtonEnabled(True)
        vbox.addWidget(input_edit)

        # 匹配状态与列表
        lbl_status = QLabel("正在检索匹配的文件...", dialog)
        lbl_status.setStyleSheet("font-weight: bold; color: #2563EB; font-size: 12px;")
        vbox.addWidget(lbl_status)

        list_widget = QListWidget(dialog)
        list_widget.setStyleSheet("""
            QListWidget {
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                background: #FFFFFF;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 4px 6px;
            }
            QListWidget::item:selected {
                background: #2563EB;
                color: #FFFFFF;
            }
        """)
        vbox.addWidget(list_widget, 1)

        def refresh_list(ext_text: str):
            raw = ext_text.strip()
            if not raw:
                list_widget.clear()
                lbl_status.setText("请输入后缀名。")
                btn_batch.setEnabled(False)
                return

            # 不区分大小写，并支持别名展开（例如输入 pcb 会自动匹配 .pcb, .pcbdoc, .pcblib, .prjpcb）
            target_exts = normalize_extensions(raw)
            clean_t = raw.lower().lstrip("*")
            if clean_t and not clean_t.startswith(".") and len(clean_t) <= 6 and "/" not in clean_t:
                single_ext = "." + clean_t
                if single_ext not in target_exts:
                    target_exts.append(single_ext)

            target_tuple = tuple(e.lower() for e in target_exts)

            list_widget.clear()
            if target_tuple:
                matched = [p for p in self.memory_paths if p.lower().endswith(target_tuple)]
            else:
                matched = [p for p in self.memory_paths if clean_t in p.lower()]

            for m in matched:
                _, name, parent, _ = FileTypeHelper.get_info(m)
                item = QListWidgetItem(f"{name}  (所在目录: {parent})")
                item.setData(Qt.UserRole, m)
                list_widget.addItem(item)

            ext_display = ", ".join(target_exts) if target_exts else clean_t
            if matched:
                lbl_status.setText(f"✅ 在当前仓库索引中找到 {len(matched)} 个【{ext_display}】文件（完全不区分大小写）：")
                lbl_status.setStyleSheet("color: #059669; font-weight: bold; font-size: 12px;")
                btn_batch.setEnabled(True)
                btn_batch.setText(f"📥 批量拉取这 {len(matched)} 个【{ext_display}】文件到本地文件夹...")
            else:
                lbl_status.setText(f"⚠️ 当前本地索引中暂未找到【{ext_display}】文件。若远程仓库已有该文件，点击【➕ 将此后缀追加到搜索索引】即可从远程同步。")
                lbl_status.setStyleSheet("color: #D97706; font-weight: bold; font-size: 12px;")
                btn_batch.setEnabled(False)
                btn_batch.setText("📥 批量拉取该后缀文件到本地...")

        input_edit.textChanged.connect(refresh_list)

        # 操作按钮区
        btn_box = QHBoxLayout()

        btn_append_index = QPushButton("➕ 将此后缀追加到搜索索引", dialog)
        btn_append_index.setObjectName("secondaryBtn")
        btn_append_index.setToolTip("从远程仓库遍历并把此后缀文件收录到本地索引库中")
        btn_box.addWidget(btn_append_index)

        btn_box.addStretch()

        btn_pull_single = QPushButton("⚡ 单独拉取并打开选中项", dialog)
        btn_pull_single.setObjectName("secondaryBtn")
        btn_box.addWidget(btn_pull_single)

        btn_batch = QPushButton("📥 批量拉取所有该后缀文件到本地...", dialog)
        btn_batch.setStyleSheet("background-color: #2563EB; color: white; padding: 6px 18px; border-radius: 4px; font-weight: bold;")
        btn_box.addWidget(btn_batch)

        btn_cancel = QPushButton("关闭", dialog)
        btn_box.addWidget(btn_cancel)
        vbox.addLayout(btn_box)

        btn_cancel.clicked.connect(dialog.reject)

        def do_append():
            ext = input_edit.text().strip()
            if not ext:
                return
            self.ext_input.setText(ext)
            dialog.accept()
            self.start_append_ext_to_index()

        btn_append_index.clicked.connect(do_append)

        def do_pull_single():
            sel = list_widget.currentItem()
            if not sel:
                QMessageBox.warning(dialog, "提示", "请在上方列表中选中一个具体文件。")
                return
            rel_p = sel.data(Qt.UserRole)
            full_url = build_full_url(target, rel_p)
            dialog.accept()
            self.pull_single_file(full_url, save_as=False, open_after=True)

        btn_pull_single.clicked.connect(do_pull_single)
        list_widget.itemDoubleClicked.connect(lambda _: do_pull_single())

        def do_batch_pull():
            matched_items = [list_widget.item(i).data(Qt.UserRole) for i in range(list_widget.count())]
            if not matched_items:
                QMessageBox.warning(dialog, "提示", "当前没有可拉取的文件。")
                return

            ext = input_edit.text().strip()
            default_dir = os.path.join(os.path.expanduser("~"), "Downloads", f"svn_{ext.lstrip('.')}_files")
            dest_dir = QFileDialog.getExistingDirectory(dialog, f"选择保存所有【{ext}】文件的本地文件夹", default_dir)
            if not dest_dir:
                return

            dialog.accept()
            self.start_batch_export(target, matched_items, dest_dir, ext)

        btn_batch.clicked.connect(do_batch_pull)

        # 初始化刷新
        refresh_list(".mpp")
        dialog.exec_()

    def show_pull_single_file_dialog(self):
        """兼容别名"""
        self.show_pull_by_extension_dialog()

    # ---------- 导出与清理 ----------

    def export_results(self):
        target = self.url_combo.currentText().strip()
        kw = self.kw_input.text().strip()
        mode_str = "全部匹配 (AND)" if self.radio_all.isChecked() else ("精确短语" if self.radio_exact.isChecked() else "任一匹配 (OR)")
        meta = {
            "target": target,
            "keywords": kw,
            "mode": mode_str
        }
        ExportHelper.export(self, self.matched_paths, meta)

    def clear_results(self):
        self.matched_paths = []
        self.table_widget.setRowCount(0)
        self.update_counter_label(0, 0)
        self.export_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("待命就绪")
        self.status_text.setText("状态: 已清空结果列表")
        self.metric_hit.setText("当前命中: 0 条")
        self.metric_time.setText("耗时: -")
        self.metric_speed.setText("处理速度: -")

    # ---------- 状态与终止控制 ----------

    def set_ui_busy(self, busy: bool):
        self.local_search_btn.setEnabled(not busy)
        self.remote_search_btn.setEnabled(not busy)
        self.full_pull_btn.setEnabled(not busy)
        self.inc_update_btn.setEnabled(not busy)
        self.check_rev_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        if busy:
            self.export_btn.setEnabled(False)

    def stop_active_task(self):
        if self.active_worker and self.active_worker.isRunning():
            self.status_text.setText("正在请求终止任务...")
            self.active_worker.stop()

    def closeEvent(self, event):
        if self.active_worker and self.active_worker.isRunning():
            self.active_worker.stop()
            self.active_worker.wait(1000)
        event.accept()


# =====================================================================
# 8. 入口函数
# =====================================================================

def main():
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    window = ModernSvnSearchApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()