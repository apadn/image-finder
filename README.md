# ImageFinder · 本地以图搜图工具

粘贴剪贴板图片（或上传图片），在本地硬盘的指定目录中查找相同/相似图片。

## 功能

- 剪贴板粘贴 / 图片上传作为搜索源
- 对指定目录建立图片索引（SQLite + 缩略图缓存），支持增量扫描
- 相同图（哈希精确匹配）与相似图（感知哈希近似匹配）检索
- Web 界面操作，数据全部保留在本地

## 目录结构

```
app.py                  主程序（HTTP 服务 + 检索逻辑）
static/                 前端页面
ImageFinder_1.4.spec    PyInstaller 打包配置（最新版）
make_icon.py            生成应用图标 app.ico
data/                   运行时生成：索引库 index.db、缩略图 thumbs/、配置 config.json
```

## 本地运行

依赖：Python 3.9+ 与 Pillow

```bash
pip install pillow
python app.py
```

启动后浏览器访问 `http://127.0.0.1:8765`。首次使用在页面右侧「索引目录」添加要搜索的文件夹，然后点击「扫描索引」。

监听地址默认 `127.0.0.1`，可用环境变量 `HOST` 覆盖（如容器内设 `0.0.0.0`）；端口固定 `8765`（代码内 `PORT`）。

启动模式：

- `python app.py` — 浏览器模式（默认）
- `python app.py --window` — 原生窗口模式（需 `pywebview`，exe 打包默认走此模式，失败自动回退浏览器）

## Docker 运行

```bash
docker load -i image-finder-1.4.tar
docker run -d --name imagefinder \
  -p 8765:8765 \
  -v imagefinder-data:/app/data \
  image-finder:1.4
```

访问 `http://127.0.0.1:8765`。`/app/data` 挂载卷保存索引与缩略图，容器重建后数据不丢；如需索引本地目录，把目录追加挂载进容器后在页面中添加对应路径。

## 打包

### Windows exe

```bash
pip install pillow pywebview pyinstaller
pyinstaller ImageFinder_1.4.spec
```

产物在 `dist/`，为绿色版目录，`data/` 生成在 exe 旁边，可随意移动。

### Docker 镜像

```bash
docker build -t image-finder:1.4 .
docker save -o image-finder-1.4.tar image-finder:1.4
```
