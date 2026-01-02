import sys
import os
import random
import re
import html
import time
import shutil
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLabel, QPushButton, 
                               QListWidget, QListWidgetItem, QFileDialog, 
                               QScrollArea, QFrame, QCheckBox, QSplitter, 
                               QTabWidget, QProgressBar, QMessageBox, QLineEdit, 
                               QGridLayout, QStyle, QInputDialog, QMenu)
from PySide6.QtCore import Qt, Signal, QTimer, QSize, QThread, QObject, QMutex
from PySide6.QtGui import QColor, QPalette, QFont, QAction

# --- Mock Interfaces (模拟接口) ---
# 实际使用时请保留您的 sys.path 设置
sys.path.append(r"F:\ThreeState")

# 尝试导入，如果没有则使用 Mock 避免报错
try:
    import danbooru_api
    from translation import translate
    HAS_REAL_API = True
except ImportError:
    HAS_REAL_API = False
    print("Warning: API modules not found, running in full mock mode.")

class MockAIInterface:
    """
    模拟外部接口 (增加了模拟延迟以演示异步效果)
    """
    def __init__(self):
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
            "cyberpunk": "赛博朋克", "mecha": "机甲", "cat ears": "猫耳"
        }

        self.category_colors = {} 
        self.palette = [
            "#FFB7B2", "#FFDAC1", "#E2F0CB", "#B5EAD7", "#C7CEEA", 
            "#E0BBE4", "#957DAD", "#D291BC", "#FEC8D8", "#FFDFD3"
        ]

    def classify_tag(self, tag_text):
        # 模拟网络/计算延迟
        time.sleep(0.01) 
        
        if HAS_REAL_API:
            try:
                return str(danbooru_api.get_tag_type(tag_text))
            except:
                pass

        clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
        if clean_tag in self.known_categories:
            return self.known_categories[clean_tag]
        
        # 简单的哈希分类模拟
        cats = ["Attribute", "Object", "Effect", "Unknown", "Artist"]
        return cats[len(clean_tag) % len(cats)]
    
    def translate_tag(self, tag_text):
        # 模拟网络/计算延迟
        time.sleep(0.01) 
        
        if HAS_REAL_API:
            try:
                clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
                clean_tag = re.sub(r':\d+(\.\d+)?$', '', clean_tag)
                return translate(clean_tag)
            except:
                pass

        clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
        clean_tag = re.sub(r':\d+(\.\d+)?$', '', clean_tag)
        return self.translations.get(clean_tag, clean_tag) # 默认返回原文

    def get_color_for_category(self, category):
        if category not in self.category_colors:
            color = self.palette[len(self.category_colors) % len(self.palette)]
            self.category_colors[category] = color
        return self.category_colors[category]

    def image_to_prompt(self, image_path):
        # 模拟较长的反推时间 (0.5s)
        time.sleep(0.5)
        
        filename = os.path.basename(image_path)
        base_tags = "masterpiece, best quality, 1girl, solo, looking at viewer"
        random_tags = ["red hair", "blue dress", "forest", "cyberpunk", "mecha", "cat ears"]
        extra = ", ".join(random.sample(random_tags, 3))
        return f"{base_tags}, {extra}, source_{filename}"

api = MockAIInterface()

# --- Logic Models ---

class PromptItem:
    """
    数据类。
    注意：为了性能，初始化时不进行完整的分类和翻译，而是由 Worker 处理。
    """
    def __init__(self, name, raw_text, is_image=False, image_path=None):
        self.name = name
        self.raw_text = raw_text
        self.is_image = is_image
        self.image_path = image_path
        self.parsed_tags = [] # List of dicts: {'text':..., 'category':..., 'enabled':..., 'trans':...}
        
        # 简单的预处理分割，但不调用 API
        raw_tags = [t.strip() for t in self.raw_text.split(',') if t.strip()]
        for t in raw_tags:
            # 默认分类 Unknown，翻译 None，等待异步填充
            self.parsed_tags.append({
                'text': t, 
                'category': 'Pending', 
                'enabled': True,
                'translation': None 
            })

# --- Workers (异步线程) ---

class ImageBatchWorker(QThread):
    """
    后台处理图片导入：
    1. 图片反推 (耗时)
    2. 初步标签分割和分类 (耗时)
    """
    progress_signal = Signal(int, int) # current, total
    item_ready_signal = Signal(object) # PromptItem object
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
                # 1. Image to Prompt
                prompt = api.image_to_prompt(path)
                
                # 2. Create Item
                base_name = os.path.splitext(os.path.basename(path))[0]
                item = PromptItem(base_name, prompt, is_image=True, image_path=path)
                
                # 3. Pre-classify tags in background to save time later
                for tag_data in item.parsed_tags:
                    tag_data['category'] = api.classify_tag(tag_data['text'])
                
                self.item_ready_signal.emit(item)
                self.progress_signal.emit(i + 1, total)
                
            except Exception as e:
                self.log_signal.emit(f"Error processing {path}: {str(e)}")
        
        self.finished_signal.emit()

    def stop(self):
        self.is_running = False

class TagBatchTranslationWorker(QThread):
    """
    后台批量翻译标签：
    用于点击列表项后，快速刷新界面，然后后台慢慢填入翻译。
    """
    # (row_index, tag_index, translation, category)
    # 包含了 category 是因为有时候我们可能想重新校准分类
    tag_updated_signal = Signal(int, int, str, str) 
    finished_signal = Signal()

    def __init__(self, row_index, tags_data, parent=None):
        super().__init__(parent)
        self.row_index = row_index
        # Make a shallow copy of data to avoid thread conflicts if possible, 
        # though strictly we are just reading 'text'
        self.tags_to_process = tags_data 
        self.is_running = True

    def run(self):
        for i, tag_data in enumerate(self.tags_to_process):
            if not self.is_running: break
            
            # 如果翻译为空，才去翻译 (避免重复工作)
            if not tag_data.get('translation'):
                text = tag_data['text']
                trans = api.translate_tag(text)
                # 顺便确认一下分类，如果之前是 Pending
                cat = tag_data['category']
                if cat == 'Pending':
                    cat = api.classify_tag(text)
                
                self.tag_updated_signal.emit(self.row_index, i, trans, cat)
        
        self.finished_signal.emit()

    def stop(self):
        self.is_running = False

class SingleTagWorker(QThread):
    """
    处理单个标签的编辑：
    编辑后重新分类、重新翻译。
    """
    result_signal = Signal(str, str, str) # text, translation, category

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text

    def run(self):
        cat = api.classify_tag(self.text)
        trans = api.translate_tag(self.text)
        self.result_signal.emit(self.text, trans, cat)

# --- Custom Widgets ---

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
        self.setMargin(5)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setWordWrap(False) # 避免自动换行导致布局抖动
        
        self.update_content()
        self.update_style()

    def set_loading_state(self):
        """显示加载中的状态"""
        self.setText(f"{self.full_text} (...)")
        self.setStyleSheet("background-color: #eee; color: #888; border: 1px dashed #aaa; border-radius: 4px; padding: 4px 8px;")

    def update_data(self, translation, category, color):
        """异步获取数据后更新"""
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
                # 只有文本变了才触发更新
                if text != self.full_text:
                    self.set_loading_state() # 立即反馈
                    self.edited.emit(text)

    def set_active_by_filter(self, active):
        if self.is_active != active:
            self.is_active = active
            self.update_style()

    def update_style(self):
        if self.is_error:
            border = "2px solid red"
        else:
            border = "1px solid #aaa"
        
        if self.is_active:
            bg = self.base_color if self.base_color else "#ddd"
            fg = "#000"
            style = f"""
                background-color: {bg}; 
                color: {fg}; 
                border-radius: 4px; 
                border: {border};
                padding: 4px 8px;
            """
        else:
            style = f"""
                background-color: #f0f0f0; 
                color: #aaa; 
                border-radius: 4px; 
                border: 1px dashed #ccc;
                text-decoration: line-through;
                padding: 4px 8px;
            """
        self.setStyleSheet(style)
        
        tooltip = f"Type: {self.category}\nTranslation: {self.translation}"
        if self.is_error and self.error_msg:
            tooltip = f"❌ 错误: {self.error_msg}\n{tooltip}"
        self.setToolTip(tooltip)

# Flow Layout (Standard Implementation)
from PySide6.QtWidgets import QLayout
from PySide6.QtCore import QRect, QPoint

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
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self._item_list.append(item)

    def count(self):
        return len(self._item_list)

    def itemAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super(FlowLayout, self).setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())
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
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()

class FlowLayoutWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_container = QVBoxLayout(self)
        self.chips = [] # Stores TagChip references
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
        self.setWindowTitle("AI 提示词转换与筛选工具 (异步增强版)")
        self.resize(1300, 800)

        self.items = [] 
        self.current_item_index = -1
        self.category_filters = {} 

        # Keep references to threads to prevent GC
        self.img_worker = None
        self.trans_worker = None
        self.single_tag_worker = None

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        # === Left Column ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        self.input_tabs = QTabWidget()
        
        # Tab 1: Text
        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("在此粘贴提示词，每行一组...")
        btn_import_text = QPushButton("导入文本")
        btn_import_text.clicked.connect(self.import_from_text)
        text_layout.addWidget(self.text_input)
        text_layout.addWidget(btn_import_text)
        
        # Tab 2: Images (Updated with ProgressBar)
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
        
        self.input_tabs.addTab(text_tab, "文本导入")
        self.input_tabs.addTab(img_tab, "图片反推 (Async)")
        
        # List Header
        list_header = QHBoxLayout()
        list_header.addWidget(QLabel("待处理列表:"))
        btn_clear = QPushButton("清空")
        btn_clear.setFixedWidth(60)
        btn_clear.setStyleSheet("background-color: #ffcccc; border: 1px solid #f99; border-radius: 3px;")
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

        # === Middle Column ===
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
        
        # Diff Section
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

        # === Right Column ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        filter_group = QFrame()
        filter_group.setFrameStyle(QFrame.StyledPanel)
        f_layout = QVBoxLayout(filter_group)
        f_layout.addWidget(QLabel("分类筛选"))
        self.filter_container = QWidget()
        self.filter_layout = QVBoxLayout(self.filter_container)
        f_layout.addWidget(self.filter_container)
        f_layout.addStretch()
        
        export_group = QFrame()
        export_group.setFrameStyle(QFrame.StyledPanel)
        e_layout = QVBoxLayout(export_group)
        e_layout.addWidget(QLabel("导出"))
        
        self.export_path_input = QLineEdit()
        btn_sel_export = QPushButton("...")
        btn_sel_export.clicked.connect(self.select_export_dir)
        h_path = QHBoxLayout()
        h_path.addWidget(self.export_path_input)
        h_path.addWidget(btn_sel_export)
        
        btn_export = QPushButton("生成节点")
        btn_export.setStyleSheet("background-color: #4CAF50; color: white; padding: 10px;")
        btn_export.clicked.connect(self.export_nodes)
        
        e_layout.addLayout(h_path)
        e_layout.addWidget(btn_export)

        right_layout.addWidget(filter_group, 2)
        right_layout.addWidget(export_group, 0)

        # Main Splitter
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(mid_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([300, 600, 250])
        
        layout.addWidget(main_splitter)

    # --- Utils ---
    def generate_unique_name(self, base_name):
        existing = {item.name for item in self.items}
        if base_name not in existing: return base_name
        c = 1
        while True:
            cand = f"{base_name}_{c}"
            if cand not in existing: return cand
            c += 1

    # --- Async Import Logic ---

    def import_from_text(self):
        text = self.text_input.toPlainText()
        if not text: return
        
        lines = text.strip().split('\n')
        for line in lines:
            if not line.strip(): continue
            name = self.generate_unique_name("Text_Item")
            # Text import is fast enough to keep sync for now, 
            # but in a real massive import we'd want async here too.
            # We will use lazy parsing for classification though.
            item = PromptItem(name, line)
            
            # Simple pre-classification for text (sync) because it's usually fast purely on string ops
            # But if classify_tag calls API, we should Background it.
            # For this demo, let's assume text import is small or we accept small freeze.
            # Or we can mark them 'Pending' and let the Detail view Loader handle it.
            self.items.append(item)
            self.item_list_widget.addItem(name)
        
        self.update_filters()
        QMessageBox.information(self, "完成", f"已导入 {len(lines)} 条文本")

    def import_from_images(self):
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not folder: return
        
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if not files: return
        
        # UI Setup
        self.img_progress.setVisible(True)
        self.img_progress.setRange(0, len(files))
        self.img_progress.setValue(0)
        self.img_status_label.setText("正在后台分析图片，请稍候...")
        self.input_tabs.setTabEnabled(0, False) # Lock other tabs
        
        # Thread Setup
        self.img_worker = ImageBatchWorker(files)
        self.img_worker.progress_signal.connect(self.on_import_progress)
        self.img_worker.item_ready_signal.connect(self.on_import_item_ready)
        self.img_worker.log_signal.connect(lambda s: print(s))
        self.img_worker.finished_signal.connect(self.on_import_finished)
        self.img_worker.start()

    def on_import_progress(self, current, total):
        self.img_progress.setValue(current)
        self.img_status_label.setText(f"处理中: {current}/{total}")

    def on_import_item_ready(self, item):
        # Rename to ensure unique if necessary (though worker tries to use filename)
        item.name = self.generate_unique_name(item.name)
        self.items.append(item)
        self.item_list_widget.addItem(f"{item.name} [IMG]")
        self.update_filters() # Update filters incrementally

    def on_import_finished(self):
        self.img_progress.setVisible(False)
        self.img_status_label.setText(f"导入完成! 共 {self.img_progress.maximum()} 张")
        self.input_tabs.setTabEnabled(0, True)
        self.img_worker = None

    # --- Item Loading (Async Tag Processing) ---

    def load_item_details(self, row):
        if row < 0 or row >= len(self.items): return
        
        # Cancel previous worker if user switches fast
        if self.trans_worker and self.trans_worker.isRunning():
            self.trans_worker.stop()
            self.trans_worker.wait() # Fast wait
        
        self.current_item_index = row
        item = self.items[row]
        self.lbl_current_info.setText(f"当前编辑: {item.name}")
        
        self.flow_widget.clear_chips()
        
        # 1. First Pass: Create Chips immediately with available data
        # If translation is missing, it will show placeholder or raw text
        for i, tag_data in enumerate(item.parsed_tags):
            cat = tag_data.get('category', 'Unknown')
            color = api.get_color_for_category(cat) if cat != 'Pending' else "#eee"
            
            # Initial text
            trans = tag_data.get('translation') # Might be None
            
            chip = self.flow_widget.add_chip(tag_data['text'], trans, cat, color)
            chip.set_translation_mode(self.cb_translate.isChecked())
            
            # Restore enabled state
            is_enabled = tag_data.get('enabled', True)
            if not self.category_filters.get(cat, True) and cat != 'Pending':
                 is_enabled = False
            
            chip.is_active = is_enabled
            chip.update_style()
            
            # Signals
            chip.toggled.connect(lambda active, td=tag_data: self.on_chip_toggled(td, active))
            chip.edited.connect(lambda text, td=tag_data, c=chip: self.on_chip_edited(td, text, c))

        # Check brackets immediately (might be inaccurate if text is messy, but good enough)
        self.check_global_brackets(item)
        self.update_diff_view(item)

        # 2. Start Async Worker to fetch missing translations/categories
        # We process ALL tags to ensure even 'Pending' ones get resolved
        self.trans_worker = TagBatchTranslationWorker(row, item.parsed_tags)
        self.trans_worker.tag_updated_signal.connect(self.on_tag_worker_update)
        self.trans_worker.start()

    def on_tag_worker_update(self, row_index, tag_index, translation, category):
        # Ensure we are still looking at the same item
        if row_index != self.current_item_index: return
        
        # Update Data
        item = self.items[row_index]
        if tag_index >= len(item.parsed_tags): return
        
        tag_data = item.parsed_tags[tag_index]
        tag_data['translation'] = translation
        tag_data['category'] = category
        
        # Update UI Chip
        if tag_index < len(self.flow_widget.chips):
            chip = self.flow_widget.chips[tag_index]
            color = api.get_color_for_category(category)
            chip.update_data(translation, category, color)
            
            # Refresh filters if new category appeared
            if category not in self.category_filters:
                self.update_filters()

    # --- Chip Editing (Async) ---

    def on_chip_edited(self, tag_data, new_text, chip_widget):
        # Update text immediately
        tag_data['text'] = new_text
        
        # Start async worker for re-classification
        worker = SingleTagWorker(new_text, self)
        # Use closure to capture the specific chip widget
        worker.result_signal.connect(lambda t, trans, cat: self.on_single_tag_result(tag_data, chip_widget, trans, cat))
        worker.finished.connect(lambda: worker.deleteLater())
        worker.start()
        
        # Keep reference to prevent GC (optional, but good practice if not using parent)
        self.single_tag_worker = worker

    def on_single_tag_result(self, tag_data, chip_widget, translation, category):
        tag_data['translation'] = translation
        tag_data['category'] = category
        
        color = api.get_color_for_category(category)
        chip_widget.update_data(translation, category, color)
        
        if self.current_item_index >= 0:
            self.check_global_brackets(self.items[self.current_item_index])
            self.update_diff_view(self.items[self.current_item_index])
        
        self.update_filters()

    # --- Other Logic (Kept mostly same) ---

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
        
        # Force layout update
        QTimer.singleShot(10, lambda: self.flow_widget.layout().activate())

    def update_diff_view(self, item):
        row = self.items.index(item)
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
            chip.set_translation_mode(False) # Simplify diff view
            chip.setToolTip("Removed in current")

        for tag in sorted(list(added)):
            chip = self.diff_flow_widget.add_chip(f"+ {tag}", "", "Added", "#ccffcc")
            chip.set_translation_mode(False)
            chip.setToolTip("Added in current")

    def check_global_brackets(self, item):
        for chip in self.flow_widget.chips:
            chip.set_error_state(False)
        
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
                if cat != 'Pending': 
                    found_cats.add(cat)
        
        # Simple rebuild
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
        
        # Visual Update
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
        self.items.pop(row)
        self.item_list_widget.takeItem(row)
        if not self.items:
            self.flow_widget.clear_chips()
            self.diff_group.setVisible(False)
            self.lbl_current_info.setText("列表为空")
        self.update_filters()

    def clear_all_items(self):
        if self.items and QMessageBox.question(self, "确认", "清空列表?") == QMessageBox.Yes:
            self.items.clear()
            self.item_list_widget.clear()
            self.flow_widget.clear_chips()
            self.update_filters()

    def select_export_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if d: self.export_path_input.setText(d)

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
                with open(os.path.join(folder_path, "tags.txt"), 'w', encoding='utf-8') as f:
                    f.write(prompt_content)
                
                if item.is_image and item.image_path and os.path.exists(item.image_path):
                    ext = os.path.splitext(item.image_path)[1]
                    shutil.copy2(item.image_path, os.path.join(folder_path, f"tmp{ext}"))
                
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