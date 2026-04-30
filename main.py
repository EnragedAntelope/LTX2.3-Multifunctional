import os
import sys
import subprocess
import threading
import time
import socket
import logging
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# ============================================================
# 配置区 / Configuration (dynamic path resolution & patch mounting)
# ============================================================
def resolve_ltx_path():
    import glob, tempfile, subprocess
    sc_dir = os.path.join(os.getcwd(), "LTX_Shortcut")
    os.makedirs(sc_dir, exist_ok=True)
    lnk_files = glob.glob(os.path.join(sc_dir, "*.lnk"))
    if not lnk_files:
        print("\033[91m[ERROR] No shortcut found in LTX_Shortcut folder!")
        print("Please copy the official LTX Desktop shortcut into the LTX_Shortcut folder and try again.\033[0m")
        sys.exit(1)
        
    lnk_path = lnk_files[0]
    # Use VBScript to resolve shortcut target, compatible with all Windows versions
    safe_path = os.path.abspath(lnk_path).replace('"', '""')
    vbs_code = f'''Set sh = CreateObject("WScript.Shell")\nSet obj = sh.CreateShortcut("{safe_path}")\nWScript.Echo obj.TargetPath'''
    fd, vbs_path = tempfile.mkstemp(suffix='.vbs')
    with os.fdopen(fd, 'w') as f:
        f.write(vbs_code)
    try:
        out = subprocess.check_output(['cscript', '//nologo', vbs_path], stderr=subprocess.STDOUT)
        target_exe = out.decode('ansi').strip()
    finally:
        os.remove(vbs_path)
        
    if not target_exe or not os.path.exists(target_exe):
        # 如果快捷方式解析失败 / If shortcut resolution fails, search default install locations
        default_paths = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\LTX Desktop\LTX Desktop.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"LTX Desktop\LTX Desktop.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"LTX Desktop\LTX Desktop.exe"),
        ]
        found = False
        for p in default_paths:
            if os.path.exists(p):
                target_exe = p
                print(f"\033[96m[INFO] Auto-detected LTX Desktop install path: {p}\033[0m")
                found = True
                break
        
        if not found:
            print(f"\033[91m[ERROR] Could not find LTX Desktop installation!\033[0m")
            print("Please clear the LTX_Shortcut folder and copy your actual LTX Desktop shortcut into it.")
            sys.exit(1)
        
    return os.path.dirname(target_exe)

USER_PROFILE = os.path.expanduser("~")
PYTHON_EXE = os.path.join(USER_PROFILE, r"AppData\Local\LTXDesktop\python\python.exe")
DATA_DIR = os.path.join(USER_PROFILE, r"AppData\Local\LTXDesktop")

# 1. 动态获取主安装路径 / Dynamically resolve main install path
LTX_INSTALL_DIR = resolve_ltx_path()
BACKEND_DIR = os.path.join(LTX_INSTALL_DIR, r"resources\backend")
UI_FILE_NAME = "UI/index.html"

def _check_ltx_version(backend_dir):
    """Warn if the installed LTX Desktop backend version doesn't match what this wrapper supports."""
    EXPECTED_BACKEND_VERSION = "1.0.0"  # corresponds to LTX Desktop v1.0.5 (backend unchanged from v1.0.4)
    SUPPORTED_DESKTOP_VERSION = "v1.0.5"
    pyproject = os.path.join(backend_dir, "pyproject.toml")
    if not os.path.exists(pyproject):
        return
    try:
        try:
            import tomllib  # stdlib in Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                return  # can't parse toml, skip silently
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        version = data.get("project", {}).get("version", "")
        if version and version != EXPECTED_BACKEND_VERSION:
            print(f"\033[1;43m [VERSION WARNING] \033[0m")
            print(f"\033[93mDetected LTX Desktop backend version: {version}")
            print(f"This wrapper is tested against backend {EXPECTED_BACKEND_VERSION} (LTX Desktop {SUPPORTED_DESKTOP_VERSION}).")
            print(f"Download the correct release: https://github.com/Lightricks/LTX-Desktop/releases/tag/{SUPPORTED_DESKTOP_VERSION}")
            print(f"Continuing anyway — things may or may not work.\033[0m\n")
    except Exception:
        pass  # never crash on a version check

_check_ltx_version(BACKEND_DIR)

# 环境致命检测 / Fatal check: if official Python hasn't been extracted yet, abort
if not os.path.exists(PYTHON_EXE):
    print(f"\n\033[1;41m [FATAL] LTX Desktop rendering engine not found! \033[0m")
    print(f"\033[93mThis app is a UI frontend and requires the official LTX Desktop environment.")
    print(f"Engine not found at: {PYTHON_EXE}\n")
    print(">> Solution:")
    print("1. Install the official LTX Desktop software on your computer.")
    print("2. Run the official app at least once (it sets up the backend environment).")
    print("3. Copy the official app's shortcut into the LTX_Shortcut folder.")
    print("4. Then restart this run.bat script!\033[0m\n")
    os._exit(1)

# 2. 从目录读取改动过的 Python 文件 / Read patched Python files (hot-fix interceptor)
PATCHES_DIR = os.path.join(os.getcwd(), "patches")
os.makedirs(PATCHES_DIR, exist_ok=True)

# 3. 默认输出定向至程序根目录 / Default output to project root
LOCAL_OUTPUTS = os.path.join(os.getcwd(), "outputs")
os.makedirs(LOCAL_OUTPUTS, exist_ok=True)

# 强制注入自定义输出目录至 LTX 缓存 / Force-inject custom output dir into LTX data cache
os.makedirs(DATA_DIR, exist_ok=True)
with open(os.path.join(DATA_DIR, "custom_dir.txt"), 'w', encoding='utf-8') as f:
    f.write(LOCAL_OUTPUTS)

os.environ["LTX_APP_DATA_DIR"] = DATA_DIR

# 将 patches 目录优先级提升 / Elevate patches dir priority for seamless Python override
os.environ["PYTHONPATH"] = f"{PATCHES_DIR};{BACKEND_DIR}"

def get_lan_ip():
    try:
        host_name = socket.gethostname()
        _, _, ip_list = socket.gethostbyname_ex(host_name)
        
        candidates = []
        for ip in ip_list:
            if ip.startswith("192.168."):
                return ip
            elif ip.startswith("10.") or (ip.startswith("172.") and 16 <= int(ip.split('.')[1]) <= 31):
                candidates.append(ip)
                
        if candidates:
            return candidates[0]
            
        # Fallback to the default socket routing approach if no obvious LAN IP found
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

LAN_IP = get_lan_ip()

# ============================================================
# 服务启动逻辑 / Server launch logic
# ============================================================
def check_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def launch_backend():
    """启动核心引擎 / Start core rendering engine — listens on 0.0.0.0 for LAN access"""
    if check_port_in_use(3000):
        print(f"\n\033[1;41m [FATAL] Port 3000 is already in use! \033[0m")
        print("\033[93m>> Most likely the official LTX Desktop is running in the background.\033[0m")
        print(">> This will cause VRAM conflicts. Check system tray, right-click and quit the official app.")
        print(">> Then restart run.bat!\n")
        os._exit(1)

    print(f"\033[96m[CORE] Starting rendering engine...\033[0m")
    # 只开启重要级别日志 / Only enable important-level logs, suppress HTTP noise
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True
    )
    
    # 构建环境拦截器 / Build env interceptor to prevent cwd hijacking of original files
    launcher_code = f"""
import sys
import os

patch_dir = r"{PATCHES_DIR}"
backend_dir = r"{BACKEND_DIR}"

# 防御性清除：强行剥离所有的默认 backend_dir 引用
sys.path = [p for p in sys.path if p and os.path.normpath(p) != os.path.normpath(backend_dir)]
sys.path = [p for p in sys.path if p and p != "." and p != ""]

# 绝对插队注入：优先搜索 PATCHES_DIR
sys.path.insert(0, patch_dir)
sys.path.insert(1, backend_dir)

import uvicorn
from ltx2_server import app

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="info", access_log=False)
"""
    launcher_path = os.path.join(PATCHES_DIR, "launcher.py")
    with open(launcher_path, "w", encoding="utf-8") as f:
        f.write(launcher_code)

    cmd = [PYTHON_EXE, launcher_path]
    env = os.environ.copy()
    result = subprocess.run(cmd, cwd=BACKEND_DIR, env=env)
    if result.returncode != 0:
        print(f"\n\033[1;41m [FATAL] Rendering engine crashed! (Exit Code: {result.returncode})\033[0m")
        print(">> Check the error messages above. Verify your GPU drivers are working correctly.")
        os._exit(1)

ui_app = FastAPI()
# 已移除静态资源挂载 / Removed insecure static mount directory

@ui_app.get("/")
async def serve_index():
    return FileResponse(os.path.join(os.getcwd(), UI_FILE_NAME))

@ui_app.get("/index.css")
async def serve_css():
    return FileResponse(os.path.join(os.getcwd(), "UI/index.css"))

@ui_app.get("/index.js")
async def serve_js():
    return FileResponse(os.path.join(os.getcwd(), "UI/index.js"))


@ui_app.get("/i18n.js")
async def serve_i18n():
    return FileResponse(os.path.join(os.getcwd(), "UI/i18n.js"))


def launch_ui_server():
    print(f"\033[92m[UI] Workstation ready!\033[0m")
    print(f"\033[92m[LOCAL] http://127.0.0.1:4000\033[0m")
    print(f"\033[93m[LAN]   http://{LAN_IP}:4000\033[0m")
    
    # 彻底压制 WinError 10054 / Suppress WinError 10054 (client forcibly disconnected) noise
    if sys.platform == 'win32':
        # Uvicorn 内部会拉起循环，所以只能通过底层 Logging Filter 拦截控制台噪音
        class UvicornAsyncioNoiseFilter(logging.Filter):
            """压掉无害 asyncio 噪音 / Suppress harmless asyncio console spam (client disconnect, Proactor pipe cleanup)."""

            def filter(self, record):
                if record.name != "asyncio":
                    return True
                msg = record.getMessage()
                if "_call_connection_lost" in msg or "_ProactorBasePipeTransport" in msg:
                    return False
                if hasattr(record, "exc_info") and record.exc_info:
                    exc_type, exc_value, _ = record.exc_info
                    if isinstance(exc_value, ConnectionResetError) and getattr(
                        exc_value, "winerror", None
                    ) == 10054:
                        return False
                if "10054" in msg and "ConnectionResetError" in msg:
                    return False
                return True

        logging.getLogger("asyncio").addFilter(UvicornAsyncioNoiseFilter())
        
    uvicorn.run(ui_app, host="0.0.0.0", port=4000, log_level="warning", access_log=False)

if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    print("\033[1;97;44m LTX-2 CINEMATIC WORKSTATION | NETWORK ENABLED \033[0m\n")
    
    threading.Thread(target=launch_backend, daemon=True).start()
    
    # 强制校验 3000 端口 / Verify backend is alive on port 3000
    print("\033[93m[SYS] Waiting for backend on port 3000...\033[0m")
    backend_ready = False
    for _ in range(30):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('127.0.0.1', 3000)) == 0:
                    backend_ready = True
                    break
        except Exception:
            pass
        time.sleep(1)
        
    if backend_ready:
        print("\033[92m[SYS] Port 3000 verified! Backend loaded successfully.\033[0m")
    else:
        print("\033[1;41m [WARNING] Port 3000 not responding after 30 seconds! \033[0m")
        print(">> The backend may be deadlocked or blocked by a firewall.")
        print(">> Check the error messages above.\n")
        
    try:
        launch_ui_server()
    except KeyboardInterrupt:
        sys.exit(0)