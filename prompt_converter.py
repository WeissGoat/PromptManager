import sys
import os
import random
import re
import html
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLabel, QPushButton, 
                               QListWidget, QListWidgetItem, QFileDialog, 
                               QScrollArea, QFrame, QCheckBox, QSplitter, 
                               QTabWidget, QProgressBar, QMessageBox, QLineEdit, 
                               QGridLayout, QStyle, QInputDialog, QMenu)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QColor, QPalette, QFont, QAction

# --- Mock Interfaces (模拟接口) ---
sys.path.append(r"F:\ThreeState")

import danbooru_api
from translation import translate

class MockAIInterface:
    """
    模拟外部接口
    """
    def __init__(self):
        # 模拟已知的一些映射，不在这个表里的随机分配
        self.known_categories = {
            "1girl": "Character", "solo": "Character", "long hair": "Attribute", 
            "blue eyes": "Attribute", "dress": "Clothing", "masterpiece": "Quality",
            "best quality": "Quality", "simple background": "Background", 
            "white background": "Background", "standing": "Pose", "looking at viewer": "Pose",
            "monochrome": "Style", "sketch": "Style", "greyscale": "Style"
        }
        
        # 模拟翻译字典
        self.translations = {
            "1girl": "1个女孩", "solo": "单人", "long hair": "长发", 
            "blue eyes": "蓝眼", "dress": "连衣裙", "masterpiece": "杰作",
            "best quality": "最佳质量", "simple background": "简单背景",
            "white background": "白背景", "standing": "站立", "looking at viewer": "看镜头",
            "monochrome": "单色", "sketch": "素描", "greyscale": "灰度",
            "red hair": "红发", "blue dress": "蓝裙子", "forest": "森林",
            "cyberpunk": "赛博朋克", "mecha": "机甲", "cat ears": "猫耳"
        }

        self.category_colors = {} # 动态分配颜色
        
        # 预定义一些好看的颜色 (Pastel tones)
        self.palette = [
            "#FFB7B2", "#FFDAC1", "#E2F0CB", "#B5EAD7", "#C7CEEA", 
            "#E0BBE4", "#957DAD", "#D291BC", "#FEC8D8", "#FFDFD3"
        ]

    def classify_tag(self, tag_text):
        """
        输入 tag, 返回类型名称
        """
        clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
        
        # 1. 查表
        # if clean_tag in self.known_categories:
        #     cat = self.known_categories[clean_tag]
        # else:
        #     # 2. 模拟：随机分配一个类型用于演示
        #     # 实际中你会调用你的分类模型
        #     cats = ["Attribute", "Object", "Effect", "Unknown", "Artist"]
        #     # 为了演示一致性，根据字符长度hash一下
        #     cat = cats[len(clean_tag) % len(cats)]
        
        return str(danbooru_api.get_tag_type(tag_text))
    
    def translate_tag(self, tag_text):
        """
        输入 tag, 返回中文翻译
        """
        # 简单的清理
        # 1. 去除括号
        clean_tag = re.sub(r'[\(\)\[\]\{\}]', '', tag_text).strip().lower()
        # 2. 去除权重 (例如 :1.2 或 :0.5)
        clean_tag = re.sub(r':\d+(\.\d+)?$', '', clean_tag)
        
        return translate(clean_tag)

    def get_color_for_category(self, category):
        """
        获取类型的颜色，如果是新类型则分配新颜色
        """
        if category not in self.category_colors:
            color = self.palette[len(self.category_colors) % len(self.palette)]
            self.category_colors[category] = color
        return self.category_colors[category]

    def image_to_prompt(self, image_path):
        """
        模拟图片反推
        """
        # 这里只是模拟返回一些数据
        filename = os.path.basename(image_path)
        base_tags = "masterpiece, best quality, 1girl, solo, looking at viewer"
        random_tags = ["red hair", "blue dress", "forest", "cyberpunk", "mecha", "cat ears"]
        extra = ", ".join(random.sample(random_tags, 3))
        return f"{base_tags}, {extra}, source_{filename}"

api = MockAIInterface()

# --- Custom Widgets ---

class TagChip(QLabel):
    """
    单个标签组件。
    支持点击切换启用/禁用状态。
    支持双击编辑。
    支持错误状态高亮。
    支持显示翻译。
    """
    toggled = Signal(bool) # 状态改变信号
    edited = Signal(str)   # 文本修改信号

    def __init__(self, text, translation, category, color, parent=None):
        super().__init__(text, parent)
        self.full_text = text
        self.translation = translation
        self.category = category
        self.base_color = color
        self.is_active = True
        
        # States
        self.is_error = False
        self.error_msg = ""
        self.show_translation = False
        
        self.setFont(QFont("Arial", 10))
        self.setMargin(5)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        
        self.update_content()
        self.update_style()

    def set_error_state(self, is_error, msg=""):
        """设置错误状态并更新样式"""
        self.is_error = is_error
        self.error_msg = msg
        self.update_style()

    def set_translation_mode(self, show):
        """切换翻译显示模式"""
        self.show_translation = show
        self.update_content()

    def update_content(self):
        """更新显示的文本内容"""
        if self.show_translation:
            self.setTextFormat(Qt.RichText)
            # 转义 HTML 字符，防止 <lora:...> 等包含尖括号的内容导致渲染异常
            safe_text = html.escape(self.full_text)
            # 使用 HTML 格式化显示，原文加粗，译文小一号
            self.setText(f"<b>{safe_text}</b><br><span style='font-size:9px; color:#555;'>{self.translation}</span>")
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
                self.full_text = text
                # Re-fetch translation for new text
                self.translation = api.translate_tag(text) 
                self.update_content()
                self.edited.emit(text)

    def set_active_by_filter(self, active):
        """外部过滤器强制控制"""
        if self.is_active != active:
            self.is_active = active
            self.update_style()

    def update_style(self):
        # 优先级: Error > Active/Inactive
        
        if self.is_error:
            border = "2px solid red"
        else:
            border = "1px solid #aaa"
        
        if self.is_active:
            bg = self.base_color
            fg = "#000"
            style = f"""
                background-color: {bg}; 
                color: {fg}; 
                border-radius: 4px; 
                border: {border};
                padding: 4px 8px;
            """
        else:
            # Disabled style
            style = f"""
                background-color: #eee; 
                color: #999; 
                border-radius: 4px; 
                border: 1px dashed #ccc;
                text-decoration: line-through;
                padding: 4px 8px;
            """
        self.setStyleSheet(style)
        
        # Update Tooltip based on state
        tooltip = f"Type: {self.category}\nTranslation: {self.translation}\nDouble-click to edit"
        if self.is_error and self.error_msg:
            tooltip = f"❌ 错误: {self.error_msg}\n{tooltip}"
        self.setToolTip(tooltip)

class FlowLayoutWidget(QWidget):
    """
    流式布局容器，用于放置 TagChip
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_container = QVBoxLayout(self)
        self.chips = []
        
        self.flow_frame = QFrame()
        self.flow_layout = FlowLayout() # Custom Layout defined below
        self.flow_frame.setLayout(self.flow_layout)
        
        self.layout_container.addWidget(self.flow_frame)
        self.layout_container.addStretch()

    def clear_chips(self):
        # Remove all widgets
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.chips = []

    def add_chip(self, text, translation, category):
        color = api.get_color_for_category(category)
        chip = TagChip(text, translation, category, color)
        self.flow_layout.addWidget(chip)
        self.chips.append(chip)
        return chip

# Standard Flow Layout for PySide (from Qt docs examples)
from PySide6.QtWidgets import QLayout, QSizePolicy
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

# --- Logic Models ---

class PromptItem:
    def __init__(self, name, raw_text, is_image=False, image_path=None):
        self.name = name
        self.raw_text = raw_text
        self.is_image = is_image
        self.image_path = image_path
        self.parsed_tags = [] # List of (text, category) tuples
        self.parse_tags()

    def parse_tags(self):
        # Simple CSV split, respecting brackets would be harder but let's assume standard CSV
        raw_tags = [t.strip() for t in self.raw_text.split(',') if t.strip()]
        self.parsed_tags = []
        for t in raw_tags:
            cat = api.classify_tag(t)
            self.parsed_tags.append({'text': t, 'category': cat, 'enabled': True})

# --- Main Window ---

class PromptConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 提示词转换与筛选工具 (Prompt Converter)")
        self.resize(1300, 800)

        self.items = [] # List of PromptItem
        self.current_item_index = -1
        self.category_filters = {} # {'CategoryName': True/False}

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        # === Left Column: Input & List ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        # 1. Input Tabs
        self.input_tabs = QTabWidget()
        
        # Tab 1: Text
        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("在此粘贴提示词，每行一组...")
        btn_import_text = QPushButton("导入文本 (Import Text)")
        btn_import_text.clicked.connect(self.import_from_text)
        text_layout.addWidget(self.text_input)
        text_layout.addWidget(btn_import_text)
        
        # Tab 2: Images
        img_tab = QWidget()
        img_layout = QVBoxLayout(img_tab)
        btn_sel_img = QPushButton("选择图片/文件夹 (Select Images)")
        btn_sel_img.clicked.connect(self.import_from_images)
        self.img_status_label = QLabel("未选择")
        img_layout.addWidget(btn_sel_img)
        img_layout.addWidget(self.img_status_label)
        img_layout.addStretch()
        
        self.input_tabs.addTab(text_tab, "文本导入")
        self.input_tabs.addTab(img_tab, "图片反推")
        
        # 2. Item List
        # Update layout to include Clear button header
        list_header_layout = QHBoxLayout()
        list_header_layout.addWidget(QLabel("待处理列表 (Items):"))
        
        btn_clear = QPushButton("清空列表")
        btn_clear.setFixedWidth(80)
        btn_clear.setStyleSheet("background-color: #ffcccc; color: #cc0000; border: 1px solid #ff9999; border-radius: 3px;")
        btn_clear.clicked.connect(self.clear_all_items)
        
        list_header_layout.addStretch()
        list_header_layout.addWidget(btn_clear)
        
        self.item_list_widget = QListWidget()
        self.item_list_widget.currentRowChanged.connect(self.load_item_details)
        # Context Menu for Delete
        self.item_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.item_list_widget.customContextMenuRequested.connect(self.show_item_context_menu)

        left_layout.addWidget(self.input_tabs, 1)
        left_layout.addLayout(list_header_layout)
        left_layout.addWidget(self.item_list_widget, 2)

        # === Middle Column: Editor & Visualizer ===
        mid_panel = QWidget()
        mid_layout = QVBoxLayout(mid_panel)
        
        # Header Info & Translation Toggle
        header_layout = QHBoxLayout()
        self.lbl_current_info = QLabel("请选择左侧列表项")
        self.lbl_current_info.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(self.lbl_current_info)
        
        header_layout.addStretch()
        
        self.cb_translate = QCheckBox("显示翻译")
        self.cb_translate.stateChanged.connect(self.toggle_translation)
        header_layout.addWidget(self.cb_translate)
        
        # Warning Label (Brackets)
        self.lbl_warning = QLabel("")
        self.lbl_warning.setStyleSheet("color: red; font-weight: bold;")

        # Flow Editor (Main)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.flow_widget = FlowLayoutWidget()
        scroll.setWidget(self.flow_widget)
        
        # Diff Section (New)
        self.diff_group = QFrame()
        self.diff_group.setFrameStyle(QFrame.StyledPanel)
        diff_layout = QVBoxLayout(self.diff_group)
        diff_layout.addWidget(QLabel("与上一条目的差异 (Diff):"))
        
        diff_scroll = QScrollArea()
        diff_scroll.setWidgetResizable(True)
        # Removed maximum height to allow better resizing
        diff_scroll.setMinimumHeight(100) 
        self.diff_flow_widget = FlowLayoutWidget()
        diff_scroll.setWidget(self.diff_flow_widget)
        diff_layout.addWidget(diff_scroll)
        
        self.diff_group.setVisible(False) # Default hidden
        
        mid_layout.addLayout(header_layout) # Added Header with Translation toggle
        mid_layout.addWidget(self.lbl_warning)
        
        # Use Splitter for resizable areas
        mid_splitter = QSplitter(Qt.Vertical)
        mid_splitter.addWidget(scroll)
        mid_splitter.addWidget(self.diff_group)
        mid_splitter.setStretchFactor(0, 3) # Editor takes 3 parts
        mid_splitter.setStretchFactor(1, 1) # Diff takes 1 part
        
        mid_layout.addWidget(mid_splitter)

        # === Right Column: Filters & Export ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Filter Group
        filter_group = QFrame()
        filter_group.setFrameStyle(QFrame.StyledPanel)
        f_layout = QVBoxLayout(filter_group)
        f_layout.addWidget(QLabel("分类筛选 (Filters)"))
        self.filter_container = QWidget()
        self.filter_layout = QVBoxLayout(self.filter_container) # Checkboxes go here
        f_layout.addWidget(self.filter_container)
        f_layout.addStretch()
        
        # Export Group
        export_group = QFrame()
        export_group.setFrameStyle(QFrame.StyledPanel)
        e_layout = QVBoxLayout(export_group)
        e_layout.addWidget(QLabel("导出设置 (Export)"))
        
        self.export_path_input = QLineEdit()
        self.export_path_input.setPlaceholderText("选择导出目录...")
        btn_sel_export = QPushButton("...")
        btn_sel_export.clicked.connect(self.select_export_dir)
        
        h_path = QHBoxLayout()
        h_path.addWidget(self.export_path_input)
        h_path.addWidget(btn_sel_export)
        
        btn_export = QPushButton("生成动作节点 (Generate Nodes)")
        btn_export.setStyleSheet("background-color: #4CAF50; color: white; padding: 10px;")
        btn_export.clicked.connect(self.export_nodes)
        
        e_layout.addLayout(h_path)
        e_layout.addWidget(btn_export)

        right_layout.addWidget(filter_group, 2)
        right_layout.addWidget(export_group, 0)

        # Layout Assembly
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(mid_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 600, 250])
        
        layout.addWidget(splitter)

    # --- Helper Functions ---
    
    def generate_unique_name(self, base_name):
        """Generates a unique name by appending _1, _2, etc. if needed."""
        existing_names = {item.name for item in self.items}
        if base_name not in existing_names:
            return base_name
        
        counter = 1
        while True:
            candidate = f"{base_name}_{counter}"
            if candidate not in existing_names:
                return candidate
            counter += 1

    # --- Import Logic ---

    def import_from_text(self):
        text = self.text_input.toPlainText()
        if not text: return
        
        lines = text.strip().split('\n')
        
        for line in lines:
            if not line.strip(): continue
            # Use unique naming logic
            name = self.generate_unique_name("Text_Item")
            item = PromptItem(name, line)
            self.items.append(item)
            
            # Add to UI
            # We add a custom widget item or just text
            # For simplicity, using text, but the row index corresponds to self.items list index
            self.item_list_widget.addItem(name)
        
        self.update_filters()
        QMessageBox.information(self, "导入完成", f"已添加 {len(lines)} 条文本数据")

    def import_from_images(self):
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not folder: return
        
        files = [f for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if not files: return
        
        # In real app, run this in thread
        self.img_status_label.setText("正在分析图片...")
        QApplication.processEvents()
        
        for f in files:
            path = os.path.join(folder, f)
            # Call Mock API
            prompt = api.image_to_prompt(path)
            
            # Use filename (without extension) as base name for unique naming
            base_name = os.path.splitext(f)[0]
            unique_name = self.generate_unique_name(base_name)
            
            item = PromptItem(unique_name, prompt, is_image=True, image_path=path)
            self.items.append(item)
            self.item_list_widget.addItem(f"{unique_name} [IMG]")
        
        self.img_status_label.setText(f"已导入 {len(files)} 张图片")
        self.update_filters()

    # --- Context Menu Logic ---

    def show_item_context_menu(self, pos):
        item = self.item_list_widget.itemAt(pos)
        if not item: return
        
        menu = QMenu()
        del_act = QAction("删除 (Delete)", self)
        del_act.triggered.connect(self.delete_selected_item)
        menu.addAction(del_act)
        menu.exec(self.item_list_widget.mapToGlobal(pos))

    def delete_selected_item(self):
        row = self.item_list_widget.currentRow()
        if row < 0: return
        
        # Remove from data
        self.items.pop(row)
        # Remove from UI
        self.item_list_widget.takeItem(row)
        
        # Reset view if list empty or selection changed
        if not self.items:
            self.clear_ui_state()
        else:
            # If we deleted the current item, logic handles refreshing to next item or none
            # If row >= len(self.items), selection might be cleared or moved to last
            pass
        
        self.update_filters()

    def clear_all_items(self):
        if not self.items: return
        
        confirm = QMessageBox.question(self, "确认", "确定要清空所有待处理项吗?", QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            self.items.clear()
            self.item_list_widget.clear()
            self.clear_ui_state()
            self.update_filters()

    def clear_ui_state(self):
        self.current_item_index = -1
        self.flow_widget.clear_chips()
        self.diff_group.setVisible(False) # Also hide diff
        self.lbl_current_info.setText("请选择左侧列表项")
        self.lbl_warning.setText("")

    # --- Display Logic ---
    
    def toggle_translation(self, state):
        """Toggle translation visibility on all chips."""
        show = (state == Qt.Checked)
        
        # 1. Update all chips content
        for chip in self.flow_widget.chips:
            chip.set_translation_mode(show)
        for chip in self.diff_flow_widget.chips:
            chip.set_translation_mode(show)
            
        # 2. Force layout recalculation
        # TagChips changed size (setText called inside set_translation_mode triggers updateGeometry on chip)
        # We need to tell the container widget to recalculate its layout hint for the ScrollArea
        self.flow_widget.updateGeometry()
        self.diff_flow_widget.updateGeometry()
        
        # 3. Manually trigger layout activation to resize immediately
        if self.flow_widget.layout():
            self.flow_widget.layout().activate()
            self.flow_widget.layout().update()
            
        if self.diff_flow_widget.layout():
            self.diff_flow_widget.layout().activate()
            self.diff_flow_widget.layout().update()

    def load_item_details(self, row):
        if row < 0 or row >= len(self.items): return
        
        self.current_item_index = row
        item = self.items[row]
        
        self.lbl_current_info.setText(f"当前编辑: {item.name}")
        self.flow_widget.clear_chips()
        
        # Create Chips
        for tag_data in item.parsed_tags:
            trans = api.translate_tag(tag_data['text'])
            chip = self.flow_widget.add_chip(tag_data['text'], trans, tag_data['category'])
            chip.set_translation_mode(self.cb_translate.isChecked())
            
            # Restore state
            is_enabled = tag_data['enabled']
            if not self.category_filters.get(tag_data['category'], True):
                is_enabled = False 
            
            chip.is_active = is_enabled
            chip.update_style()
            
            # Connect signals using closures
            chip.toggled.connect(lambda active, td=tag_data: self.on_chip_toggled(td, active))
            chip.edited.connect(lambda text, td=tag_data, c=chip: self.on_chip_edited(td, text, c))

        # Perform check AFTER chips are populated
        self.check_global_brackets(item)
        
        # Update Diff View
        self.update_diff_view(item)

    def update_diff_view(self, item):
        """Calculates and displays diff between current item and previous item in the list."""
        row = self.items.index(item)
        
        if row <= 0:
            self.diff_group.setVisible(False)
            return
            
        self.diff_group.setVisible(True)
        self.diff_flow_widget.clear_chips()
        
        prev_item = self.items[row-1]
        
        # Get set of text for comparison
        curr_tags = {t['text'] for t in item.parsed_tags}
        prev_tags = {t['text'] for t in prev_item.parsed_tags}
        
        added = curr_tags - prev_tags
        removed = prev_tags - curr_tags
        
        if not added and not removed:
             # Can show "No Difference" or hide. Let's just keep it visible but empty or with label.
             # Or hide it to save space if identical.
             # Let's add a placeholder chip
             pass

        # Show Removed first (Red)
        for tag in sorted(list(removed)):
            trans = api.translate_tag(tag)
            chip = self.diff_flow_widget.add_chip(f"- {tag}", trans, "Removed")
            chip.set_translation_mode(self.cb_translate.isChecked())
            chip.base_color = "#ffcccc" # Red
            chip.update_style()
            chip.setToolTip("In previous, not in current")
            # TagChips are interactive by default, clicking them in diff view doesn't change data model
            # so it's fine.

        # Show Added (Green)
        for tag in sorted(list(added)):
            trans = api.translate_tag(tag)
            chip = self.diff_flow_widget.add_chip(f"+ {tag}", trans, "Added")
            chip.set_translation_mode(self.cb_translate.isChecked())
            chip.base_color = "#ccffcc" # Green
            chip.update_style()
            chip.setToolTip("In current, not in previous")

    def on_chip_toggled(self, tag_data, active):
        """Handle chip active state toggling."""
        tag_data['enabled'] = active
        # Re-check brackets because a bracket might have been disabled
        if self.current_item_index >= 0:
            self.check_global_brackets(self.items[self.current_item_index])

    def on_chip_edited(self, tag_data, new_text, chip_widget):
        """Handle chip text editing."""
        tag_data['text'] = new_text
        
        # Re-classify category based on new text
        new_cat = api.classify_tag(new_text)
        tag_data['category'] = new_cat
        
        # Update UI: Color and Tooltip
        chip_widget.category = new_cat
        chip_widget.base_color = api.get_color_for_category(new_cat)
        chip_widget.update_style()
        
        # Re-check brackets
        if self.current_item_index >= 0:
            item = self.items[self.current_item_index]
            self.check_global_brackets(item)
            # Update diff as text changed
            self.update_diff_view(item)
        
        # Need to refresh filters list potentially if new category appeared
        self.update_filters()

    def check_global_brackets(self, item):
        """
        Stack-based bracket checking across all enabled tags.
        Highlights specific TagChips that cause errors.
        """
        # 1. Reset all errors first
        for chip in self.flow_widget.chips:
            chip.set_error_state(False)
        
        stack = [] # Stores tuple: (bracket_char, chip_index)
        pairs = {')': '(', ']': '[', '}': '{'}
        
        # Map enabled tags to their chips index
        # flow_widget.chips array corresponds 1-to-1 with item.parsed_tags
        
        first_error_msg = ""
        
        for i, tag_data in enumerate(item.parsed_tags):
            if not tag_data['enabled']: continue
            
            text = tag_data['text']
            for char in text:
                if char in "([{":
                    stack.append((char, i))
                elif char in ")]}":
                    if not stack:
                        # Case A: Unexpected closing bracket
                        self.flow_widget.chips[i].set_error_state(True, f"多余的右括号 '{char}'")
                        if not first_error_msg: first_error_msg = f"发现多余的 '{char}'"
                        # We don't break here, we try to find more errors or just mark this one
                    else:
                        top_char, top_idx = stack[-1]
                        if pairs[char] == top_char:
                            # Match found, pop
                            stack.pop()
                        else:
                            # Case B: Mismatch (e.g. expected ) got ] )
                            # Mark CURRENT chip (closing bracket)
                            self.flow_widget.chips[i].set_error_state(True, f"不匹配: 期望 '{top_char}' 的闭合，但发现了 '{char}'")
                            
                            # Mark OPENING chip (where the unmatched open bracket was)
                            self.flow_widget.chips[top_idx].set_error_state(True, f"这里的 '{top_char}' 未正确闭合")
                            
                            if not first_error_msg: first_error_msg = f"符号不匹配: '{top_char}' vs '{char}'"
                            
                            # Assume typo and pop to continue checking? 
                            # Or assume missing bracket?
                            # Let's pop to prevent cascading errors from one typo
                            stack.pop()

        # Case C: Unclosed brackets remaining in stack
        if stack:
            unique_indices = set(idx for _, idx in stack)
            for idx in unique_indices:
                char = item.parsed_tags[idx]['text'] # rough approximation or logic
                # Better: get the specific char from stack but for UI simple error is enough
                self.flow_widget.chips[idx].set_error_state(True, "存在未闭合的左括号")
            
            if not first_error_msg:
                first_error_msg = f"存在 {len(stack)} 个未闭合的左括号"

        if first_error_msg:
            self.lbl_warning.setText(f"⚠️ {first_error_msg}")
        else:
            self.lbl_warning.setText("")

    def update_tag_state(self, tag_data, active):
        # Legacy/Simple method, now replaced by on_chip_toggled for more logic
        tag_data['enabled'] = active

    def update_filters(self):
        """Rebuild filter checkboxes based on all categories found in items."""
        found_cats = set()
        for item in self.items:
            for tag in item.parsed_tags:
                found_cats.add(tag['category'])
        
        # Clear old widgets
        while self.filter_layout.count():
            w = self.filter_layout.takeAt(0).widget()
            if w: w.deleteLater()
            
        # Add new widgets
        for cat in sorted(list(found_cats)):
            cb = QCheckBox(cat)
            # Restore checked state or default True
            cb.setChecked(self.category_filters.get(cat, True))
            # Set color indicator
            color = api.get_color_for_category(cat)
            cb.setStyleSheet(f"QCheckBox {{ background-color: {color}; padding: 3px; border-radius: 3px; }}")
            
            cb.stateChanged.connect(lambda state, c=cat: self.on_filter_change(c, state))
            self.filter_layout.addWidget(cb)
            
            # Init dict if new
            if cat not in self.category_filters:
                self.category_filters[cat] = True

    def on_filter_change(self, category, state):
        is_checked = (state == Qt.Checked)
        self.category_filters[category] = is_checked
        
        # Update current view immediately
        if self.current_item_index >= 0:
            # Refresh all chips in current view
            for chip in self.flow_widget.chips:
                if chip.category == category:
                    chip.set_active_by_filter(is_checked)
            
            # Re-check brackets after filter change (hidden tags don't count?)
            # Usually filters are just for organizing, but if they disable export,
            # they should probably also remove from bracket check?
            # Let's assume filter = disable for export AND check.
            self.check_global_brackets(self.items[self.current_item_index])
            
        # Also need to update the data model for ALL items (Batch operation)
        for item in self.items:
            for tag in item.parsed_tags:
                if tag['category'] == category:
                    tag['enabled'] = is_checked
        
    # --- Export Logic ---

    def select_export_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if d: self.export_path_input.setText(d)

    def export_nodes(self):
        target_dir = self.export_path_input.text()
        if not target_dir or not os.path.exists(target_dir):
            QMessageBox.warning(self, "错误", "请选择有效的导出目录")
            return

        count = 0
        # Timestamp based naming
        timestamp = datetime.now().strftime("%Y%m%d%H%M")
        
        # Find start index if we want to continue sequence or start from 1
        # Reuquest says: StartupTime_Counter.
        # This implies Counter starts from 1 for this export batch.
        current_idx = 1

        for item in self.items:
            # Build valid tags
            valid_tags = [t['text'] for t in item.parsed_tags if t['enabled']]
            if not valid_tags: continue
            
            prompt_content = ", ".join(valid_tags)
            
            # Folder Name: YYYYMMDDHHMM_Counter
            folder_name = f"{timestamp}_{current_idx}"
            folder_path = os.path.join(target_dir, folder_name)
            
            try:
                os.makedirs(folder_path, exist_ok=True)
                
                # Write tags.txt
                with open(os.path.join(folder_path, "tags.txt"), 'w', encoding='utf-8') as f:
                    f.write(prompt_content)
                
                # Copy Reference Image if available
                if item.is_image and item.image_path and os.path.exists(item.image_path):
                    # Destination image name
                    # We name it 'tmp.png' (or jpg) to act as the reference image for the manager
                    ext = os.path.splitext(item.image_path)[1]
                    shutil.copy2(item.image_path, os.path.join(folder_path, f"tmp{ext}"))
                
                count += 1
                current_idx += 1
            except Exception as e:
                print(f"Failed to export {folder_name}: {e}")

        QMessageBox.information(self, "导出完成", f"成功生成 {count} 个动作节点到:\n{target_dir}")

import shutil

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PromptConverterApp()
    window.show()
    sys.exit(app.exec())