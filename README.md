# 22/7 音乐的时间 本地服务器

**Nanaon Private Server / ナナオン プライベートサーバー**

《22/7 音乐的时间》（22/7 音楽の時間）本地服务器。

Windows 一键安装器以 **雷电模拟器 9（LDPlayer 9）** 为主，内置的 Python 与 ADB。

## 发行包结构

不要改变以下相对位置：

```text
Nanaon Private Server/
|-- private_server/                       服务端、数据库、兼容资源与运行环境
|-- com.aniplex.nananiji/                 Android 外部资源包
|-- main.5465.com.aniplex.nananiji/       服务端读取的基础资源
`-- main.5465.com.aniplex.nananiji.obb     Android OBB
```

`private_server/compat_assets` 是服务端核心内容，包含已恢复的剧情/结算 EventScript 兼容 Bundle，以及部分音频替换。它用于补足原档中不存在或只含试听版的资源，不能删除。

`private_server/var` 不属于发行内容。首次启动时会自动生成，用于保存本机 TLS 证书、进程状态、设备安装临时文件和日志；停止服务后可以删除，下次启动会重新创建。

## 一键安装前的要求

1. Windows 10 或 Windows 11；
2. 雷电设置中已开启 ADB 调试和 root 权限，雷电模拟器需开启系统盘可写入模式（设置->磁盘->系统盘设置->勾选“可写入”）；
3. 雷电模拟器 9 （安卓 9 / Android 9）启动并进入桌面；
4. 游戏APK `ナナオン_2.4.0.apk` 已安装；
5. 模拟器至少有约 8GB 可用空间；
6. Windows 上的 TCP 443、8000、8888 未被其他程序占用；
7. Windows 防火墙允许发行包内置 Python 在专用网络监听上述端口。

有 Magisk 时使用 systemless 模块；无 Magisk 时需要雷电的可写 `/system`。

## 一键安装并运行

在项目目录双击：

```text
private_server\Install-And-Run.cmd
```

也可以先进入服务端目录再执行：

```powershell
cd "<发行根目录>\private_server"
.\Install-And-Run.cmd
```

安装器会依次完成：

1. 使用 `runtime\python\python.exe` 启动 API、CDN 和 TLS 网关；
2. 首次运行时在 `var\certs` 生成本地 TLS 证书；
3. 使用 `runtime\platform-tools\adb.exe` 探测雷电常用的 5555、7555 端口；
4. 自动读取电脑可用 IPv4，并从 Android 端测试可达地址；
5. 增量推送 `com.aniplex.nananiji` 和 OBB；
6. 在 Android 端安装五个原游戏域名的 hosts 映射和系统 CA；
7. 必要时重启 Android，随后校验资源、hosts、CA 与三个服务端口；
8. 默认启动游戏，并等待客户端访问本地 API。

成功时应看到：

```text
Server: OK
Hosts: OK (5 domains)
Android system CA: OK
Port 443: LISTENING
Port 8000: LISTENING
Port 8888: LISTENING
Client API connection: OK
```

首次运行会推送全量资源包，请耐心等待。

同一台雷电实例可能同时显示为 `emulator-<编号>` 和 `127.0.0.1:<端口>`。安装器会按 Android 设备身份去重，只配置一次。

## 常用参数

先查看内置 ADB 识别到的设备：

```powershell
cd "<发行根目录>\private_server"
.\runtime\platform-tools\adb.exe devices -l
```

指定设备：

```powershell
.\Install-And-Run.cmd -Serial <设备序列号>
```

配置设备并启动服务器，但不自动打开游戏：

```powershell
.\Install-And-Run.cmd -Serial <设备序列号> -NoLaunch
```

资源已经同步完成时跳过资源推送：

```powershell
.\Install-And-Run.cmd -SkipResources
```

延长等待客户端连接的时间：

```powershell
.\Install-And-Run.cmd -ConnectionWaitSeconds 90
```

## 一键推送全部资源

只推送资源目录和 OBB，不启动服务器、不修改 hosts/CA，也不要求 root：

```text
private_server\Push-All-Resources.cmd
```

指定设备：

```powershell
cd "<发行根目录>\private_server"
.\Push-All-Resources.cmd -Serial <设备序列号>
```

推送脚本使用内置 ADB，检查模拟器剩余空间，增量补齐文件并核对文件数、目录大小和 OBB 字节数。它不删除客户端已有文件。

## 手工启动和停止服务

设备已经配置好 hosts 与系统 CA 时，可以只启动服务：

```powershell
cd "<发行根目录>\private_server"
.\runtime\python\python.exe -B .\run.py
```

停止全部服务：

```powershell
.\Stop-Server.cmd
```

也可以执行：

```powershell
.\runtime\python\python.exe -B .\run.py --stop
```

健康检查地址为 <http://127.0.0.1:8888/health>。

关闭运行 `run.py` 的前台 CMD 窗口会停止其 API、CDN 和 TLS 网关子进程。通过一键安装器启动时，服务会在后台继续运行，需要使用 `Stop-Server.cmd` 停止。

## 常见错误

### `No Android device is connected`

在雷电设置中开启 ADB，完全重启模拟器，再运行安装器。若使用自定义端口，先手工连接：

```powershell
.\runtime\platform-tools\adb.exe connect 127.0.0.1:<模拟器端口>
```

### `has no working root shell`

雷电 ADB 已连接，但 `su -c id` 没有返回 `uid=0(root)`。在雷电设置中开启 root 后完全重启实例。

### `root bind-mount fallback failed`

模拟器有 root，但安装器无法绑定 Android hosts 或系统 CA。先完全重启模拟器并确认雷电 root 已开启，再重新运行安装器。正常的只读 `/system` 不会触发此错误，安装器会自动使用当前启动周期有效的绑定挂载。

### `Trust anchor for certification path not found`

Android 未加载当前服务器 CA。重新运行安装器；如果脚本刚更新过证书，等待 Android 重启完成后再运行一次，并确认输出包含 `Android system CA: OK`。

### 端口被占用

安装器只检测和报告 443、8000、8888 的占用程序与 PID，不会终止其他程序，也不会新增、修改或删除 Windows `portproxy` 规则。先运行 `Stop-Server.cmd`；仍冲突时检查：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 443,8000,8888
netsh interface portproxy show all
```

关闭占用端口的程序或由用户自行处理旧端口转发规则，然后重新启动。

### 游戏启动黑屏

确认 OBB 位于 Android 的 `/sdcard/Android/obb/com.aniplex.nananiji/`。可以运行 `Push-All-Resources.cmd` 自动补齐并校验。

### 连接失败

按顺序检查：

1. 一键窗口是否显示 `Server: OK`；
2. 三个端口是否全部为 `LISTENING`；
3. Windows 防火墙是否允许内置 Python；
4. Android hosts 与系统 CA 是否均为 `OK`；
5. `var\logs\gateway.log` 是否出现客户端请求。

## 数据库与内置用户

活动数据库为 `data\private_server.sqlite3`。发行数据库只包含一个内置用户（ID `100004`）；新的客户端 UUID 会自动映射到该用户，不会创建额外账号。

数据库持久化用户等级、货币、卡片、角色、道具、称号、服装、编队、家具、故事、Quest、歌曲、视频进度、Live 成绩和交易记录。用户在 App 中保存编队、购买道具、切换称号或服装后，API 会同步写入 SQLite。

## 服务端目录

```text
private_server/
|-- api/                         API、数据模型与 SQLite 存储
|-- cdn/                         本地资源解析与 CDN
|-- compat_assets/               剧情、结算与歌曲兼容资源
|-- crypto/                      NanaPacker 协议与 FuncId
|-- data/                        主数据目录与内置用户数据库
|-- runtime/python/              内置 Python 与依赖
|-- runtime/platform-tools/      内置 ADB
|-- Install-And-Run.cmd          雷电 9 一键安装并运行
|-- Push-All-Resources.cmd       一键增量推送资源和 OBB
|-- Stop-Server.cmd              停止服务
|-- config.yaml                  本地监听与协议配置
`-- run.py                       服务进程入口
```

## 已知问题

1. 无法抽卡；
2. 部分剧情因缺少音频或文本会直接跳过或报错；
3. 一些额外功能未作修复（例如：自制谱面）。
