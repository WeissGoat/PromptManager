import sys
import os
import random
import re
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLabel, QPushButton, 
                               QListWidget, QListWidgetItem, QFileDialog, 
                               QScrollArea, QFrame, QCheckBox, QSplitter, 
                               QTabWidget, QProgressBar, QMessageBox, QLineEdit, 
                               QGridLayout, QStyle, QInputDialog)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QColor, QPalette, QFont

# --- Mock Interfaces (模拟接口) ---

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
        if clean_tag in self.known_categories:
            cat = self.known_categories[clean_tag]
        else:
            # 2. 模拟：随机分配一个类型用于演示
            # 实际中你会调用你的分类模型
            cats = ["Attribute", "Object", "Effect", "Unknown", "Artist"]
            # 为了演示一致性，根据字符长度hash一下
            cat = cats[len(clean_tag) % len(cats)]
        
        return cat

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
    """
    toggled = Signal(bool) # 状态改变信号
    edited = Signal(str)   # 文本修改信号

    def __init__(self, text, category, color, parent=None):
        super().__init__(text, parent)
        self.full_text = text
        self.category = category
        self.base_color = color
        self.is_active = True
        
        self.setFont(QFont("Arial", 10))
        self.setMargin(5)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        
        # 移除了单个Tag的括号检测，改为依赖全局检测
        # self.has_error = not self.check_brackets(text) 
        
        self.update_style()

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
                self.setText(text)
                self.edited.emit(text)

    def set_active_by_filter(self, active):
        """外部过滤器强制控制"""
        if self.is_active != active:
            self.is_active = active
            self.update_style()
            # 注意：这里我们不发送 toggled 信号以避免递归循环，
            # 或者是让父级逻辑处理数据同步

    def update_style(self):
        # 移除了红色边框警告
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
        # Tooltip shows category
        self.setToolTip(f"Type: {self.category}\nDouble-click to edit")

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

    def add_chip(self, text, category):
        color = api.get_color_for_category(category)
        chip = TagChip(text, category, color)
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
        self.item_list_widget = QListWidget()
        self.item_list_widget.currentRowChanged.connect(self.load_item_details)

        left_layout.addWidget(self.input_tabs, 1)
        left_layout.addWidget(QLabel("待处理列表 (Items):"))
        left_layout.addWidget(self.item_list_widget, 2)

        # === Middle Column: Editor & Visualizer ===
        mid_panel = QWidget()
        mid_layout = QVBoxLayout(mid_panel)
        
        # Header Info
        self.lbl_current_info = QLabel("请选择左侧列表项")
        self.lbl_current_info.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        # Warning Label (Brackets)
        self.lbl_warning = QLabel("")
        self.lbl_warning.setStyleSheet("color: red; font-weight: bold;")

        # Flow Editor
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.flow_widget = FlowLayoutWidget()
        scroll.setWidget(self.flow_widget)
        
        mid_layout.addWidget(self.lbl_current_info)
        mid_layout.addWidget(self.lbl_warning)
        mid_layout.addWidget(scroll)

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

    # --- Import Logic ---

    def import_from_text(self):
        text = self.text_input.toPlainText()
        if not text: return
        
        lines = text.strip().split('\n')
        count = len(self.items) + 1
        
        for i, line in enumerate(lines):
            if not line.strip(): continue
            name = f"Text_Item_{count + i}"
            item = PromptItem(name, line)
            self.items.append(item)
            self.item_list_widget.addItem(f"{count+i}. {name}")
        
        self.update_filters()
        QMessageBox.information(self, "导入完成", f"已添加 {len(lines)} 条文本数据")

    def import_from_images(self):
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not folder: return
        
        files = [f for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if not files: return
        
        count = len(self.items) + 1
        # In real app, run this in thread
        self.img_status_label.setText("正在分析图片...")
        QApplication.processEvents()
        
        for i, f in enumerate(files):
            path = os.path.join(folder, f)
            # Call Mock API
            prompt = api.image_to_prompt(path)
            item = PromptItem(f, prompt, is_image=True, image_path=path)
            self.items.append(item)
            self.item_list_widget.addItem(f"{count+i}. {f} [IMG]")
        
        self.img_status_label.setText(f"已导入 {len(files)} 张图片")
        self.update_filters()

    # --- Display Logic ---

    def load_item_details(self, row):
        if row < 0 or row >= len(self.items): return
        
        self.current_item_index = row
        item = self.items[row]
        
        self.lbl_current_info.setText(f"当前编辑: {item.name}")
        self.flow_widget.clear_chips()
        
        # Check global brackets initially
        self.check_global_brackets(item)

        # Create Chips
        for tag_data in item.parsed_tags:
            chip = self.flow_widget.add_chip(tag_data['text'], tag_data['category'])
            
            # Restore state
            is_enabled = tag_data['enabled']
            if not self.category_filters.get(tag_data['category'], True):
                is_enabled = False 
            
            chip.is_active = is_enabled
            chip.update_style()
            
            # Connect signals using closures
            chip.toggled.connect(lambda active, td=tag_data: self.on_chip_toggled(td, active))
            chip.edited.connect(lambda text, td=tag_data, c=chip: self.on_chip_edited(td, text, c))

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
            self.check_global_brackets(self.items[self.current_item_index])
        
        # Need to refresh filters list potentially if new category appeared
        self.update_filters()

    def check_global_brackets(self, item):
        """
        Check brackets across ALL ENABLED tags for the item.
        Ignores disabled tags.
        """
        full_text = ""
        for tag in item.parsed_tags:
            if tag['enabled']:
                full_text += tag['text'] + " "
        
        open_b = full_text.count('(')
        close_b = full_text.count(')')
        open_sq = full_text.count('[')
        close_sq = full_text.count(']')
        open_cr = full_text.count('{')
        close_cr = full_text.count('}')
        
        warnings = []
        if open_b != close_b:
            warnings.append(f"()不匹配: {open_b} vs {close_b}")
        if open_sq != close_sq:
            warnings.append(f"[]不匹配: {open_sq} vs {close_sq}")
        if open_cr != close_cr:
            warnings.append(f"{{}}不匹配: {open_cr} vs {close_cr}")
            
        if warnings:
            self.lbl_warning.setText("⚠️ " + "  ".join(warnings))
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
        existing_folders = [f for f in os.listdir(target_dir) if os.path.isdir(os.path.join(target_dir, f))]
        # Find max index to continue numbering
        max_idx = 0
        for f in existing_folders:
            match = re.match(r'^(\d+)_', f)
            if match:
                idx = int(match.group(1))
                if idx > max_idx: max_idx = idx
        
        current_idx = max_idx + 1

        for item in self.items:
            # Build valid tags
            valid_tags = [t['text'] for t in item.parsed_tags if t['enabled']]
            if not valid_tags: continue
            
            prompt_content = ", ".join(valid_tags)
            
            # Folder Name
            safe_name = re.sub(r'[\\/:*?"<>|]', '', item.name)[:30] # Limit length
            folder_name = f"{current_idx}_{safe_name}"
            folder_path = os.path.join(target_dir, folder_name)
            
            try:
                os.makedirs(folder_path, exist_ok=True)
                
                # Write tags.txt
                with open(os.path.join(folder_path, "tags.txt"), 'w', encoding='utf-8') as f:
                    f.write(prompt_content)
                
                # Copy Reference Image if available
                if item.is_image and item.image_path and os.path.exists(item.image_path):
                    # Destination image name, let's keep it simple "ref.png" or original
                    ext = os.path.splitext(item.image_path)[1]
                    shutil.copy2(item.image_path, os.path.join(folder_path, f"ref{ext}"))
                
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