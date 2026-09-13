# 独立运行环境

服务端只需要 Python 3.12 和 `requirements.txt` 中的依赖。可以使用 [CPython 官方 Windows 发行](https://www.python.org/downloads/windows/)创建新版自己的 `.venv`，也可使用配套便携包的 `runtime/python/`。两者均无需旧服务端目录。

`Start-Server.cmd` 优先使用本目录 `.venv/Scripts/python.exe`，其次使用 `runtime/python/python.exe`。若另行准备了离线依赖 ZIP，`Setup-Runtime.ps1` 可以校验其 SHA-256 并在本目录解压。GitHub 不附带运行环境，也不会在首次运行时从旧服务器复制依赖。

普通服务运行和数据库迁移不需要 Java、Android SDK、ADB、Unity、系统证书或 root。APK 生成属于独立客户端构建包；服务端仅可通过本地 `/play` 分发已经准备好的 APK。

资源必须置于本目录 `resources/`，或者明确用 `--resource-root` 指定独立资源根目录。默认不搜索父目录。计划删除旧服务器时，先把资源复制到新目录，不要指定即将删除的旧路径，也不要使用目录链接。
