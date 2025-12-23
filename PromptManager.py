import sys
import os
import re
import shutil
import glob
import subprocess
import datetime
from pathlib import Path
# from natsort import natsorted  # 可选：如果需要更自然的排序建议安装 natsort，这里手写简单排序
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QListWidget, QListWidgetItem, QTextEdit, 
                             QLabel, QPushButton, QSplitter, QFileDialog, QInputDialog,
                             QMenu, QMessageBox, QAbstractItemView, QDialog, QLineEdit,
                             QDialogButtonBox, QGridLayout)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QPoint, QMimeData
from PyQt6.QtGui import QAction, QPixmap, QIcon, QDrag, QCursor
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl

# 尝试导入 pywin32 用于处理快捷方式，如果失败则给出提示
try:
    import win32com.client
except ImportError:
    print("请安装 pywin32: pip install pywin32")
    sys.exit(1)

# ================= 配置与工具函数 =================

def resolve_lnk(path):
    """解析 Windows .lnk 快捷方式指向的真实路径"""
    if not path.lower().endswith('.lnk'):
        return path
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(path))
        return shortcut.TargetPath
    except Exception as e:
        print(f"解析快捷方式失败: {e}")
        return path

def create_lnk(target_path, lnk_path):
    """创建快捷方式"""
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
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
        self.setMinimumSize(300, 300)
        self.setStyleSheet("background-color: #2b2b2b; border: 1px solid #555;")
        self.setText("无预览图")
        
        # 数据状态
        self.current_node_path = None
        self.sub_folders = [] # 日期文件夹列表 (及根目录)
        self.current_folder_index = 0
        self.current_images = [] # 当前文件夹下的图片列表
        self.current_image_index = 0

    def load_node(self, node_path):
        self.current_node_path = resolve_lnk(node_path)
        self.scan_folders()
        self.show_image()

    def scan_folders(self):
        """扫描节点下的所有包含图片的文件夹，按时间倒序排列"""
        if not self.current_node_path or not os.path.exists(self.current_node_path):
            self.sub_folders = []
            return

        # 策略：
        # 1. 根目录
        # 2. 所有子目录（假设是日期文件夹），按修改时间倒序
        
        candidates = [self.current_node_path]
        
        # 获取所有一级子目录
        try:
            subdirs = [os.path.join(self.current_node_path, d) for d in os.listdir(self.current_node_path) 
                       if os.path.isdir(os.path.join(self.current_node_path, d)) or d.endswith('.lnk')]
            
            # 解析lnk文件夹
            resolved_subdirs = []
            for d in subdirs:
                real_path = resolve_lnk(d)
                if os.path.isdir(real_path):
                    resolved_subdirs.append(real_path)

            # 按修改时间倒序
            resolved_subdirs.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            candidates.extend(resolved_subdirs)
        except Exception as e:
            print(f"扫描文件夹出错: {e}")

        # 过滤掉没有图片的文件夹
        self.sub_folders = []
        for folder in candidates:
            imgs = self.get_images_in_folder(folder)
            if imgs:
                self.sub_folders.append(folder)

        self.current_folder_index = 0
        if self.sub_folders:
            self.load_images_in_current_folder()

    def get_images_in_folder(self, folder):
        valid_ext = ['.png', '.jpg', '.jpeg', '.webp', '.bmp']
        files = []
        if not os.path.exists(folder):
            return []
        for f in os.listdir(folder):
            _, ext = os.path.splitext(f)
            if ext.lower() in valid_ext:
                files.append(os.path.join(folder, f))
        # 简单按名称排序，通常生成图是有序的
        files.sort(key=lambda x: natural_sort_key(os.path.basename(x)))
        return files

    def load_images_in_current_folder(self):
        if not self.sub_folders:
            self.current_images = []
            return
        
        folder = self.sub_folders[self.current_folder_index]
        self.current_images = self.get_images_in_folder(folder)
        
        # 尝试寻找 tags.txt 同级的 tmp.png 或第一张图作为默认
        # 只有在初始化加载根目录时才这样做，切换文件夹时默认显示第一张
        self.current_image_index = 0
        
        # 特殊逻辑：如果在根目录，且存在 tmp.png，优先显示
        if folder == self.current_node_path:
            for i, path in enumerate(self.current_images):
                if "tmp.png" in os.path.basename(path).lower():
                    self.current_image_index = i
                    break

    def show_image(self):
        if not self.current_images:
            self.setText("该节点下无图片")
            self.setPixmap(QPixmap())
            return
        
        img_path = self.current_images[self.current_image_index]
        pixmap = QPixmap(img_path)
        
        if pixmap.isNull():
            self.setText("图片损坏")
            return
            
        # 缩放以适应
        scaled_pixmap = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled_pixmap)
        
        # 更新提示文本 (方便调试)
        folder_name = os.path.basename(self.sub_folders[self.current_folder_index])
        file_name = os.path.basename(img_path)
        self.setToolTip(f"目录: {folder_name} ({self.current_folder_index+1}/{len(self.sub_folders)})\n文件: {file_name}")

    def wheelEvent(self, event):
        """滚轮：切换当前文件夹内的前后图片"""
        if not self.current_images:
            return
        
        delta = event.angleDelta().y()
        if delta > 0: # 向上滚，上一张
            self.current_image_index = (self.current_image_index - 1) % len(self.current_images)
        else: # 向下滚，下一张
            self.current_image_index = (self.current_image_index + 1) % len(self.current_images)
        self.show_image()

    def mousePressEvent(self, event):
        """左键：下一个文件夹，右键：上一个文件夹"""
        if not self.sub_folders:
            return
        
        updated = False
        if event.button() == Qt.MouseButton.LeftButton:
            # 下一个文件夹
            if len(self.sub_folders) > 1:
                self.current_folder_index = (self.current_folder_index + 1) % len(self.sub_folders)
                updated = True
        elif event.button() == Qt.MouseButton.RightButton:
            # 上一个文件夹
            if len(self.sub_folders) > 1:
                self.current_folder_index = (self.current_folder_index - 1) % len(self.sub_folders)
                updated = True
        
        if updated:
            self.load_images_in_current_folder()
            self.show_image()
            # 显示一个临时的状态提示，告知用户切换了文件夹
            folder_name = os.path.basename(self.sub_folders[self.current_folder_index])
            print(f"切换至文件夹: {folder_name}")

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
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection) # 支持多选

    def dropEvent(self, event):
        super().dropEvent(event)
        self.itemMoved.emit()
    
    def contextMenuEvent(self, event):
        # 屏蔽默认右键菜单，交由主窗口处理
        pass

# ================= 场景构建对话框 =================

class SceneBuilderDialog(QDialog):
    """用于新建场景组合的对话框"""
    def __init__(self, library_path, all_nodes_map, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建组合场景")
        self.resize(800, 600)
        self.library_path = library_path
        self.all_nodes_map = all_nodes_map # key: node_name, value: full_path

        layout = QVBoxLayout(self)

        # 顶部：名称输入
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("新场景名称:"))
        self.name_edit = QLineEdit()
        top_layout.addWidget(self.name_edit)
        layout.addLayout(top_layout)

        # 中间：左右两个列表
        mid_layout = QHBoxLayout()
        
        # 左侧：源节点 (所有场景的动作)
        left_group = QVBoxLayout()
        left_group.addWidget(QLabel("所有可用动作节点 (拖拽到右侧):"))
        self.source_list = QListWidget()
        self.source_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.source_list.setDragEnabled(True)
        
        # 填充源数据
        for name, path in self.all_nodes_map.items():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.source_list.addItem(item)
            
        left_group.addWidget(self.source_list)
        mid_layout.addLayout(left_group)

        # 右侧：目标场景包含的节点
        right_group = QVBoxLayout()
        right_group.addWidget(QLabel("新场景包含的动作 (可排序):"))
        self.target_list = QListWidget()
        self.target_list.setAcceptDrops(True)
        self.target_list.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop) 
        # 这里为了简化，我们只允许从左侧添加，不支持内部重排(需要另外实现InternalMove和AcceptDrops的组合逻辑)
        # 简单的实现：接收 Drop，如果是从 source 来的，就复制一个 item
        
        right_group.addWidget(self.target_list)
        mid_layout.addLayout(right_group, 2) # 右侧宽一点

        layout.addLayout(mid_layout)

        # 底部按钮
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 连接拖拽事件处理 (需要稍微hack一下QListWidget的默认行为)
        # 简单起见，使用按钮添加
        btn_add = QPushButton("添加选中节点 ->")
        btn_add.clicked.connect(self.add_selected_nodes)
        mid_layout.insertWidget(1, btn_add)
        
    def add_selected_nodes(self):
        for item in self.source_list.selectedItems():
            new_item = QListWidgetItem(item.text())
            new_item.setData(Qt.ItemDataRole.UserRole, item.data(Qt.ItemDataRole.UserRole))
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
        self.resize(1200, 800)
        
        self.library_path = ""
        self.current_scene_path = ""
        self.current_node_path = ""
        
        # 用于记录场景上次选择的节点索引 {scene_path: row_index}
        self.scene_selection_history = {} 

        self.setup_ui()
        self.init_directory()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # === 区域 1: 目录树 (场景列表) ===
        scene_layout = QVBoxLayout()
        scene_layout.addWidget(QLabel("场景列表"))
        self.scene_list = QListWidget()
        self.scene_list.itemClicked.connect(self.on_scene_selected)
        self.scene_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scene_list.customContextMenuRequested.connect(self.show_scene_context_menu)
        scene_layout.addWidget(self.scene_list)
        
        btn_new_scene = QPushButton("新建组合场景")
        btn_new_scene.clicked.connect(self.on_create_scene)
        scene_layout.addWidget(btn_new_scene)

        # === 区域 2: 动作节点框 ===
        node_layout = QVBoxLayout()
        node_layout.addWidget(QLabel("动作节点 (拖拽排序)"))
        self.node_list = DraggableListWidget()
        self.node_list.itemClicked.connect(self.on_node_selected)
        self.node_list.itemMoved.connect(self.on_nodes_reordered)
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
        op_layout.addWidget(QLabel("提示词编辑"))
        self.editor = QTextEdit()
        op_layout.addWidget(self.editor, 1)

        # 3.3 底部功能区
        action_layout = QHBoxLayout()
        
        btn_save = QPushButton("保存提示词")
        btn_save.clicked.connect(self.save_current_tags)
        action_layout.addWidget(btn_save)

        btn_batch_edit = QPushButton("批量追加编辑")
        btn_batch_edit.clicked.connect(self.on_batch_edit)
        action_layout.addWidget(btn_batch_edit)

        btn_run = QPushButton("运行 (Run)")
        btn_run.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
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
        dirs.sort(key=natural_sort_key)
        
        for d in dirs:
            item = QListWidgetItem(d)
            item.setData(Qt.ItemDataRole.UserRole, os.path.join(self.library_path, d))
            self.scene_list.addItem(item)

    def on_scene_selected(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        self.current_scene_path = path
        self.refresh_node_list()
        
        # 恢复上次选中的节点
        last_row = self.scene_selection_history.get(path, 0)
        if self.node_list.count() > 0:
            if last_row >= self.node_list.count():
                last_row = 0
            self.node_list.setCurrentRow(last_row)
            self.on_node_selected(self.node_list.item(last_row))

    def on_create_scene(self):
        """创建新组合场景"""
        # 1. 收集所有场景下的所有节点
        all_nodes = {}
        for scene_name in os.listdir(self.library_path):
            scene_path = os.path.join(self.library_path, scene_name)
            if os.path.isdir(scene_path):
                nodes = sorted(os.listdir(scene_path), key=natural_sort_key)
                for node_file in nodes:
                    full_path = os.path.join(scene_path, node_file)
                    # 排除非文件夹且非lnk
                    if os.path.isdir(full_path) or full_path.endswith('.lnk'):
                        # 提取干净的名称 (去掉 (1) 前缀)
                        clean_name = re.sub(r'^\(\d+\)', '', node_file)
                        display_name = f"[{scene_name}] {clean_name}"
                        all_nodes[display_name] = full_path

        # 2. 弹出对话框
        dialog = SceneBuilderDialog(self.library_path, all_nodes, self)
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
            
            # 3. 创建 .lnk
            for idx, node_info in enumerate(nodes, 1):
                src_path = resolve_lnk(node_info['path']) # 确保源是指向真实文件夹
                # 提取原始文件夹名用于命名，或者使用用户定义的? 这里我们使用 (1)原始名.lnk 格式
                original_name = os.path.basename(src_path)
                # 去掉可能存在的原有序号
                original_name = re.sub(r'^\(\d+\)', '', original_name)
                
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
            # 过滤：必须是文件夹或lnk
            valid_items = []
            for name in items:
                full_path = os.path.join(self.current_scene_path, name)
                if os.path.isdir(full_path) or name.lower().endswith('.lnk'):
                    valid_items.append(name)
            
            # 排序 (1)...(2)...
            valid_items.sort(key=natural_sort_key)

            for name in valid_items:
                full_path = os.path.join(self.current_scene_path, name)
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, full_path)
                
                # 检查是否是lnk，给不同的图标或颜色（可选）
                if name.endswith('.lnk'):
                    item.setToolTip(f"快捷方式 -> {resolve_lnk(full_path)}")
                    
                self.node_list.addItem(item)
        except Exception as e:
            print(f"刷新节点列表错误: {e}")

    def on_node_selected(self, item):
        if not item: return
        
        # 记录选中位置
        current_row = self.node_list.row(item)
        self.scene_selection_history[self.current_scene_path] = current_row
        
        node_path = item.data(Qt.ItemDataRole.UserRole)
        self.current_node_path = resolve_lnk(node_path)
        
        # 1. 加载 Tags
        self.load_tags(self.current_node_path)
        
        # 2. 加载图片预览
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
        if not self.current_node_path or not os.path.exists(self.current_node_path):
            QMessageBox.warning(self, "错误", "未选择有效的动作节点")
            return
            
        content = self.editor.toPlainText()
        tag_file = os.path.join(self.current_node_path, "tags.txt")
        try:
            with open(tag_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 状态栏提示保存成功 (此处用print代替)
            print(f"已保存: {tag_file}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def on_add_node(self):
        if not self.current_scene_path:
            QMessageBox.warning(self, "错误", "请先选择一个场景")
            return
            
        text, ok = QInputDialog.getText(self, "新增动作节点", "节点名称:")
        if ok and text:
            # 计算序号
            count = self.node_list.count()
            prefix = f"({count + 1})"
            folder_name = f"{prefix}{text}"
            new_path = os.path.join(self.current_scene_path, folder_name)
            
            try:
                os.makedirs(new_path)
                # 创建空的 tags.txt
                with open(os.path.join(new_path, "tags.txt"), 'w', encoding='utf-8') as f:
                    f.write("")
                
                # 如果当前编辑器有内容，是否要保存到新节点？用户需求说“在操作框编辑新动作节点提示词, 保存后新建”，
                # 这里的逻辑稍微简化：先建节点，然后用户去编辑保存。
                # 或者：如果当前编辑器有内容，且用户刚点击了新增，可以把内容写入。
                # 按照用户描述：“在动作节点框新增动作节点, 在操作框编辑新动作节点提示词”
                # 我们采用：创建完 -> 自动选中 -> 用户编辑 -> 用户点保存
                
                self.refresh_node_list()
                
                # 选中新建的
                for i in range(self.node_list.count()):
                    if self.node_list.item(i).text() == folder_name:
                        self.node_list.setCurrentRow(i)
                        self.on_node_selected(self.node_list.item(i))
                        break
                        
            except Exception as e:
                QMessageBox.critical(self, "创建失败", str(e))

    def on_nodes_reordered(self):
        """拖拽排序后的逻辑：重命名文件以匹配新顺序"""
        if not self.current_scene_path: return
        
        # 1. 获取列表当前顺序的名称和路径
        new_order = []
        for i in range(self.node_list.count()):
            item = self.node_list.item(i)
            new_order.append({
                'current_name': item.text(),
                'full_path': item.data(Qt.ItemDataRole.UserRole)
            })
            
        # 2. 执行重命名
        # 策略：先全部重命名为临时名字，防止冲突，然后再命名回来
        # 格式： (Index)Name
        
        try:
            temp_map = []
            
            # 第一步：重命名为UUID或临时名
            for idx, info in enumerate(new_order, 1):
                old_path = info['full_path']
                dir_name = os.path.dirname(old_path)
                file_name = os.path.basename(old_path)
                
                # 去除旧的 (N) 前缀
                clean_name = re.sub(r'^\(\d+\)', '', file_name)
                
                temp_name = f"__TEMP_{idx}__{clean_name}"
                temp_path = os.path.join(dir_name, temp_name)
                
                os.rename(old_path, temp_path)
                temp_map.append({'temp_path': temp_path, 'clean_name': clean_name})
            
            # 第二步：重命名为目标顺序 (1)... (2)...
            for idx, info in enumerate(temp_map, 1):
                dir_name = os.path.dirname(info['temp_path'])
                final_name = f"({idx}){info['clean_name']}"
                final_path = os.path.join(dir_name, final_name)
                
                os.rename(info['temp_path'], final_path)
            
            self.refresh_node_list()
            
        except Exception as e:
            QMessageBox.critical(self, "重排序失败", f"文件正在被使用？\n{e}")
            self.refresh_node_list() # 恢复显示

    # ================= 批量操作 =================

    def on_batch_export(self):
        items = self.node_list.selectedItems()
        if not items: return
        
        combined_text = ""
        for item in items:
            path = resolve_lnk(item.data(Qt.ItemDataRole.UserRole))
            tag_file = os.path.join(path, "tags.txt")
            if os.path.exists(tag_file):
                with open(tag_file, 'r', encoding='utf-8') as f:
                    combined_text += f"--- {item.text()} ---\n"
                    combined_text += f.read().strip() + "\n\n"
        
        self.editor.setText(combined_text)
        QMessageBox.information(self, "提示", "已将选中节点的提示词导出到编辑框 (仅供查看，保存只会覆盖当前选中节点)")

    def on_batch_edit(self):
        items = self.node_list.selectedItems()
        if not items: 
            QMessageBox.warning(self, "提示", "请先选择要批量编辑的节点")
            return
            
        text, ok = QInputDialog.getMultiLineText(self, "批量追加", "输入要追加到所有选中节点的提示词:")
        if ok and text:
            count = 0
            for item in items:
                path = resolve_lnk(item.data(Qt.ItemDataRole.UserRole))
                tag_file = os.path.join(path, "tags.txt")
                try:
                    # 读取旧内容
                    old_content = ""
                    if os.path.exists(tag_file):
                        with open(tag_file, 'r', encoding='utf-8') as f:
                            old_content = f.read()
                    
                    # 追加 (如果没有换行符则添加)
                    if old_content and not old_content.endswith('\n'):
                        old_content += "\n"
                    
                    with open(tag_file, 'w', encoding='utf-8') as f:
                        f.write(old_content + text)
                    count += 1
                except Exception as e:
                    print(f"写入 {path} 失败: {e}")
            
            QMessageBox.information(self, "成功", f"已成功追加到 {count} 个节点")
            # 刷新当前显示
            if self.current_node_path:
                self.load_tags(self.current_node_path)

    def on_run(self):
        """运行回调"""
        items = self.node_list.selectedItems()
        if not items:
            QMessageBox.warning(self, "提示", "请选择要运行的动作节点")
            return
            
        paths = [resolve_lnk(item.data(Qt.ItemDataRole.UserRole)) for item in items]
        
        # ---------------------------------------------------------
        # 这里是你对接后台接口的地方
        # ---------------------------------------------------------
        print(">>> 触发后台运行接口")
        print("参数列表:", paths)
        
        QMessageBox.information(self, "运行", f"已发送 {len(paths)} 个节点路径到后台。\n(请查看控制台输出路径)")

    # ================= 右键菜单 =================

    def show_scene_context_menu(self, pos):
        item = self.scene_list.itemAt(pos)
        if not item: return
        
        menu = QMenu()
        open_action = QAction("在资源管理器打开", self)
        open_action.triggered.connect(lambda: self.open_in_explorer(item.data(Qt.ItemDataRole.UserRole)))
        menu.addAction(open_action)
        menu.exec(self.scene_list.mapToGlobal(pos))

    def show_node_context_menu(self, pos):
        item = self.node_list.itemAt(pos)
        if not item: return
        
        menu = QMenu()
        path = item.data(Qt.ItemDataRole.UserRole)
        
        open_action = QAction("在资源管理器打开节点文件夹", self)
        open_action.triggered.connect(lambda: self.open_in_explorer(resolve_lnk(path)))
        menu.addAction(open_action)
        
        if path.endswith('.lnk'):
             orig_action = QAction("打开快捷方式源位置", self)
             orig_action.triggered.connect(lambda: self.open_in_explorer(resolve_lnk(path), select=False))
             menu.addAction(orig_action)

        menu.exec(self.node_list.mapToGlobal(pos))

    def open_in_explorer(self, path, select=True):
        path = os.path.normpath(path)
        if not os.path.exists(path): return
        
        if os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            # 如果是文件，选中它
            subprocess.Popen(f'explorer /select,"{path}"')

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 设置深色主题风格 (可选)
    app.setStyle("Fusion")
    
    window = PromptManagerApp()
    window.show()
    sys.exit(app.exec())