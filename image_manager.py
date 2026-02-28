import sys
import os
import shutil
import re
import math
from PyQt6.QtWidgets import (QApplication, QMainWindow, QListWidget, QListWidgetItem, 
                             QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QFileDialog, 
                             QLabel, QSlider, QMessageBox, QAbstractItemView, QProgressDialog)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QUrl, QPoint
from PyQt6.QtGui import QIcon, QPixmap, QImage, QAction, QDragEnterEvent, QDropEvent, QDragMoveEvent
from natsort import natsorted
import win32com.client 
import pythoncom

# 允许的文件扩展名
VALID_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.lnk')

class ImageLoader(QThread):
    """
    异步加载图片的缩略图
    """
    # 信号：原始路径, 缩略图QImage, 真实路径
    preview_loaded = pyqtSignal(str, QImage, str) 

    def __init__(self, file_paths, icon_size):
        super().__init__()
        self.file_paths = file_paths
        self.icon_size = icon_size

    def resolve_path(self, shell, path):
        """解析路径，如果是lnk则返回目标路径"""
        if path.lower().endswith('.lnk'):
            try:
                shortcut = shell.CreateShortcut(path)
                return shortcut.TargetPath
            except:
                return path
        return path

    def run(self):
        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            
            for path in self.file_paths:
                if self.isInterruptionRequested():
                    break
                    
                real_path = self.resolve_path(shell, path)
                
                # 读取图片
                image = QImage(real_path)
                
                if not image.isNull():
                    # 预先缩放
                    scaled_image = image.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    self.preview_loaded.emit(path, scaled_image, real_path)
                else:
                    self.preview_loaded.emit(path, QImage(), real_path)
                    
        except Exception as e:
            print(f"Loading error: {e}")
        finally:
            pythoncom.CoUninitialize()

class SortableListWidget(QListWidget):
    """
    自定义的 ListWidget，支持外部拖入和内部优化排序
    """
    files_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        
        # 核心设置：Static 模式保持网格稳定，DragDropMode 开启拖拽
        self.setMovement(QListWidget.Movement.Static) 
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        
        # 禁止“覆盖”模式
        self.setDragDropOverwriteMode(False)
        
        self.setSpacing(12)
        self.current_icon_size = 180
        self.update_grid()

    def update_grid(self):
        self.setIconSize(QSize(self.current_icon_size, self.current_icon_size))
        self.setGridSize(QSize(self.current_icon_size + 20, self.current_icon_size + 50))

    def supportedDropActions(self):
        # 强制声明支持 MoveAction，这有助于让光标显示正确
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    def dragEnterEvent(self, event: QDragEnterEvent):
        # 如果是内部拖拽，强制接受，并且【不调用】super()
        # 调用 super() 可能会导致 Qt 检查 Item 属性然后拒绝拖拽（显示红叉）
        if event.source() == self:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            # 外部文件拖入，通常是 CopyAction
            event.acceptProposedAction()
            # 这里调用 super 是为了让 Qt 处理一些基础逻辑，但如果不调用也行
            # 为了保险，外部文件可以调用 super 或手动 accept
            if event.mimeData().hasUrls():
                event.accept()
            else:
                super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent):
        # 核心修复：无论鼠标下面是什么，只要是内部拖拽，一律强制允许移动
        # 并且【绝对不要】调用 super().dragMoveEvent(event)
        if event.source() == self:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        elif event.mimeData().hasUrls():
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        """
        重写 dropEvent 实现插入式排序
        """
        # 1. 外部文件拖入处理
        if event.mimeData().hasUrls():
            event.accept()
            urls = event.mimeData().urls()
            file_paths = [u.toLocalFile() for u in urls if u.toLocalFile().lower().endswith(VALID_EXTENSIONS)]
            if file_paths:
                self.files_dropped.emit(file_paths)
            return

        # 2. 内部排序处理
        if event.source() == self:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            
            # 获取选中的 items
            selected_items = self.selectedItems()
            if not selected_items:
                return
            
            # 按照当前的视觉顺序排序（行号）
            selected_items.sort(key=lambda i: self.row(i))
            
            # 找到目标插入位置
            target_item = self.itemAt(event.position().toPoint())
            if target_item:
                drop_row = self.row(target_item)
            else:
                drop_row = self.count()

            # 提取数据，准备重新创建 items (这是最稳健的方法，避免对象引用问题)
            items_data = []
            for item in selected_items:
                items_data.append({
                    'text': item.text(),
                    'icon': item.icon(),
                    'user_role': item.data(Qt.ItemDataRole.UserRole),
                    'user_role_1': item.data(Qt.ItemDataRole.UserRole + 1),
                    'flags': item.flags() # 保留 flags
                })

            # 执行移动逻辑：
            # 我们需要计算移除旧 item 后，新的插入位置应该是哪里
            
            # 1. 获取所有选中项的行号
            rows_to_pop = [self.row(i) for i in selected_items]
            
            # 2. 从后往前删除，这样不会影响前面的行号
            for row in sorted(rows_to_pop, reverse=True):
                self.takeItem(row)
            
            # 3. 修正 drop_row
            # 如果我们删除了 drop_row 之前的项目，drop_row 需要减小
            # 计算有多少个被删除的项目是在 drop_row 之前的
            # 注意：这里的 row 是原始行号
            adjustment = sum(1 for r in rows_to_pop if r < drop_row)
            final_drop_row = max(0, drop_row - adjustment)

            # 4. 插入新 items
            for data in items_data:
                new_item = QListWidgetItem()
                new_item.setText(data['text'])
                new_item.setIcon(data['icon'])
                new_item.setData(Qt.ItemDataRole.UserRole, data['user_role'])
                new_item.setData(Qt.ItemDataRole.UserRole + 1, data['user_role_1'])
                
                # 确保 Flags 正确
                # 即使去掉了 ItemIsDropEnabled，因为我们重写了 dragMoveEvent 强制 accept，所以也能拖
                # 这里只给基础属性即可
                new_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
                
                self.insertItem(final_drop_row, new_item)
                new_item.setSelected(True)
                final_drop_row += 1 # 下一个插在后面
            
            return

        super().dropEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.current_icon_size = min(512, self.current_icon_size + 20)
            else:
                self.current_icon_size = max(64, self.current_icon_size - 20)
            self.update_grid()
            event.accept()
        else:
            super().wheelEvent(event)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 图集排序整理工具 (Python版)")
        self.resize(1200, 800)

        # 主要布局
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        
        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        
        btn_add_files = QPushButton("载入图片")
        btn_add_files.clicked.connect(self.add_images_dialog)

        btn_add_folder = QPushButton("载入文件夹")
        btn_add_folder.clicked.connect(self.add_folder_dialog)
        
        btn_sort = QPushButton("自然排序 (重置)")
        btn_sort.clicked.connect(self.apply_natural_sort)
        
        btn_rename = QPushButton("应用排序并重命名")
        btn_rename.clicked.connect(self.rename_in_place)
        
        btn_export = QPushButton("导出当前排序")
        btn_export.clicked.connect(self.export_sorted)

        toolbar.addWidget(btn_add_files)
        toolbar.addWidget(btn_add_folder)
        toolbar.addWidget(btn_sort)
        toolbar.addStretch()
        toolbar.addWidget(btn_rename)
        toolbar.addWidget(btn_export)
        
        layout.addLayout(toolbar)

        # --- 列表视图 ---
        self.list_widget = SortableListWidget()
        self.list_widget.files_dropped.connect(self.handle_files_dropped)
        layout.addWidget(self.list_widget)

        # --- 状态栏 ---
        self.status_label = QLabel("就绪 | 支持拖入 .jpg, .png 及 .lnk 快捷方式")
        layout.addWidget(self.status_label)

        self.setCentralWidget(main_widget)
        self.loading_thread = None

    def add_images_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择图片", "", "Images (*.png *.jpg *.jpeg *.webp *.lnk)")
        if files:
            self.handle_files_dropped(files)

    def add_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含图片的文件夹")
        if folder:
            files = []
            for root, dirs, filenames in os.walk(folder):
                for filename in filenames:
                    if filename.lower().endswith(VALID_EXTENSIONS):
                        files.append(os.path.join(root, filename))
            
            if files:
                self.handle_files_dropped(files)
            else:
                self.status_label.setText("所选文件夹中没有支持的图片文件。")

    def handle_files_dropped(self, file_paths):
        # 1. 过滤重复
        existing_paths = {self.list_widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list_widget.count())}
        new_paths_raw = [p for p in file_paths if p not in existing_paths]
        
        if not new_paths_raw:
            return

        # 2. 修复自然排序问题：
        # 载入前先对新文件进行一次 natsort 自然排序
        new_paths = natsorted(new_paths_raw)

        self.status_label.setText(f"正在加载 {len(new_paths)} 张图片...")
        
        if self.loading_thread and self.loading_thread.isRunning():
            self.loading_thread.requestInterruption()
            self.loading_thread.wait()

        self.loading_thread = ImageLoader(new_paths, self.list_widget.current_icon_size)
        self.loading_thread.preview_loaded.connect(self.on_item_loaded)
        self.loading_thread.finished.connect(lambda: self.status_label.setText(f"总计: {self.list_widget.count()} 张"))
        self.loading_thread.start()

    def on_item_loaded(self, path, qimage, real_path):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setData(Qt.ItemDataRole.UserRole + 1, real_path)
        item.setText(os.path.basename(path))
        
        # 恢复正常的 Flags，不需要 DropEnabled，因为我们在 Event 中强制 Accept
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)

        if not qimage.isNull():
            pixmap = QPixmap.fromImage(qimage)
            icon = QIcon(pixmap)
            item.setIcon(icon)
        else:
            item.setText(item.text() + " (无效)")
        
        self.list_widget.addItem(item)

    def apply_natural_sort(self):
        """需求5: 自然排序"""
        items = []
        for i in range(self.list_widget.count()):
            items.append(self.list_widget.takeItem(0))
        
        items.sort(key=lambda x: natsorted(x.text())[0] if x.text() else "")
        
        for item in items:
            self.list_widget.addItem(item)
            
        self.status_label.setText("已重置为自然排序")

    def rename_in_place(self):
        """需求6: 原位修改排序"""
        count = self.list_widget.count()
        if count == 0:
            return

        reply = QMessageBox.question(self, "确认重命名", 
                                     f"将对列表中的 {count} 个文件进行重命名。\n"
                                     "格式: 001_原文件名.ext\n\n"
                                     "操作不可逆，确定继续吗？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.No:
            return

        pad_width = len(str(count))
        renamed_count = 0
        errors = []

        for i in range(count):
            item = self.list_widget.item(i)
            file_path = item.data(Qt.ItemDataRole.UserRole)
            
            dir_name = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)
            
            clean_name = re.sub(r'^\d+_', '', file_name)
            
            new_prefix = f"{i+1:0{pad_width}d}_"
            new_name = new_prefix + clean_name
            new_path = os.path.join(dir_name, new_name)
            
            if file_path == new_path:
                continue
                
            try:
                os.rename(file_path, new_path)
                item.setData(Qt.ItemDataRole.UserRole, new_path)
                item.setText(new_name)
                renamed_count += 1
            except Exception as e:
                errors.append(f"{file_name}: {str(e)}")

        msg = f"完成！成功重命名 {renamed_count} 个文件。"
        if errors:
            msg += f"\n\n失败 {len(errors)} 个:\n" + "\n".join(errors[:5])
        
        QMessageBox.information(self, "重命名结果", msg)

    def export_sorted(self):
        """需求6: 导出自定义排序后的文件"""
        target_dir = QFileDialog.getExistingDirectory(self, "选择导出目标文件夹")
        if not target_dir:
            return

        count = self.list_widget.count()
        pad_width = len(str(count))
        
        progress = QProgressDialog("正在导出...", "取消", 0, count, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        for i in range(count):
            if progress.wasCanceled():
                break
                
            item = self.list_widget.item(i)
            src_path = item.data(Qt.ItemDataRole.UserRole)
            real_path = item.data(Qt.ItemDataRole.UserRole + 1)
            
            ext = os.path.splitext(real_path)[1]
            original_name = os.path.splitext(os.path.basename(src_path))[0]
            original_name = re.sub(r'^\d+_', '', original_name)

            new_name = f"{i+1:0{pad_width}d}_{original_name}{ext}"
            dst_path = os.path.join(target_dir, new_name)
            
            try:
                shutil.copy2(real_path, dst_path)
            except Exception as e:
                print(f"Copy error: {e}")
            
            progress.setValue(i + 1)

        QMessageBox.information(self, "导出完成", "文件已按顺序导出到目标文件夹。")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())