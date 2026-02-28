from pathlib import Path
import os
import win32com.client

shell = win32com.client.Dispatch("WScript.Shell")
HAS_WIN32 = True

def resolve_path(path):
    """
    If path is a .lnk file, resolve to its target.
    Otherwise return path as is.
    """
    path_obj = Path(path)
    if path_obj.suffix.lower() == '.lnk' and shell:
        try:
            shortcut = shell.CreateShortcut(str(path_obj.resolve()))
            target = shortcut.TargetPath
            if os.path.exists(target):
                return target
        except Exception as e:
            print(f"Error resolving lnk {path}: {e}")
    return str(path)

def create_shortcut(target_path, link_path):
    """Creates a Windows shortcut (.lnk)."""
    if not shell:
        return False
    try:
        shortcut = shell.CreateShortcut(link_path)
        shortcut.TargetPath = target_path
        shortcut.Save()
        return True
    except Exception as e:
        print(f"Error creating shortcut: {e}")
        return False