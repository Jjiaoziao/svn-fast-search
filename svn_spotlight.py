# -*- coding: utf-8 -*-
"""
SVN 极速搜索 - Spotlight 极简悬浮搜索测试版
特性：
1. 全局快捷键随时唤醒与隐藏（默认 Alt+Q，支持自由自定义配置）；
2. 极简悬浮居中搜索框，右侧提供【💻 电子】、【📐 结构】、【🛠 工艺】三部门一键切换；
3. 初始状态仅显示单行紧凑搜索条，按回车或输入后自适应向下展开结果列表；
4. 键盘上下键选中、回车或双击直接调起 TortoiseSVN Repo-Browser 打开所在目录，并同步复制完整路径；
5. 按 Esc 或再次按下快捷键极速隐藏收起，常驻系统托盘，内存占用极低。
"""

import sys
import os
import re
import time
import winreg
import ctypes
import ctypes.wintypes
import win32con

from PyQt5.QtCore import (
    Qt, QPoint, QSettings, pyqtSignal, QAbstractNativeEventFilter, QTimer, QEvent
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMenu, QAction, QDialog, QComboBox,
    QGraphicsDropShadowEffect, QSystemTrayIcon, QMessageBox, QFrame
)
from PyQt5.QtGui import QIcon, QFont, QColor

def get_resource_path(relative_path: str) -> str:
    """获取资源文件绝对路径（兼容 PyInstaller 单文件打包模式）"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, relative_path)


_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# 引入核心辅助函数与索引管理引擎（复用 svn_search.py）
from svn_search import (
    canonicalize_svn_url,
    open_in_repo_browser,
    build_full_url,
    build_dir_url,
    FileTypeHelper,
    SvnIndexManager,
    DEFAULT_PRESET_REPOS,
    SvnExportWorker,
    normalize_extensions,
    SvnDiffWorker,
    SvnInfoWorker,
    find_svn_exe
)


# =====================================================================
# 1. 部门仓库配置映射
# =====================================================================

DEPARTMENTS = [
    {
        "id": "elec",
        "name": "💻 电子",
        "full_name": "电子中心 (Electronic trunk)",
        "url": "https://dell740-svn/svn/ElectronicCentreRepository/Electronic trunk"
    },
    {
        "id": "phys",
        "name": "📐 结构",
        "full_name": "结构设计 (PhysicalDesign)",
        "url": "https://dell740-svn/svn/PhysicalDesignRepositories"
    },
    {
        "id": "proc",
        "name": "🛠 工艺",
        "full_name": "工艺设计 (ProcessDesign)",
        "url": "https://dell740-svn/svn/ProcessDesignRepositories"
    }
]


# =====================================================================
# 2. Windows 全局快捷键管理器 (基于原生 Win32 API)
# =====================================================================

HOTKEY_MODIFIERS = {
    "Alt": 0x0001,
    "Ctrl": 0x0002,
    "Shift": 0x0004,
    "Win": 0x0008,
}

HOTKEY_PRESETS = [
    ("Alt + Q (推荐, 冲突极低)", "Alt", "Q"),
    ("Ctrl + Shift + F (查找惯用)", "Ctrl+Shift", "F"),
    ("Ctrl + Shift + Space (仿Spotlight)", "Ctrl+Shift", "Space"),
    ("Alt + S (Search快捷)", "Alt", "S"),
    ("Ctrl + Alt + S", "Ctrl+Alt", "S"),
]


def parse_hotkey_string(hk_str: str):
    """解析如 'Alt+Q' 或 'Ctrl + Shift + F' 为 (mod_flags, vk_code)"""
    parts = [p.strip() for p in hk_str.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return 0x0001, ord("Q")

    mod_flag = 0x4000  # MOD_NOREPEAT 避免长按重复触发
    key_part = parts[-1].upper()

    for p in parts[:-1]:
        p_up = p.upper()
        if p_up in ("CTRL", "CONTROL"):
            mod_flag |= 0x0002
        elif p_up == "ALT":
            mod_flag |= 0x0001
        elif p_up == "SHIFT":
            mod_flag |= 0x0004
        elif p_up in ("WIN", "META"):
            mod_flag |= 0x0008

    SPECIAL_VK = {
        "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D,
        "ESC": 0x1B, "ESCAPE": 0x1B,
        "`": 0xC0, "~": 0xC0, "-": 0xBD, "=": 0xBB,
        ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA,
        "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC
    }

    if key_part in SPECIAL_VK:
        vk = SPECIAL_VK[key_part]
    elif len(key_part) == 1 and "A" <= key_part <= "Z":
        vk = ord(key_part)
    elif len(key_part) == 1 and "0" <= key_part <= "9":
        vk = ord(key_part)
    elif key_part.startswith("F") and key_part[1:].isdigit():
        f_num = int(key_part[1:])
        vk = 0x70 + (f_num - 1)
    else:
        vk = ord("Q")

    return mod_flag, vk


WM_SPOTLIGHT_WAKE = ctypes.windll.user32.RegisterWindowMessageW("SvnSpotlightWakeUpMessage")


class WinHotKeyFilter(QAbstractNativeEventFilter):
    """拦截系统 WM_HOTKEY 消息与多开唤醒广播消息"""
    def __init__(self, hotkey_id: int, on_hotkey, on_wake=None):
        super().__init__()
        self.hotkey_id = hotkey_id
        self.on_hotkey = on_hotkey
        self.on_wake = on_wake

    def nativeEventFilter(self, eventType, message):
        if eventType == "windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(message.__int__())
            if msg.message == win32con.WM_HOTKEY and msg.wParam == self.hotkey_id:
                self.on_hotkey()
                return True, 0
            elif WM_SPOTLIGHT_WAKE and msg.message == WM_SPOTLIGHT_WAKE:
                if self.on_wake:
                    self.on_wake()
                return True, 0
        return False, 0


# =====================================================================
# 3. Windows 开机自启注册表管理
# =====================================================================

REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_APP_NAME = "SvnSpotlightSearch"


def is_autostart_enabled() -> bool:
    """检查是否已设置开机自启"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, REG_APP_NAME)
        winreg.CloseKey(key)
        return bool(val)
    except Exception:
        return False


def set_autostart_enabled(enable: bool) -> bool:
    """设置或取消开机自启动"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE)
        if enable:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, REG_APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, REG_APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"设置开机自启失败: {e}")
        return False



# =====================================================================
# 3. 快捷键设置与键盘直接录制控件
# =====================================================================

class HotkeyRecorderEdit(QLineEdit):
    """支持在键盘上按下任意组合键直接录制的输入框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("👉 请直接在此按下键盘按键 (如 Ctrl+Alt+K, Alt+Space...)")
        self.setStyleSheet("""
            QLineEdit {
                background-color: #F8FAFC;
                border: 2px dashed #94A3B8;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 15px;
                font-weight: bold;
                color: #1E40AF;
                text-align: center;
            }
            QLineEdit:focus {
                background-color: #EFF6FF;
                border: 2px solid #2563EB;
                color: #1D4ED8;
            }
        """)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            mod_strs = []
            if modifiers & Qt.ControlModifier: mod_strs.append("Ctrl")
            if modifiers & Qt.AltModifier: mod_strs.append("Alt")
            if modifiers & Qt.ShiftModifier: mod_strs.append("Shift")
            if modifiers & Qt.MetaModifier: mod_strs.append("Win")
            if mod_strs:
                self.setText(" + ".join(mod_strs) + " + ...")
            event.accept()
            return

        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            self.clear()
            event.accept()
            return

        parts = []
        if modifiers & Qt.ControlModifier: parts.append("Ctrl")
        if modifiers & Qt.AltModifier: parts.append("Alt")
        if modifiers & Qt.ShiftModifier: parts.append("Shift")
        if modifiers & Qt.MetaModifier: parts.append("Win")

        key_name = ""
        if Qt.Key_A <= key <= Qt.Key_Z:
            key_name = chr(key)
        elif Qt.Key_0 <= key <= Qt.Key_9:
            key_name = chr(key)
        elif Qt.Key_F1 <= key <= Qt.Key_F24:
            key_name = f"F{key - Qt.Key_F1 + 1}"
        elif key == Qt.Key_Space:
            key_name = "Space"
        elif key == Qt.Key_Tab:
            key_name = "Tab"
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            key_name = "Enter"
        elif key == Qt.Key_Escape:
            key_name = "Esc"
        elif key == Qt.Key_QuoteLeft:
            key_name = "`"
        elif key == Qt.Key_Minus:
            key_name = "-"
        elif key == Qt.Key_Equal:
            key_name = "="
        elif key == Qt.Key_Comma:
            key_name = ","
        elif key == Qt.Key_Period:
            key_name = "."
        elif key == Qt.Key_Slash:
            key_name = "/"
        elif key == Qt.Key_Semicolon:
            key_name = ";"
        elif key == Qt.Key_Apostrophe:
            key_name = "'"
        elif key == Qt.Key_BracketLeft:
            key_name = "["
        elif key == Qt.Key_BracketRight:
            key_name = "]"
        elif key == Qt.Key_Backslash:
            key_name = "\\"

        if key_name:
            if not parts and not key_name.startswith("F"):
                parts.append("Alt")
            parts.append(key_name)
            self.setText(" + ".join(parts))
            event.accept()
        else:
            super().keyPressEvent(event)


class HotkeySettingsDialog(QDialog):
    """自定义快捷键设置窗口"""
    def __init__(self, current_hotkey: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙ 自定义全局唤醒快捷键")
        self.resize(440, 260)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.current_hotkey = current_hotkey
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        tip_lbl = QLabel(
            "<b>自定义唤醒 / 隐藏全局快捷键</b><br>"
            "<span style='color: #64748B; font-size: 12px;'>"
            "请点击下方虚线框，直接在键盘上按下您喜欢的按键组合（支持 Ctrl、Alt、Shift、Win、F1~F12 等任意组合）："
            "</span>"
        )
        tip_lbl.setTextFormat(Qt.RichText)
        tip_lbl.setWordWrap(True)
        layout.addWidget(tip_lbl)

        # 键盘直接录制框
        self.recorder_edit = HotkeyRecorderEdit(self)
        self.recorder_edit.setText(self.current_hotkey)
        layout.addWidget(self.recorder_edit)

        # 推荐预设快捷选项
        preset_box = QHBoxLayout()
        preset_lbl = QLabel("或选用常用预设:")
        preset_lbl.setStyleSheet("color: #475569; font-size: 12px;")
        preset_box.addWidget(preset_lbl)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("── 点击可直接选用推荐预设 ──", "")
        for label, mod, k in HOTKEY_PRESETS:
            val = f"{mod} + {k}" if "+" not in mod else f"{' + '.join(mod.split('+'))} + {k}"
            self.preset_combo.addItem(label, val)
        self.preset_combo.currentIndexChanged.connect(self.on_preset_selected)
        preset_box.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_box)

        # 底部操作按钮
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("保存生效")
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #2563EB; color: white; font-weight: bold;
                padding: 7px 20px; border-radius: 6px; font-size: 13px;
            }
            QPushButton:hover { background-color: #1D4ED8; }
        """)
        self.save_btn.clicked.connect(self.on_save_clicked)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #F1F5F9; color: #334155; border: 1px solid #CBD5E1;
                padding: 7px 16px; border-radius: 6px; font-size: 13px;
            }
            QPushButton:hover { background-color: #E2E8F0; }
        """)
        self.cancel_btn.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

    def on_preset_selected(self, idx: int):
        val = self.preset_combo.currentData()
        if val:
            self.recorder_edit.setText(val)

    def on_save_clicked(self):
        val = self.recorder_edit.text().strip()
        if not val or "..." in val:
            QMessageBox.warning(self, "按键不完整", "请在录制框中按下完整的快捷键组合（例如 Alt + Q 或 Ctrl + Alt + F）！")
            return
        self.accept()

    def get_selected_hotkey(self):
        return self.recorder_edit.text().strip()



# =====================================================================
# 4. Spotlight 极简悬浮搜索主窗口
# =====================================================================

class SpotlightSearchWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("SvnTools", "SvnSpotlight")
        self.index_mgr = SvnIndexManager()

        # 状态记录
        self.current_dept_idx = 0
        self.memory_paths = []
        self.matched_paths = []
        self.current_target_url = DEPARTMENTS[0]["url"]
        self.info_worker = None
        self.diff_worker = None

        # 热键配置
        self.hotkey_id = 9527
        self.hotkey_str = self.settings.value("global_hotkey", "Alt+Q", type=str)
        self.hotkey_registered = False

        # 窗口拖拽移动与位置记忆
        self._drag_pos = None
        self.saved_x = self.settings.value("window_x", None, type=int)
        self.saved_y = self.settings.value("window_y", None, type=int)

        self.init_window_attrs()
        self.init_ui()
        self.init_hotkey()
        self.init_tray()

        # 加载初始部门缓存
        self.switch_department(0, trigger_search=False)

    def init_window_attrs(self):
        """窗口外观属性设置：无边框、置顶、半透明圆角"""
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(920, 68)

    def init_ui(self):
        # 根布局：带阴影的悬浮外层容器
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)

        self.card_container = QFrame(self)
        self.card_container.setObjectName("spotlightContainer")
        self.card_container.setStyleSheet("""
            QFrame#spotlightContainer {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 12px;
            }
        """)

        # 柔和的现代投影效果
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 6)
        self.card_container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(self.card_container)
        container_layout.setContentsMargins(14, 10, 14, 10)
        container_layout.setSpacing(8)

        # -------------------------------------------------------------
        # 顶部搜索条 (Search Bar)
        # -------------------------------------------------------------
        search_bar = QHBoxLayout()
        search_bar.setSpacing(8)

        # 拖拽把手图标（提示用户可鼠标按住任意拖动窗口）
        self.drag_handle = QLabel("⠿")
        self.drag_handle.setCursor(Qt.SizeAllCursor)
        self.drag_handle.setToolTip("按住此处拖拽移动窗口位置")
        self.drag_handle.setStyleSheet("font-size: 16px; color: #94A3B8; padding-right: 2px;")
        search_bar.addWidget(self.drag_handle)

        # 放大镜图标
        self.icon_label = QLabel("🔍")
        self.icon_label.setStyleSheet("font-size: 17px; color: #64748B;")
        self.icon_label.setCursor(Qt.SizeAllCursor)
        self.icon_label.setToolTip("按住此处拖拽移动窗口位置")
        search_bar.addWidget(self.icon_label)

        # 核心搜索框
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键词搜索... (支持空格多词，按 Enter 检索，Esc 退出)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet("""
            QLineEdit {
                border: none;
                background: transparent;
                font-size: 15px;
                color: #0F172A;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                selection-background-color: #2563EB;
            }
        """)
        self.search_input.returnPressed.connect(self.trigger_search)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        search_bar.addWidget(self.search_input, 1)

        # 搜索范围切换胶囊 (单独搜文件名 / 单独搜路径 / 全路径)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("📄 搜文件名", "filename")
        self.scope_combo.addItem("📁 搜所在路径", "directory")
        self.scope_combo.addItem("🌐 全路径匹配", "fullpath")
        self.scope_combo.setCursor(Qt.PointingHandCursor)
        self.scope_combo.setToolTip("搜索范围：仅搜文件名（推荐）、仅搜所在路径目录，或全路径匹配")
        self.scope_combo.setStyleSheet("""
            QComboBox {
                background-color: #F1F5F9;
                color: #334155;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 500;
            }
            QComboBox:hover {
                background-color: #E2E8F0;
                color: #0F172A;
                border-color: #94A3B8;
            }
            QComboBox::drop-down {
                border: none;
                width: 14px;
            }
            QComboBox QAbstractItemView {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                selection-background-color: #DBEAFE;
                selection-color: #1E3A8A;
                padding: 4px;
                outline: none;
            }
        """)
        saved_scope = self.settings.value("search_scope", "filename", type=str)
        for i in range(self.scope_combo.count()):
            if self.scope_combo.itemData(i) == saved_scope:
                self.scope_combo.setCurrentIndex(i)
                break
        self.scope_combo.currentIndexChanged.connect(self.on_scope_changed)
        search_bar.addWidget(self.scope_combo)

        # 快捷后缀/类型筛选胶囊
        self.ext_combo = QComboBox()
        self.ext_combo.addItem("🏷 全部后缀", "")
        self.ext_combo.addItem("⚡ 原理图/PCB", "eda")
        self.ext_combo.addItem("📐 CAD图纸", "cad")
        self.ext_combo.addItem("🧠 思维导图", "xmind")
        self.ext_combo.addItem("📊 项目计划", "mpp")
        self.ext_combo.addItem("📊 表格/Excel", "excel")
        self.ext_combo.addItem("📄 文档/Word", "word")
        self.ext_combo.addItem("📄 PDF文档", "pdf")
        self.ext_combo.addItem("💻 代码/脚本", "code")
        self.ext_combo.setCursor(Qt.PointingHandCursor)
        self.ext_combo.setToolTip("快速筛选指定文件后缀或类型（支持在搜索框内直接输入 ext:xxx 或 *.xxx）")
        self.ext_combo.setStyleSheet("""
            QComboBox {
                background-color: #F1F5F9;
                color: #334155;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 500;
            }
            QComboBox:hover {
                background-color: #E2E8F0;
                color: #0F172A;
                border-color: #94A3B8;
            }
            QComboBox::drop-down {
                border: none;
                width: 14px;
            }
            QComboBox QAbstractItemView {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                selection-background-color: #DBEAFE;
                selection-color: #1E3A8A;
                padding: 4px;
                outline: none;
            }
        """)
        self.ext_combo.currentIndexChanged.connect(self.on_ext_combo_changed)
        search_bar.addWidget(self.ext_combo)


        # 部门胶囊切换按钮组 (电子 / 结构 / 工艺)
        dept_layout = QHBoxLayout()
        dept_layout.setSpacing(4)
        self.dept_buttons = []

        for idx, dept in enumerate(DEPARTMENTS):
            btn = QPushButton(dept["name"])
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"切换到【{dept['full_name']}】索引")
            btn.clicked.connect(lambda checked, i=idx: self.switch_department(i, trigger_search=True))
            self.dept_buttons.append(btn)
            dept_layout.addWidget(btn)

        search_bar.addLayout(dept_layout)

        # 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #E2E8F0;")
        search_bar.addWidget(sep)

        # 增量同步更新按钮 (极简轻量，按需检查并同步最新提交)
        self.sync_btn = QPushButton("🔄")
        self.sync_btn.setCursor(Qt.PointingHandCursor)
        self.sync_btn.setToolTip("检查并增量更新当前仓库索引 (svn diff)")
        self.sync_btn.setStyleSheet("""
            QPushButton {
                border: none; background: transparent; font-size: 14px; color: #64748B; padding: 4px;
            }
            QPushButton:hover { color: #2563EB; }
        """)
        self.sync_btn.clicked.connect(self.trigger_incremental_sync)
        search_bar.addWidget(self.sync_btn)

        # 快捷键设置按钮
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setToolTip("设置全局唤醒快捷键")
        self.settings_btn.setStyleSheet("""
            QPushButton {
                border: none; background: transparent; font-size: 15px; color: #64748B; padding: 4px;
            }
            QPushButton:hover { color: #2563EB; }
        """)
        self.settings_btn.clicked.connect(self.open_hotkey_settings)
        search_bar.addWidget(self.settings_btn)

        # 退出/关闭按钮
        self.close_btn = QPushButton("✕")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip("隐藏搜索框 (Esc)")
        self.close_btn.setStyleSheet("""
            QPushButton {
                border: none; background: transparent; font-size: 14px; color: #94A3B8; padding: 4px;
            }
            QPushButton:hover { color: #EF4444; }
        """)
        self.close_btn.clicked.connect(self.hide_spotlight)
        search_bar.addWidget(self.close_btn)

        container_layout.addLayout(search_bar)

        # -------------------------------------------------------------
        # 结果区域容器 (默认隐藏，点击回车搜索后展开)
        # -------------------------------------------------------------
        self.results_container = QWidget()
        results_layout = QVBoxLayout(self.results_container)
        results_layout.setContentsMargins(0, 6, 0, 0)
        results_layout.setSpacing(6)

        # 二次筛选与即时过滤栏
        subfilter_row = QHBoxLayout()
        subfilter_row.setSpacing(8)

        self.filter_icon = QLabel("🎯 二次筛选:")
        self.filter_icon.setStyleSheet("font-size: 12px; font-weight: bold; color: #475569;")
        self.filter_icon.setCursor(Qt.SizeAllCursor)
        self.filter_icon.setToolTip("按住此处拖拽移动窗口")
        subfilter_row.addWidget(self.filter_icon)

        self.subfilter_input = QLineEdit()
        self.subfilter_input.setPlaceholderText("在当前匹配结果中即时过滤... (输入关键词实时筛选，支持空格多词)")
        self.subfilter_input.setClearButtonEnabled(True)
        self.subfilter_input.setStyleSheet("""
            QLineEdit {
                background-color: #F8FAFC;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                color: #0F172A;
            }
            QLineEdit:focus {
                background-color: #FFFFFF;
                border-color: #2563EB;
            }
        """)
        self.subfilter_input.textChanged.connect(self.filter_current_table)
        subfilter_row.addWidget(self.subfilter_input, 1)

        self.counter_label = QLabel("待命就绪")
        self.counter_label.setStyleSheet("color: #2563EB; font-weight: bold; font-size: 12px;")
        self.counter_label.setCursor(Qt.SizeAllCursor)
        self.counter_label.setToolTip("按住此处拖拽移动窗口")
        subfilter_row.addWidget(self.counter_label)

        self.tip_label = QLabel("💡 双击直达 Repo-Browser")
        self.tip_label.setStyleSheet("color: #64748B; font-size: 11px;")
        self.tip_label.setCursor(Qt.SizeAllCursor)
        self.tip_label.setToolTip("按住此处拖拽移动窗口")
        subfilter_row.addWidget(self.tip_label)

        results_layout.addLayout(subfilter_row)

        # 检索结果表格
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["类型", "文件名称", "所在目录", "格式"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.horizontalHeader().resizeSection(1, 280)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                background-color: #FFFFFF;
                gridline-color: #F1F5F9;
                selection-background-color: #DBEAFE;
                selection-color: #1E3A8A;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #F8FAFC;
                color: #475569;
                font-weight: bold;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                padding: 6px 8px;
            }
        """)
        self.table.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        results_layout.addWidget(self.table)
        container_layout.addWidget(self.results_container)

        root_layout.addWidget(self.card_container)
        self.setLayout(root_layout)

        # 初始只呈现搜索条，隐藏下方结果区域
        self.results_container.setVisible(False)
        self.update_dept_buttons_style()

        # 安装拖拽事件过滤器：用户在把手、图标、卡片空白处或顶栏按下鼠标均可拖动窗口
        for w in (self.card_container, self.drag_handle, self.icon_label, self.filter_icon, self.counter_label, self.tip_label):
            w.installEventFilter(self)


    # ---------- 全局快捷键与托盘 ----------

    def init_hotkey(self):
        """注册全局快捷键与多开唤醒过滤"""
        self.hotkey_filter = WinHotKeyFilter(
            self.hotkey_id,
            on_hotkey=self.toggle_spotlight,
            on_wake=self.show_spotlight
        )
        QApplication.instance().installNativeEventFilter(self.hotkey_filter)
        self.register_global_hotkey(self.hotkey_str)

    def register_global_hotkey(self, hk_str: str) -> bool:
        """调用 Win32 API 注册全局热键"""
        hwnd = int(self.winId())
        if self.hotkey_registered:
            try:
                ctypes.windll.user32.UnregisterHotKey(hwnd, self.hotkey_id)
            except Exception:
                pass
            self.hotkey_registered = False

        mod_flags, vk_code = parse_hotkey_string(hk_str)
        ok = ctypes.windll.user32.RegisterHotKey(hwnd, self.hotkey_id, mod_flags, vk_code)
        if ok:
            self.hotkey_registered = True
            self.hotkey_str = hk_str
            self.settings.setValue("global_hotkey", hk_str)
            return True
        else:
            return False

    def init_tray(self):
        """系统托盘图标与常驻运行"""
        self.tray = QSystemTrayIcon(self)
        icon_path = get_resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            self.tray.setIcon(app_icon)
            self.setWindowIcon(app_icon)
        else:
            app_icon = self.style().standardIcon(QApplication.style().SP_FileDialogContentsView)
            self.tray.setIcon(app_icon)
            self.setWindowIcon(app_icon)

        tray_menu = QMenu()
        self.act_show = QAction(f"🔍 唤醒搜索 ({self.hotkey_str})", self)
        self.act_show.triggered.connect(self.show_spotlight)
        act_sync = QAction("🔄 增量同步更新", self)
        act_sync.triggered.connect(self.trigger_incremental_sync)

        act_setting = QAction("⚙ 快捷键设置...", self)
        act_setting.triggered.connect(self.open_hotkey_settings)

        self.act_autostart = QAction("🚀 开机自启动", self, checkable=True)
        self.act_autostart.setChecked(is_autostart_enabled())
        self.act_autostart.toggled.connect(self.toggle_autostart)

        act_quit = QAction("✕ 退出程序", self)
        act_quit.triggered.connect(self.quit_app)

        tray_menu.addAction(self.act_show)
        tray_menu.addAction(act_sync)
        tray_menu.addAction(act_setting)
        tray_menu.addAction(self.act_autostart)
        tray_menu.addSeparator()
        tray_menu.addAction(act_quit)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

        # 显示就绪气泡通知（提示已常驻托盘与快捷键）
        self.tray.showMessage(
            "⚡ SVN 极速搜索 (Spotlight版) 已就绪",
            f"已常驻右下角托盘！\n随时按快捷键【{self.hotkey_str}】唤醒极速搜索框。",
            QSystemTrayIcon.Information,
            3500
        )

    def quit_app(self):
        """优雅退出程序并清理后台工作线程"""
        if self.diff_worker and self.diff_worker.isRunning():
            self.diff_worker.stop()
        if self.info_worker and self.info_worker.isRunning():
            self.info_worker.terminate()
        QApplication.instance().quit()

    def toggle_autostart(self, checked: bool):
        """开机自启切换处理"""
        ok = set_autostart_enabled(checked)
        msg = "已成功开启开机自启动！" if checked else "已取消开机自启动！"
        if not ok:
            msg = "设置开机自启失败，请检查安全软件或管理员权限。"
        self.tray.showMessage("开机自启设置", msg, QSystemTrayIcon.Information, 2500)

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_spotlight()


    # ---------- 窗口显隐与动效控制 ----------

    def toggle_spotlight(self):
        """快捷键触发：切换显隐"""
        if self.isVisible() and self.isActiveWindow():
            self.hide_spotlight()
        else:
            self.show_spotlight()

    def show_spotlight(self):
        """展示并聚焦输入框"""
        screen = QApplication.primaryScreen().geometry()
        x = self.saved_x
        y = self.saved_y
        if x is None or y is None or x < -1000 or x > screen.width() + 1000 or y < -500 or y > screen.height() + 500:
            x = (screen.width() - 860) // 2
            y = int(screen.height() * 0.16)
        self.move(x, y)

        self.show()
        self.raise_()
        self.activateWindow()
        self.search_input.setFocus()
        self.search_input.selectAll()

    def hide_spotlight(self):
        """隐藏窗口"""
        self.hide()

    def changeEvent(self, event):
        """失去焦点或点击外部区域时自动隐藏（Mac Spotlight / Listary 规范）"""
        if event.type() == QEvent.ActivationChange:
            if not self.isActiveWindow():
                QTimer.singleShot(100, self._check_hide_on_deactivate)
        super().changeEvent(event)

    def _check_hide_on_deactivate(self):
        """检测并执行失焦隐藏"""
        if getattr(self, "_drag_pos", None) is not None:
            return  # 正在拖拽移动中，绝不收起
        if not self.isActiveWindow():
            # 若有自身弹出的子窗口（如设置窗口）或右键上下文菜单激活中，不误隐藏
            if not QApplication.activeModalWidget() and not QApplication.activePopupWidget():
                self.hide_spotlight()

    # ---------- 鼠标拖拽移动窗口与位置记忆 ----------

    def eventFilter(self, obj, event):
        """捕获把手、图标、卡片容器等鼠标事件，实现无边框悬浮窗流畅拖动"""
        drag_targets = (
            getattr(self, "card_container", None),
            getattr(self, "drag_handle", None),
            getattr(self, "icon_label", None),
            getattr(self, "filter_icon", None),
            getattr(self, "counter_label", None),
            getattr(self, "tip_label", None),
        )
        if obj in drag_targets:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_pos = event.globalPos() - self.pos()
                return True
            elif event.type() == QEvent.MouseMove and (event.buttons() & Qt.LeftButton):
                if getattr(self, "_drag_pos", None) is not None:
                    self.move(event.globalPos() - self._drag_pos)
                    return True
            elif event.type() == QEvent.MouseButtonRelease:
                if getattr(self, "_drag_pos", None) is not None:
                    self._drag_pos = None
                    self.saved_x = self.x()
                    self.saved_y = self.y()
                    self.settings.setValue("window_x", self.saved_x)
                    self.settings.setValue("window_y", self.saved_y)
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        """窗口空白区域鼠标按下启动拖拽"""
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """鼠标移动拖拽窗口"""
        if getattr(self, "_drag_pos", None) is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """释放鼠标记录并持久化窗口新位置"""
        if getattr(self, "_drag_pos", None) is not None:
            self._drag_pos = None
            self.saved_x = self.x()
            self.saved_y = self.y()
            self.settings.setValue("window_x", self.saved_x)
            self.settings.setValue("window_y", self.saved_y)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        """按键事件捕获：Esc 隐藏/回退，上下键多层穿透，回车直达 Repo-Browser"""
        if event.key() == Qt.Key_Escape:
            if self.table.hasFocus():
                if self.subfilter_input.text():
                    self.subfilter_input.setFocus()
                    self.subfilter_input.selectAll()
                else:
                    self.search_input.setFocus()
                    self.search_input.selectAll()
                event.accept()
                return
            if self.subfilter_input.hasFocus():
                if self.subfilter_input.text():
                    self.subfilter_input.clear()
                else:
                    self.search_input.setFocus()
                    self.search_input.selectAll()
                event.accept()
                return
            if self.results_container.isVisible() and (self.search_input.text() or self.subfilter_input.text()):
                # 第一次按 Esc：折叠结果框并清空文字
                self.collapse_results()
                self.search_input.clear()
                self.subfilter_input.clear()
            else:
                # 再次按 Esc 或无内容时：直接隐藏窗口
                self.hide_spotlight()
            event.accept()
            return
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.results_container.isVisible():
                if self.table.hasFocus() and self.table.currentRow() >= 0:
                    item = self.table.item(self.table.currentRow(), 1)
                    if item:
                        self.on_item_double_clicked(item)
                        event.accept()
                        return
                elif self.subfilter_input.hasFocus():
                    # 二次筛选框按回车：直接定位并打开第一个可见匹配结果
                    for r in range(self.table.rowCount()):
                        if not self.table.isRowHidden(r):
                            item = self.table.item(r, 1)
                            if item:
                                self.on_item_double_clicked(item)
                                event.accept()
                                return
                            break
        elif event.key() == Qt.Key_Down:
            if self.results_container.isVisible():
                if self.search_input.hasFocus():
                    self.subfilter_input.setFocus()
                    self.subfilter_input.selectAll()
                    event.accept()
                    return
                elif self.subfilter_input.hasFocus():
                    if self.table.rowCount() > 0:
                        self.table.setFocus()
                        for r in range(self.table.rowCount()):
                            if not self.table.isRowHidden(r):
                                self.table.selectRow(r)
                                break
                        event.accept()
                        return
        elif event.key() == Qt.Key_Up:
            if self.results_container.isVisible():
                if self.table.hasFocus():
                    curr = self.table.currentRow()
                    first_visible = 0
                    for r in range(self.table.rowCount()):
                        if not self.table.isRowHidden(r):
                            first_visible = r
                            break
                    if curr <= first_visible:
                        self.subfilter_input.setFocus()
                        self.subfilter_input.selectAll()
                        event.accept()
                        return
                elif self.subfilter_input.hasFocus():
                    self.search_input.setFocus()
                    self.search_input.selectAll()
                    event.accept()
                    return
        super().keyPressEvent(event)


    # ---------- 部门切换与索引管理 ----------

    def switch_department(self, idx: int, trigger_search: bool = True):
        """切换电子 / 结构 / 工艺部门索引"""
        if idx < 0 or idx >= len(DEPARTMENTS):
            return
        self.current_dept_idx = idx
        dept = DEPARTMENTS[idx]
        self.current_target_url = dept["url"]
        self.update_dept_buttons_style()

        # 加载目标仓库的本地索引
        self.load_department_index(dept["url"])

        # 若当前已有搜索词，立即在当前部门重新检索刷新
        if trigger_search and self.search_input.text().strip():
            self.trigger_search()

    def update_dept_buttons_style(self):
        """更新分段胶囊按钮高亮样式"""
        for i, btn in enumerate(self.dept_buttons):
            if i == self.current_dept_idx:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #2563EB;
                        color: #FFFFFF;
                        font-weight: bold;
                        border-radius: 6px;
                        padding: 5px 12px;
                        font-size: 13px;
                        border: none;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #F1F5F9;
                        color: #475569;
                        border-radius: 6px;
                        padding: 5px 12px;
                        font-size: 13px;
                        border: 1px solid #E2E8F0;
                    }
                    QPushButton:hover {
                        background-color: #E2E8F0;
                        color: #1E293B;
                    }
                """)

    def load_department_index(self, target_url: str):
        """从本地缓存装载当前选中部门的常驻内存索引"""
        meta = self.index_mgr.get_meta(target_url)
        if meta and meta.get("count", 0) > 0:
            self.memory_paths = self.index_mgr.load_cache_lines(target_url)
            rev_str = f"r{meta.get('rev')}" if meta.get("rev") else ""
            self.counter_label.setText(
                f"✅ 已载入【{DEPARTMENTS[self.current_dept_idx]['name']}】本地索引: {len(self.memory_paths):,} 项 ({rev_str})"
            )
        else:
            self.memory_paths = []
            self.counter_label.setText(
                f"⚠️ 【{DEPARTMENTS[self.current_dept_idx]['name']}】尚未建立本地缓存，请在主程序中全量拉取"
            )

    # ---------- 极简增量更新 ----------

    def trigger_incremental_sync(self):
        """轻量触发当前部门仓库的增量检查与同步更新 (svn diff)"""
        if (self.info_worker and self.info_worker.isRunning()) or (self.diff_worker and self.diff_worker.isRunning()):
            self.counter_label.setText("⏳ 增量同步正在进行中，请稍候...")
            return

        svn_exe = find_svn_exe()
        if not svn_exe:
            self.counter_label.setText("⚠️ 未检测到 SVN 命令行工具，请确保已安装 TortoiseSVN 命令行组件")
            return

        target_url = self.current_target_url
        dept_name = DEPARTMENTS[self.current_dept_idx]["name"]
        meta = self.index_mgr.get_meta(target_url)
        cached_rev = meta.get("rev") if meta else ""

        if not cached_rev:
            self.counter_label.setText(f"⚠️ 【{dept_name}】本地暂无基准版本号，请在完整版中执行初次更新")
            return

        self.sync_btn.setEnabled(False)
        self.counter_label.setText(f"🔄 正在探测【{dept_name}】远程最新提交 (本地 r{cached_rev})...")

        self.info_worker = SvnInfoWorker(target_url)
        self.info_worker.finished_signal.connect(
            lambda ok, rev, msg: self._on_sync_info_finished(ok, rev, msg, cached_rev, meta)
        )
        self.info_worker.start()

    def _on_sync_info_finished(self, ok, remote_rev, msg, cached_rev, meta):
        dept_name = DEPARTMENTS[self.current_dept_idx]["name"]
        target_url = self.current_target_url

        if not ok or not remote_rev:
            self.counter_label.setText(f"⚠️ 无法连接 SVN 获取最新版本: {msg}")
            self.sync_btn.setEnabled(True)
            return

        try:
            r_num = int(remote_rev)
            c_num = int(cached_rev)
        except ValueError:
            r_num = c_num = 0

        if r_num <= c_num:
            self.counter_label.setText(f"✅【{dept_name}】已是远程最新版本 (r{cached_rev})，无需更新")
            self.sync_btn.setEnabled(True)
            return

        self.counter_label.setText(f"🔄 发现新提交 r{remote_rev} (本地 r{cached_rev})，正在同步变更...")

        allowed_exts = meta.get("extensions") or []

        self.diff_worker = SvnDiffWorker(
            target_url,
            old_rev=cached_rev,
            new_rev=remote_rev,
            allowed_exts=allowed_exts,
            filter_noise=True
        )
        self.diff_worker.progress_signal.connect(
            lambda cnt, txt: self.counter_label.setText(f"🔄 增量同步中: 发现 {cnt} 项变动...")
        )
        self.diff_worker.finished_signal.connect(
            lambda s, a, d, nr, m: self._on_sync_diff_finished(s, a, d, nr, m, meta)
        )
        self.diff_worker.start()

    def _on_sync_diff_finished(self, success, added, deleted, new_rev, message, meta):
        self.sync_btn.setEnabled(True)
        dept_name = DEPARTMENTS[self.current_dept_idx]["name"]
        target_url = self.current_target_url

        if not success:
            self.counter_label.setText(f"❌ 增量更新失败: {message}")
            return

        curr_lines = self.index_mgr.load_cache_lines(target_url)
        path_set = set(curr_lines)

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

        # 保护扩展名全集
        updated_exts = set()
        for p in new_list:
            _, ext = os.path.splitext(p)
            if ext:
                updated_exts.add(ext.lower())
        if meta and meta.get("extensions"):
            for e in meta["extensions"]:
                if e:
                    updated_exts.add(e.lower())

        self.index_mgr.save_cache_lines(
            target_url,
            new_list,
            rev=new_rev,
            exts=sorted(list(updated_exts))
        )

        self.counter_label.setText(
            f"🎉【{dept_name}】增量同步完成！升级至 r{new_rev} (新增 {len(added)} 项, 移除 {len(deleted)} 项)"
        )

        if self.search_input.text().strip():
            self.trigger_search()

    # ---------- 极速检索与结果展现 ----------

    def on_scope_changed(self, index: int):
        """切换搜索匹配范围（仅文件名 / 仅路径 / 全路径）"""
        scope = self.scope_combo.currentData()
        self.settings.setValue("search_scope", scope)
        if scope == "filename":
            self.search_input.setPlaceholderText("仅搜索文件名... (支持空格多词，按 Enter 检索，Esc 退出)")
        elif scope == "directory":
            self.search_input.setPlaceholderText("仅搜索所在目录/路径... (支持空格多词，按 Enter 检索，Esc 退出)")
        else:
            self.search_input.setPlaceholderText("搜索文件名或所在完整路径... (支持空格多词，按 Enter 检索，Esc 退出)")

        # 若已有输入内容，切换范围时立即自动重搜
        if self.search_input.text().strip():
            self.trigger_search()

    def on_search_text_changed(self, text: str):
        """文字改变事件：如果文字被清空，自动收起结果框恢复极简单条状态"""
        if not text.strip():
            self.collapse_results()

    def on_ext_combo_changed(self, idx: int):
        if self.search_input.text().strip():
            self.trigger_search()

    def trigger_search(self):
        """执行内存极速搜索并展开结果面板"""
        query = self.search_input.text().strip()
        if not query:
            self.collapse_results()
            return

        if not self.memory_paths:
            self.expand_results()
            self.table.setRowCount(0)
            self.counter_label.setText("⚠️ 当前部门暂无索引数据，请在完整版中执行全量拉取。")
            return

        # 获取匹配范围 (默认使用下拉框，并支持 dir: 或 file: 语法前缀覆盖)
        scope = self.scope_combo.currentData() if hasattr(self, "scope_combo") else "filename"
        q_lower = query.lower()
        if q_lower.startswith(("dir:", "path:")):
            scope = "directory"
            query_clean = query.split(":", 1)[1].strip()
        elif q_lower.startswith(("file:", "name:")):
            scope = "filename"
            query_clean = query.split(":", 1)[1].strip()
        else:
            query_clean = query

        # 解析 ext:xxx 语法、*.xxx 后缀限定以及下拉框选定的后缀/类型
        ext_filter_tokens = []
        combo_ext = self.ext_combo.currentData() if hasattr(self, "ext_combo") else ""
        if combo_ext:
            ext_filter_tokens.append(combo_ext)

        clean_tokens = []
        for tok in query_clean.split():
            tok_low = tok.lower()
            if tok_low.startswith("ext:"):
                ext_filter_tokens.append(tok_low.split(":", 1)[1].strip().lstrip("."))
            elif tok_low.startswith("*."):
                ext_filter_tokens.append(tok_low[2:].strip())
            else:
                clean_tokens.append(tok_low)
        keywords = clean_tokens

        # 展开所有后缀别名 (如 eda, cad, xmind, mpp 等)
        target_exts = set()
        for eft in ext_filter_tokens:
            for e in normalize_extensions(eft):
                target_exts.add(e.lower())

        t_start = time.perf_counter()
        matched = []

        for p in self.memory_paths:
            p_low = p.lower()
            # 后缀限制匹配
            if target_exts and not any(p_low.endswith(te) for te in target_exts):
                continue

            if scope == "filename":
                target_str = p.rsplit('/', 1)[-1].lower()
            elif scope == "directory":
                target_str = p.rsplit('/', 1)[0].lower() if '/' in p else ''
            else:
                target_str = p_low

            if not keywords or all(k in target_str for k in keywords):
                matched.append(p)
                if len(matched) >= 500:
                    break

        cost_ms = (time.perf_counter() - t_start) * 1000
        self.matched_paths = matched

        # 展开并渲染表格
        self.expand_results()
        self.render_results_table(matched)

        scope_desc = "仅文件名" if scope == "filename" else ("仅目录路径" if scope == "directory" else "全路径")
        if target_exts:
            ext_summary = "/".join(sorted(target_exts)[:3])
            if len(target_exts) > 3:
                ext_summary += "..."
            scope_desc += f", 后缀:{ext_summary}"
        dept_name = DEPARTMENTS[self.current_dept_idx]["name"]
        if matched:
            self.counter_label.setText(
                f"🎯 {dept_name} [{scope_desc}]：命中 {len(matched):,} 条结果 (耗时 {cost_ms:.1f}ms)"
            )
        else:
            self.counter_label.setText(f"🔍 {dept_name} [{scope_desc}]：未找到与「{query_clean}」匹配的文件")

        # 每次主检索完成后重置二次筛选输入框
        self.subfilter_input.blockSignals(True)
        self.subfilter_input.clear()
        self.subfilter_input.blockSignals(False)

    def filter_current_table(self, text: str):
        """在当前已匹配的结果表格中执行二次即时过滤（支持名称/路径识别与 ext: 后缀过滤）"""
        kw = text.strip().lower()
        visible_count = 0
        self.table.setUpdatesEnabled(False)
        total_rows = self.table.rowCount()

        sub_scope = "auto"
        ext_filter = None
        if kw.startswith(("dir:", "path:")):
            sub_scope = "directory"
            kw_clean = kw.split(":", 1)[1].strip()
        elif kw.startswith(("file:", "name:")):
            sub_scope = "filename"
            kw_clean = kw.split(":", 1)[1].strip()
        elif kw.startswith("ext:"):
            ext_filter = kw.split(":", 1)[1].strip().lstrip(".")
            kw_clean = ""
        else:
            kw_clean = kw

        norm_exts = set(e.lower() for e in normalize_extensions(ext_filter)) if ext_filter else set()
        parts = kw_clean.split()

        for r in range(total_rows):
            if not kw_clean and not norm_exts:
                self.table.setRowHidden(r, False)
                visible_count += 1
            else:
                name_item = self.table.item(r, 1)
                dir_item = self.table.item(r, 2)
                ext_item = self.table.item(r, 3)

                name_str = name_item.text().lower() if name_item else ""
                dir_str = dir_item.text().lower() if dir_item else ""
                ext_str = ext_item.text().lower() if ext_item else ""

                if norm_exts and not any(ext_str.endswith(ne) for ne in norm_exts):
                    self.table.setRowHidden(r, True)
                    continue

                if sub_scope == "filename":
                    target_str = f"{name_str} {ext_str}"
                elif sub_scope == "directory":
                    target_str = dir_str
                else:
                    target_str = f"{name_str} {dir_str} {ext_str}"

                matched = not parts or all(p in target_str for p in parts)

                if matched:
                    self.table.setRowHidden(r, False)
                    visible_count += 1
                else:
                    self.table.setRowHidden(r, True)

        self.table.setUpdatesEnabled(True)
        dept_name = DEPARTMENTS[self.current_dept_idx]["name"]
        if kw:
            self.counter_label.setText(f"🎯 二次筛选：显示 {visible_count:,} / 共 {total_rows:,} 条")
        else:
            self.counter_label.setText(f"🎯 {dept_name}：显示 {total_rows:,} 条结果")


    def expand_results(self):
        """向下展开展示结果列表"""
        if not self.results_container.isVisible():
            self.results_container.setVisible(True)
            self.resize(920, 520)

    def collapse_results(self):
        """折叠收起结果列表，恢复单行搜索框"""
        if self.results_container.isVisible():
            self.results_container.setVisible(False)
            self.resize(920, 68)


    def render_results_table(self, paths: list):
        """渲染结果表格"""
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(paths))
        target = self.current_target_url

        for row, p in enumerate(paths):
            badge, name, parent, ext = FileTypeHelper.get_info(p)
            full_url = build_full_url(target, p)
            dir_url = build_dir_url(target, parent)

            # 1. 类型徽标
            item_badge = QTableWidgetItem(badge)
            item_badge.setTextAlignment(Qt.AlignCenter)

            # 2. 文件名 (加粗)
            item_name = QTableWidgetItem(name)
            font = item_name.font()
            font.setBold(True)
            item_name.setFont(font)
            item_name.setData(Qt.UserRole, full_url)
            item_name.setToolTip(f"完整 URL:\n{full_url}")

            # 3. 所在目录 (精简相对路径)
            display_dir = "/" if not parent or parent == "/" else f"/{parent.strip('/')}/"
            item_dir = QTableWidgetItem(display_dir)
            item_dir.setData(Qt.UserRole, dir_url)
            item_dir.setToolTip(f"所在目录完整 URL:\n{dir_url}\n\n💡 双击直达 SVN Repo-Browser 目录")

            # 4. 格式
            item_ext = QTableWidgetItem(ext)
            item_ext.setTextAlignment(Qt.AlignCenter)

            self.table.setItem(row, 0, item_badge)
            self.table.setItem(row, 1, item_name)
            self.table.setItem(row, 2, item_dir)
            self.table.setItem(row, 3, item_ext)

        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)

    # ---------- 交互事件：双击与右键直达 ----------

    def on_item_double_clicked(self, item: QTableWidgetItem):
        """双击表格行：直接调起 Repo-Browser 定位所在目录并复制完整 URL"""
        row = item.row()
        name_item = self.table.item(row, 1)
        dir_item = self.table.item(row, 2)
        badge_item = self.table.item(row, 0)

        full_url = name_item.data(Qt.UserRole) or name_item.text()
        dir_url = dir_item.data(Qt.UserRole) or dir_item.text()
        file_name = name_item.text()
        is_dir = "目录" in (badge_item.text() if badge_item else "")

        # 1. 同步复制文件完整 URL 到剪贴板
        QApplication.clipboard().setText(full_url)

        # 2. 调用 TortoiseSVN Repo-Browser 直达目录
        target_open_url = full_url if is_dir else dir_url
        ok = open_in_repo_browser(target_open_url)
        self.hide_spotlight()  # 调起后自动隐藏，不遮挡 TortoiseSVN 界面
        if ok:
            self.tray.showMessage(
                "🚀 已在 Repo-Browser 打开",
                f"目标：【{file_name}】\n完整 URL 已复制到剪贴板！",
                QSystemTrayIcon.Information,
                2000
            )
        else:
            # 降级系统浏览器打开
            try:
                os.startfile(canonicalize_svn_url(target_open_url))
            except Exception:
                pass

    def pull_single_file(self, full_url: str, save_as: bool = False, open_after: bool = True):
        """单独拉取远程 SVN 文件（如 .mpp）并在完成后直接打开或保存到本地"""
        from PyQt5.QtWidgets import QFileDialog
        clean_url = canonicalize_svn_url(full_url)
        file_name = full_url.rstrip("/").split("/")[-1]

        if save_as:
            default_path = os.path.join(os.path.expanduser("~"), "Desktop", file_name)
            save_path, _ = QFileDialog.getSaveFileName(self, f"另存为文件 - {file_name}", default_path, "所有文件 (*.*)")
            if not save_path:
                return
        else:
            cache_save_dir = os.path.join(os.path.expanduser("~"), ".svn_search_cache", "pulled_files")
            os.makedirs(cache_save_dir, exist_ok=True)
            save_path = os.path.join(cache_save_dir, file_name)

        self.tray.showMessage(
            "⏳ 正在拉取文件",
            f"正在从 SVN 拉取【{file_name}】到本地...",
            QSystemTrayIcon.Information,
            2500
        )

        if not hasattr(self, "_active_export_workers"):
            self._active_export_workers = []

        worker = SvnExportWorker(clean_url, save_path, open_after=open_after)
        self._active_export_workers.append(worker)

        def on_finished(success, final_path, err_msg, was_open_after):
            if worker in self._active_export_workers:
                self._active_export_workers.remove(worker)
            if success:
                action_desc = "并已直接调用程序打开" if was_open_after else "已成功保存到本地"
                self.tray.showMessage(
                    "✅ 拉取成功",
                    f"【{file_name}】{action_desc}！\n路径: {final_path}",
                    QSystemTrayIcon.Information,
                    4000
                )
            else:
                self.tray.showMessage(
                    "❌ 拉取失败",
                    f"文件【{file_name}】拉取失败:\n{err_msg[:120]}",
                    QSystemTrayIcon.Warning,
                    5000
                )

        worker.finished_signal.connect(on_finished)
        worker.start()

    def show_context_menu(self, pos: QPoint):
        """右键菜单"""
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        name_item = self.table.item(row, 1)
        dir_item = self.table.item(row, 2)
        badge_item = self.table.item(row, 0)

        full_url = name_item.data(Qt.UserRole) or name_item.text()
        dir_url = dir_item.data(Qt.UserRole) or dir_item.text()
        file_name = name_item.text()
        rel_dir = dir_item.text()
        is_dir = "目录" in (badge_item.text() if badge_item else "")

        menu = QMenu(self.table)

        # 优先提供单文件单独拉取与直接打开功能 (支持 .mpp 等格式)
        if full_url.startswith(("http://", "https://", "svn://")) and not is_dir:
            act_pull_and_open = QAction(f"⚡ 单独拉取并直接打开此文件 ({file_name})", self)
            font_pull_open = act_pull_and_open.font()
            font_pull_open.setBold(True)
            act_pull_and_open.setFont(font_pull_open)
            act_pull_and_open.triggered.connect(
                lambda: (self.pull_single_file(full_url, save_as=False, open_after=True), self.hide_spotlight())
            )
            menu.addAction(act_pull_and_open)

            act_pull_save_as = QAction("📥 单独拉取此文件到本地 (另存为...)", self)
            act_pull_save_as.triggered.connect(
                lambda: (self.pull_single_file(full_url, save_as=True, open_after=False), self.hide_spotlight())
            )
            menu.addAction(act_pull_save_as)
            menu.addSeparator()

        act_repobrowser = QAction("🚀 在 Repo-Browser 中打开所在目录", self)
        target_open_url = full_url if is_dir else dir_url
        act_repobrowser.triggered.connect(lambda: (open_in_repo_browser(target_open_url), self.hide_spotlight()))

        act_open_web = QAction("🌐 在浏览器中打开 SVN 页面", self)
        act_open_web.triggered.connect(lambda: os.startfile(canonicalize_svn_url(target_open_url)))

        act_copy_full = QAction("📋 复制完整 URL 路径", self)
        act_copy_full.triggered.connect(lambda: QApplication.clipboard().setText(full_url))

        act_copy_name = QAction("📄 复制文件名", self)
        act_copy_name.triggered.connect(lambda: QApplication.clipboard().setText(file_name))

        act_copy_dir = QAction("📁 复制所在目录完整 URL", self)
        act_copy_dir.triggered.connect(lambda: QApplication.clipboard().setText(dir_url))

        act_copy_rel = QAction("📂 复制相对目录路径", self)
        act_copy_rel.triggered.connect(lambda: QApplication.clipboard().setText(rel_dir))

        menu.addAction(act_repobrowser)
        menu.addAction(act_open_web)
        menu.addSeparator()
        menu.addAction(act_copy_full)
        menu.addAction(act_copy_name)
        menu.addAction(act_copy_dir)
        menu.addAction(act_copy_rel)

        menu.exec_(self.table.mapToGlobal(pos))

    # ---------- 快捷键设置面板 ----------

    def open_hotkey_settings(self):
        dlg = HotkeySettingsDialog(self.hotkey_str, self)
        if dlg.exec_() == QDialog.Accepted:
            new_hk = dlg.get_selected_hotkey()
            if new_hk != self.hotkey_str:
                ok = self.register_global_hotkey(new_hk)
                if ok:
                    self.act_show.setText(f"🔍 唤醒搜索 ({new_hk})")
                    QMessageBox.information(
                        self, "快捷键已更新",
                        f"全局唤醒快捷键已成功修改为：【{new_hk}】\n按此快捷键即可随时唤醒或收起搜索框！"
                    )
                else:
                    QMessageBox.warning(
                        self, "快捷键冲突",
                        f"快捷键【{new_hk}】已被系统或其他软件占用，请选择其他快捷键！"
                    )


# =====================================================================
# 5. 入口函数（后台常驻与单实例防多开）
# =====================================================================

def main():
    # 1. 单实例互斥检查：防止多次重复启动冲突快捷键
    MUTEX_NAME = "Local\\SvnSpotlightSingleInstanceMutex"
    h_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # 已有实例在后台常驻运行，发送广播消息将其唤醒，并退出当前重复进程
        wake_msg = ctypes.windll.user32.RegisterWindowMessageW("SvnSpotlightWakeUpMessage")
        ctypes.windll.user32.PostMessageW(0xFFFF, wake_msg, 0, 0)
        sys.exit(0)

    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 保持后台常驻，关闭窗口不退出

    window = SpotlightSearchWindow()
    # 注意：启动后静默常驻系统托盘（右下角），不主动弹出搜索框打扰工作
    # 用户需要时按快捷键（默认 Alt+Q）或点击托盘图标即可唤醒

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
