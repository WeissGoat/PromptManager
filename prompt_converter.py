import sys
import os
import random
import re
import html
import time
import shutil
import json
from natsort import natsorted
from datetime import datetime
# 引入 queue 用于线程安全队列
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLabel, QPushButton, 
                               QListWidget, QListWidgetItem, QFileDialog, 
                               QScrollArea, QFrame, QCheckBox, QSplitter, 
                               QTabWidget, QProgressBar, QMessageBox, QLineEdit, 
                               QGridLayout, QStyle, QInputDialog, QMenu, QLayout)
from PySide6.QtCore import Qt, Signal, QTimer, QSize, QThread, QObject, QMutex, QWaitCondition, QRect, QPoint
from PySide6.QtGui import QColor, QPalette, QFont, QAction, QPixmap
from util import resolve_path, create_shortcut

# --- API Import Setup ---
sys.path.append(r"F:\ThreeState")

import tag_classifier
HAS_REAL_API = True
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
    混合接口：优先使用本地硬编码字典 -> 本地文件缓存 -> Google 翻译
    """
    def __init__(self):
        if HAS_TRANSLATOR:
            # self.translator = GoogleTranslator(source='auto', target='zh-CN', proxies={'http': '127.0.0.1:7890'})
            self.translator = GoogleTranslator(source='auto', target='zh-CN')
        
        self.runtime_cache = {}
        
        # --- 本地字典缓存路径 ---
        self.cache_file = resolve_path("translation_cache.json")
        self.persistent_cache = self.load_cache()

        self.known_categories = {
            "loli": "special",
        }
        
        self.translations = {
        }

        self.category_colors = {} 
        self.palette = [
            "#FFB7B2", "#FFDAC1", "#E2F0CB", "#B5EAD7", "#C7CEEA", 
            "#E0BBE4", "#957DAD", "#D291BC", "#FEC8D8", "#FFDFD3"
        ]

    def load_cache(self):
        """加载本地翻译缓存文件"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading cache: {e}")
                return {}
        return {}

    def save_cache(self):
        """保存翻译缓存到本地文件"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.persistent_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving cache: {e}")

    def classify_tag(self, tag_text):


        clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
        if clean_tag in self.known_categories:
            return self.known_categories[clean_tag]

        return str(tag_classifier.get_tag_type2(clean_tag))
    
    
    def translate_tag(self, tag_text):
        """
        翻译逻辑升级：
        1. 清洗文本
        2. 硬编码字典 (最准)
        3. 本地文件缓存 (历史积累)
        4. Google 翻译 (联网获取并保存)
        """
        clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
        clean_tag = re.sub(r':\d+(\.\d+)?$', '', clean_tag)

        if not clean_tag: return ""

        # Level 1: 硬编码字典
        if clean_tag in self.translations:
            return self.translations[clean_tag]
        
        # Level 2: 本地文件缓存
        if clean_tag in self.persistent_cache:
            return self.persistent_cache[clean_tag]

        # Level 3: 运行时缓存 (防止同一次运行重复请求)
        if clean_tag in self.runtime_cache:
            return self.runtime_cache[clean_tag]

        # Level 4: Google 翻译
        if HAS_TRANSLATOR:
            try:
                result = self.translator.translate(clean_tag)
                
                # 写入缓存
                self.runtime_cache[clean_tag] = result
                self.persistent_cache[clean_tag] = result
                
                # 立即保存到文件 (虽然频繁IO，但标签量不大，保证数据安全)
                self.save_cache()
                
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
    
    def on_tag_category_changed(self, tag, cat):
        print(f"Tag {tag} changed to {cat}")
        tag_classifier.TagTypeCache.get_instance()[tag] = cat
        

api = MockAIInterface()

# --- Logic Models ---

class PromptItem:
    def __init__(self, name, raw_text, is_image=False, image_path=None):
        self.name = name
        self.raw_text = raw_text
        self.is_image = is_image
        self.image_path = image_path
        self.parsed_tags = [] 
        self.is_deleted = False 
        self.status = "ready" # pending, ready, analyzing
        
        # 如果 raw_text 为空 (例如刚导入图片还没反推)，不进行分割
        if self.raw_text:
            self.parse_tags()
        else:
            self.status = "pending"

    def parse_tags(self):
        """解析 raw_text 到 parsed_tags"""
        self.parsed_tags = []
        raw_tags = [t.strip() for t in self.raw_text.split(',') if t.strip()]
        for t in raw_tags:
            self.parsed_tags.append({
                'text': t, 
                'category': 'Pending', # 默认为 Pending，等待后台分类
                'enabled': True,
                'translation': None 
            })
        self.status = "ready"

# --- Workers (Async) ---

class ImageBatchWorker(QThread):
    """
    负责耗时的图片反推
    修改：只负责 image_to_prompt，不再负责分类，以加快反推速度
    """
    progress_signal = Signal(int, int)
    item_updated_signal = Signal(object) # 发送更新后的 Item 对象
    finished_signal = Signal()
    log_signal = Signal(str)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.items_to_process = items
        self.is_running = True

    def run(self):
        total = len(self.items_to_process)
        for i, item in enumerate(self.items_to_process):
            if not self.is_running: break
            if item.is_deleted: continue # 跳过已删除的

            try:
                # 状态标记为分析中
                item.status = "analyzing"
                
                # 调用耗时接口 (只做这一件事，做完就通知)
                prompt = api.image_to_prompt(item.image_path)
                
                # 更新 Item 数据
                item.raw_text = prompt
                item.parse_tags() # 解析标签 (此时 Category 全是 Pending)
                
                # 移除这里的分类代码，放到 GlobalTranslationWorker 去做
                
                # 完成，通知 UI，UI 会把它丢给 GlobalWorker 去分类和翻译
                self.item_updated_signal.emit(item)
                self.progress_signal.emit(i + 1, total)
            except Exception as e:
                self.log_signal.emit(f"Error processing {item.name}: {str(e)}")
                item.status = "error"
        
        self.finished_signal.emit()

    def stop(self):
        self.is_running = False

class GlobalTranslationWorker(QThread):
    """
    全局后台翻译线程。
    一直运行，自动处理队列中的 Item。支持“插队”。
    同时负责补全 Pending 状态的分类。
    """
    tag_processed = Signal(object, int, str, str) 
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue = [] 
        self.priority_item = None 
        self.is_running = True
        self.mutex = QMutex()
        self.cond = QWaitCondition()

    def add_item(self, item):
        self.mutex.lock()
        if item not in self.queue:
            self.queue.append(item)
        self.cond.wakeAll() 
        self.mutex.unlock()

    def set_priority(self, item):
        self.mutex.lock()
        self.priority_item = item
        self.cond.wakeAll() 
        self.mutex.unlock()

    def remove_item(self, item):
        self.mutex.lock()
        if item in self.queue:
            self.queue.remove(item)
        if self.priority_item == item:
            self.priority_item = None
        self.mutex.unlock()

    def clear_queue(self):
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
            if not self.queue and not self.priority_item:
                self.cond.wait(self.mutex)
            
            target_item = None
            if self.priority_item:
                target_item = self.priority_item
            elif self.queue:
                target_item = self.queue[0]
            
            self.mutex.unlock()

            if not self.is_running: break
            if not target_item: continue

            if target_item.is_deleted:
                self.mutex.lock()
                if target_item in self.queue: self.queue.remove(target_item)
                if target_item == self.priority_item: self.priority_item = None
                self.mutex.unlock()
                continue

            processed_something = False
            
            # 使用 list() 创建副本进行遍历，防止主线程修改 list (如分割标签) 导致 crash
            tags_copy = list(target_item.parsed_tags)
            
            for i, tag_data in enumerate(tags_copy):
                if not self.is_running: break
                if target_item.is_deleted: break
                if self.priority_item and target_item != self.priority_item: break 

                # 检查翻译，同时也检查分类是否为 Pending
                if tag_data.get('translation') is None or tag_data.get('category') == 'Pending':
                    text = tag_data['text']
                    
                    # 补充分类 (如果需要)
                    cat = tag_data['category']
                    if cat == 'Pending':
                        cat = api.classify_tag(text)
                    
                    # 补充翻译 (如果需要)
                    trans = tag_data.get('translation')
                    if trans is None:
                        trans = api.translate_tag(text)
                    
                    tag_data['translation'] = trans
                    tag_data['category'] = cat
                    
                    self.tag_processed.emit(target_item, i, trans, cat)
                    processed_something = True
                    
                    if not HAS_TRANSLATOR:
                        QThread.msleep(5)

            self.mutex.lock()
            # 检查是否全部完成 (既有翻译，分类也不是 Pending)
            # 注意：这里需要检查原始 list，因为可能有新加的
            is_fully_done = all(t.get('translation') is not None and t.get('category') != 'Pending' for t in target_item.parsed_tags)
            
            if target_item.is_deleted:
                if target_item in self.queue: self.queue.remove(target_item)
                if target_item == self.priority_item: self.priority_item = None
            elif is_fully_done:
                if target_item in self.queue: self.queue.remove(target_item)
                if target_item == self.priority_item: self.priority_item = None
            
            self.mutex.unlock()

            if not processed_something and is_fully_done:
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
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setWordWrap(False)
        
        # 启用自定义右键菜单策略
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        
        # 定时器用于区分单击和双击
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.setInterval(320) # 延迟220ms以检测双击
        self.click_timer.timeout.connect(self._perform_toggle)
        
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
            # 启动定时器，延迟触发切换
            self.click_timer.start()
        # 右键事件由 CustomContextMenu 处理
    
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 停止单击定时器，避免触发切换
            self.click_timer.stop()
            
            text, ok = QInputDialog.getText(self, "编辑提示词", "修改提示词内容:", text=self.full_text)
            if ok and text:
                if text != self.full_text:
                    self.set_loading_state()
                    self.edited.emit(text)

    def _perform_toggle(self):
        """实际执行切换逻辑"""
        self.is_active = not self.is_active
        self.update_style()
        self.toggled.emit(self.is_active)

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
        self.setWindowTitle("AI 提示词转换与筛选工具 ")
        self.resize(1300, 800)

        self.items = [] 
        self.current_item_index = -1
        self.category_filters = {} 

        # 临时线程管理 (用于 Image Import 和 Single Tag Edit)
        self.running_threads = [] 
        self.img_worker = None

        # --- 初始化全局后台 Worker ---
        self.global_worker = GlobalTranslationWorker()
        self.global_worker.tag_processed.connect(self.on_global_worker_update)
        self.global_worker.start() 
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
        if self.global_worker:
            self.global_worker.stop()
            self.global_worker.wait(500)
        
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
        
        # === 图片预览区域 ===
        self.preview_container = QWidget()
        self.preview_container.setVisible(False) # 默认隐藏
        self.preview_container.setFixedHeight(220) # 固定高度区域
        
        preview_layout = QVBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 10)
        preview_layout.addWidget(QLabel("图片预览:"))
        
        self.lbl_image_preview = QLabel()
        self.lbl_image_preview.setAlignment(Qt.AlignCenter)
        self.lbl_image_preview.setStyleSheet("background-color: #f0f0f0; border: 1px dashed #ccc; border-radius: 5px;")
        preview_layout.addWidget(self.lbl_image_preview)
        
        right_layout.addWidget(self.preview_container)
        # ==========================
        
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
        files = natsorted(files)
        if not files: return
        
        self.img_progress.setVisible(True)
        self.img_progress.setRange(0, len(files))
        self.img_progress.setValue(0)
        self.img_status_label.setText("正在后台分析图片...")
        self.input_tabs.setTabEnabled(0, False)
        
        # --- 核心修改：立即创建 Item，并传递给 Worker ---
        new_items = []
        for path in files:
            base_name = os.path.splitext(os.path.basename(path))[0]
            name = self.generate_unique_name(base_name)
            
            # 创建空 PromptItem (raw_text="", status="pending")
            item = PromptItem(name, "", is_image=True, image_path=path)
            self.items.append(item)
            new_items.append(item)
            
            # 立即显示在列表中
            self.item_list_widget.addItem(f"{item.name} [等待分析...]")
        
        # 启动 Worker，处理这些已存在的 Item
        self.img_worker = ImageBatchWorker(new_items)
        self.img_worker.progress_signal.connect(self.on_import_progress)
        self.img_worker.item_updated_signal.connect(self.on_single_image_processed) # 每处理完一张触发
        self.img_worker.log_signal.connect(print)
        self.img_worker.finished_signal.connect(self.on_import_finished)
        
        self.register_thread(self.img_worker)

    def on_import_progress(self, c, t):
        self.img_progress.setValue(c)
        self.img_status_label.setText(f"处理中: {c}/{t}")

    def on_single_image_processed(self, item):
        """当单张图片在后台分析完成后调用"""
        # 更新列表中的显示文本
        try:
            row = self.items.index(item)
            list_item = self.item_list_widget.item(row)
            if list_item:
                list_item.setText(f"{item.name} [IMG]")
        except ValueError:
            pass # Item 可能被删除了
        
        # 刷新筛选器 (因为可能有新分类出现)
        self.update_filters()
        
        # 核心：加入翻译队列，让 GlobalTranslationWorker 去处理分类和翻译
        self.global_worker.add_item(item)

        # 如果当前正选中这个项目，刷新详情页
        if self.current_item_index >= 0 and self.current_item_index < len(self.items):
             if self.items[self.current_item_index] == item:
                 self.load_item_details(self.current_item_index)

    def on_import_finished(self):
        self.img_progress.setVisible(False)
        self.img_status_label.setText("所有图片分析完成")
        self.input_tabs.setTabEnabled(0, True)

    def load_item_details(self, row):
        if row < 0 or row >= len(self.items): return
        
        self.current_item_index = row
        item = self.items[row]
        self.lbl_current_info.setText(f"当前编辑: {item.name}")

        # === 图片预览逻辑 ===
        if item.is_image and item.image_path and os.path.exists(item.image_path):
            self.preview_container.setVisible(True)
            pixmap = QPixmap(item.image_path)
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(self.lbl_image_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.lbl_image_preview.setPixmap(scaled_pixmap)
            else:
                self.lbl_image_preview.setText("无法加载图片")
        else:
            self.preview_container.setVisible(False)
        # ====================
        
        self.flow_widget.clear_chips()

        # 处理未分析完成的情况
        if item.status == "pending" or item.status == "analyzing":
            self.flow_widget.layout_container.addWidget(QLabel("⏳ 正在分析图片提示词，请稍候...", alignment=Qt.AlignCenter))
            return
        
        for i, tag_data in enumerate(item.parsed_tags):
            cat = tag_data.get('category', 'Unknown')
            color = api.get_color_for_category(cat) if cat != 'Pending' else "#eee"
            trans = tag_data.get('translation') 
            
            chip = self.flow_widget.add_chip(tag_data['text'], trans, cat, color)
            chip.set_translation_mode(self.cb_translate.isChecked())
            
            # --- 绑定右键菜单 ---
            # 连接右键信号到主程序的处理函数
            chip.customContextMenuRequested.connect(lambda pos, c=chip, td=tag_data: self.show_tag_context_menu(pos, c, td))
            # ------------------
            
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

    # --- 右键菜单功能 ---
    def show_tag_context_menu(self, pos, chip, tag_data):
        menu = QMenu(self)

        known_cats = self.get_found_cats()
        
        # 2. 添加分类选项
        for cat in sorted(known_cats):
            act = QAction(cat, self)
            # 如果是当前分类，标记一下（或者禁用）
            if cat == tag_data['category']:
                act.setCheckable(True)
                act.setChecked(True)
                act.setEnabled(False)
            
            # 连接槽函数
            act.triggered.connect(lambda checked, c=cat: self.change_tag_category(tag_data, chip, c))
            menu.addAction(act)
            
        menu.addSeparator()
        
        # 3. 添加新增分类选项
        new_cat_act = QAction("(+) 新增分类...", self)
        new_cat_act.triggered.connect(lambda: self.add_new_category_dialog(tag_data, chip))
        menu.addAction(new_cat_act)
        
        # 4. 显示菜单
        menu.exec(chip.mapToGlobal(pos))

    def change_tag_category(self, tag_data, chip, new_category):
        tag_data['category'] = new_category
        api.on_tag_category_changed(tag_data['text'], new_category)
        # 更新颜色
        color = api.get_color_for_category(new_category)
        
        # 更新 UI
        chip.update_data(tag_data['translation'], new_category, color)
        
        # 更新筛选列表（如果这是个新分类）
        if new_category not in self.category_filters:
            self.update_filters()
            
    def add_new_category_dialog(self, tag_data, chip):
        text, ok = QInputDialog.getText(self, "新增分类", "请输入新分类名称:")
        if ok and text:
            # 清理输入
            new_cat = text.strip()
            if new_cat:
                self.change_tag_category(tag_data, chip, new_cat)
    # -------------------

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
        # 检查是否包含分割符
        if ',' in new_text or '，' in new_text:
            # 1. 分割文本
            parts = re.split(r'[,，]', new_text)
            parts = [p.strip() for p in parts if p.strip()]
            
            if not parts: return # 分割后为空
            
            # 2. 获取当前操作的 Item
            if self.current_item_index < 0: return
            item = self.items[self.current_item_index]
            
            # 3. 找到原有 tag 的位置
            try:
                idx = item.parsed_tags.index(tag_data)
            except ValueError:
                return # 找不到数据，可能已被修改
                
            # 4. 构建新标签列表
            new_tags_data = []
            for p in parts:
                new_tags_data.append({
                    'text': p, 
                    # 关键：新分割出来的 Tag 分类设为 Pending，触发后台重新分类
                    'category': 'Pending', 
                    'enabled': tag_data.get('enabled', True), # 保持原有启用状态
                    'translation': None 
                })
                
            # 5. 替换：删除旧的，插入新的
            # 使用切片替换将新标签插入到原有位置
            item.parsed_tags[idx:idx+1] = new_tags_data
            
            # 6. 刷新界面 (重新加载详情)
            self.load_item_details(self.current_item_index)
            
            # 7. 确保新标签被后台处理 (加入优先队列)
            self.global_worker.add_item(item)
            self.global_worker.set_priority(item)
            
            return

        # 如果没有逗号，执行原有的单标签更新逻辑
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

    def get_found_cats(self):
        found_cats = set()
        for item in self.items:
            for tag in item.parsed_tags:
                cat = tag.get('category', 'Unknown')
                if cat != 'Pending': found_cats.add(cat)
        return found_cats

    def update_filters(self):
        found_cats = self.get_found_cats()
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
        # 1. 修复状态判断 (state 是 int, 0=Unchecked, 2=Checked)
        is_filter_on = (state != 0)
        self.category_filters[category] = is_filter_on
        
        if self.current_item_index >= 0:
            item = self.items[self.current_item_index]
            
            # 2. 遍历当前显示的 Chip，结合“过滤器状态”和“用户手动状态”来决定最终状态
            # 注意：chips 和 item.parsed_tags 是一一对应的顺序
            for i, chip in enumerate(self.flow_widget.chips):
                if chip.category == category:
                    # 获取该 Tag 用户是否手动启用了它 (默认为 True)
                    user_enabled = item.parsed_tags[i].get('enabled', True)
                    
                    # 只有当 [过滤器开启] 且 [用户未手动禁用] 时，Tag 才显示为激活
                    should_be_active = is_filter_on and user_enabled
                    
                    chip.set_active_by_filter(should_be_active)
            
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