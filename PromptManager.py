import sys
import os
import re
import shutil
import glob
import subprocess
import datetime
from pathlib import Path
from natsort import natsorted  # 用于自然的数字排序
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QListWidget, QListWidgetItem, QTextEdit, 
                             QLabel, QPushButton, QSplitter, QFileDialog, QInputDialog,
                             QMenu, QMessageBox, QAbstractItemView, QDialog, QLineEdit,
                             QDialogButtonBox, QGridLayout, QTreeWidget, QTreeWidgetItem)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QPoint, QMimeData
from PyQt6.QtGui import QAction, QPixmap, QIcon, QDrag, QCursor, QDesktopServices
from PyQt6.QtCore import QUrl

# 尝试导入 pywin32 用于处理快捷方式
try:
    import win32com.client
except ImportError:
    print("请安装 pywin32: pip install pywin32")
    sys.exit(1)

# ================= 配置与工具函数 =================

# 全局缓存 Shell 对象，避免每次调用 resolve_lnk 都重新创建，解决卡顿问题
_shell_instance = None

def get_shell():
    global _shell_instance
    if _shell_instance is None:
        try:
            _shell_instance = win32com.client.Dispatch("WScript.Shell")
        except Exception as e:
            print(f"Shell Dispatch Error: {e}")
    return _shell_instance

def resolve_lnk(path):
    """解析 Windows .lnk 快捷方式指向的真实路径"""
    path = str(path)
    if not path.lower().endswith('.lnk'):
        return path
    try:
        shell = get_shell()
        if shell:
            shortcut = shell.CreateShortCut(path)
            return shortcut.TargetPath
    except Exception as e:
        print(f"解析快捷方式失败: {e}")
    return path

def create_lnk(target_path, lnk_path):
    """创建快捷方式"""
    try:
        shell = get_shell()
        if shell:
            shortcut = shell.CreateShortCut(str(lnk_path))
            shortcut.TargetPath = str(target_path)
            shortcut.Save()
    except Exception as e:
        print(f"创建快捷方式失败: {e}")

def natural_sort_key(s):
    """用于自然排序 (1), (2), (10)"""
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', s)]

# ================= 自定义控件 =================

class ImagePreviewLabel(QLabel):
    """
    自定义图片预览控件，支持滚轮和点击交互
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200) # 稍微调小一点，适应弹窗
        self.setStyleSheet("background-color: #2b2b2b; border: 1px solid #555; color: #aaa;")
        self.setText("无预览图")
        self.setScaledContents(False) # 自行控制缩放以保持比例
        
        # 数据状态
        self.current_node_path = None
        self.folder_list = []      # [(name, path), ...] 文件夹列表
        self.current_folder_idx = 0
        self.image_list = []       # 当前文件夹下的图片路径列表
        self.current_image_idx = 0

    def load_node(self, node_path):
        """加载节点，扫描图片"""
        self.current_node_path = resolve_lnk(node_path)
        self.scan_folders()
        self.load_images_in_current_folder()
        self.show_image()
    
    def has_images(self, folder):
        """快速检查文件夹下是否有图片，不加载所有文件列表"""
        valid_ext = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
        if not os.path.exists(folder):
            return False
        try:
            # 使用 scandir 提高性能
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in valid_ext:
                        return True
        except:
            pass
        return False

    def scan_folders(self):
        """扫描节点下的所有包含图片的文件夹"""
        if not self.current_node_path or not os.path.exists(self.current_node_path):
            self.folder_list = []
            return

        candidates = []

        # 1. 根目录 (通常放参考图 tmp.png)
        candidates.append(("(根目录)", self.current_node_path))
        
        # 2. 子目录 (通常是日期文件夹)
        try:
            # 获取所有项目
            items = os.listdir(self.current_node_path)
            subdirs = []
            for item in items:
                full_path = os.path.join(self.current_node_path, item)
                
                # 处理快捷方式文件夹
                real_path = full_path
                if item.lower().endswith('.lnk'):
                    real_path = resolve_lnk(full_path)
                
                if os.path.isdir(real_path):
                    # 获取修改时间用于排序
                    mtime = os.path.getmtime(real_path)
                    subdirs.append({
                        "name": item,
                        "path": real_path,
                        "mtime": mtime
                    })
            
            # 按时间倒序排列 (最新的在前)
            subdirs.sort(key=lambda x: x["mtime"], reverse=True)
            
            for d in subdirs:
                candidates.append((d["name"], d["path"]))
                
        except Exception as e:
            print(f"扫描子文件夹出错: {e}")

        # 过滤掉没有图片的文件夹
        self.folder_list = []
        has_tmp_in_root = False
        
        for name, path in candidates:
            # 优化：只检查有没有图片，不完全加载
            if self.has_images(path):
                self.folder_list.append((name, path))
                
                # 特殊检查：如果是根目录，看看是不是只有 tmp.png
                # 这里为了性能，如果确定有图，稍后再确认是不是tmp
                # 但为了逻辑准确，还是得简单看一下文件名
                if name == "(根目录)":
                    try:
                        for f in os.listdir(path):
                            if "tmp" in f.lower() and f.lower().endswith(('.png', '.jpg')):
                                has_tmp_in_root = True
                                break
                    except: pass

        # 默认选中逻辑：
        self.current_folder_idx = 0
        if len(self.folder_list) > 1 and not has_tmp_in_root:
            # 优先显示最新的子文件夹
            self.current_folder_idx = 1
        
        if not self.folder_list:
            self.current_folder_idx = -1

    def get_images_in_folder(self, folder):
        valid_ext = ['.png', '.jpg', '.jpeg', '.webp', '.bmp']
        files = []
        if not os.path.exists(folder):
            return []
        try:
            for f in os.listdir(folder):
                _, ext = os.path.splitext(f)
                if ext.lower() in valid_ext:
                    files.append(os.path.join(folder, f))
        except:
            pass
        # 按名称排序
        files.sort(key=lambda x: natural_sort_key(os.path.basename(x)))
        return files

    def load_images_in_current_folder(self):
        if self.current_folder_idx < 0 or self.current_folder_idx >= len(self.folder_list):
            self.image_list = []
            return
        
        name, path = self.folder_list[self.current_folder_idx]
        self.image_list = self.get_images_in_folder(path)
        self.current_image_idx = 0
        
        # 如果在根目录，尝试找 tmp.png 作为默认
        if name == "(根目录)":
            for i, img_path in enumerate(self.image_list):
                if "tmp" in os.path.basename(img_path).lower():
                    self.current_image_idx = i
                    break

    def show_image(self):
        if not self.image_list:
            self.setText("该节点下无图片\n(滚轮/点击可切换文件夹)")
            self.setPixmap(QPixmap())
            self.setToolTip("")
            return
        
        img_path = self.image_list[self.current_image_idx]
        pixmap = QPixmap(img_path)
        
        if pixmap.isNull():
            self.setText("图片损坏")
            return
            
        # 缩放
        scaled_pixmap = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled_pixmap)
        
        # 更新信息
        folder_name = self.folder_list[self.current_folder_idx][0]
        file_name = os.path.basename(img_path)
        idx_info = f"{self.current_image_idx + 1}/{len(self.image_list)}"
        folder_info = f"{self.current_folder_idx + 1}/{len(self.folder_list)}"
        
        info_text = f"目录: {folder_name} ({folder_info})\n文件: {file_name} ({idx_info})\n(左键:下个文件夹 | 右键:上个文件夹 | 滚轮:切图)"
        self.setToolTip(info_text)

    def wheelEvent(self, event):
        """滚轮切换图片"""
        if not self.image_list: return
        
        delta = event.angleDelta().y()
        if delta > 0: # 向上滚，上一张
            self.current_image_idx = (self.current_image_idx - 1) % len(self.image_list)
        else: # 向下滚，下一张
            self.current_image_idx = (self.current_image_idx + 1) % len(self.image_list)
        self.show_image()

    def mousePressEvent(self, event):
        """鼠标点击切换文件夹 - 不循环"""
        if len(self.folder_list) <= 1: return
        
        updated = False
        if event.button() == Qt.MouseButton.LeftButton:
            # 下一个文件夹 (如果没有下一个，则不动)
            if self.current_folder_idx < len(self.folder_list) - 1:
                self.current_folder_idx += 1
                updated = True
                
        elif event.button() == Qt.MouseButton.RightButton:
            # 上一个文件夹 (如果没有上一个，则不动)
            if self.current_folder_idx > 0:
                self.current_folder_idx -= 1
                updated = True
        
        if updated:
            self.load_images_in_current_folder()
            self.show_image()

    def resizeEvent(self, event):
        self.show_image()
        super().resizeEvent(event)


class DraggableListWidget(QListWidget):
    """支持拖拽排序的列表"""
    itemMoved = pyqtSignal() # 拖拽完成信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def dropEvent(self, event):
        # 检查来源是否是 QTreeWidget (从新建场景对话框的树拖拽过来)
        source = event.source()
        if isinstance(source, QTreeWidget):
            # 如果是从树拖拽到这里，需要特殊处理
            # 这里我们只处理同类型的拖拽，跨控件拖拽在 SceneBuilderDialog 内部处理
            pass
            
        super().dropEvent(event)
        self.itemMoved.emit()
        
    def contextMenuEvent(self, event):
        # 允许父级处理
        super().contextMenuEvent(event)

class TargetListWidget(QListWidget):
    """用于场景构建器的目标列表，支持从 TreeWidget 接收拖拽"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        if event.source() != self:
             event.accept()
        else:
             super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.source() != self:
             event.accept()
        else:
             super().dragMoveEvent(event)

    def dropEvent(self, event):
        source = event.source()
        if isinstance(source, QTreeWidget):
            # 处理从树拖拽过来的项目
            items = source.selectedItems()
            for item in items:
                # 只添加动作节点（子节点），不添加场景文件夹（父节点）
                if item.childCount() == 0 and item.data(0, Qt.ItemDataRole.UserRole):
                    name = item.text(0)
                    path = item.data(0, Qt.ItemDataRole.UserRole)
                    
                    # 创建新项目
                    new_item = QListWidgetItem(name)
                    new_item.setData(Qt.ItemDataRole.UserRole, path)
                    self.addItem(new_item)
            event.accept()
        else:
            # 内部排序
            super().dropEvent(event)

# ================= 场景构建对话框 (重构版) =================

class SceneBuilderDialog(QDialog):
    """用于新建场景组合的对话框 - 树形视图版"""
    def __init__(self, library_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建组合场景")
        self.resize(1000, 700)
        self.library_path = library_path
        
        layout = QVBoxLayout(self)

        # 顶部：名称输入
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("新场景名称:"))
        self.name_edit = QLineEdit()
        top_layout.addWidget(self.name_edit)
        layout.addLayout(top_layout)

        # 主体：Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # --- 左侧：资源树 + 预览 ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        left_layout.addWidget(QLabel("动作库 (可拖拽到右侧):"))
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("场景与节点")
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True) # 允许拖拽
        self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed)
        
        left_layout.addWidget(self.tree)
        
        # 左下角预览
        left_layout.addWidget(QLabel("选中节点预览:"))
        self.preview_label = ImagePreviewLabel()
        self.preview_label.setMinimumSize(200, 200)
        left_layout.addWidget(self.preview_label)
        
        left_widget.setLayout(left_layout)
        splitter.addWidget(left_widget)

        # --- 中间按钮 (可选) ---
        # 如果只想靠拖拽，可以不要，但为了易用性保留
        btn_widget = QWidget()
        btn_layout = QVBoxLayout(btn_widget)
        btn_add = QPushButton("添加 ->")
        btn_add.clicked.connect(self.add_nodes_from_tree)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_add)
        btn_layout.addStretch()
        # splitter.addWidget(btn_widget) # 放在 Splitter 里或者直接布局

        # --- 右侧：目标列表 ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        right_layout.addWidget(QLabel("新场景包含的动作 (拖拽排序):"))
        self.target_list = TargetListWidget()
        right_layout.addWidget(self.target_list)
        
        right_widget.setLayout(right_layout)
        splitter.addWidget(right_widget)
        
        # 设置 Splitter 比例
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        
        # 组合中间部分
        mid_layout = QHBoxLayout()
        mid_layout.addWidget(splitter)
        # 将按钮条放在中间覆盖在 splitter 上可能比较复杂，简单起见放在两栏之间
        # 这里为了布局简单，把按钮放在 splitter 的左侧面板底部或者独立
        # 我们用一个包含 splitter 和 按钮的布局
        
        container_layout = QHBoxLayout()
        container_layout.addWidget(left_widget, 1)
        container_layout.addLayout(btn_layout)
        container_layout.addWidget(right_widget, 1)
        
        layout.addLayout(container_layout)

        # 底部
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        # 初始化数据
        self.init_tree_data()

    def init_tree_data(self):
        self.tree.clear()
        if not self.library_path: return
        
        scene_dirs = [d for d in os.listdir(self.library_path) if os.path.isdir(os.path.join(self.library_path, d))]
        scene_dirs.sort(key=natural_sort_key)
        
        for scene_name in scene_dirs:
            scene_path = os.path.join(self.library_path, scene_name)
            scene_item = QTreeWidgetItem(self.tree)
            scene_item.setText(0, scene_name)
            scene_item.setData(0, Qt.ItemDataRole.UserRole, None) # 文件夹没有路径数据用于拖拽
            
            # 加载节点
            try:
                nodes = sorted(os.listdir(scene_path), key=natural_sort_key)
                for node_file in nodes:
                    full_path = os.path.join(scene_path, node_file)
                    if os.path.isdir(full_path) or full_path.endswith('.lnk'):
                        # 节点显示名
                        display_name = re.sub(r'^\(\d+\)', '', node_file)
                        if display_name.lower().endswith('.lnk'):
                            display_name = display_name[:-4]
                        
                        node_item = QTreeWidgetItem(scene_item)
                        node_item.setText(0, display_name)
                        node_item.setData(0, Qt.ItemDataRole.UserRole, full_path)
            except: pass
        
        # self.tree.expandAll() # 不再默认展开

    def on_tree_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            self.preview_label.setText("未选择")
            return
            
        # 预览选中的最后一个有效项
        item = items[-1]
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.preview_label.load_node(path)
        else:
            self.preview_label.setText("请选择动作节点")

    def add_nodes_from_tree(self):
        for item in self.tree.selectedItems():
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path: # 只有节点有 path，父文件夹是 None
                name = item.text(0)
                # 检查是否已存在（可选，这里允许重复）
                new_item = QListWidgetItem(name)
                new_item.setData(Qt.ItemDataRole.UserRole, path)
                self.target_list.addItem(new_item)

    def get_result(self):
        scene_name = self.name_edit.text().strip()
        nodes = []
        for i in range(self.target_list.count()):
            item = self.target_list.item(i)
            nodes.append({
                "name": item.text(),
                "path": item.data(Qt.ItemDataRole.UserRole)
            })
        return scene_name, nodes

# ================= 主窗口 =================

class PromptManagerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 提示词库管理工具")
        self.resize(1300, 850)
        
        self.library_path = ""
        self.current_scene_path = ""
        self.current_node_path = ""
        
        # 记录场景的选择历史: {scene_path: selected_row_index}
        self.scene_selection_history = {}

        self.setup_ui()
        self.init_directory()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # === 区域 1: 目录树 (场景列表) ===
        scene_layout = QVBoxLayout()
        scene_layout.addWidget(QLabel("📂 场景列表 (右键管理)"))
        self.scene_list = QListWidget()
        self.scene_list.itemClicked.connect(self.on_scene_selected)
        # 启用右键菜单
        self.scene_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scene_list.customContextMenuRequested.connect(self.show_scene_context_menu)
        
        scene_layout.addWidget(self.scene_list)
        
        btn_new_scene = QPushButton("➕ 新建组合场景")
        btn_new_scene.clicked.connect(self.on_create_scene)
        scene_layout.addWidget(btn_new_scene)

        # === 区域 2: 动作节点框 ===
        node_layout = QVBoxLayout()
        node_layout.addWidget(QLabel("🎬 动作节点 (拖拽排序/右键管理)"))
        self.node_list = DraggableListWidget()
        self.node_list.itemClicked.connect(self.on_node_selected)
        self.node_list.itemMoved.connect(self.on_nodes_reordered)
        # 启用右键菜单
        self.node_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.node_list.customContextMenuRequested.connect(self.show_node_context_menu)
        
        node_layout.addWidget(self.node_list)

        # 节点操作按钮
        node_btns = QHBoxLayout()
        btn_add_node = QPushButton("新增节点")
        btn_add_node.clicked.connect(self.on_add_node)
        node_btns.addWidget(btn_add_node)
        
        btn_export = QPushButton("批量导出")
        btn_export.clicked.connect(self.on_batch_export)
        node_btns.addWidget(btn_export)
        
        node_layout.addLayout(node_btns)

        # === 区域 3: 操作与预览 ===
        op_layout = QVBoxLayout()
        
        # 3.1 预览框
        self.preview_label = ImagePreviewLabel()
        op_layout.addWidget(self.preview_label, 1) # 占比 1

        # 3.2 操作框 (提示词编辑器)
        op_layout.addWidget(QLabel("📝 提示词编辑"))
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("在此处编辑提示词 tags...")
        op_layout.addWidget(self.editor, 1)

        # 3.3 底部功能区
        action_layout = QHBoxLayout()
        
        btn_save = QPushButton("保存提示词")
        btn_save.clicked.connect(self.save_current_tags)
        action_layout.addWidget(btn_save)

        btn_batch_edit = QPushButton("批量追加编辑")
        btn_batch_edit.clicked.connect(self.on_batch_edit)
        action_layout.addWidget(btn_batch_edit)

        btn_run = QPushButton("▶ 运行 (Run)")
        btn_run.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 5px;")
        btn_run.clicked.connect(self.on_run)
        action_layout.addWidget(btn_run)

        op_layout.addLayout(action_layout)

        # 使用 Splitter 分割三个区域
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        w_scene = QWidget()
        w_scene.setLayout(scene_layout)
        splitter.addWidget(w_scene)
        
        w_node = QWidget()
        w_node.setLayout(node_layout)
        splitter.addWidget(w_node)
        
        w_op = QWidget()
        w_op.setLayout(op_layout)
        splitter.addWidget(w_op)

        # 设置初始比例 1:1:2
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 2)

        main_layout.addWidget(splitter)

    def init_directory(self):
        # 让用户选择库的根目录
        folder = QFileDialog.getExistingDirectory(self, "选择提示词库根目录")
        if folder:
            self.library_path = folder
            self.refresh_scene_list()
        else:
            sys.exit(0)

    # ================= 场景逻辑 =================

    def refresh_scene_list(self):
        self.scene_list.clear()
        if not self.library_path: return
        
        dirs = [d for d in os.listdir(self.library_path) if os.path.isdir(os.path.join(self.library_path, d))]
        # 自然排序
        dirs.sort(key=natural_sort_key)
        
        for d in dirs:
            item = QListWidgetItem(d)
            item.setData(Qt.ItemDataRole.UserRole, os.path.join(self.library_path, d))
            self.scene_list.addItem(item)

    def on_scene_selected(self, item):
        # 1. 保存前一个场景的选择状态 (如果存在)
        if self.current_scene_path:
            current_row = self.node_list.currentRow()
            if current_row >= 0:
                self.scene_selection_history[self.current_scene_path] = current_row

        # 2. 切换场景
        path = item.data(Qt.ItemDataRole.UserRole)
        self.current_scene_path = path
        
        # 3. 刷新节点列表
        self.refresh_node_list()
        
        # 4. 恢复历史选择 (默认选中第一个)
        target_row = self.scene_selection_history.get(path, 0)
        
        if self.node_list.count() > 0:
            if target_row >= self.node_list.count():
                target_row = 0
            self.node_list.setCurrentRow(target_row)
            # 手动触发点击逻辑以加载内容
            self.on_node_selected(self.node_list.item(target_row))
        else:
            # 清空右侧
            self.current_node_path = ""
            self.editor.clear()
            self.preview_label.setText("无节点")
            self.preview_label.setPixmap(QPixmap())

    def on_create_scene(self):
        """创建新组合场景 - 使用新对话框"""
        dialog = SceneBuilderDialog(self.library_path, self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, nodes = dialog.get_result()
            if not name:
                QMessageBox.warning(self, "错误", "场景名称不能为空")
                return
            
            new_scene_path = os.path.join(self.library_path, name)
            if os.path.exists(new_scene_path):
                QMessageBox.warning(self, "错误", "该场景名称已存在")
                return
            
            os.makedirs(new_scene_path)
            
            # 创建lnk
            for idx, node_info in enumerate(nodes, 1):
                src_path = resolve_lnk(node_info['path'])
                original_name = os.path.basename(src_path)
                original_name = re.sub(r'^\(\d+\)', '', original_name)
                
                if original_name.lower().endswith('.lnk'):
                    original_name = original_name[:-4]
                
                lnk_name = f"({idx}){original_name}.lnk"
                lnk_full_path = os.path.join(new_scene_path, lnk_name)
                
                create_lnk(src_path, lnk_full_path)
            
            self.refresh_scene_list()
            QMessageBox.information(self, "成功", f"场景 '{name}' 创建成功!")

    # ================= 节点逻辑 =================

    def refresh_node_list(self):
        self.node_list.clear()
        if not self.current_scene_path: return
        
        try:
            items = os.listdir(self.current_scene_path)
            valid_items = []
            for name in items:
                full_path = os.path.join(self.current_scene_path, name)
                if os.path.isdir(full_path) or name.lower().endswith('.lnk'):
                    valid_items.append(name)
            
            valid_items.sort(key=natural_sort_key)

            for name in valid_items:
                full_path = os.path.join(self.current_scene_path, name)
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, full_path)
                
                if name.endswith('.lnk'):
                    item.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_FileLinkIcon))
                    
                self.node_list.addItem(item)
        except Exception as e:
            print(f"刷新节点列表错误: {e}")

    def on_node_selected(self, item):
        if not item: return
        
        node_path = item.data(Qt.ItemDataRole.UserRole)
        self.current_node_path = resolve_lnk(node_path)
        
        self.load_tags(self.current_node_path)
        self.preview_label.load_node(self.current_node_path)

    def load_tags(self, node_path):
        tag_file = os.path.join(node_path, "tags.txt")
        if os.path.exists(tag_file):
            try:
                with open(tag_file, 'r', encoding='utf-8') as f:
                    self.editor.setText(f.read())
            except Exception as e:
                self.editor.setText(f"读取错误: {e}")
        else:
            self.editor.setText("")

    def save_current_tags(self):
        if not self.current_node_path: return
        if not os.path.exists(self.current_node_path):
             QMessageBox.warning(self, "错误", "节点路径不存在")
             return
            
        content = self.editor.toPlainText()
        tag_file = os.path.join(self.current_node_path, "tags.txt")
        try:
            with open(tag_file, 'w', encoding='utf-8') as f:
                f.write(content)
            # 在状态栏闪烁一下成功
            self.statusBar().showMessage(f"已保存: {os.path.basename(self.current_node_path)}", 2000)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def on_add_node(self):
        if not self.current_scene_path:
            QMessageBox.warning(self, "错误", "请先选择一个场景")
            return
            
        text, ok = QInputDialog.getText(self, "新增动作节点", "节点名称:")
        if ok and text:
            count = self.node_list.count()
            folder_name = f"({count + 1}){text}"
            new_path = os.path.join(self.current_scene_path, folder_name)
            
            try:
                os.makedirs(new_path)
                with open(os.path.join(new_path, "tags.txt"), 'w', encoding='utf-8') as f:
                    f.write("")
                
                self.refresh_node_list()
                
                # 自动选中新建项
                for i in range(self.node_list.count()):
                    if self.node_list.item(i).text() == folder_name:
                        self.node_list.setCurrentRow(i)
                        self.on_node_selected(self.node_list.item(i))
                        break
            except Exception as e:
                QMessageBox.critical(self, "创建失败", str(e))

    def on_nodes_reordered(self):
        """拖拽后重命名以固化顺序"""
        if not self.current_scene_path: return
        
        new_order = []
        for i in range(self.node_list.count()):
            item = self.node_list.item(i)
            new_order.append({
                'current_name': item.text(),
                'full_path': item.data(Qt.ItemDataRole.UserRole)
            })
            
        try:
            temp_map = []
            # 1. 重命名为临时名
            for idx, info in enumerate(new_order, 1):
                old_path = info['full_path']
                dir_name = os.path.dirname(old_path)
                file_name = os.path.basename(old_path)
                clean_name = re.sub(r'^\(\d+\)', '', file_name)
                
                temp_name = f"__TEMP_{idx}__{clean_name}"
                temp_path = os.path.join(dir_name, temp_name)
                
                os.rename(old_path, temp_path)
                temp_map.append({'temp_path': temp_path, 'clean_name': clean_name})
            
            # 2. 重命名为目标顺序
            for idx, info in enumerate(temp_map, 1):
                dir_name = os.path.dirname(info['temp_path'])
                final_name = f"({idx}){info['clean_name']}"
                final_path = os.path.join(dir_name, final_name)
                os.rename(info['temp_path'], final_path)
            
            self.refresh_node_list()
            
            # 恢复选中状态
            if self.node_list.count() > 0:
                self.node_list.setCurrentRow(0)
                self.on_node_selected(self.node_list.item(0))

        except Exception as e:
            QMessageBox.critical(self, "重排序失败", f"文件可能正在被占用: {e}")
            self.refresh_node_list()

    # ================= 批量操作与运行 =================

    def on_batch_export(self):
        items = self.node_list.selectedItems()
        if not items: return
        
        combined_text = ""
        for item in items:
            path = resolve_lnk(item.data(Qt.ItemDataRole.UserRole))
            tag_file = os.path.join(path, "tags.txt")
            if os.path.exists(tag_file):
                with open(tag_file, 'r', encoding='utf-8') as f:
                    combined_text += f"### {item.text()} ###\n"
                    combined_text += f.read().strip() + "\n\n"
        
        self.editor.setText(combined_text)
        QMessageBox.information(self, "提示", "已导出到编辑框。")

    def on_batch_edit(self):
        items = self.node_list.selectedItems()
        if not items: 
            QMessageBox.warning(self, "提示", "请先选择要批量编辑的节点")
            return
            
        text, ok = QInputDialog.getMultiLineText(self, "批量追加", "输入追加内容:")
        if ok and text:
            count = 0
            for item in items:
                path = resolve_lnk(item.data(Qt.ItemDataRole.UserRole))
                tag_file = os.path.join(path, "tags.txt")
                try:
                    old_content = ""
                    if os.path.exists(tag_file):
                        with open(tag_file, 'r', encoding='utf-8') as f:
                            old_content = f.read()
                    
                    if old_content and not old_content.endswith('\n'):
                        old_content += "\n"
                    
                    with open(tag_file, 'w', encoding='utf-8') as f:
                        f.write(old_content + text)
                    count += 1
                except Exception as e:
                    print(f"Failed: {path} - {e}")
            
            QMessageBox.information(self, "成功", f"已追加到 {count} 个节点")
            if self.current_node_path:
                self.load_tags(self.current_node_path)

    def on_run(self):
        items = self.node_list.selectedItems()
        if not items:
            QMessageBox.warning(self, "提示", "请选择要运行的节点")
            return
            
        paths = [resolve_lnk(item.data(Qt.ItemDataRole.UserRole)) for item in items]
        
        # --- 回调接口 ---
        print(">>> Calling Backend API with paths:")
        for p in paths:
            print(p)
        # --------------
        
        QMessageBox.information(self, "运行", f"已发送 {len(paths)} 个任务到后台")

    # ================= 右键菜单处理 =================

    def show_scene_context_menu(self, pos):
        item = self.scene_list.itemAt(pos)
        if not item: return
        
        menu = QMenu()
        path = item.data(Qt.ItemDataRole.UserRole)
        
        open_action = QAction("📂 在资源管理器打开", self)
        open_action.triggered.connect(lambda: self.open_in_explorer(path))
        menu.addAction(open_action)
        
        menu.exec(self.scene_list.mapToGlobal(pos))

    def show_node_context_menu(self, pos):
        item = self.node_list.itemAt(pos)
        if not item: return
        
        menu = QMenu()
        path = item.data(Qt.ItemDataRole.UserRole)
        real_path = resolve_lnk(path)
        
        open_node_action = QAction("📂 打开节点文件夹", self)
        open_node_action.triggered.connect(lambda: self.open_in_explorer(real_path))
        menu.addAction(open_node_action)
        
        if path.endswith('.lnk'):
             orig_action = QAction("🔗 打开快捷方式源位置", self)
             orig_action.triggered.connect(lambda: self.open_in_explorer(real_path, select=False))
             menu.addAction(orig_action)

        menu.exec(self.node_list.mapToGlobal(pos))

    def open_in_explorer(self, path, select=True):
        path = os.path.normpath(path)
        if not os.path.exists(path): return
        
        # Windows Explorer 用法
        if os.name == 'nt':
            if os.path.isdir(path):
                subprocess.Popen(['explorer', path])
            else:
                subprocess.Popen(['explorer', '/select,', path])
        else:
            # Mac/Linux fallback
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = PromptManagerApp()
    window.show()
    sys.exit(app.exec())