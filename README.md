# PDFSwitch 网页版

统一网页入口 + 每台 Windows 电脑自己的转换连接器。网页使用 GitHub Pages；转换、文件和核对报告保存在访问者自己的电脑。

**当前状态：本地实现和转换验证已完成，尚未发布到 GitHub。**

## 在另一台电脑使用

1. 将 `site/downloads/PDFSwitch-Windows.zip` 复制到另一台 Windows 10/11 64 位电脑。
2. 完整解压到 D 盘或其他非 C 盘，双击 `PDFSwitch-Connector.exe`，保留窗口。
3. 在打开的页面点击“回到网站并配对”。网站尚未发布时，点击“使用本机网页”即可先使用。
4. 确认六位配对码，浏览器询问时允许连接本机，然后选择文件。

每台电脑需要安装并激活实际使用的 Illustrator、CorelDRAW、Office 或 WPS。无需安装 Python。浏览器不支持从网站连接本机时，使用连接器附带的本机网页。

## 发布到 GitHub Pages

目标账号：`lih20040623-star`。

1. 在该账号下创建公开仓库 `lih20040623-star.github.io`，并让当前 GitHub 连接可以访问该仓库。
2. 将本项目推送到仓库的 `main` 分支。`site/downloads/PDFSwitch-Windows.zip` 是公开分发包；保留它。请勿上传 `.tmp`、`.cache`、`.build-tools`、`build`、`release`、任何 `data` 目录或原项目历史任务。
3. 打开仓库 **Settings → Pages → Source → GitHub Actions**。
4. 在 **Actions → Deploy PDFSwitch to GitHub Pages → Run workflow** 运行发布。
5. 工作流成功后，预期地址为 `https://lih20040623-star.github.io/`。在部署成功且页面实际可访问之前，不应视为已经上线。

工作流只发布 `site`，不会把 Python 源码、测试数据和构建缓存变成网站文件。

## 目录

- `site/`：静态网页、配对客户端和下载包。
- `connector/main.py`：仅监听 `127.0.0.1:18765` 的连接器，配对、认证和来源限制。
- `connector/engine/`：从原项目复制的转换与核对引擎；原项目未修改。
- `tools/`：打包、许可证采集和真实可执行程序验证工具。
- `tests/`：原引擎测试和新增连接器测试。
- `release/verification.json`：可执行程序验证结果。

## 连接与数据

六位配对码 10 分钟有效，使用后立即更新；每个连接器进程生成独立的高熵访问令牌。令牌仅存于浏览器当前标签页的 sessionStorage，请求通过 Authorization 头发送到本机，文件链接不携带令牌。重启连接器后需重新配对。

允许的正式网站来源仅为 `https://lih20040623-star.github.io`，以及连接器自己的本机来源。连接器不向局域网或公网监听。配对页面仅允许浏览器顶层导航读取，拒绝跨站 fetch 和 iframe；所有任务、软件检测和生成文件接口均要求认证。公网网站不接收文档。

文件保存在连接器目录的 `data`，可配置临时目录和字体缓存保存在 `runtime-tmp`。程序拒绝以 C 盘为数据根目录。Windows、浏览器和桌面软件自身的内部缓存不由连接器控制。没有添加开机项、计划任务、系统服务或注册表协议。

## 验证范围

48 项原引擎测试、10 项连接测试，以及真实 Windows 可执行程序的两页 PDF 上传、参考核对、6 个 PNG 预览、原样下载、源文件保护和列表移除保留文件验证通过。

未在另一台实体电脑上验收，也未把所有 Office/WPS/设计软件版本逐个实测。保留原项目的结果分类：自动核对通过、视觉阈值忽略、参考核对一致、内嵌 PDF 原样保留、发现差异、核对未完成。每页视觉差异低于 2% 忽略；页数、尺寸、文字等检查独立判定。

## 重建连接器

使用 Python 3.12 64 位，安装 `connector/engine/requirements.txt`、`pyinstaller` 和 `pefile`。所有依赖缓存、TEMP、TMP 和 PYINSTALLER_CONFIG_DIR 应显式放到 D 盘。准备含 `pdftoppm.exe` 和 DLL 的 Windows Poppler 目录后，执行：

```powershell
python -B tools/collect-licenses.py
.\tools\build-connector.ps1 -PythonPath 'D:\你的Python\python.exe' -PopplerDirectory 'D:\你的Poppler\Library\bin'
python -B tools/smoke-connector.py
python -B tools/package-connector.py
```

重建前请停止已有连接器。复制公开网页和明确的程序文件，不要将运行数据加入发布包。

