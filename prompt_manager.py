import os
import sys
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, Menu
from PIL import Image, ImageTk
import time

# ================= 配置区域 =================
# 请将此处改为你的真实库目录路径
ROOT_DIR = r"D:\AI\design\动作改2" 
# 支持的图片格式
IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
# ===========================================

class LnkResolver:
    """ 
    专门处理 .lnk 的工具类 
    由于 Python 原生不支持 lnk，这里调用 PowerShell，
    但为了防止卡顿，通常建议只在后台线程调用。
    """
    @staticmethod
    def resolve(path):
        if not path.lower().endswith('.lnk'):
            return path
        try:
            cmd = f'(New-Object -COM WScript.Shell).CreateShortcut("{path}").TargetPath'
            # creationflags=0x08000000 (CREATE_NO_WINDOW) 防止弹出黑框
            result = subprocess.run(
                ["powershell", "-Command", cmd], 
                capture_output=True, 
                text=True, 
                creationflags=0x08000000
            )
            target = result.stdout.strip()
            if target and os.path.exists(target):
                return target
        except Exception as e:
            print(f"LNK 解析失败: {path} -> {e}")
        return path

class PromptManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PromptManager Pro v2.0")
        self.root.geometry("1200x800")
        
        # 样式与配色
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.bg_color = "#1e1e2e"
        self.fg_color = "#cdd6f4"
        self.accent_color = "#89b4fa"
        self.list_bg = "#313244"
        self.root.configure(bg=self.bg_color)
        self.configure_styles()

        # 状态变量
        self.current_scene = None
        self.current_node = None
        self.is_composer_mode = False
        self.composer_selected_nodes = []
        
        # 记忆系统: 记录每个场景上次选中的节点名（去除序号后的纯名）
        # 格式: { "场景A路径": "站立回眸", ... }
        self.scene_selection_history = {} 

        # 图片浏览相关
        self.folder_playlist = [] # [{'name': 'Root', 'path': '...'}, {'name': '2023-10-01', 'path': '...'}]
        self.current_folder_index = 0
        self.current_images = [] # 当前文件夹下的图片路径列表
        self.current_image_index = 0
        self.loading_thread = None

        # 布局
        self.create_layout()
        self.create_context_menus()
        
        # 初始化
        self.check_root_dir()
        self.load_scenes()

    def configure_styles(self):
        self.style.configure("TFrame", background=self.bg_color)
        self.style.configure("TLabel", background=self.bg_color, foreground=self.fg_color, font=("Segoe UI", 10))
        self.style.configure("TButton", background="#45475a", foreground=self.fg_color, borderwidth=1)
        self.style.map("TButton", background=[("active", "#585b70")])
        self.style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"), foreground=self.accent_color)

    def check_root_dir(self):
        if not os.path.exists(ROOT_DIR):
            try:
                os.makedirs(ROOT_DIR)
            except:
                pass

    def create_layout(self):
        # ... (布局代码保持大部分不变，微调细节) ...
        main_container = tk.Frame(self.root, bg=self.bg_color)
        main_container.pack(fill=tk.BOTH, expand=True)

        # 1. 左侧：场景列表
        self.left_panel = tk.Frame(main_container, bg=self.bg_color, width=250)
        self.left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        self.left_panel.pack_propagate(False)
        ttk.Label(self.left_panel, text="目录树 (Scenes)", style="Header.TLabel").pack(anchor="w", pady=5)
        self.scene_listbox = tk.Listbox(self.left_panel, bg=self.list_bg, fg=self.fg_color, 
                                        selectbackground=self.accent_color, selectforeground="#1e1e2e", 
                                        borderwidth=0, font=("Segoe UI", 11))
        self.scene_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        self.scene_listbox.bind("<<ListboxSelect>>", self.on_scene_select)
        ttk.Button(self.left_panel, text="+ 新增场景", command=self.create_new_scene).pack(fill=tk.X, pady=5)
        self.composer_btn = tk.Button(self.left_panel, text="切换组合模式", bg="#313244", fg="#a6e3a1", 
                                      command=self.toggle_composer_mode, relief="flat")
        self.composer_btn.pack(fill=tk.X)

        # 2. 中间：动作节点
        self.mid_panel = tk.Frame(main_container, bg=self.bg_color, width=300)
        self.mid_panel.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        self.mid_panel.pack_propagate(False)
        mid_header = tk.Frame(self.mid_panel, bg=self.bg_color)
        mid_header.pack(fill=tk.X)
        self.node_header_label = ttk.Label(mid_header, text="动作节点", style="Header.TLabel")
        self.node_header_label.pack(side=tk.LEFT, pady=5)
        sort_frame = tk.Frame(mid_header, bg=self.bg_color)
        sort_frame.pack(side=tk.RIGHT)
        ttk.Button(sort_frame, text="↑", width=3, command=lambda: self.move_node(-1)).pack(side=tk.LEFT, padx=1)
        ttk.Button(sort_frame, text="↓", width=3, command=lambda: self.move_node(1)).pack(side=tk.LEFT, padx=1)
        self.node_listbox = tk.Listbox(self.mid_panel, bg=self.list_bg, fg=self.fg_color,
                                       selectbackground=self.accent_color, selectforeground="#1e1e2e",
                                       borderwidth=0, font=("Segoe UI", 11))
        self.node_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        self.node_listbox.bind("<<ListboxSelect>>", self.on_node_select)
        ttk.Button(self.mid_panel, text="+ 新增动作节点", command=self.add_node).pack(fill=tk.X, pady=5)

        # 3. 右侧：编辑与预览
        self.right_panel = tk.Frame(main_container, bg=self.bg_color)
        self.right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 图片预览区 (支持滚轮和左右键)
        self.preview_frame = tk.Frame(self.right_panel, bg="#11111b", height=400)
        self.preview_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 5))
        
        self.image_label = tk.Label(self.preview_frame, bg="#11111b", text="无预览图")
        self.image_label.pack(expand=True, fill=tk.BOTH)
        
        # 绑定事件
        self.image_label.bind("<MouseWheel>", self.on_mouse_wheel) # Windows 滚轮
        self.image_label.bind("<Button-4>", lambda e: self.change_preview_image(-1)) # Linux 滚轮上
        self.image_label.bind("<Button-5>", lambda e: self.change_preview_image(1))  # Linux 滚轮下
        
        # 文件夹切换 (左键下一个，右键上一个)
        self.image_label.bind("<Button-1>", lambda e: self.change_folder(1))
        self.image_label.bind("<Button-3>", lambda e: self.change_folder(-1))

        # 浮层信息 (左上角)
        self.info_overlay = tk.Label(self.preview_frame, bg="#11111b", fg="#fab387", text="", justify=tk.LEFT, font=("Segoe UI", 9))
        self.info_overlay.place(x=10, y=10)
        
        # 计数浮层 (右下角)
        self.count_overlay = tk.Label(self.preview_frame, bg="#1e1e2e", fg="white", text="", font=("Arial", 8))
        self.count_overlay.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-10)

        # 文本编辑区
        editor_frame = tk.Frame(self.right_panel, bg=self.bg_color, height=300)
        editor_frame.pack(side=tk.BOTTOM, fill=tk.X)
        toolbar = tk.Frame(editor_frame, bg=self.bg_color)
        toolbar.pack(fill=tk.X, pady=2)
        ttk.Label(toolbar, text="tags.txt").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="保存 tags", command=self.save_tags).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="复制全部", command=self.copy_tags).pack(side=tk.RIGHT, padx=5)
        self.text_editor = tk.Text(editor_frame, bg="#313244", fg=self.fg_color, font=("Consolas", 10),
                                   insertbackground="white", height=10, borderwidth=0)
        self.text_editor.pack(fill=tk.BOTH, expand=True)

    def create_context_menus(self):
        # 场景列表右键菜单
        self.scene_menu = Menu(self.root, tearoff=0)
        self.scene_menu.add_command(label="📂 在资源管理器打开", command=self.open_scene_in_explorer)
        self.scene_listbox.bind("<Button-3>", self.show_scene_menu)

        # 动作节点右键菜单
        self.node_menu = Menu(self.root, tearoff=0)
        self.node_menu.add_command(label="📂 在资源管理器打开", command=self.open_node_in_explorer)
        self.node_listbox.bind("<Button-3>", self.show_node_menu)

    # ================= 右键菜单逻辑 =================
    
    def show_scene_menu(self, event):
        # 先选中鼠标指向的项
        idx = self.scene_listbox.nearest(event.y)
        self.scene_listbox.selection_clear(0, tk.END)
        self.scene_listbox.selection_set(idx)
        self.scene_listbox.event_generate("<<ListboxSelect>>")
        self.scene_menu.post(event.x_root, event.y_root)

    def show_node_menu(self, event):
        idx = self.node_listbox.nearest(event.y)
        self.node_listbox.selection_clear(0, tk.END)
        self.node_listbox.selection_set(idx)
        self.node_listbox.event_generate("<<ListboxSelect>>")
        self.node_menu.post(event.x_root, event.y_root)

    def open_scene_in_explorer(self):
        if self.current_scene:
            os.startfile(self.current_scene)

    def open_node_in_explorer(self):
        # 这里需要解析一下，如果是 lnk，打开其指向的目标文件夹
        if self.current_node:
            real_path = LnkResolver.resolve(self.current_node)
            if os.path.isdir(real_path):
                os.startfile(real_path)
            else:
                os.startfile(os.path.dirname(real_path)) # 如果是文件，打开所在文件夹

    # ================= 核心逻辑 =================

    def load_scenes(self):
        self.scene_listbox.delete(0, tk.END)
        if not os.path.exists(ROOT_DIR): return
        items = sorted([d for d in os.listdir(ROOT_DIR) if os.path.isdir(os.path.join(ROOT_DIR, d))])
        for item in items:
            self.scene_listbox.insert(tk.END, item)

    def on_scene_select(self, event):
        selection = self.scene_listbox.curselection()
        if not selection: return
        scene_name = self.scene_listbox.get(selection[0])
        self.current_scene = os.path.join(ROOT_DIR, scene_name)
        
        self.load_nodes()
        
        # 记忆回溯逻辑: 选中上一次操作的节点
        last_selected_node_name = self.scene_selection_history.get(self.current_scene)
        target_index = 0 # 默认选中第一个
        
        if last_selected_node_name:
            # 遍历寻找匹配（因为序号可能变了，所以对比去序号后的名字）
            all_items = self.node_listbox.get(0, tk.END)
            for i, item_name in enumerate(all_items):
                _, clean_name = self.get_index_name(item_name)
                # 处理 link 后缀
                if clean_name.lower().endswith('.lnk'): clean_name = clean_name[:-4]
                if clean_name == last_selected_node_name:
                    target_index = i
                    break
        
        # 自动选中并触发事件
        if self.node_listbox.size() > 0:
            self.node_listbox.selection_clear(0, tk.END)
            self.node_listbox.selection_set(target_index)
            self.node_listbox.event_generate("<<ListboxSelect>>")

    def load_nodes(self):
        self.node_listbox.delete(0, tk.END)
        if not self.current_scene: return
        try:
            items = os.listdir(self.current_scene)
            nodes = []
            for item in items:
                full_path = os.path.join(self.current_scene, item)
                if os.path.isdir(full_path) or item.lower().endswith('.lnk'):
                    nodes.append(item)
            
            # 自然排序
            nodes.sort(key=lambda x: [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', x)])
            for node in nodes:
                self.node_listbox.insert(tk.END, node)
        except Exception as e:
            print(f"Error loading nodes: {e}")

    def on_node_select(self, event):
        selection = self.node_listbox.curselection()
        if not selection: return
        node_name = self.node_listbox.get(selection[0])
        node_path = os.path.join(self.current_scene, node_name)
        
        # 记录选择 (用于记忆)
        _, clean_name = self.get_index_name(node_name)
        if clean_name.lower().endswith('.lnk'): clean_name = clean_name[:-4]
        self.scene_selection_history[self.current_scene] = clean_name

        if self.is_composer_mode:
            self.handle_composer_select(selection[0], node_path)
            return

        self.current_node = node_path
        
        # 1. 立即加载文本 (快)
        self.load_text_content(node_path)
        
        # 2. 异步加载图片结构 (慢)
        self.start_folder_scanning_thread(node_path)

    def load_text_content(self, node_path):
        self.text_editor.delete(1.0, tk.END)
        self.text_editor.insert(tk.END, "加载中...")
        
        def _read():
            real_path = LnkResolver.resolve(node_path)
            tags_file = os.path.join(real_path, "tags.txt")
            content = ""
            if os.path.exists(tags_file):
                try:
                    with open(tags_file, "r", encoding="utf-8") as f:
                        content = f.read()
                except: content = "读取失败"
            else:
                content = "" # 空文件
            
            self.root.after(0, lambda: self.update_editor(content))
            
        threading.Thread(target=_read, daemon=True).start()

    def update_editor(self, content):
        self.text_editor.delete(1.0, tk.END)
        self.text_editor.insert(tk.END, content)

    # ================= 图片多文件夹加载逻辑 =================

    def start_folder_scanning_thread(self, node_path):
        # 重置图片区状态
        self.folder_playlist = [] 
        self.current_images = []
        self.current_folder_index = 0
        self.current_image_index = 0
        
        self.image_label.config(image="", text="正在扫描文件夹...")
        self.info_overlay.config(text="")
        self.count_overlay.config(text="")
        
        self.loading_thread = threading.Thread(target=self.scan_folders_worker, args=(node_path,))
        self.loading_thread.daemon = True
        self.loading_thread.start()

    def scan_folders_worker(self, node_path):
        """ 后台扫描: 找出所有包含图片的文件夹（包括Root和日期子文件夹） """
        real_path = LnkResolver.resolve(node_path)
        
        if not os.path.exists(real_path):
            self.root.after(0, lambda: self.image_label.config(text="路径不存在"))
            return

        # 1. 准备列表
        # 结构: {'name': 'Root', 'path': real_path}
        folders_found = []

        # A. 根目录 (总是存在，放在列表最前面，或者稍后根据是否有图决定)
        folders_found.append({'name': '(Root) 参考图', 'path': real_path, 'is_root': True})

        # B. 扫描子目录 (可能是日期文件夹)
        sub_items = []
        try:
            for item in os.listdir(real_path):
                full_path = os.path.join(real_path, item)
                # 检查是文件夹 或是 lnk
                is_dir = os.path.isdir(full_path)
                is_lnk = item.lower().endswith('.lnk')
                
                if is_dir or is_lnk:
                    # 如果是 lnk，需要解析看是否指向文件夹
                    target_path = full_path
                    if is_lnk:
                        target_path = LnkResolver.resolve(full_path)
                    
                    if os.path.isdir(target_path):
                        # 获取创建时间用于排序
                        try:
                            ctime = os.path.getctime(full_path) # 用原文件的时间，还是目标时间？通常用原文件(生成时间)
                        except: ctime = 0
                        sub_items.append({'name': item, 'path': target_path, 'time': ctime, 'is_root': False})
        except Exception as e:
            print(f"Scan error: {e}")

        # C. 排序子目录: 按时间倒序 (最新的在前)
        sub_items.sort(key=lambda x: x['time'], reverse=True)
        folders_found.extend(sub_items)

        self.folder_playlist = folders_found
        
        # 2. 决定初始显示的文件夹
        # 逻辑: 
        # 1. 检查 Root 是否有图
        # 2. 如果 Root 有图 -> 显示 Root
        # 3. 如果 Root 无图 -> 检查第一个子文件夹 (最新的) -> 显示它
        
        target_folder_index = 0 # 默认为 Root
        
        # 快速检查 Root 图片
        root_has_img = self.folder_has_images(folders_found[0]['path'])
        
        if not root_has_img and len(folders_found) > 1:
            # Root没图，且有子文件夹，尝试选中第一个子文件夹(最新的)
            target_folder_index = 1
        
        self.current_folder_index = target_folder_index
        
        # 3. 加载选中文件夹的图片
        self.load_images_for_current_folder()

    def folder_has_images(self, path):
        try:
            for f in os.listdir(path):
                if f.lower().endswith(IMG_EXTS):
                    return True
        except: pass
        return False

    def load_images_for_current_folder(self):
        """ 加载当前 current_folder_index 指向的文件夹内的图片列表 """
        if not self.folder_playlist:
            self.root.after(0, lambda: self.image_label.config(text="无图片目录"))
            return
            
        folder_data = self.folder_playlist[self.current_folder_index]
        path = folder_data['path']
        
        images = []
        try:
            images = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(IMG_EXTS)]
            images.sort() # 文件名排序
        except: pass
        
        self.current_images = images
        self.current_image_index = 0
        
        # 回主线程刷新UI
        self.root.after(0, self.show_current_image)

    def show_current_image(self):
        # 如果当前文件夹没图，显示提示
        folder_info = self.folder_playlist[self.current_folder_index]
        folder_name_display = folder_info['name']
        
        if not self.current_images:
            self.image_label.config(image="", text=f"文件夹为空:\n{folder_name_display}")
            self.info_overlay.config(text=f"📂 {folder_name_display}\n(无图片)")
            self.count_overlay.config(text="0/0")
            return

        # 边界检查
        if self.current_image_index < 0: self.current_image_index = 0
        if self.current_image_index >= len(self.current_images): self.current_image_index = len(self.current_images) - 1

        img_path = self.current_images[self.current_image_index]
        file_name = os.path.basename(img_path)
        
        # 加载图片 (PIL)
        try:
            pil_img = Image.open(img_path)
            # 适应窗口大小
            win_h = self.preview_frame.winfo_height() or 400
            win_w = self.preview_frame.winfo_width() or 500
            
            # 计算缩放
            ratio = min(win_w / pil_img.width, win_h / pil_img.height)
            if ratio < 1: # 只缩小不放大
                new_w = int(pil_img.width * ratio)
                new_h = int(pil_img.height * ratio)
                pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            self.tk_img = ImageTk.PhotoImage(pil_img)
            self.image_label.config(image=self.tk_img, text="")
            
            # 更新浮层信息
            idx_display = f"{self.current_image_index + 1}/{len(self.current_images)}"
            self.info_overlay.config(text=f"📂 {folder_name_display}\n📄 {file_name}")
            self.count_overlay.config(text=idx_display)
            
        except Exception as e:
            self.image_label.config(image="", text=f"无法加载: {file_name}")

    # ================= 交互控制 =================

    def on_mouse_wheel(self, event):
        # 滚轮切换图片
        if event.delta > 0:
            self.change_preview_image(-1) # 上一张
        else:
            self.change_preview_image(1) # 下一张

    def change_preview_image(self, offset):
        if not self.current_images: return
        new_idx = self.current_image_index + offset
        
        # 边界限制 (不循环，到底停止)
        if 0 <= new_idx < len(self.current_images):
            self.current_image_index = new_idx
            self.show_current_image()

    def change_folder(self, offset):
        """ 切换文件夹 (offset=1 下一个/旧的, offset=-1 上一个/新的) """
        if not self.folder_playlist: return
        
        new_idx = self.current_folder_index + offset
        
        # 循环切换文件夹？还是到底停止？这里做循环比较方便浏览
        if new_idx >= len(self.folder_playlist):
            new_idx = 0 # 循环到最新
        elif new_idx < 0:
            new_idx = len(self.folder_playlist) - 1 # 循环到最旧
            
        self.current_folder_index = new_idx
        # 需要重新加载该文件夹的图片列表
        threading.Thread(target=self.load_images_for_current_folder, daemon=True).start()

    # ================= 辅助功能 =================

    def get_index_name(self, filename):
        match = re.match(r'\((\d+)\)(.*)', filename)
        if match:
            return int(match.group(1)), match.group(2)
        return 9999, filename

    def save_tags(self):
        if not self.current_node: return
        content = self.text_editor.get(1.0, tk.END).strip()
        
        def _save():
            real_path = LnkResolver.resolve(self.current_node)
            tags_file = os.path.join(real_path, "tags.txt")
            try:
                with open(tags_file, "w", encoding="utf-8") as f:
                    f.write(content)
                self.root.after(0, lambda: messagebox.showinfo("成功", "Tags 已保存"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
        
        threading.Thread(target=_save).start()

    def copy_tags(self):
        content = self.text_editor.get(1.0, tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(content)

    def create_new_scene(self):
        name = simpledialog.askstring("新建场景", "请输入场景文件夹名称:")
        if name:
            path = os.path.join(ROOT_DIR, name)
            if not os.path.exists(path):
                os.makedirs(path)
                self.load_scenes()
            else:
                messagebox.showerror("错误", "场景已存在")

    def add_node(self):
        if not self.current_scene: return
        name = simpledialog.askstring("新增节点", "请输入动作名称 (无需序号):")
        if not name: return
        nodes = self.node_listbox.get(0, tk.END)
        max_idx = 0
        for node in nodes:
            idx, _ = self.get_index_name(node)
            if idx < 9999 and idx > max_idx: max_idx = idx
        new_folder_name = f"({max_idx + 1}){name}"
        try:
            os.makedirs(os.path.join(self.current_scene, new_folder_name))
            with open(os.path.join(self.current_scene, new_folder_name, "tags.txt"), 'w', encoding='utf-8') as f:
                f.write("")
            self.load_nodes()
        except Exception as e: messagebox.showerror("错误", str(e))

    def move_node(self, direction):
        if not self.current_scene: return
        selection = self.node_listbox.curselection()
        if not selection: return
        idx = selection[0]
        all_nodes = list(self.node_listbox.get(0, tk.END))
        if direction == -1 and idx == 0: return 
        if direction == 1 and idx == len(all_nodes) - 1: return
        target_idx = idx + direction
        all_nodes[idx], all_nodes[target_idx] = all_nodes[target_idx], all_nodes[idx]
        
        # 重命名逻辑
        try:
            temp_map = []
            for i, filename in enumerate(all_nodes):
                old_path = os.path.join(self.current_scene, filename)
                _, clean_name = self.get_index_name(filename)
                is_lnk = filename.lower().endswith('.lnk')
                if is_lnk: clean_name = clean_name.replace('.lnk', '').replace('.LNK', '')
                new_name = f"({i+1}){clean_name}"
                if is_lnk: new_name += ".lnk"
                temp_uuid = f"__temp_{i}_{int(time.time())}"
                temp_path = os.path.join(self.current_scene, temp_uuid)
                os.rename(old_path, temp_path)
                temp_map.append((temp_path, new_name))
            for temp_path, new_name in temp_map:
                final_path = os.path.join(self.current_scene, new_name)
                os.rename(temp_path, final_path)
            self.load_nodes()
            self.node_listbox.selection_set(target_idx)
            self.on_node_select(None)
        except Exception as e:
            messagebox.showerror("重命名失败", str(e))
            self.load_nodes()

    def toggle_composer_mode(self):
        self.is_composer_mode = not self.is_composer_mode
        if self.is_composer_mode:
            self.composer_btn.config(text="【组合模式开启】点击保存", bg="#a6e3a1", fg="#1e1e2e")
            self.node_header_label.config(text="选择节点以组合...")
            self.composer_selected_nodes = []
            new_scene = simpledialog.askstring("组合场景", "请输入新场景名称:")
            if not new_scene:
                self.toggle_composer_mode()
                return
            self.composer_target_scene = new_scene
        else:
            if not self.composer_selected_nodes:
                self.composer_btn.config(text="切换组合模式", bg="#313244", fg="#a6e3a1")
                self.node_header_label.config(text="动作节点")
                return
            target_dir = os.path.join(ROOT_DIR, self.composer_target_scene)
            if not os.path.exists(target_dir): os.makedirs(target_dir)
            for i, source_path in enumerate(self.composer_selected_nodes):
                real_source = LnkResolver.resolve(source_path)
                filename = os.path.basename(source_path)
                _, clean_name = self.get_index_name(filename)
                clean_name = clean_name.replace('.lnk', '').replace('.LNK', '')
                lnk_name = f"({i+1}){clean_name}.lnk"
                lnk_path = os.path.join(target_dir, lnk_name)
                try:
                    cmd = f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{lnk_path}");$s.TargetPath="{real_source}";$s.Save()'
                    subprocess.run(["powershell", "-Command", cmd], creationflags=0x08000000)
                except: pass
            messagebox.showinfo("完成", "场景创建成功")
            self.composer_btn.config(text="切换组合模式", bg="#313244", fg="#a6e3a1")
            self.node_header_label.config(text="动作节点")
            self.composer_selected_nodes = []
            self.load_scenes()

    def handle_composer_select(self, idx, node_path):
        if node_path in self.composer_selected_nodes:
            self.composer_selected_nodes.remove(node_path)
            self.node_listbox.itemconfig(idx, bg=self.list_bg)
        else:
            self.composer_selected_nodes.append(node_path)
            self.node_listbox.itemconfig(idx, bg="#45475a")

if __name__ == "__main__":
    root = tk.Tk()
    app = PromptManagerApp(root)
    root.mainloop()