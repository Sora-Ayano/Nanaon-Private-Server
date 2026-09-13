# 22/7 音乐的时间 本地服务器

**Nanaon Private Server / ナナオン プライベートサーバー**

《22/7 音乐的时间》（22/7 音楽の時間）本地服务器。

## 2026.9.13：独立局域网服务端

本仓库 `main` 只发布服务端代码、数据库结构、静态数据目录、必要的兼容资源与文档。**不上传 APK、客户端构建工具、完整游戏资源、中文二进制补丁、运行环境、个人存档、签名材料或日志。** 新数据库由服务端首次启动创建，数据库结构参考 `data/schema.sql`，以 `api/storage.py` 的实际初始化逻辑为准。

新版从自己的目录读取数据，不搜索旧服务端和父目录。准备好新版自身依赖与资源，迁移需要保留的存档后，即可删除旧服务端；也可以按下面说明覆盖程序文件。原来的 USB 推送、证书网关和 Docker 入口不再使用。

## 首次准备与启动

1. 下载本仓库到新的可写目录。源码运行需要 Windows x64 的 Python 3.12；完整本地便携包如已有 `runtime/python/python.exe`，可跳过安装 Python 依赖这一步。
2. 在服务端根目录打开 PowerShell，准备独立环境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

3. 将配套完整资源包放入**新版自己的** `resources/`。不要通过链接指向旧服务端；删除旧目录前确认这些是已复制完成的真实文件。资源包不在 GitHub 中。
4. 若有语言包，将其中的 `patches/` 放到新版根目录。没有语言包时，日语使用原始日文资源；选择中文时必须提供相应补丁清单和文件。
5. 有旧存档时先按下一节迁移；没有旧存档则直接双击 **`Start-Server.cmd`**。回车或输入 `1` 默认日语，输入 `2` 为简体中文实验选项。中文运行验收状态见 [验证记录](docs/VALIDATION.md)。
6. 手机与电脑连接同一局域网，使用配套的 **Nanaon LAN** 客户端。运行期间保持服务窗口打开；关闭窗口或按 Ctrl+C 停止。

服务自动检测私有 IPv4，默认使用 TCP 18080。手机可用浏览器访问窗口显示的 `/play` 地址。若 Windows 弹出防火墙请求，允许专用网络访问，不必关闭整个防火墙。

**客户端制作与服务端启动分开。** 本 GitHub 仓库只启动服务，不重打包 APK。另行使用配套客户端构建包生成安装包；可将生成的 `nanaon-lan-*.apk` 放入本机 `dist/`，安装页会自动提供下载。已有 LAN 客户端可以在启动器中更新服务器地址。

## 保留旧服务端的游玩数据

旧版通常将存档存为 `data/private_server.sqlite3`；上一版 LAN 服务为 `var/data/users.sqlite3`。如果改过数据库路径，请使用实际文件。**先停止旧、新两个服务端，再迁移。** 不要从仍在运行的 SQLite 库中单独拖拽主文件并漏掉 WAL 事务。

推荐把旧存档备份到服务目录以外，并保留原文件，直到确认新版本的昵称、卡片、编队、道具和成绩均正确。在新版根目录运行：

```powershell
.\.venv\Scripts\python.exe tools/migrate_save.py --source '<旧数据库的绝对路径>'
```

便携包把命令开头替换为 ` .\runtime\python\python.exe `。默认导入到新版的 `var/data/users.sqlite3`。

迁移工具执行 SQLite 一致性备份，在临时副本上升级表结构，然后恢复并逐表对比原有玩家记录，避免旧升级逻辑重置卡片或余额。源库不修改；通过完整性、外键和玩家记录校验后才替换目标文件。测试覆盖了旧库的实际副本及缺少较新升级标记的旧结构情形。

- **目标已有存档**：默认停止，保留两边数据。确定采用旧库覆盖新库时，加 `--replace`；目标库会先自动备份到新版 `var/backups/`。
- **旧库有多个账号**：所有账号均保留。单账号自动选用；多账号中存在旧版内置账号 `100004` 时默认选它，否则要求通过 `--user-id <账号ID>` 指定。选定账号记录在数据库中，新客户端登录后继续使用它，不会因为安装 UUID 改变而创建空白账号。
- **不是合并**：`--replace` 替换当前存档，不会合并两台电脑的游玩进度。
- **回滚**：停止服务后，使用相同工具将 `var/backups/` 中的备份作为 `--source`，并加 `--replace`。当前存档仍会先备份。也可以保留原服务和原存档，直接回到原环境。

覆盖原目录更新时，先把数据库备份到该目录之外，再覆盖新版程序文件。保留需要的 `resources/` 和 `var/`；首次启动新版前，使用上述命令从备份导入。不要用旧的 Install-And-Run、USB、Docker 或证书脚本启动新版。新版没有“自动猜测并读取旧数据库”的回退行为。

如果还使用本地客户端构建包，另行备份它的 `var/signing/`，以便保持 APK 更新签名。**数据库迁移不需要旧证书或 APK 签名密钥**，也不会把它们上传。迁移完成且验证无误后，旧服务端可直接删除；新版继续使用自己的资源、存档和依赖。

## 日常语言选择与网络设置

日常启动仅运行服务，不生成 APK。切换语言不需要重新安装：先退出手机游戏并停止服务，再启动选择语言，随后重新打开客户端。文件通过局域网按资源版本下载，支持大小 / SHA-256 校验和断点续传。一个服务实例的语言对所有连接它的客户端生效。

```powershell
.\Start-Server.cmd --locale ja-JP
.\Start-Server.cmd --locale zh-Hans
.\Start-Server.cmd --ip <电脑局域网IPv4> --port 18080
```

不带 `--locale` 的非交互启动也默认日语。VPN、多网卡和访客 Wi-Fi / AP 隔离可能影响连通性；先用手机浏览器访问 `/play` 检查。服务不会强制终止占用端口的其他程序。

## 独立目录布局

```text
Nanaon-Server/
|-- Start-Server.cmd               日常启动与语言选择
|-- api/ cdn/ crypto/ lan/         服务端代码
|-- data/                         静态 JSON 数据与 schema.sql
|-- compat_assets/                服务端必需的兼容资源
|-- tools/migrate_save.py          存档迁移
|-- .venv/ 或 runtime/            新版自己的运行依赖，不提交 Git
|-- patches/                      可选语言包，不提交 Git
|-- resources/                    新版完整资源，不提交 Git
|   |-- main.5465.com.aniplex.nananiji.obb
|   |-- main.5465.com.aniplex.nananiji/assets/
|   `-- com.aniplex.nananiji/files/
|       |-- TTSCriProject.acf
|       `-- DownloadCache/
|-- dist/                         可选的本地客户端分发，不提交 Git
`-- var/
    |-- data/users.sqlite3         本机存档，不提交 Git
    |-- backups/                  迁移前备份，不提交 Git
    `-- logs/                     本机日志，不提交 Git
```

`var/` 中含有真实存档与备份，不是可以随意删除的缓存。当前仍为单人保存模式，同一实例的客户端可能共享账号，尚不是独立多用户服务。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

[验证记录](docs/VALIDATION.md) · [补丁接入](docs/CHINESE_PATCH_INTEGRATION.md) · [运行环境](docs/PORTABLE_RUNTIME.md)

配套客户端基线仍为 Unity 2018.4.18f1 / IL2CPP。已有 Android 14、16、17 的 4 KiB 页设备启动测试或用户确认，不等于所有机型、16 KiB 页设备与全部功能都已适配。
