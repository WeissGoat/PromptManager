import sys
import os
import random
import re
import html
import time
import shutil
from datetime import datetime
# 引入 queue 用于线程安全队列 (虽然 Python 的 list 在某些操作下原子，但标准队列更稳健，这里我们用简单的 list + mutex 配合)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLabel, QPushButton, 
                               QListWidget, QListWidgetItem, QFileDialog, 
                               QScrollArea, QFrame, QCheckBox, QSplitter, 
                               QTabWidget, QProgressBar, QMessageBox, QLineEdit, 
                               QGridLayout, QStyle, QInputDialog, QMenu, QLayout)
from PySide6.QtCore import Qt, Signal, QTimer, QSize, QThread, QObject, QMutex, QWaitCondition, QRect, QPoint
from PySide6.QtGui import QColor, QPalette, QFont, QAction
from util import resolve_path, create_shortcut

# --- API Import Setup ---
sys.path.append(r"F:\ThreeState")

try:
    import danbooru_api
    HAS_REAL_API = True
except ImportError:
    HAS_REAL_API = False
    print("Warning: API modules not found, running in full mock mode.")
from utils import image, uai

# --- Translation Import Setup ---
try:
    from deep_translator import GoogleTranslator
    HAS_TRANSLATOR = True
except ImportError:
    HAS_TRANSLATOR = False
    print("Warning: deep-translator not installed.")

# --- Interface Logic ---

class MockAIInterface:
    """
    混合接口：优先使用本地硬编码字典，未命中则使用 Google 翻译
    """
    def __init__(self):
        if HAS_TRANSLATOR:
            # self.translator = GoogleTranslator(source='auto', target='zh-CN', proxies={'http': '127.0.0.1:7890'})
            self.translator = GoogleTranslator(source='auto', target='zh-CN')
        
        self.runtime_cache = {}

        self.known_categories = {
            "1girl": "Character", "solo": "Character", "long hair": "Attribute", 
            "blue eyes": "Attribute", "dress": "Clothing", "masterpiece": "Quality",
            "best quality": "Quality", "simple background": "Background", 
            "white background": "Background", "standing": "Pose", "looking at viewer": "Pose",
            "monochrome": "Style", "sketch": "Style", "greyscale": "Style"
        }
        
        self.translations = {
            "1girl": "1个女孩", "solo": "单人", "long hair": "长发", 
            "blue eyes": "蓝眼", "dress": "连衣裙", "masterpiece": "杰作",
            "best quality": "最佳质量", "simple background": "简单背景",
            "white background": "白背景", "standing": "站立", "looking at viewer": "看镜头",
            "monochrome": "单色", "sketch": "素描", "greyscale": "灰度",
            "red hair": "红发", "blue dress": "蓝裙子", "forest": "森林",
            "cyberpunk": "赛博朋克", "mecha": "机甲", "cat ears": "猫耳",
            "cowboy shot": "七分身", "abs": "腹肌" 
        }

        self.category_colors = {} 
        self.palette = [
            "#FFB7B2", "#FFDAC1", "#E2F0CB", "#B5EAD7", "#C7CEEA", 
            "#E0BBE4", "#957DAD", "#D291BC", "#FEC8D8", "#FFDFD3"
        ]

    def classify_tag(self, tag_text):
        if HAS_REAL_API:
            try:
                return str(danbooru_api.get_tag_type(tag_text))
            except:
                pass

        clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
        if clean_tag in self.known_categories:
            return self.known_categories[clean_tag]
        
        cats = ["Attribute", "Object", "Effect", "Unknown", "Artist"]
        return cats[len(clean_tag) % len(cats)]
    
    def translate_tag(self, tag_text):
        clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
        clean_tag = re.sub(r':\d+(\.\d+)?$', '', clean_tag)

        if not clean_tag: return ""

        if clean_tag in self.translations:
            return self.translations[clean_tag]
        
        if clean_tag in self.runtime_cache:
            return self.runtime_cache[clean_tag]

        if HAS_TRANSLATOR:
            try:
                result = self.translator.translate(clean_tag)
                self.runtime_cache[clean_tag] = result
                return result
            except Exception as e:
                print(f"Trans Error: {e}")
                return clean_tag 
        
        return clean_tag

    def get_color_for_category(self, category):
        if category not in self.category_colors:
            color = self.palette[len(self.category_colors) % len(self.palette)]
            self.category_colors[category] = color
        return self.category_colors[category]

    def image_to_prompt(self, image_path):
        # time.sleep(0.1) 
        image_path = resolve_path(image_path)
        return image.get_ai_image_prompt(image_path, True)
        

api = MockAIInterface()

# --- Logic Models ---

class PromptItem:
    def __init__(self, name, raw_text, is_image=False, image_path=None):
        self.name = name
        self.raw_text = raw_text
        self.is_image = is_image
        self.image_path = image_path
        self.parsed_tags = [] 
        self.is_deleted = False # 增加标记，用于通知后台线程该任务已失效
        
        raw_tags = [t.strip() for t in self.raw_text.split(',') if t.strip()]
        for t in raw_tags:
            self.parsed_tags.append({
                'text': t, 
                'category': 'Pending', 
                'enabled': True,
                'translation': None 
            })

# --- Workers (Async) ---

class ImageBatchWorker(QThread):
    """
    负责耗时的图片反推和初步生成 Item 对象
    """
    progress_signal = Signal(int, int)
    item_ready_signal = Signal(object)
    finished_signal = Signal()
    log_signal = Signal(str)

    def __init__(self, file_paths, parent=None):
        super().__init__(parent)
        self.file_paths = file_paths
        self.is_running = True

    def run(self):
        total = len(self.file_paths)
        for i, path in enumerate(self.file_paths):
            if not self.is_running: break
            try:
                prompt = api.image_to_prompt(path)
                base_name = os.path.splitext(os.path.basename(path))[0]
                item = PromptItem(base_name, prompt, is_image=True, image_path=path)
                # 预分类 (可选，也可以交给 GlobalWorker，但这里做一部分可以分担压力)
                for tag_data in item.parsed_tags:
                    tag_data['category'] = api.classify_tag(tag_data['text'])
                
                self.item_ready_signal.emit(item)
                self.progress_signal.emit(i + 1, total)
            except Exception as e:
                self.log_signal.emit(f"Error: {str(e)}")
        self.finished_signal.emit()

    def stop(self):
        self.is_running = False

class GlobalTranslationWorker(QThread):
    """
    全局后台翻译线程。
    一直运行，自动处理队列中的 Item。支持“插队”（优先处理当前选中的 Item）。
    """
    # 信号: Item对象, Tag索引, 翻译结果, 分类结果
    tag_processed = Signal(object, int, str, str) 
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue = [] # 待处理的 Item 列表
        self.priority_item = None # 当前优先处理的 Item
        self.is_running = True
        self.mutex = QMutex()
        self.cond = QWaitCondition()

    def add_item(self, item):
        """添加新任务到队列"""
        self.mutex.lock()
        if item not in self.queue:
            self.queue.append(item)
        self.cond.wakeAll() # 唤醒线程
        self.mutex.unlock()

    def set_priority(self, item):
        """设置高优先级任务（通常是用户当前点击的任务）"""
        self.mutex.lock()
        self.priority_item = item
        self.cond.wakeAll() # 唤醒线程以防它正在睡觉
        self.mutex.unlock()

    def remove_item(self, item):
        """从队列中移除任务（如果存在）"""
        self.mutex.lock()
        if item in self.queue:
            self.queue.remove(item)
        if self.priority_item == item:
            self.priority_item = None
        self.mutex.unlock()

    def clear_queue(self):
        """清空所有待处理任务"""
        self.mutex.lock()
        self.queue.clear()
        self.priority_item = None
        self.mutex.unlock()

    def stop(self):
        self.is_running = False
        self.mutex.lock()
        self.cond.wakeAll()
        self.mutex.unlock()

    def run(self):
        while self.is_running:
            self.mutex.lock()
            # 如果没有任务且没有优先任务，就睡觉等待
            if not self.queue and not self.priority_item:
                self.cond.wait(self.mutex)
            
            # 决定处理哪个 Item
            target_item = None
            if self.priority_item:
                target_item = self.priority_item
            elif self.queue:
                target_item = self.queue[0]
            
            self.mutex.unlock()

            if not self.is_running: break
            if not target_item: continue

            # 核心检查：如果该 Item 已经被标记为删除，跳过所有处理，直接清理
            if target_item.is_deleted:
                self.mutex.lock()
                if target_item in self.queue:
                    self.queue.remove(target_item)
                if target_item == self.priority_item:
                    self.priority_item = None
                self.mutex.unlock()
                continue

            # 开始处理该 Item 的标签
            processed_something = False
            
            # 遍历标签
            for i, tag_data in enumerate(target_item.parsed_tags):
                if not self.is_running: break
                
                # 再次检查删除标记：如果在处理过程中被用户删除了，立即停止
                if target_item.is_deleted:
                    break

                # 如果我们在处理普通队列时，用户突然插队设置了 priority_item
                # 我们应该尽快切换 (除非 target 就是 priority)
                if self.priority_item and target_item != self.priority_item:
                    break 

                if tag_data.get('translation') is None:
                    # 执行翻译 (耗时操作)
                    text = tag_data['text']
                    trans = api.translate_tag(text)
                    cat = tag_data['category']
                    if cat == 'Pending':
                        cat = api.classify_tag(text)
                    
                    # 更新数据
                    tag_data['translation'] = trans
                    tag_data['category'] = cat
                    
                    # 发送信号更新 UI
                    self.tag_processed.emit(target_item, i, trans, cat)
                    processed_something = True
                    
                    if not HAS_TRANSLATOR:
                        QThread.msleep(5)

            # 再次检查该 Item 是否全部完成
            self.mutex.lock()
            # 重新扫描是否有漏网之鱼 (防止多线程并发修改导致的状态不一致)
            is_fully_done = all(t.get('translation') is not None for t in target_item.parsed_tags)
            
            # 再次检查删除，防止竞态条件
            if target_item.is_deleted:
                if target_item in self.queue: self.queue.remove(target_item)
                if target_item == self.priority_item: self.priority_item = None
            elif is_fully_done:
                if target_item in self.queue:
                    self.queue.remove(target_item)
                if target_item == self.priority_item:
                    self.priority_item = None # 优先任务完成，回归正常队列
            
            self.mutex.unlock()

            if not processed_something and is_fully_done:
                # 避免死循环占用 CPU (如果队列里都是已完成的项目)
                QThread.msleep(50)

class SingleTagWorker(QThread):
    result_signal = Signal(str, str, str)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text

    def run(self):
        cat = api.classify_tag(self.text)
        trans = api.translate_tag(self.text)
        self.result_signal.emit(self.text, trans, cat)

# --- Custom Widgets (TagChip, FlowLayout 等保持不变) ---

class TagChip(QLabel):
    toggled = Signal(bool)
    edited = Signal(str)

    def __init__(self, text, translation, category, color, parent=None):
        super().__init__(text, parent)
        self.full_text = text
        self.translation = translation
        self.category = category
        self.base_color = color
        self.is_active = True
        self.is_error = False
        self.error_msg = ""
        self.show_translation = False
        
        self.setFont(QFont("Arial", 10))
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setWordWrap(False)
        
        self.update_content()
        self.update_style()

    def set_loading_state(self):
        self.setText(f"{self.full_text} (...)")
        self.setStyleSheet("background-color: #eee; color: #888; border: 1px dashed #aaa; border-radius: 4px; padding: 4px 8px;")

    def update_data(self, translation, category, color):
        self.translation = translation
        self.category = category
        self.base_color = color
        self.update_content()
        self.update_style()

    def set_error_state(self, is_error, msg=""):
        self.is_error = is_error
        self.error_msg = msg
        self.update_style()

    def set_translation_mode(self, show):
        self.show_translation = show
        self.update_content()

    def update_content(self):
        if self.show_translation:
            self.setTextFormat(Qt.RichText)
            safe_text = html.escape(self.full_text)
            trans_text = self.translation if self.translation else "..."
            self.setText(f"<b>{safe_text}</b><br><span style='font-size:9px; color:#555;'>{trans_text}</span>")
        else:
            self.setTextFormat(Qt.PlainText)
            self.setText(self.full_text)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_active = not self.is_active
            self.update_style()
            self.toggled.emit(self.is_active)
    
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            text, ok = QInputDialog.getText(self, "编辑提示词", "修改提示词内容:", text=self.full_text)
            if ok and text:
                if text != self.full_text:
                    self.set_loading_state()
                    self.edited.emit(text)

    def set_active_by_filter(self, active):
        if self.is_active != active:
            self.is_active = active
            self.update_style()

    def update_style(self):
        border = "2px solid red" if self.is_error else "1px solid #aaa"
        if self.is_active:
            bg = self.base_color if self.base_color else "#ddd"
            style = f"background-color: {bg}; color: black; border-radius: 4px; border: {border}; padding: 4px 8px;"
        else:
            style = f"background-color: #f0f0f0; color: #aaa; border-radius: 4px; border: 1px dashed #ccc; text-decoration: line-through; padding: 4px 8px;"
        self.setStyleSheet(style)
        
        tooltip = f"Type: {self.category}\nTranslation: {self.translation}"
        if self.is_error and self.error_msg: tooltip = f"❌ 错误: {self.error_msg}\n{tooltip}"
        self.setToolTip(tooltip)

class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, hSpacing=5, vSpacing=5):
        super(FlowLayout, self).__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._item_list = []
        self._h_spacing = hSpacing
        self._v_spacing = vSpacing

    def __del__(self):
        item = self.takeAt(0)
        while item: item = self.takeAt(0)

    def addItem(self, item): self._item_list.append(item)
    def count(self): return len(self._item_list)
    def itemAt(self, index): return self._item_list[index] if 0 <= index < len(self._item_list) else None
    def takeAt(self, index): return self._item_list.pop(index) if 0 <= index < len(self._item_list) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self._do_layout(QRect(0, 0, width, 0), True)
    def setGeometry(self, rect):
        super(FlowLayout, self).setGeometry(rect)
        self._do_layout(rect, False)
    def sizeHint(self): return self.minimumSize()
    def minimumSize(self):
        size = QSize()
        for item in self._item_list: size = size.expandedTo(item.minimumSize())
        size += QSize(2 * self.contentsMargins().top(), 2 * self.contentsMargins().top())
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self._h_spacing

        for item in self._item_list:
            wid = item.widget()
            space_x = spacing
            space_y = self._v_spacing
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only: item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()

class FlowLayoutWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_container = QVBoxLayout(self)
        self.chips = []
        self.flow_frame = QFrame()
        self.flow_layout = FlowLayout()
        self.flow_frame.setLayout(self.flow_layout)
        self.layout_container.addWidget(self.flow_frame)
        self.layout_container.addStretch()

    def clear_chips(self):
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            widget = item.widget()
            if widget: widget.deleteLater()
        self.chips = []

    def add_chip(self, text, translation, category, color="#ddd"):
        chip = TagChip(text, translation, category, color)
        self.flow_layout.addWidget(chip)
        self.chips.append(chip)
        return chip

# --- Main Window ---

class PromptConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 提示词转换与筛选工具 (全局后台翻译版)")
        self.resize(1300, 800)

        self.items = [] 
        self.current_item_index = -1
        self.category_filters = {} 

        # 临时线程管理 (用于 Image Import 和 Single Tag Edit)
        self.running_threads = [] 
        self.img_worker = None

        # --- 核心改动：初始化全局后台 Worker ---
        self.global_worker = GlobalTranslationWorker()
        self.global_worker.tag_processed.connect(self.on_global_worker_update)
        self.global_worker.start() # 启动后一直运行，等待任务
        # -----------------------------------

        self.init_ui()

    # --- 线程管理 ---
    def register_thread(self, thread):
        """注册临时线程"""
        self.running_threads.append(thread)
        thread.finished.connect(lambda: self.cleanup_thread(thread))
        thread.start()

    def cleanup_thread(self, thread):
        if thread in self.running_threads:
            self.running_threads.remove(thread)
        thread.deleteLater()

    def closeEvent(self, event):
        # 停止全局 Worker
        if self.global_worker:
            self.global_worker.stop()
            self.global_worker.wait(500)
        
        # 停止临时线程
        for t in self.running_threads:
            if hasattr(t, 'stop'): t.stop()
            t.quit()
            t.wait(100)
        event.accept()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        # Left Column
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        self.input_tabs = QTabWidget()
        
        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("在此粘贴提示词...")
        btn_import_text = QPushButton("导入文本")
        btn_import_text.clicked.connect(self.import_from_text)
        text_layout.addWidget(self.text_input)
        text_layout.addWidget(btn_import_text)
        
        img_tab = QWidget()
        img_layout = QVBoxLayout(img_tab)
        btn_sel_img = QPushButton("批量导入图片并反推")
        btn_sel_img.clicked.connect(self.import_from_images)
        self.img_progress = QProgressBar()
        self.img_progress.setVisible(False)
        self.img_status_label = QLabel("未选择")
        self.img_status_label.setWordWrap(True)
        img_layout.addWidget(btn_sel_img)
        img_layout.addWidget(self.img_progress)
        img_layout.addWidget(self.img_status_label)
        img_layout.addStretch()
        
        self.input_tabs.addTab(text_tab, "文本")
        self.input_tabs.addTab(img_tab, "图片")
        
        list_header = QHBoxLayout()
        list_header.addWidget(QLabel("待处理列表:"))
        btn_clear = QPushButton("清空")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self.clear_all_items)
        list_header.addStretch()
        list_header.addWidget(btn_clear)
        
        self.item_list_widget = QListWidget()
        self.item_list_widget.currentRowChanged.connect(self.load_item_details)
        self.item_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.item_list_widget.customContextMenuRequested.connect(self.show_item_context_menu)

        left_layout.addWidget(self.input_tabs, 1)
        left_layout.addLayout(list_header)
        left_layout.addWidget(self.item_list_widget, 2)

        # Middle Column
        mid_panel = QWidget()
        mid_layout = QVBoxLayout(mid_panel)
        
        header_layout = QHBoxLayout()
        self.lbl_current_info = QLabel("请选择列表项")
        self.lbl_current_info.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.cb_translate = QCheckBox("显示翻译")
        self.cb_translate.stateChanged.connect(self.toggle_translation)
        header_layout.addWidget(self.lbl_current_info)
        header_layout.addStretch()
        header_layout.addWidget(self.cb_translate)
        
        self.lbl_warning = QLabel("")
        self.lbl_warning.setStyleSheet("color: red; font-weight: bold;")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.flow_widget = FlowLayoutWidget()
        scroll.setWidget(self.flow_widget)
        
        self.diff_group = QFrame()
        diff_layout = QVBoxLayout(self.diff_group)
        diff_layout.addWidget(QLabel("差异对比 (Diff):"))
        diff_scroll = QScrollArea()
        diff_scroll.setWidgetResizable(True)
        diff_scroll.setMinimumHeight(100)
        self.diff_flow_widget = FlowLayoutWidget()
        diff_scroll.setWidget(self.diff_flow_widget)
        diff_layout.addWidget(diff_scroll)
        self.diff_group.setVisible(False)
        
        mid_layout.addLayout(header_layout)
        mid_layout.addWidget(self.lbl_warning)
        splitter_mid = QSplitter(Qt.Vertical)
        splitter_mid.addWidget(scroll)
        splitter_mid.addWidget(self.diff_group)
        splitter_mid.setStretchFactor(0, 3)
        splitter_mid.setStretchFactor(1, 1)
        mid_layout.addWidget(splitter_mid)

        # Right Column
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        filter_group = QFrame()
        f_layout = QVBoxLayout(filter_group)
        f_layout.addWidget(QLabel("分类筛选"))
        self.filter_container = QWidget()
        self.filter_layout = QVBoxLayout(self.filter_container)
        f_layout.addWidget(self.filter_container)
        f_layout.addStretch()
        
        export_group = QFrame()
        e_layout = QVBoxLayout(export_group)
        e_layout.addWidget(QLabel("导出"))
        self.export_path_input = QLineEdit()
        btn_sel_export = QPushButton("...")
        btn_sel_export.clicked.connect(self.select_export_dir)
        h_path = QHBoxLayout()
        h_path.addWidget(self.export_path_input)
        h_path.addWidget(btn_sel_export)
        btn_export = QPushButton("生成节点")
        btn_export.setStyleSheet("background-color: #4CAF50; color: white;")
        btn_export.clicked.connect(self.export_nodes)
        e_layout.addLayout(h_path)
        e_layout.addWidget(btn_export)

        right_layout.addWidget(filter_group, 2)
        right_layout.addWidget(export_group, 0)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(mid_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([300, 600, 250])
        layout.addWidget(main_splitter)

    # --- Methods ---

    def import_from_text(self):
        text = self.text_input.toPlainText()
        if not text: return
        lines = text.strip().split('\n')
        for line in lines:
            if not line.strip(): continue
            name = self.generate_unique_name("Text_Item")
            item = PromptItem(name, line)
            self.items.append(item)
            self.item_list_widget.addItem(name)
            
            # 立即加入后台翻译队列
            self.global_worker.add_item(item)
            
        self.update_filters()
        QMessageBox.information(self, "完成", f"已导入 {len(lines)} 条文本，正在后台翻译...")

    def import_from_images(self):
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not folder: return
        files = []
        for f in os.listdir(folder):
            path = os.path.join(folder, f)
            path = resolve_path(path)
            if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')): 
                continue
            files.append(path)
        
        if not files: return
        
        self.img_progress.setVisible(True)
        self.img_progress.setRange(0, len(files))
        self.img_progress.setValue(0)
        self.img_status_label.setText("正在后台分析图片...")
        self.input_tabs.setTabEnabled(0, False)
        
        self.img_worker = ImageBatchWorker(files)
        self.img_worker.progress_signal.connect(self.on_import_progress)
        self.img_worker.item_ready_signal.connect(self.on_import_item_ready)
        self.img_worker.log_signal.connect(print)
        self.img_worker.finished_signal.connect(self.on_import_finished)
        
        self.register_thread(self.img_worker)

    def on_import_progress(self, c, t):
        self.img_progress.setValue(c)
        self.img_status_label.setText(f"处理中: {c}/{t}")

    def on_import_item_ready(self, item):
        item.name = self.generate_unique_name(item.name)
        self.items.append(item)
        self.item_list_widget.addItem(f"{item.name} [IMG]")
        self.update_filters()
        
        # 核心：图片生成Item后，立即加入后台翻译队列
        self.global_worker.add_item(item)

    def on_import_finished(self):
        self.img_progress.setVisible(False)
        self.img_status_label.setText("导入完成，翻译线程正在后台运行")
        self.input_tabs.setTabEnabled(0, True)

    def load_item_details(self, row):
        """
        切换列表项时，不再启动新翻译线程，而是告诉全局线程：我想优先看这个！
        """
        if row < 0 or row >= len(self.items): return
        
        self.current_item_index = row
        item = self.items[row]
        self.lbl_current_info.setText(f"当前编辑: {item.name}")
        
        # 1. 渲染当前已有数据 (可能部分已翻译，部分未翻译)
        self.flow_widget.clear_chips()
        for i, tag_data in enumerate(item.parsed_tags):
            cat = tag_data.get('category', 'Unknown')
            color = api.get_color_for_category(cat) if cat != 'Pending' else "#eee"
            trans = tag_data.get('translation') 
            
            chip = self.flow_widget.add_chip(tag_data['text'], trans, cat, color)
            chip.set_translation_mode(self.cb_translate.isChecked())
            
            is_enabled = tag_data.get('enabled', True)
            if not self.category_filters.get(cat, True) and cat != 'Pending':
                 is_enabled = False
            chip.is_active = is_enabled
            chip.update_style()
            
            chip.toggled.connect(lambda active, td=tag_data: self.on_chip_toggled(td, active))
            chip.edited.connect(lambda text, td=tag_data, c=chip: self.on_chip_edited(td, text, c))

        self.check_global_brackets(item)
        self.update_diff_view(item)

        # 2. 告诉全局线程插队
        self.global_worker.set_priority(item)

    def on_global_worker_update(self, item_obj, tag_index, translation, category):
        """
        全局 Worker 更新了某个 Item 的某个 Tag。
        我们只在当前界面正好是该 Item 时才更新 UI。
        """
        # 检查当前界面显示的 Item 是否是更新的这个
        if self.current_item_index < 0: return
        try:
            current_item = self.items[self.current_item_index]
        except IndexError:
            return

        if item_obj == current_item:
            # 找到对应的 Chip 并更新
            if tag_index < len(self.flow_widget.chips):
                chip = self.flow_widget.chips[tag_index]
                color = api.get_color_for_category(category)
                chip.update_data(translation, category, color)
                if category not in self.category_filters:
                    self.update_filters()

    def on_chip_edited(self, tag_data, new_text, chip_widget):
        tag_data['text'] = new_text
        # 编辑单个标签还是用临时线程，为了极速响应
        worker = SingleTagWorker(new_text, self)
        worker.result_signal.connect(lambda t, trans, cat: self.on_single_tag_result(tag_data, chip_widget, trans, cat))
        self.register_thread(worker)

    def on_single_tag_result(self, tag_data, chip_widget, translation, category):
        tag_data['translation'] = translation
        tag_data['category'] = category
        color = api.get_color_for_category(category)
        chip_widget.update_data(translation, category, color)
        if self.current_item_index >= 0:
            self.check_global_brackets(self.items[self.current_item_index])
            self.update_diff_view(self.items[self.current_item_index])
        self.update_filters()

    def on_chip_toggled(self, tag_data, active):
        tag_data['enabled'] = active
        if self.current_item_index >= 0:
            self.check_global_brackets(self.items[self.current_item_index])

    def toggle_translation(self, state):
        show = (state == Qt.Checked)
        for chip in self.flow_widget.chips:
            chip.set_translation_mode(show)
        for chip in self.diff_flow_widget.chips:
            chip.set_translation_mode(show)
        QTimer.singleShot(10, lambda: self.flow_widget.layout().activate())

    def update_diff_view(self, item):
        try:
            row = self.items.index(item)
        except ValueError:
            return 
            
        if row <= 0:
            self.diff_group.setVisible(False)
            return
        
        self.diff_group.setVisible(True)
        self.diff_flow_widget.clear_chips()
        
        prev_item = self.items[row-1]
        curr_tags = {t['text'] for t in item.parsed_tags}
        prev_tags = {t['text'] for t in prev_item.parsed_tags}
        
        added = curr_tags - prev_tags
        removed = prev_tags - curr_tags
        
        for tag in sorted(list(removed)):
            chip = self.diff_flow_widget.add_chip(f"- {tag}", "", "Removed", "#ffcccc")
            chip.set_translation_mode(False)
            chip.setToolTip("Removed in current")

        for tag in sorted(list(added)):
            chip = self.diff_flow_widget.add_chip(f"+ {tag}", "", "Added", "#ccffcc")
            chip.set_translation_mode(False)
            chip.setToolTip("Added in current")

    def check_global_brackets(self, item):
        for chip in self.flow_widget.chips: chip.set_error_state(False)
        stack = []
        pairs = {')': '(', ']': '[', '}': '{'}
        first_error_msg = ""
        
        for i, tag_data in enumerate(item.parsed_tags):
            if not tag_data['enabled']: continue
            text = tag_data['text']
            for char in text:
                if char in "([{":
                    stack.append((char, i))
                elif char in ")]}":
                    if not stack:
                        self.flow_widget.chips[i].set_error_state(True, f"多余的 '{char}'")
                        if not first_error_msg: first_error_msg = f"多余右括号 '{char}'"
                    else:
                        top_char, top_idx = stack[-1]
                        if pairs[char] == top_char:
                            stack.pop()
                        else:
                            self.flow_widget.chips[i].set_error_state(True, "括号不匹配")
                            self.flow_widget.chips[top_idx].set_error_state(True, "括号不匹配")
                            if not first_error_msg: first_error_msg = f"'{top_char}' 与 '{char}' 不匹配"
                            stack.pop()

        if stack:
            unique_indices = set(idx for _, idx in stack)
            for idx in unique_indices:
                self.flow_widget.chips[idx].set_error_state(True, "未闭合")
            if not first_error_msg: first_error_msg = "存在未闭合的括号"

        self.lbl_warning.setText(f"⚠️ {first_error_msg}" if first_error_msg else "")

    def update_filters(self):
        found_cats = set()
        for item in self.items:
            for tag in item.parsed_tags:
                cat = tag.get('category', 'Unknown')
                if cat != 'Pending': found_cats.add(cat)
        
        while self.filter_layout.count():
            w = self.filter_layout.takeAt(0).widget()
            if w: w.deleteLater()
            
        for cat in sorted(list(found_cats)):
            cb = QCheckBox(cat)
            cb.setChecked(self.category_filters.get(cat, True))
            color = api.get_color_for_category(cat)
            cb.setStyleSheet(f"QCheckBox {{ background-color: {color}; padding: 3px; border-radius: 3px; }}")
            cb.stateChanged.connect(lambda state, c=cat: self.on_filter_change(c, state))
            self.filter_layout.addWidget(cb)
            
            if cat not in self.category_filters:
                self.category_filters[cat] = True

    def on_filter_change(self, category, state):
        is_checked = (state == Qt.Checked)
        self.category_filters[category] = is_checked
        if self.current_item_index >= 0:
            for chip in self.flow_widget.chips:
                if chip.category == category:
                    chip.set_active_by_filter(is_checked)
            self.check_global_brackets(self.items[self.current_item_index])

    def show_item_context_menu(self, pos):
        item = self.item_list_widget.itemAt(pos)
        if not item: return
        menu = QMenu()
        del_act = QAction("删除", self)
        del_act.triggered.connect(self.delete_selected_item)
        menu.addAction(del_act)
        menu.exec(self.item_list_widget.mapToGlobal(pos))

    def delete_selected_item(self):
        row = self.item_list_widget.currentRow()
        if row < 0: return
        
        # 1. 标记删除，通知后台线程
        item = self.items[row]
        item.is_deleted = True
        self.global_worker.remove_item(item)
        
        # 2. UI 清理
        self.items.pop(row)
        self.item_list_widget.takeItem(row)
        
        if not self.items:
            self.flow_widget.clear_chips()
            self.diff_group.setVisible(False)
            self.lbl_current_info.setText("列表为空")
            self.current_item_index = -1
        else:
            # 如果删除的是当前显示的，或者删除导致索引变化，需要刷新一下
            # 简单起见，如果列表不为空，手动触发一下 currentItemChanged 对应的逻辑可能更好，
            # 但因为 pop 后 currentRow 会自动变（或变成-1），Qt 会发送信号。
            # 这里我们手动处理一下 current_item_index 的边界情况
            if self.current_item_index == row:
                self.flow_widget.clear_chips()
                self.lbl_current_info.setText("请选择列表项")
                self.current_item_index = -1
            elif self.current_item_index > row:
                self.current_item_index -= 1
        
        self.update_filters()

    def clear_all_items(self):
        if self.items and QMessageBox.question(self, "确认", "清空列表?") == QMessageBox.Yes:
            # 标记所有项目为已删除
            for item in self.items:
                item.is_deleted = True
            
            # 清空后台队列
            self.global_worker.clear_queue()
            
            # UI 清空
            self.items.clear()
            self.item_list_widget.clear()
            self.flow_widget.clear_chips()
            self.update_filters()
            self.current_item_index = -1
            self.lbl_current_info.setText("列表为空")

    def select_export_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if d: self.export_path_input.setText(d)

    def generate_unique_name(self, base_name):
        existing = {item.name for item in self.items}
        if base_name not in existing: return base_name
        c = 1
        while True:
            cand = f"{base_name}_{c}"
            if cand not in existing: return cand
            c += 1

    def export_nodes(self):
        target_dir = self.export_path_input.text()
        if not target_dir or not os.path.exists(target_dir):
            QMessageBox.warning(self, "错误", "请选择有效的导出目录")
            return

        count = 0
        timestamp = datetime.now().strftime("%Y%m%d%H%M")
        current_idx = 1

        for item in self.items:
            valid_tags = [t['text'] for t in item.parsed_tags if t.get('enabled', True)]
            if not valid_tags: continue
            
            prompt_content = ", ".join(valid_tags)
            folder_name = f"{timestamp}_{current_idx}"
            folder_path = os.path.join(target_dir, folder_name)
            
            try:
                os.makedirs(folder_path, exist_ok=True)
                
                if item.is_image and item.image_path and os.path.exists(item.image_path):
                    shutil.copy(item.image_path, folder_path)
                    prompt_content = uai.gen_ainode_content(item.image_path, prompt_content)
                
                with open(os.path.join(folder_path, "tags.txt"), 'w', encoding='utf-8') as f:
                    f.write(prompt_content)
                count += 1
                current_idx += 1
            except Exception as e:
                print(f"Export failed: {e}")

        QMessageBox.information(self, "导出完成", f"已生成 {count} 个节点文件夹")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PromptConverterApp()
    window.show()
    sys.exit(app.exec())