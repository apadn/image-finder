# -*- coding: utf-8 -*-
"""
本地以图搜图工具 ImageFinder
粘贴剪贴板图片，搜索本地硬盘中的相同/相似图片。
依赖：Python 3.9+ 与 Pillow（pip install pillow）
运行：python app.py  然后浏览器访问 http://127.0.0.1:8765
"""
import json
import math
import os
import sqlite3
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import urlparse, parse_qs, unquote

from PIL import Image, ImageOps

# 打包为 exe 时：static 从解包资源目录读，data/ 放在 exe 旁边（绿色版可随意移动）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    STATIC_DIR = os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "static")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    STATIC_DIR = os.path.join(BASE_DIR, "static")

DATA_DIR = os.path.join(BASE_DIR, "data")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
DB_PATH = os.path.join(DATA_DIR, "index.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
PORT = 8765

os.makedirs(THUMB_DIR, exist_ok=True)


def log(msg):
    """--noconsole 打包下 stdout 为 None，静默处理"""
    try:
        print(msg)
    except Exception:
        pass

IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".jfif"}
SKIP_DIRS = {"$recycle.bin", "system volume information", "windows", "appdata", "$windows.~bt", "node_modules", ".git", "__pycache__"}

# ---------------- 感知哈希 ----------------
_SIZE, _SMALL = 32, 8
_dct = [
    [math.cos((2 * x + 1) * u * math.pi / (2 * _SIZE)) * (math.sqrt(0.5) if u == 0 else 1.0)
     for x in range(_SIZE)]
    for u in range(_SMALL)
]


def phash(img):
    """64-bit pHash：32x32 灰度 DCT，取 8x8 低频（不含 DC），中位数二值化。"""
    g = img.convert("L").resize((_SIZE, _SIZE), Image.LANCZOS)
    px = g.tobytes()
    rows = [[sum(px[y * _SIZE + x] * _dct[u][x] for x in range(_SIZE)) for u in range(_SMALL)]
            for y in range(_SIZE)]
    vals = [sum(rows[y][u] * _dct[v][y] for y in range(_SIZE))
            for v in range(_SMALL) for u in range(_SMALL)][1:]  # 去掉 DC
    med = sorted(vals)[len(vals) // 2]  # 中位数（保持 vals 原始顺序）
    bits = 0
    for i, v in enumerate(vals):
        if v > med:
            bits |= 1 << (62 - i)
    return bits


def dhash(img):
    """64-bit dHash：9x8 灰度水平梯度。"""
    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = g.tobytes()
    bits = 0
    for y in range(8):
        for x in range(8):
            if px[y * 9 + x] > px[y * 9 + x + 1]:
                bits |= 1 << (y * 8 + x)
    return bits


def popcount(x):
    return bin(x).count("1")


def hamming_hex(a, b):
    return popcount(int(a, 16) ^ int(b, 16))


# ---------------- 数据库 ----------------
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


DB = db()
DB_LOCK = threading.Lock()
DB.execute("""CREATE TABLE IF NOT EXISTS images(
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    phash TEXT, dhash TEXT,
    pflip TEXT, dflip TEXT,
    size INTEGER, mtime REAL,
    w INTEGER, h INTEGER,
    fails INTEGER DEFAULT 0
)""")
DB.execute("CREATE INDEX IF NOT EXISTS idx_images_path ON images(path)")
DB.commit()


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"roots": []}


CONFIG = load_config()


def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=2)


# ---------------- 扫描器 ----------------
class Scanner:
    def __init__(self):
        self.scanning = False
        self.stop_flag = False
        self.scanned = 0
        self.total = 0
        self.found_new = 0
        self.errors = 0
        self.current = ""
        self.last_finish = 0
        self._thread = None

    def status(self):
        return {
            "scanning": self.scanning,
            "scanned": self.scanned,
            "total": self.total,
            "found_new": self.found_new,
            "errors": self.errors,
            "current": self.current,
            "last_finish": self.last_finish,
        }

    def start(self):
        if self.scanning:
            return False
        self.stop_flag = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self):
        self.scanning = True
        self.scanned = self.found_new = self.errors = 0
        roots = [r for r in CONFIG.get("roots", []) if os.path.isdir(r)]
        files = []
        for root in roots:
            root_low = os.path.basename(root).lower()
            for dirpath, dirnames, filenames in os.walk(root):
                if self.stop_flag:
                    break
                if os.path.basename(dirpath).lower() in SKIP_DIRS or root_low in SKIP_DIRS:
                    dirnames[:] = []
                    continue
                dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
                for fn in filenames:
                    if os.path.splitext(fn)[1].lower() in IMG_EXTS:
                        files.append(os.path.join(dirpath, fn))
        self.total = len(files)

        with DB_LOCK:
            known = {}
            for path, size, mtime in DB.execute("SELECT path,size,mtime FROM images"):
                known[path] = (size, mtime)

        for i, path in enumerate(files):
            if self.stop_flag:
                break
            self.scanned = i + 1
            self.current = path
            try:
                st = os.stat(path)
            except OSError:
                self.errors += 1
                continue
            key = (st.st_size, int(st.st_mtime))
            if known.get(path) == key:
                continue  # 未变化，跳过
            try:
                with Image.open(path) as im:
                    im.draft("L", (64, 64))
                    im = ImageOps.exif_transpose(im)
                    ph, dh = phash(im), dhash(im)
                    flip = im.transpose(Image.FLIP_LEFT_RIGHT)
                    pf, df = phash(flip), dhash(flip)
                    w, h = im.size
                with DB_LOCK:
                    DB.execute(
                        """INSERT INTO images(path,phash,dhash,pflip,dflip,size,mtime,w,h,fails)
                           VALUES(?,?,?,?,?,?,?,?,?,0)
                           ON CONFLICT(path) DO UPDATE SET
                           phash=excluded.phash, dhash=excluded.dhash,
                           pflip=excluded.pflip, dflip=excluded.dflip,
                           size=excluded.size, mtime=excluded.mtime,
                           w=excluded.w, h=excluded.h, fails=0""",
                        (path, f"{ph:016x}", f"{dh:016x}",
                         f"{pf:016x}", f"{df:016x}", st.st_size, int(st.st_mtime), w, h))
                    DB.commit()
                self.found_new += 1
            except Exception:
                self.errors += 1
                with DB_LOCK:
                    DB.execute(
                        """INSERT INTO images(path,phash,dhash,pflip,dflip,size,mtime,w,h,fails)
                           VALUES(?,?,?,NULL,NULL,?,0,0,0,1)
                           ON CONFLICT(path) DO UPDATE SET
                           size=excluded.size, mtime=excluded.mtime, fails=1""",
                        (path, None, None, st.st_size))
                    DB.commit()
        self.last_finish = time.time()
        self.scanning = False
        self.current = ""


SCANNER = Scanner()


# ---------------- 搜索 ----------------
def search_image_bytes(data):
    with Image.open(BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        q_ph, q_dh = phash(im), dhash(im)
        flip = im.transpose(Image.FLIP_LEFT_RIGHT)
        q_pf, q_df = phash(flip), dhash(flip)
        qw, qh = im.size

    results = []
    with DB_LOCK:
        rows = DB.execute(
            "SELECT id,path,phash,dhash,pflip,dflip,size,w,h FROM images WHERE phash IS NOT NULL").fetchall()
    for rid, path, ph, dh, pf, df, size, w, h in rows:
        # 正向匹配 + 翻转匹配（支持素材被水平翻转使用的情况）
        d = min(popcount(int(ph, 16) ^ q_ph), popcount(int(dh, 16) ^ q_dh),
                popcount(int(pf, 16) ^ q_ph), popcount(int(df, 16) ^ q_dh),
                popcount(int(ph, 16) ^ q_pf), popcount(int(dh, 16) ^ q_df))
        if d <= 20:
            results.append({
                "id": rid, "path": path, "size": size, "w": w, "h": h,
                "dist": d,
                "same": d <= 5,
            })
    results.sort(key=lambda r: r["dist"])
    return {"total": len(rows), "results": results[:200]}


# ---------------- 缩略图 ----------------
def get_thumb(rid):
    cache = os.path.join(THUMB_DIR, f"{rid}.jpg")
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    with DB_LOCK:
        row = DB.execute("SELECT path FROM images WHERE id=?", (rid,)).fetchone()
    if not row:
        return None
    try:
        with Image.open(row[0]) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((420, 420), Image.LANCZOS)
            buf = BytesIO()
            im.save(buf, "JPEG", quality=82)
            out = buf.getvalue()
    except Exception:
        return None
    try:
        with open(cache, "wb") as f:
            f.write(out)
    except OSError:
        pass
    return out


# ---------------- HTTP ----------------
def reveal_in_explorer(path):
    """用 Shell 官方 API 打开文件所在文件夹并选中它。
    比 `explorer /select,path` 可靠：subprocess 加引号后 explorer 常忽略 select 定位到错误位置。"""
    path = os.path.normpath(path)
    import ctypes
    from ctypes import wintypes
    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.OleDLL("shell32")
    pidl = ctypes.c_void_p()
    sfgao = wintypes.ULONG()
    ole32.CoInitialize(None)
    try:
        if shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, ctypes.byref(sfgao)) != 0:
            return False
        try:
            return shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0) == 0
        finally:
            ole32.CoTaskMemFree(pidl)
    finally:
        ole32.CoUninitialize()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ImageFinder/1.0"

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        if n > 64 * 1024 * 1024:
            raise ValueError("body too large")
        return self.rfile.read(n)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            try:
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._json({"error": "index.html missing"}, 500)
        elif u.path == "/api/status":
            with DB_LOCK:
                count = DB.execute("SELECT COUNT(*) FROM images WHERE phash IS NOT NULL").fetchone()[0]
                failed = DB.execute("SELECT COUNT(*) FROM images WHERE fails=1").fetchone()[0]
            self._json({**SCANNER.status(), "indexed": count, "failed": failed,
                        "roots": CONFIG.get("roots", [])})
        elif u.path == "/api/reveal":
            qs = parse_qs(u.query)
            path = (qs.get("path") or [""])[0]
            with DB_LOCK:
                ok = DB.execute("SELECT 1 FROM images WHERE path=?", (path,)).fetchone()
            if ok and os.path.exists(path):
                try:
                    success = reveal_in_explorer(path)
                except Exception as e:
                    log(f"reveal 失败: {e}")
                    success = False
                if success:
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "打开文件夹失败"}, 500)
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        elif u.path.startswith("/thumb/"):
            try:
                rid = int(u.path.rsplit("/", 1)[1])
            except ValueError:
                self._json({"error": "bad id"}, 400)
                return
            data = get_thumb(rid)
            if data is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send(200, data, "image/jpeg")
        elif u.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/search":
                data = self._read_body()
                if len(data) < 64:
                    self._json({"error": "empty or invalid image"}, 400)
                    return
                self._json(search_image_bytes(data))
            elif u.path == "/api/scan":
                started = SCANNER.start()
                self._json({"ok": True, "started": started})
            elif u.path == "/api/stop":
                SCANNER.stop_flag = True
                self._json({"ok": True})
            elif u.path == "/api/shutdown":
                self._json({"ok": True})
                threading.Timer(0.4, os._exit, args=(0,)).start()
            elif u.path == "/api/roots":
                body = json.loads(self._read_body().decode("utf-8") or "{}")
                action = body.get("action", "set")
                roots = CONFIG.setdefault("roots", [])
                if action == "add":
                    for r in body.get("roots", []):
                        r = os.path.normpath(r)
                        if os.path.isdir(r) and r.lower() not in [x.lower() for x in roots]:
                            roots.append(r)
                elif action == "remove":
                    rm = {os.path.normpath(x).lower() for x in body.get("roots", [])}
                    CONFIG["roots"] = [r for r in roots if os.path.normpath(r).lower() not in rm]
                elif action == "set":
                    CONFIG["roots"] = [r for r in body.get("roots", []) if os.path.isdir(r)]
                save_config()
                self._json({"ok": True, "roots": CONFIG["roots"]})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)


class Api:
    """暴露给窗口内 JS：pywebview.api"""
    def quit(self):
        os._exit(0)

    def pick_folder(self):
        """弹出 Windows 原生文件夹选择框，返回所选路径或 None"""
        try:
            import webview
            if not webview.windows:
                return None
            result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                return result[0]
        except Exception as e:
            log(f"pick_folder 失败: {e}")
        return None


def start_server():
    # 容器内需监听 0.0.0.0 才能被端口映射访问；本地桌面默认 127.0.0.1
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    if "--web" in sys.argv:
        run_web_mode()
        return
    if "--window" in sys.argv or getattr(sys, "frozen", False):
        run_window_mode()
        return
    run_web_mode()


def run_window_mode():
    """exe 默认：原生窗口（WebView2 内核渲染界面），失败自动回退浏览器。"""
    start_server()
    url = f"http://127.0.0.1:{PORT}"
    log(f"ImageFinder 窗口模式: {url}")
    try:
        import webview
        webview.create_window(
            "ImageFinder · 本地搜图", url,
            js_api=Api(), width=1280, height=860,
            min_size=(1024, 700), background_color="#f6f7f9",
        )
        webview.start()  # 窗口关闭即返回，daemon 线程随之结束
    except Exception as e:
        log(f"原生窗口启动失败({e})，回退浏览器模式。")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


def run_web_mode():
    """脚本调试模式：浏览器访问。"""
    start_server()
    url = f"http://127.0.0.1:{PORT}"
    log(f"ImageFinder 已启动: {url}  (Ctrl+C 退出)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if not CONFIG.get("roots"):
        log("提示：首次使用请在网页右侧『索引目录』中添加要搜索的文件夹，然后点击『扫描索引』。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log("\n已退出")


if __name__ == "__main__":
    main()
