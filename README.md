# 22/7 音乐的时间 服务端

**Nanaon Private Server / ナナオン プライベートサーバー**

《22/7 音乐的时间》（22/7 音楽の時間）保存项目，支持局域网游玩，以及云端登录和独立玩家存档。

## 2026.10.3：本地资源与云端存档分开

配套新版 LAN 客户端在最初下载页提供“本地服务器（默认）”和“云服务器 · 手动输入”。首次约 7 GiB 完整资源由自己的局域网资源服务提供；下载完成后，云端模式可关闭本地资源服务。游戏读取设备内已校验的资源，云端只处理登录、游戏 API 和各玩家进度，按需提供增量包。旧客户端需要更新才能使用这套选择与资源分离功能。

本仓库 `main` 只发布服务端代码、数据库结构、静态数据目录、必要的兼容资源与文档。**不上传 APK、客户端构建工具、完整游戏资源、中文二进制补丁、运行环境、个人存档、签名材料或日志。** 新数据库由服务端首次启动创建，数据库结构参考 `data/schema.sql`，以 `api/storage.py` 的实际初始化逻辑为准。

新版从自己的目录读取数据，不搜索旧服务端和父目录。准备好新版自身依赖与资源，迁移需要保留的存档后，即可删除旧服务端；也可以按下面说明覆盖程序文件。原来的 USB 推送、证书网关和 Docker 入口不再使用。

## 局域网首次准备与启动

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

## 云端运行与玩家账号

云服务器只需本仓库代码和 Python 3.12 依赖，**不需要 APK、Java、ADB、Unity 或完整游戏资源**。Linux 可使用 `python3 -m venv .venv` 和 `.venv/bin/python`；Windows 使用上面的 `.venv\Scripts\python.exe`。

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python cloud_launcher.py --public-url https://game.example.org --host 127.0.0.1 --port 18081 --state-dir var
```

`--public-url` 是玩家实际输入的域名或 IP 地址，必须带 `http://` 或 `https://`，支持端口，不支持网址子目录。外部 HTTPS 入口转发到本机 `127.0.0.1:18081`。临时 HTTP 测试可绑定 `0.0.0.0` 并使用 `http://<服务器IP>:18081`；正式对外服务使用 HTTPS，因为账号恢复码本身就是登录凭证。没有指定 `--updates-dir` 时云端不提供任何资源下载。

客户端首次下载时，同时填写本地资源地址与云服务器地址。资源完整后保留云地址即可继续游玩；本地地址只在首次下载、缺失补全、语言切换或不兼容版本更新时使用。首次完整下载与资源更新都走 HTTP，完全不依赖 USB 推送。

每个云服务器地址对应一个随机账号恢复码。同一账号的请求串行处理，余额、卡片、编队、换装、成绩等存入云端 `var/data/users.sqlite3`，不会按原游戏 UUID 或固定内置账号共享进度。账号创建、抽卡扣费和奖励写入使用数据库事务。

请在客户端的“云端账号恢复码 · 查看 / 导入”中私下备份恢复码。更换设备、重装应用、从 IP 改用域名或更改端口时，先导入原恢复码再登录。持有码者能够进入对应账号；服务端仅保存摘要，管理员不能从数据库反查原码。原游戏的数据继承界面不管理这个恢复码。

本地模式继续使用一个保存账号，适合单人局域网；多人互联网服务应使用 `cloud_launcher.py`。本地与云端存档分别保存，切换服务器不会自动上传或合并旧进度。

目前采用单个 Waitress 进程和 SQLite，适合小规模部署；同一存档目录有互斥锁，不要同时启动多个服务进程。备份玩家数据时停服，或使用 SQLite 一致性备份；迁移到新主机需保留整个数据库，包括 `cloud_accounts` 表，并保持原恢复码和服务器地址的对应关系。

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

### 把旧本地进度迁入云端

在云端第一次启动之前，使用同一迁移工具，将旧数据库导入云端独立目录：

```sh
.venv/bin/python tools/migrate_save.py --source /backup/old-users.sqlite3 --destination /srv/nanaon/var/data/users.sqlite3 --user-id 100004
.venv/bin/python tools/bind_cloud_save.py --database /srv/nanaon/var/data/users.sqlite3 --user-id 100004
```

将示例账号 ID 替换为旧库实际账号。绑定命令会隐藏输入地询问该玩家的 64 位恢复码；先在客户端填好目标云地址，查看或生成恢复码，再由管理员在停服状态下绑定。绑定后客户端用同一码登录便可继续旧进度。多账号旧库的记录全部保留，每个需要登录的旧账号分别绑定不同的码。

请在玩家首次云端登录创建空白账号之前完成绑定。已有绑定默认拒绝覆盖；迁移工具也不会把不同数据库合并。已有多人云库时不要用旧单人库整体替换，否则会移走其他玩家进度。需要合并多个独立旧库的管理员应另行进行逐账号导入，当前工具不提供这种合并。

## 可选的云端增量下载

保存每个完整资源版本的 `/bootstrap/manifest.json` 作为发布基线。资源修复完成后，在持有新版完整资源的本地目录生成增量包：

```sh
python tools/build_updates.py --old-manifest releases/previous-manifest.json --resource-root resources --locale ja-JP --output releases/update-001
```

中文包使用 `--locale zh-Hans`，并准备同一目录的 `patches/zh-Hans`。工具只复制 SHA-256 变化或新增的文件到 `update-001/files/`，生成不含本机路径的 `manifest.json`。它不读取玩家存档。也支持 `--new-manifest PATH --files PATH` 从已经导出的 `cache/`、`obb/`、`acf/` 文件树构建。

只把生成的增量目录上传到云端，启动时加 `--updates-dir /srv/nanaon/update-001`。客户端比较基线 revision 和语言，按大小 / SHA-256 校验下载，支持暂停和续传。下载完成才提交新清单，启动前同步原游戏自己的资源目录，避免新图片与旧 CRC 混用。

当前一次服务一个指定语言、指定基线的增量包：已是目标版本返回无更新；其他基线或语言明确要求先在本地更新。暂不支持删除文件、跨语言更新、多个历史基线链或 APK 自动升级。增量包包含哪些文件由发布者决定，云端不会自动搜索或暴露 7 GiB 完整档案。

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

`var/` 中含有真实存档、账号绑定和备份，不是可以随意删除的缓存。完整资源、中文补丁、APK 与存档均在 Git 之外单独保存。

## 当前修复范围

- 按恢复的主数据建立 144 个抽卡池与 383 个价格选项，支持宝石、付费宝石、抽卡券、保底组、重复卡突破和奖励持久化。
- 解锁资源完整的 359 套 2D 衣服，修复换装和主推成员保存；缺少真实模型或图标的衣服不会伪装成可用。主数据共列出 483 套，目前其余 124 套仍缺资源。
- 修复空房间布局、家具等级超出主数据上限、展示室空对象与奖杯统计。
- 补齐原客户端换装、资料和商店请求路径，修复“预览变化但选择未保存”；商店恢复体力按真实主数据扣费并保存奖励、次数和流水，失败时回滚。
- 配套中文补丁修复 TMP 字体和 OBB 内置回退字体，同步主数据、字体和游戏清单。部分剧情、图片和硬编码文字仍是日语。
- 部分缺失抽卡图片使用注明“保存版 / 復元表示”的替代图；缺失的同一服装角度预览使用该服装已有正面模型。完整功能和全部资源仍需继续验收，具体结果见验证记录。

过期抽卡兑换、真实付费、多人联机和部分活动仍有保存模式简化逻辑，不能将本次修复视为所有游戏功能都已完成。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

[验证记录](docs/VALIDATION.md) · [补丁接入](docs/CHINESE_PATCH_INTEGRATION.md) · [运行环境](docs/PORTABLE_RUNTIME.md)

配套客户端基线仍为 Unity 2018.4.18f1 / IL2CPP。已有 Android 14、16、17 的 4 KiB 页设备启动测试或用户确认，不等于所有机型、16 KiB 页设备与全部功能都已适配。
