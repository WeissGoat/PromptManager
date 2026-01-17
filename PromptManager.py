import sys
import os
import shutil
import re
import subprocess
import tempfile
import html
import json
import traceback
from pathlib import Path

# PySide6 Imports
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTreeWidget, QTreeWidgetItem, QListWidget, 
                               QListWidgetItem, QTextEdit, QLabel, QSplitter, 
                               QPushButton, QFileDialog, QMenu, QInputDialog, 
                               QMessageBox, QAbstractItemView, QFrame, QLineEdit, 
                               QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QHeaderView,
                               QStyledItemDelegate, QStyleOptionViewItem)
from PySide6.QtCore import Qt, QSize, QUrl, Signal, QPoint, QFile, QRunnable, QThreadPool, QObject, Slot, QRect
from PySide6.QtGui import (QPixmap, QAction, QIcon, QDragEnterEvent, QDropEvent, 
                           QMouseEvent, QWheelEvent, QImageReader, QColor, QBrush,
                           QShortcut, QKeySequence, QTextCursor, QTextDocument, QTextCharFormat, QPainter)

# Windows Shortcut Handling
try:
    import win32com.client
    import pythoncom # Required for threading
    shell = win32com.client.Dispatch("WScript.Shell")
    HAS_WIN32 = True
except ImportError:
    shell = None
    HAS_WIN32 = False
    print("Warning: pywin32 not installed. .lnk support will be limited.")

# --- Helper Functions ---
def get_ori_prompt(prompt):
    return prompt.split('\n=')[0]

from util import resolve_path, create_shortcut

def reset_ext_node_type(txt, key, param):
    data = txt.split('\n')
    need_split = True
    for i, item in enumerate(data):
        if item[:len(key)] == key:
            data[i] = param
            break
        if item == "=":
            need_split = False
        if i == len(data) - 1:
            if need_split:
                data.append("=")
            data.append(param)
    return '\n'.join(data)

def reset_ainode_ext_node_type(node_path, key, param):
    tags_path = os.path.join(node_path, "tags.txt")
    if not os.path.exists(tags_path):
        raise FileNotFoundError(f"tags.txt not found in {node_path}")
    txt = open(tags_path, "r+", encoding="utf-8").read()
    ntxt = reset_ext_node_type(txt, key, param)
    open(tags_path, "w", encoding="utf-8").write(ntxt)

def is_image_file(filename):
    return filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp'))

def has_images_fast(folder_path):
    """
    Checks if a folder contains at least one image file using scandir for performance.
    Stops as soon as one is found.
    """
    try:
        with os.scandir(folder_path) as it:
            for entry in it:
                if entry.is_file() and is_image_file(entry.name):
                    return True
    except OSError:
        pass
    return False

def natural_sort_key(s):
    """For sorting strings containing numbers naturally (1, 2, 10 instead of 1, 10, 2)"""
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', s)]

def clean_node_name(name):
    """
    Removes the ordering prefix from a node name.
    Supports both old format '(1)Name' and new format '1_Name'.
    """
    # Regex matches:
    # 1. ^\(\d+\) -> Starts with (digits)
    # 2. ^\d+_    -> Starts with digits_
    return re.sub(r'^(\(\d+\)|\d+_)', '', name)

def parse_tags_set(text):
    if not text: return set()
    clean = get_ori_prompt(text).replace('\n', ',')
    return {t.strip() for t in clean.split(',') if t.strip()}

def normalize_key(path):
    return os.path.normcase(os.path.normpath(path))

# --- Async Worker ---

class WorkerSignals(QObject):
    finished = Signal()
    result = Signal(object) 

class DiffCalculatorWorker(QRunnable):
    def __init__(self, node_paths):
        super(DiffCalculatorWorker, self).__init__()
        self.node_paths = node_paths
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        print(f"[DiffWorker] Starting thread. Processing {len(self.node_paths)} nodes.")
        
        local_shell = None
        if HAS_WIN32:
            try:
                pythoncom.CoInitialize()
                local_shell = win32com.client.Dispatch("WScript.Shell")
            except Exception as e:
                print(f"[DiffWorker] COM Init Failed: {e}")

        diff_map = {} 
        previous_tags = set()
        
        try:
            for i, path in enumerate(self.node_paths):
                key = normalize_key(path)
                resolved = path
                path_obj = Path(path)
                
                if path_obj.suffix.lower() == '.lnk':
                    if local_shell:
                        try:
                            shortcut = local_shell.CreateShortcut(str(path_obj.resolve()))
                            target = shortcut.TargetPath
                            if os.path.exists(target):
                                resolved = target
                        except:
                            pass 
                
                tags_file = os.path.join(resolved, "tags.txt")
                current_tags = set()
                
                if os.path.exists(tags_file):
                    try:
                        with open(tags_file, 'r', encoding='utf-8') as f:
                            current_tags = parse_tags_set(f.read())
                    except:
                        pass
                
                if i == 0:
                    diff_map[key] = (0, 0)
                else:
                    added = len(current_tags - previous_tags)
                    removed = len(previous_tags - current_tags)
                    diff_map[key] = (added, removed)
                
                previous_tags = current_tags
        
        except Exception as e:
            print(f"[DiffWorker] Critical Error in run loop: {e}")
            traceback.print_exc()
        finally:
            if HAS_WIN32:
                try:
                    pythoncom.CoUninitialize()
                except:
                    pass
            
        self.signals.result.emit(diff_map)
        self.signals.finished.emit()

# --- Custom Widgets ---

class DiffDelegate(QStyledItemDelegate):
    """
    Custom delegate to paint diff stats.
    """
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        # Default paint (Text, selection bg)
        super().paint(painter, option, index)
        
        # Get Diff Data
        diff_data = index.data(Qt.UserRole + 100) 
        
        if not diff_data:
            return
            
        # FIX: Allow list or tuple (PySide might convert tuple to list)
        if not isinstance(diff_data, (tuple, list)):
            return
            
        added, removed = diff_data
        
        # Skip if nothing to show
        if added == 0 and removed == 0:
            return

        rect = option.rect
        painter.save()
        
        # Setup Font
        font = option.font
        font.setPointSize(max(8, font.pointSize() - 2)) 
        font.setBold(True)
        painter.setFont(font)
        
        # Prepare Text
        text_added = f"+{added}" if added > 0 else ""
        text_removed = f"-{removed}" if removed > 0 else ""
        
        # Metrics
        fm = painter.fontMetrics()
        
        # Layout from Right Edge
        padding = 8
        spacing = 4
        current_x = rect.right() - padding
        
        # Vertical centering
        h = rect.height()
        y_pos = rect.top()
        
        # Draw Function
        def draw_pill(text, bg_col, txt_col):
            nonlocal current_x
            text_w = fm.horizontalAdvance(text)
            pill_w = text_w + 10 # Padding inside pill
            pill_h = min(16, h - 4) # Height limit
            pill_y = y_pos + (h - pill_h) // 2
            
            pill_rect = QRect(current_x - pill_w, pill_y, pill_w, pill_h)
            
            # Draw BG
            painter.setBrush(QBrush(bg_col))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(pill_rect, 4, 4)
            
            # Draw Text
            painter.setPen(txt_col)
            painter.drawText(pill_rect, Qt.AlignCenter, text)
            
            # Move X pointer
            current_x -= (pill_w + spacing)

        # 1. Draw Removed (Red)
        if text_removed:
            draw_pill(text_removed, QColor("#FFEBEE"), QColor("#D32F2F"))

        # 2. Draw Added (Green)
        if text_added:
            draw_pill(text_added, QColor("#E8F5E9"), QColor("#2E7D32"))

        painter.restore()

class RunParamsDialog(QDialog):
    def __init__(self, params_file, parent=None):
        super().__init__(parent)
        self.setWindowTitle("运行参数配置 (Run Parameters)")
        self.resize(600, 350)
        self.params_file = params_file
        self.params_data = {}
        
        self.layout = QVBoxLayout(self)
        
        # Info
        self.layout.addWidget(QLabel("设置传递给脚本的额外参数 (Key=Value):"))
        
        # Table
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["启用 (Enable)", "参数名 (Key)", "参数值 (Value)"])
        
        # Adjust Header Sizing
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents) # Checkbox column
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        
        self.layout.addWidget(self.table)
        
        # Edit Buttons
        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("添加参数 (+)")
        self.btn_add.clicked.connect(self.add_row)
        self.btn_del = QPushButton("删除选中 (-)")
        self.btn_del.clicked.connect(self.remove_row)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_del)
        self.layout.addLayout(btn_layout)
        
        # Dialog Buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.button(QDialogButtonBox.Ok).setText("运行 (Run)")
        self.button_box.button(QDialogButtonBox.Cancel).setText("取消 (Cancel)")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)
        
        self.load_params()

    def add_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        
        # Checkbox Item
        check_item = QTableWidgetItem()
        check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        check_item.setCheckState(Qt.Checked) # Default enabled
        self.table.setItem(row, 0, check_item)
        
        self.table.setItem(row, 1, QTableWidgetItem("Key"))
        self.table.setItem(row, 2, QTableWidgetItem("Value"))

    def remove_row(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            self.table.removeRow(current_row)

    def load_params(self):
        if os.path.exists(self.params_file):
            try:
                with open(self.params_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.params_data = data
                    for k, v in self.params_data.items():
                        row = self.table.rowCount()
                        self.table.insertRow(row)
                        val_str = ""
                        key_str = ""
                        is_enabled = True
                        if isinstance(v, dict) and 'value' in v:
                            val_str = str(v['value'])
                            is_enabled = v.get('enabled', True)
                            key_str = str(v['key'])
                        else:
                            val_str = str(v)
                            is_enabled = True
                        check_item = QTableWidgetItem()
                        check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                        check_item.setCheckState(Qt.Checked if is_enabled else Qt.Unchecked)
                        self.table.setItem(row, 0, check_item)
                        self.table.setItem(row, 1, QTableWidgetItem(key_str))
                        self.table.setItem(row, 2, QTableWidgetItem(val_str))
            except Exception as e:
                print(f"Error loading params: {e}")

    def save_params(self):
        data = {}
        for i in range(self.table.rowCount()):
            check_item = self.table.item(i, 0)
            key_item = self.table.item(i, 1)
            val_item = self.table.item(i, 2)
            if key_item and val_item and key_item.text().strip():
                key = key_item.text().strip()
                val = val_item.text().strip()
                enabled = (check_item.checkState() == Qt.Checked)
                data[i] = {"key": key, "value": val, "enabled": enabled}
        self.params_data = data
        try:
            with open(self.params_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving params: {e}")

    def accept(self):
        self.save_params()
        super().accept()
        
    def get_params_list(self):
        params_list = []
        for k, v in self.params_data.items():
            if isinstance(v, dict) and 'value' in v:
                if v.get('enabled', True):
                    params_list.append(f"--{v['key']}#{v['value']}")
            else:
                params_list.append(f"--{k}#{v}")
        return params_list

class ClickableImageLabel(QLabel):
    """
    Preview Box: 
    - Left Click: Switch folder (source)
    - Wheel: Switch image inside current folder
    - Right Click: Go back
    """
    clicked = Signal()
    right_clicked = Signal()
    scrolled = Signal(int) 

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: #2b2b2b; color: #888; border: 1px solid #444;")
        self.setText("预览区域\n(无图片)")
        self.setMinimumHeight(200)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        elif event.button() == Qt.RightButton:
            self.right_clicked.emit()
        super().mousePressEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta > 0:
            self.scrolled.emit(-1)
        else:
            self.scrolled.emit(1)
        super().wheelEvent(event)

class DraggableListWidget(QListWidget):
    item_moved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSpacing(2) 
        
    def dropEvent(self, event: QDropEvent):
        super().dropEvent(event)
        self.item_moved.emit()

    def startDrag(self, supportedActions):
        super().startDrag(Qt.MoveAction | Qt.CopyAction)

# --- Main Application ---

class PromptManagerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 提示词库管理工具 (Prompt Manager)")
        self.resize(1200, 800)
        
        # State
        self.root_dir = r"D:\AI\design\动作改2"
        self.current_scene_path = None
        self.current_node_path = None
        self.previous_node_path = None # Track last selected node for diff
        self.scene_selection_history = {} # {scene_path: selected_row_index}
        self.bat_script_path = r"C:\Users\WhiteSheep\AppData\Roaming\Microsoft\Windows\SendTo\ct.blackboard.run_next_character.bat" # Store selected bat script path
        self.threadpool = QThreadPool()
        
        # Bookmarks State
        self.bookmarks = set()
        self.bookmarks_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookmarks.json")
        self.load_bookmarks()
        
        # Run Params File
        self.run_params_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_params.json")
        
        # Preview State
        self.image_sources = [] # List of dicts: {'name', 'path', 'status': 'valid'|'pending'|'invalid'}
        self.current_source_index = 0
        self.current_image_list = [] # Images in current source
        self.current_image_index = 0

        # UI Initialization
        self.init_ui()
        
        # Select Root on startup
        self.select_root_directory()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Splitter for 3 columns
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- Left: Scene Tree ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("场景列表 (Scenes)")
        left_label.setStyleSheet("font-weight: bold; padding: 5px;")
        
        self.scene_tree = QTreeWidget()
        self.scene_tree.setHeaderHidden(True)
        self.scene_tree.setDragEnabled(True)
        self.scene_tree.setAcceptDrops(True) # Accept drops from node list (for linking)
        self.scene_tree.setDropIndicatorShown(True)
        self.scene_tree.itemClicked.connect(self.on_scene_selected)
        self.scene_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scene_tree.customContextMenuRequested.connect(self.show_scene_context_menu)
        
        # Custom drop event for Tree to handle "Copy/Link Node"
        self.scene_tree.dropEvent = self.tree_drop_event

        left_layout.addWidget(left_label)
        left_layout.addWidget(self.scene_tree)
        
        # --- Middle: Action Nodes ---
        mid_widget = QWidget()
        mid_layout = QVBoxLayout(mid_widget)
        mid_layout.setContentsMargins(0, 0, 0, 0)
        mid_label = QLabel("动作节点 (Actions)")
        mid_label.setStyleSheet("font-weight: bold; padding: 5px;")
        
        self.node_list = DraggableListWidget()
        self.node_list.itemClicked.connect(self.on_node_selected)
        self.node_list.item_moved.connect(self.on_node_reordered)
        self.node_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.node_list.customContextMenuRequested.connect(self.show_node_context_menu)
        
        # Set Delegate
        self.node_list.setItemDelegate(DiffDelegate(self.node_list))

        mid_layout.addWidget(mid_label)
        mid_layout.addWidget(self.node_list)

        # --- Right: Operation & Preview ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # 1. Preview Area
        self.preview_label = ClickableImageLabel()
        self.preview_label.clicked.connect(self.next_image_source)
        self.preview_label.right_clicked.connect(self.prev_image_source)
        self.preview_label.scrolled.connect(self.scroll_image)
        
        self.source_info_label = QLabel("Source: None")
        self.source_info_label.setAlignment(Qt.AlignRight)
        self.source_info_label.setStyleSheet("font-size: 10px; color: #666;")

        # Text Area Splitter (Editor vs Diff)
        text_splitter = QSplitter(Qt.Vertical)

        # 2. Prompt Editor Container
        editor_container = QWidget()
        ec_layout = QVBoxLayout(editor_container)
        ec_layout.setContentsMargins(0, 0, 0, 0)
        
        # Search Bar (Initially Hidden)
        self.search_bar = QWidget()
        self.search_bar.setVisible(False)
        sb_layout = QHBoxLayout(self.search_bar)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search...")
        self.search_input.textChanged.connect(self.highlight_matches)
        self.search_input.returnPressed.connect(self.find_next)
        
        btn_next = QPushButton("↓")
        btn_next.setFixedWidth(30)
        btn_next.clicked.connect(self.find_next)
        
        btn_prev = QPushButton("↑")
        btn_prev.setFixedWidth(30)
        btn_prev.clicked.connect(self.find_prev)
        
        btn_close = QPushButton("×")
        btn_close.setFixedWidth(30)
        btn_close.clicked.connect(self.close_search)
        
        sb_layout.addWidget(self.search_input)
        sb_layout.addWidget(btn_prev)
        sb_layout.addWidget(btn_next)
        sb_layout.addWidget(btn_close)
        
        ec_layout.addWidget(self.search_bar) # Add above editor label

        editor_label = QLabel("提示词编辑 (Prompts)")
        editor_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        self.prompt_editor = QTextEdit()
        self.prompt_editor.textChanged.connect(self.on_prompt_edited) # Connect for realtime diff
        ec_layout.addWidget(editor_label)
        ec_layout.addWidget(self.prompt_editor)
        
        # Shortcut for Search
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self.prompt_editor)
        self.search_shortcut.activated.connect(self.open_search)

        # 3. Diff Viewer Container
        diff_container = QWidget()
        dc_layout = QVBoxLayout(diff_container)
        dc_layout.setContentsMargins(0, 0, 0, 0)
        diff_label = QLabel("与上一次选中差异 (Diff vs Last Selected):")
        diff_label.setStyleSheet("font-weight: bold; margin-top: 5px; color: #555;")
        self.diff_viewer = QTextEdit()
        self.diff_viewer.setReadOnly(True)
        self.diff_viewer.setStyleSheet("background-color: #f8f8f8; color: #333; font-family: Consolas, monospace;")
        dc_layout.addWidget(diff_label)
        dc_layout.addWidget(self.diff_viewer)

        text_splitter.addWidget(editor_container)
        text_splitter.addWidget(diff_container)
        text_splitter.setStretchFactor(0, 3) # Editor takes 3 parts
        text_splitter.setStretchFactor(1, 1) # Diff takes 1 part
        
        # 4. Buttons Area
        btn_layout = QHBoxLayout()
        
        self.btn_save = QPushButton("保存 (Save)")
        self.btn_save.clicked.connect(self.save_prompt)
        
        self.btn_add = QPushButton("新增节点 (Add)")
        self.btn_add.clicked.connect(self.add_node)
        
        self.btn_run = QPushButton("运行 (Run)")
        self.btn_run.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.btn_run.clicked.connect(self.run_process)

        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_run)
        
        # Extra Tools
        tools_layout = QHBoxLayout()
        self.btn_new_scene = QPushButton("新建组合场景")
        self.btn_new_scene.clicked.connect(self.create_new_scene_mode)
        
        self.btn_batch_export = QPushButton("批量导出")
        self.btn_batch_export.clicked.connect(self.batch_export)
        
        self.btn_batch_edit = QPushButton("批量编辑")
        self.btn_batch_edit.clicked.connect(self.batch_edit)

        tools_layout.addWidget(self.btn_new_scene)
        tools_layout.addWidget(self.btn_batch_export)
        tools_layout.addWidget(self.btn_batch_edit)

        # Add to Right Layout
        right_layout.addWidget(self.preview_label, 2) # Stretch factor 2
        right_layout.addWidget(self.source_info_label)
        right_layout.addWidget(text_splitter, 3) # Splitter takes more space
        right_layout.addLayout(btn_layout)
        right_layout.addLayout(tools_layout)

        # Add widgets to splitter
        splitter.addWidget(left_widget)
        splitter.addWidget(mid_widget)
        splitter.addWidget(right_widget)
        
        # Set initial sizes
        splitter.setSizes([200, 250, 450])

    # --- Search Logic ---
    
    def open_search(self):
        # Pre-fill search with selected text if any
        cursor = self.prompt_editor.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText()
            self.search_input.setText(selected_text)

        self.search_bar.setVisible(True)
        self.search_input.setFocus()
        self.search_input.selectAll()
        self.highlight_matches()

    def close_search(self):
        self.search_bar.setVisible(False)
        self.prompt_editor.setFocus()
        # Clear highlights
        self.prompt_editor.setExtraSelections([])

    def highlight_matches(self):
        search_text = self.search_input.text()
        if not search_text:
            self.prompt_editor.setExtraSelections([])
            return
            
        extra_selections = []
        
        # Save current cursor
        # original_cursor = self.prompt_editor.textCursor()
        
        # Start search from beginning
        cursor = self.prompt_editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#FFFF00")) # Yellow
        fmt.setForeground(QColor("#000000"))
        
        while True:
            # Find next occurrence
            cursor = self.prompt_editor.document().find(search_text, cursor)
            if cursor.isNull():
                break
                
            sel = QTextEdit.ExtraSelection()
            sel.format = fmt
            sel.cursor = cursor
            extra_selections.append(sel)
            
        self.prompt_editor.setExtraSelections(extra_selections)

    def find_next(self):
        text = self.search_input.text()
        if not text: return
        
        found = self.prompt_editor.find(text)
        if not found:
            # Wrap around
            self.prompt_editor.moveCursor(QTextCursor.Start)
            self.prompt_editor.find(text)

    def find_prev(self):
        text = self.search_input.text()
        if not text: return
        
        found = self.prompt_editor.find(text, QTextDocument.FindBackward)
        if not found:
            # Wrap around
            self.prompt_editor.moveCursor(QTextCursor.End)
            self.prompt_editor.find(text, QTextDocument.FindBackward)

    # --- Logic: Loading Data ---

    def select_root_directory(self):
        if not self.root_dir:
            folder = QFileDialog.getExistingDirectory(self, "选择图集根目录")
            if folder:
                self.root_dir = folder
            else:
                sys.exit() # Exit if no folder selected
        self.load_scenes()
    
    def load_bookmarks(self):
        if os.path.exists(self.bookmarks_file):
            try:
                with open(self.bookmarks_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.bookmarks = set(data.get("scenes", []))
            except:
                self.bookmarks = set()

    def save_bookmarks(self):
        try:
            with open(self.bookmarks_file, 'w', encoding='utf-8') as f:
                json.dump({"scenes": list(self.bookmarks)}, f)
        except Exception as e:
            print(f"Failed to save bookmarks: {e}")

    def load_scenes(self):
        self.scene_tree.clear()
        if not self.root_dir: return

        # Get all subdirectories
        scenes = [d for d in os.listdir(self.root_dir) if os.path.isdir(os.path.join(self.root_dir, d))]
        scenes.sort(key=natural_sort_key)

        for scene in scenes:
            item = QTreeWidgetItem(self.scene_tree)
            item.setData(0, Qt.UserRole, os.path.join(self.root_dir, scene))
            
            # Apply Bookmark Style
            self.update_scene_item_style(item, scene)

    def update_scene_item_style(self, item, scene_name):
        is_bookmarked = scene_name in self.bookmarks
        
        if is_bookmarked:
            item.setText(0, f"⭐ {scene_name}")
            # Light Yellow background for visibility
            brush = QBrush(QColor("#FFF9C4")) 
            item.setBackground(0, brush)
            # Make text bold?
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        else:
            item.setText(0, scene_name)
            item.setBackground(0, QBrush(Qt.NoBrush)) # Reset
            font = item.font(0)
            font.setBold(False)
            item.setFont(0, font)

    def on_scene_selected(self, item, column):
        path = item.data(0, Qt.UserRole)
        
        # Save current scene selection before switching
        if self.current_scene_path:
            self.scene_selection_history[self.current_scene_path] = self.node_list.currentRow()

        if self.current_scene_path == path:
            return
        
        self.current_scene_path = path
        self.load_nodes_for_scene(path)
        
        # Restore selection for this scene
        idx = 0
        if path in self.scene_selection_history:
            saved_idx = self.scene_selection_history[path]
            if 0 <= saved_idx < self.node_list.count():
                idx = saved_idx
        
        self.node_list.setCurrentRow(idx)
        self.on_node_selected(self.node_list.item(idx))

    def load_nodes_for_scene(self, scene_path):
        self.node_list.clear()
        if not os.path.exists(scene_path): return

        # Get folders and lnk files
        items = []
        try:
            # Use scandir for faster directory listing
            with os.scandir(scene_path) as it:
                for entry in it:
                    # Check for tags.txt to filter valid nodes
                    target_path = None
                    if entry.is_dir():
                        target_path = entry.path
                    elif entry.name.lower().endswith('.lnk'):
                        target_path = resolve_path(entry.path)
                    
                    # Only add if target is a directory and contains tags.txt
                    if target_path and os.path.isdir(target_path):
                        if os.path.exists(os.path.join(target_path, "tags.txt")):
                            items.append(entry.name)
        except OSError:
            pass
        items.sort(key=natural_sort_key)
        
        paths_to_diff = []
        for i, name in enumerate(items):
            item = QListWidgetItem(name)
            full_path = os.path.join(scene_path, name)
            item.setData(Qt.UserRole, full_path)
            
            # Remove MOCK DATA
            # mock_diff = (5, 2) 
            # item.setData(Qt.UserRole + 100, mock_diff) 
            
            self.node_list.addItem(item)
            paths_to_diff.append(os.path.normpath(full_path))
            
        if paths_to_diff:
            worker = DiffCalculatorWorker(paths_to_diff)
            worker.signals.result.connect(self.on_diff_calculated)
            self.threadpool.start(worker)

    def on_diff_calculated(self, diff_map):
        # print(f"Main: Received diff results. Count: {len(diff_map)}")
        updated_count = 0
        for i in range(self.node_list.count()):
            item = self.node_list.item(i)
            path = normalize_key(item.data(Qt.UserRole))
            if path in diff_map:
                item.setData(Qt.UserRole + 100, diff_map[path])
                updated_count += 1
        # print(f"Main: Updated {updated_count} items with real diff data.")
        self.node_list.viewport().update()

    def on_node_selected(self, item):
        if not item: return
        
        new_path = item.data(Qt.UserRole)
        
        # Only update history if the path is actually different
        # This prevents history update on re-clicking the same item or UI refreshes
        if self.current_node_path and self.current_node_path != new_path:
             self.previous_node_path = self.current_node_path
        
        self.current_node_path = new_path
        self.last_selected_node_index = self.node_list.row(item)
        
        # Resolve path (in case of .lnk)
        real_path = resolve_path(self.current_node_path)
        
        # 1. Load Tags (Must happen before calculating diff)
        self.load_tags(real_path)
        
        # 2. Update Diff with Previous Node
        self.update_diff_display(item)
        
        # 3. Setup Preview Logic (Lazy Load)
        self.setup_preview_sources(real_path)

    def load_tags(self, folder_path):
        tag_file = os.path.join(folder_path, "tags.txt")
        # Block signals to prevent triggering diff update while loading
        self.prompt_editor.blockSignals(True)
        if os.path.exists(tag_file):
            try:
                with open(tag_file, 'r', encoding='utf-8') as f:
                    self.prompt_editor.setText(f.read())
            except Exception as e:
                self.prompt_editor.setText(f"Error reading tags: {e}")
        else:
            self.prompt_editor.clear()
        self.prompt_editor.blockSignals(False)
        
        # Re-apply highlight if search is active
        if self.search_bar.isVisible():
            self.highlight_matches()

    def read_tags_content(self, folder_path):
        """Helper to read tags without loading into editor"""
        tag_file = os.path.join(folder_path, "tags.txt")
        if os.path.exists(tag_file):
            try:
                with open(tag_file, 'r', encoding='utf-8') as f:
                    return f.read()
            except:
                return ""
        return ""

    def on_prompt_edited(self):
        item = self.node_list.currentItem()
        if item:
            self.update_diff_display(item)
        if self.search_bar.isVisible():
            self.highlight_matches()
            
    def update_list_diff_for_current_item(self):
        if self.current_scene_path:
            paths = [os.path.normpath(self.node_list.item(i).data(Qt.UserRole)) for i in range(self.node_list.count())]
            worker = DiffCalculatorWorker(paths)
            worker.signals.result.connect(self.on_diff_calculated)
            self.threadpool.start(worker)

    def update_diff_display(self, current_item):
        if not self.previous_node_path:
            self.diff_viewer.clear()
            self.diff_viewer.setPlaceholderText("无上一次选中记录 (No previous selection)")
            return
            
        # Get Previous Node Path
        prev_path = resolve_path(self.previous_node_path)
        
        if not os.path.exists(prev_path):
             self.diff_viewer.clear()
             self.diff_viewer.setPlaceholderText("上一次选中的节点已不存在")
             return
        
        # Read Contents
        prev_text = get_ori_prompt(self.read_tags_content(prev_path))
        curr_text = get_ori_prompt(self.prompt_editor.toPlainText()) # Current active text
        
        # Simple Parser (Comma separated)
        def parse_tags(text):
            # Clean up newlines and extra spaces
            # Assuming format: tag1, tag2, tag3...
            # Also handle multiline if user uses lines
            clean = text.replace('\n', ',')
            return {t.strip() for t in clean.split(',') if t.strip()}
            
        prev_tags = parse_tags(prev_text)
        curr_tags = parse_tags(curr_text)
        
        added = curr_tags - prev_tags
        removed = prev_tags - curr_tags
        
        # Generate HTML
        html_content = ""
        if not added and not removed:
            html_content = "<span style='color:#888;'>无差异 (No changes)</span>"
        else:
            # Removed (Red)
            for tag in sorted(list(removed)):
                safe_tag = html.escape(tag)
                html_content += f"<span style='background-color:#ffe6e6; color:#cc0000; padding:2px 4px; border-radius:3px; margin:2px;'>- {safe_tag}</span> "
            
            if removed and added:
                html_content += "<br><br>"
                
            # Added (Green)
            for tag in sorted(list(added)):
                safe_tag = html.escape(tag)
                html_content += f"<span style='background-color:#e6ffe6; color:#006600; padding:2px 4px; border-radius:3px; margin:2px;'>+ {safe_tag}</span> "
                
        self.diff_viewer.setHtml(html_content)

    # --- Logic: Preview System ---

    def setup_preview_sources(self, node_path):
        """
        Lazy loading of sources.
        Does NOT resolve lnk or scan subfolders immediately.
        Just builds a list of candidates.
        """
        self.image_sources = []
        
        if not os.path.exists(node_path):
            self.update_preview_display()
            return

        # 1. Reference Image (Root)
        # Check immediately as it's the default view and usually fast (no COM)
        if has_images_fast(node_path):
            self.image_sources.append({
                "name": "参考图 (Ref)", 
                "path": node_path, 
                "status": "valid"
            })

        # 2. Sub-items (Lazy Load - don't resolve yet)
        try:
            entries = []
            with os.scandir(node_path) as it:
                for entry in it:
                    if entry.is_dir() or entry.name.lower().endswith('.lnk'):
                        # Get creation time to sort by newest first
                        try:
                            # On Windows, st_ctime is creation time. 
                            # On Unix, it's metadata change time (often close enough for new folders)
                            ctime = entry.stat().st_ctime
                        except OSError:
                            ctime = 0
                        entries.append((ctime, entry.name))
            
            # Sort by creation time: Newest first (Reverse)
            entries.sort(key=lambda x: x[0], reverse=True)
            
            for _, name in entries:
                full_path = os.path.join(node_path, name)
                self.image_sources.append({
                    "name": name,
                    "path": full_path, # Could be .lnk or folder
                    "status": "pending" # We haven't checked content yet
                })
        except OSError:
            pass

        # Initial Load: Try to find the first valid source starting from index 0
        self.current_source_index = -1
        self.find_and_load_source(start_index=0, direction=1)

    def find_and_load_source(self, start_index, direction):
        """
        Iterates from start_index in direction (+1 or -1) to find a valid source.
        Resolves pending sources on the fly.
        """
        idx = start_index
        found = False
        
        # Loop until we find a valid source or run out of candidates
        while 0 <= idx < len(self.image_sources):
            source = self.image_sources[idx]
            
            # Validate if pending
            if source['status'] == 'pending':
                # 1. Resolve Path (if lnk)
                real_path = source['path']
                if real_path.lower().endswith('.lnk'):
                    real_path = resolve_path(real_path)
                
                # 2. Check if it is a directory and has images
                if os.path.isdir(real_path) and has_images_fast(real_path):
                    source['path'] = real_path # Update to resolved path
                    source['status'] = 'valid'
                else:
                    source['status'] = 'invalid'
            
            if source['status'] == 'valid':
                self.current_source_index = idx
                self.load_images_from_source()
                found = True
                break
            
            # If invalid, continue to next
            idx += direction

        if not found:
            # If we couldn't find anything in that direction
            # If this was initial load (index -1), show empty state
            if self.current_source_index == -1:
                self.current_image_list = []
                self.update_preview_display()

    def load_images_from_source(self):
        if not self.image_sources or self.current_source_index < 0:
            self.current_image_list = []
            self.update_preview_display()
            return

        source = self.image_sources[self.current_source_index]
        path = source['path']
        
        try:
            files = [f for f in os.listdir(path) if is_image_file(f)]
            files.sort(key=natural_sort_key)
            self.current_image_list = [os.path.join(path, f) for f in files]
            self.current_image_index = 0
        except:
            self.current_image_list = []

        self.update_preview_display()

    def update_preview_display(self):
        if not self.current_image_list:
            self.preview_label.setText("无图片")
            self.preview_label.setPixmap(QPixmap()) # Clear
            
            source_name = "None"
            if 0 <= self.current_source_index < len(self.image_sources):
                source_name = self.image_sources[self.current_source_index]['name']
                
            self.source_info_label.setText(f"Folder: {source_name} | No Images")
            return

        img_path = self.current_image_list[self.current_image_index]
        
        # OPTIMIZATION: Use QImageReader to load scaled image directly
        reader = QImageReader(img_path)
        reader.setAutoTransform(True) 
        
        orig_size = reader.size()
        if not orig_size.isEmpty():
            target_size = self.preview_label.size()
            scale_factor = min(target_size.width() / orig_size.width(), 
                               target_size.height() / orig_size.height())
            if scale_factor < 1.0:
                new_width = int(orig_size.width() * scale_factor)
                new_height = int(orig_size.height() * scale_factor)
                reader.setScaledSize(QSize(new_width, new_height))
        
        img = reader.read()
        
        if not img.isNull():
            self.preview_label.setPixmap(QPixmap.fromImage(img))
        else:
            self.preview_label.setText("无法加载图片")

        # Update Info Label
        source_name = self.image_sources[self.current_source_index]['name']
        img_name = os.path.basename(img_path)
        self.source_info_label.setText(f"Folder: {source_name} | Img: {img_name} ({self.current_image_index+1}/{len(self.current_image_list)})")

    def next_image_source(self):
        # Left Click: Next folder (No Loop, skip invalid)
        if self.current_source_index < len(self.image_sources) - 1:
            self.find_and_load_source(self.current_source_index + 1, 1)

    def prev_image_source(self):
        # Right Click: Previous folder (No Loop, skip invalid)
        if self.current_source_index > 0:
            self.find_and_load_source(self.current_source_index - 1, -1)

    def scroll_image(self, direction):
        # Wheel: Switch images in current folder (No Loop)
        if not self.current_image_list: return
        
        new_index = self.current_image_index + direction
        if 0 <= new_index < len(self.current_image_list):
            self.current_image_index = new_index
            self.update_preview_display()

    # --- Logic: Editing & Actions ---

    def save_prompt(self):
        if not self.current_node_path: return
        real_path = resolve_path(self.current_node_path)
        tag_file = os.path.join(real_path, "tags.txt")
        
        try:
            with open(tag_file, 'w', encoding='utf-8') as f:
                f.write(self.prompt_editor.toPlainText())
            QMessageBox.information(self, "Success", "提示词已保存")
            self.update_list_diff_for_current_item()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def add_node(self):
        if not self.current_scene_path:
            QMessageBox.warning(self, "Warning", "请先选择一个场景")
            return

        name, ok = QInputDialog.getText(self, "新增节点", "输入动作名称:")
        if ok and name:
            # Auto numbering based on existing items
            count = self.node_list.count() + 1
            # NEW FORMAT: 1_Name
            folder_name = f"{count}_{name}" 
            new_path = os.path.join(self.current_scene_path, folder_name)
            
            try:
                os.makedirs(new_path)
                # Create empty tags.txt
                with open(os.path.join(new_path, "tags.txt"), 'w', encoding='utf-8') as f:
                    f.write(self.prompt_editor.toPlainText()) # Save current editor text to new node? Or empty? Assuming editor text.
                
                self.load_nodes_for_scene(self.current_scene_path)
                # Select new item
                items = self.node_list.findItems(folder_name, Qt.MatchExactly)
                if items:
                    self.node_list.setCurrentItem(items[0])
            except Exception as e:
                QMessageBox.critical(self, "Error", f"创建失败: {e}")

    def on_node_reordered(self):
        """
        Called when Drag & Drop reordering happens within the list.
        Renames folders to match new order.
        Supports automatic migration from (1)Name to 1_Name.
        """
        if not self.current_scene_path: return
        
        # Iterate through list items in new order
        for i in range(self.node_list.count()):
            item = self.node_list.item(i)
            old_full_path = item.data(Qt.UserRole)
            old_name = os.path.basename(old_full_path)
            
            # Extract pure name using helper that supports both formats
            pure_name = clean_node_name(old_name)
            
            # NEW FORMAT: i_Name
            new_name = f"{i+1}_{pure_name}"
            new_full_path = os.path.join(self.current_scene_path, new_name)
            
            if old_full_path != new_full_path:
                try:
                    os.rename(old_full_path, new_full_path)
                    item.setText(new_name)
                    item.setData(Qt.UserRole, new_full_path)
                except Exception as e:
                    print(f"Rename failed: {e}")
        self.update_list_diff_for_current_item()

    # --- Logic: Cross-Scene Linking (Drag to Tree) ---

    def tree_drop_event(self, event: QDropEvent):
        """Handle dropping a Node onto a Scene in the Tree."""
        target_item = self.scene_tree.itemAt(event.position().toPoint())
        if not target_item: 
            event.ignore()
            return
            
        target_scene_path = target_item.data(0, Qt.UserRole)
        
        source_items = self.node_list.selectedItems()
        if not source_items: return

        for item in source_items:
            source_path = item.data(Qt.UserRole)
            source_real_path = resolve_path(source_path)
            
            # Determine new link name
            name = os.path.basename(source_real_path)
            pure_name = clean_node_name(name)
            
            # Determine next number in target scene
            existing = os.listdir(target_scene_path)
            count = len([x for x in existing if os.path.isdir(os.path.join(target_scene_path, x)) or x.endswith('.lnk')]) + 1
            
            # NEW FORMAT: 1_Name.lnk
            link_name = f"{count}_{pure_name}.lnk"
            link_full_path = os.path.join(target_scene_path, link_name)
            
            create_shortcut(source_real_path, link_full_path)

        QMessageBox.information(self, "Info", f"已链接 {len(source_items)} 个动作到场景: {target_item.text(0)}")
        event.accept()

    # --- Logic: New Scene Composition ---

    def create_new_scene_mode(self):
        """
        User wants to create a new scene by picking nodes.
        UI flow: Ask for name -> Create Folder -> Let user drag nodes.
        Since we support drag-to-tree, we just create the folder and select it!
        """
        name, ok = QInputDialog.getText(self, "新建组合场景", "输入新场景名称:")
        if ok and name:
            new_scene_path = os.path.join(self.root_dir, name)
            try:
                os.makedirs(new_scene_path, exist_ok=True)
                self.load_scenes()
                
                # Find and select the new scene in tree
                iterator = QTreeWidgetItemIterator(self.scene_tree)
                while iterator.value():
                    item = iterator.value()
                    if item.text(0) == name:
                        self.scene_tree.setCurrentItem(item)
                        self.on_scene_selected(item, 0)
                        break
                    iterator += 1 # 修复: 必须推进迭代器，否则会卡死在 while 循环中
                
                QMessageBox.information(self, "提示", "场景已创建。请从其他场景拖拽动作节点到左侧树状图的该场景上进行组合。")
                
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    # --- Logic: Batch Operations ---

    def batch_export(self):
        items = self.node_list.selectedItems()
        if not items: return
        
        all_tags = []
        for item in items:
            path = resolve_path(item.data(Qt.UserRole))
            tag_file = os.path.join(path, "tags.txt")
            if os.path.exists(tag_file):
                with open(tag_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    split = "=\n"
                    if "type,\n" in content:
                        split = "type,\n"
                    real_content = content.split(split)[0]
                    all_tags.append(f"{real_content}")
        
        full_content = "\n\n".join(all_tags)
        self.prompt_editor.setText(full_content)
        
        # 创建临时文件并写入内容
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix='batch_export_') as temp_file:
            temp_file.write(full_content)
            temp_file_path = temp_file.name
        
        # 将临时文件复制到剪贴板，这样可以直接粘贴到其他文件夹
        clipboard = QApplication.clipboard()
        clipboard.setMimeData(self.create_mime_data_with_file(temp_file_path))
        
        # 显示信息提示用户
        QMessageBox.information(self, "批量导出", f"内容已导出到临时文件，可直接粘贴到目标文件夹:\n{temp_file_path}")
    
    def create_mime_data_with_file(self, file_path):
        """
        创建包含文件路径的MIME数据，用于文件拖放和粘贴操作
        """
        from PySide6.QtCore import QMimeData
        mime_data = QMimeData()
        # 设置文件URL列表
        urls = [QUrl.fromLocalFile(file_path)]
        mime_data.setUrls(urls)
        return mime_data

    def batch_edit(self):
        items = self.node_list.selectedItems()
        if not items: return
        
        text, ok = QInputDialog.getMultiLineText(self, "批量追加", "输入要追加的提示词:")
        if ok and text:
            # key = ','.join(text.split(',')[:2])
            # value = ','.join(text.split(',')[2:])
            prefix = text.split(',')[0]

            key = f"tag_node,{os.path.basename(self.current_scene_path)}"
            value = text
            for item in items:
                path = resolve_path(item.data(Qt.UserRole))
                # tag_file = os.path.join(path, "tags.txt")
                
                # content = ""
                # if os.path.exists(tag_file):
                #     with open(tag_file, 'r', encoding='utf-8') as f:
                #         content = f.read()
                
                # # Append
                # if content and not content.endswith('\n'):
                #     content += '\n'
                # content += text + "," # Append comma safely?
                
                # with open(tag_file, 'w', encoding='utf-8') as f:
                #     f.write(content)


                reset_ainode_ext_node_type(path, f"{key},{prefix}", f"{key},{value}")
            QMessageBox.information(self, "Success", f"key:{key} item:{value} 编辑成功")
            # Refresh current view if needed
            if self.node_list.currentItem() in items:
                self.load_tags(resolve_path(self.node_list.currentItem().data(Qt.UserRole)))

    def run_process(self):
        """
        Run with dialog to add extra params.
        """
        items = self.node_list.selectedItems()
        paths = [item.data(Qt.UserRole) for item in items]
        
        if not paths:
            # Fallback if no specific node selected? Maybe run current scene context?
            # Or assume current scene path itself
            paths = [self.current_scene_path]

        # 1. Select BAT script (Save selection for session?)
        if not hasattr(self, 'bat_script_path') or not self.bat_script_path:
             start_dir = self.root_dir if self.root_dir else ""
             file_path, _ = QFileDialog.getOpenFileName(self, "选择运行脚本 (Select .bat script)", start_dir, "Batch Files (*.bat);;All Files (*)")
             if file_path:
                 self.bat_script_path = file_path
             else:
                 return # Cancelled

        # 2. Show Params Dialog
        dlg = RunParamsDialog(self.run_params_file, self)
        if dlg.exec() == QDialog.Accepted:
            extra_params = dlg.get_params_list() # ["key=val", "key2=val2"]
            
            # 3. Construct Command
            # Command structure: script.bat "path1" "path2" "key=val" "key2=val2"
            cmd = [self.bat_script_path] + paths + extra_params
            print(f"Running command: {cmd}")
            try:
                # Windows specific flag to open a new console window
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.CREATE_NEW_CONSOLE
                
                subprocess.Popen(cmd, cwd=os.path.dirname(self.bat_script_path), creationflags=creation_flags)
            except Exception as e:
                QMessageBox.critical(self, "Run Error", f"执行脚本失败:\n{e}")

    # --- Context Menus ---

    def show_scene_context_menu(self, pos):
        item = self.scene_tree.itemAt(pos)
        if not item: return
        
        path = item.data(0, Qt.UserRole)
        name = item.text(0).replace("⭐ ", "") # Clean name for logic if needed
        is_bookmarked = name in self.bookmarks
        
        menu = QMenu()
        
        # Bookmark Action
        bookmark_text = "取消书签 (Remove Bookmark)" if is_bookmarked else "加入书签 (Add Bookmark)"
        bookmark_act = QAction(bookmark_text, self)
        bookmark_act.triggered.connect(lambda: self.toggle_bookmark(item, name))
        menu.addAction(bookmark_act)
        
        menu.addSeparator()
        
        open_act = QAction("在资源管理器中打开", self)
        open_act.triggered.connect(lambda: os.startfile(path))
        menu.addAction(open_act)
        
        menu.exec(self.scene_tree.mapToGlobal(pos))

    def toggle_bookmark(self, item, name):
        if name in self.bookmarks:
            self.bookmarks.remove(name)
        else:
            self.bookmarks.add(name)
        
        self.save_bookmarks()
        self.update_scene_item_style(item, name)

    def show_node_context_menu(self, pos):
        item = self.node_list.itemAt(pos)
        if not item: return
        
        # Resolve for opening
        resolved_path = resolve_path(item.data(Qt.UserRole))
        
        menu = QMenu()
        
        open_act = QAction("在资源管理器中打开", self)
        open_act.triggered.connect(lambda: os.startfile(resolved_path))
        menu.addAction(open_act)
        
        menu.addSeparator()
        
        reset_sort_act = QAction("重置排序 (Reset Sorting)", self)
        reset_sort_act.triggered.connect(self.reset_node_sorting)
        menu.addAction(reset_sort_act)
        
        menu.addSeparator()
        
        del_act = QAction("删除 (移至回收站)", self)
        del_act.triggered.connect(self.delete_selected_nodes)
        menu.addAction(del_act)
        
        menu.exec(self.node_list.mapToGlobal(pos))

    def reset_node_sorting(self):
        if not self.current_scene_path: return
        
        # confirm
        if QMessageBox.question(self, "确认", "确定要移除所有序号并重置排序吗?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        # 1. Rename all folders to remove prefixes
        for i in range(self.node_list.count()):
            item = self.node_list.item(i)
            current_full_path = item.data(Qt.UserRole)
            current_name = os.path.basename(current_full_path)
            
            pure_name = clean_node_name(current_name)
            
            if pure_name != current_name:
                new_full_path = os.path.join(self.current_scene_path, pure_name)
                try:
                    os.rename(current_full_path, new_full_path)
                    item.setText(pure_name)
                    item.setData(Qt.UserRole, new_full_path)
                except Exception as e:
                    print(f"Error renaming {current_name} to {pure_name}: {e}")
        
        # 2. Sort the list widget items alphabetically
        self.node_list.sortItems(Qt.AscendingOrder)
        self.update_list_diff_for_current_item()

    def delete_selected_nodes(self):
        items = self.node_list.selectedItems()
        if not items: return
        
        confirm = QMessageBox.question(self, "确认删除", f"确定要将这 {len(items)} 个节点移至回收站吗?", QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            bakup_path = os.path.join(self.current_scene_path, "add")
            os.makedirs(bakup_path, exist_ok=True)
            for item in items:
                path = item.data(Qt.UserRole) # Use direct path (lnk or folder)
                shutil.copy(path, bakup_path)
                if QFile.moveToTrash(path):
                    # Remove from list
                    row = self.node_list.row(item)
                    self.node_list.takeItem(row)
                else:
                    print(f"Failed to delete {path}")
            self.update_list_diff_for_current_item()

# --- Entry Point ---
from PySide6.QtWidgets import QTreeWidgetItemIterator # Added specific import needed later

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Optional: Enable High DPI support
    # app.setAttribute(Qt.AA_EnableHighDpiScaling)

    window = PromptManagerApp()
    window.show()
    
    sys.exit(app.exec())