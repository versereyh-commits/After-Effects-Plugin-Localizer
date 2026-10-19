# AE汉化工具（After Effects Plugin Localizer）

把 After Effects 里的**效果名称**和**参数名称**实时改成中文的 Windows 小工具。

它直接修改正在运行的 AE 中显示的名称：“效果和预设”面板、效果控件里的参数标签都会变成中文，数值仍用 AE 原有的控件调节。它**不修改任何插件文件**，也不需要重启 AE。

> 本项目与 Adobe 无关。After Effects 是 Adobe 的商标。

## 能做什么

- 扫描已安装的 AE 和第三方插件，列出所有效果名称。
- 调用你自己配置的大模型翻译名称（DeepSeek、OpenAI、Claude、Gemini、通义千问、智谱 GLM、Kimi、豆包、硅基流动，或兼容 OpenAI 接口的自定义服务），也可以手动填写。
- 一键把勾选的名称写入正在运行的 AE，随时恢复原名。
- 可选“双语写入”：写成“英文原名 (中文)”。
- 翻译方案：保存、切换、导入、导出整套汉化。
- AE 重启后，连接时自动重新写入上次应用的效果名称。
- AE 后台连接（推荐）：读写参数在 AE 后台进行，不打扰 AE，窗口和焦点保持不变，见下文。
- 深色模式、可自定义快捷键；每个新版本第一次打开时显示一次更新内容。

## 支持的 AE 版本

实时写入只对已验证的 AE 构建开放：

| AE 版本 | 构建号 |
|---|---|
| After Effects 2026 | 26.5.0.89 |
| After Effects 2026 | 26.0.0.67 |
| After Effects 2025 | 25.3.0.71 |
| After Effects 2021 | 18.2.1.8 |

其他构建可以扫描、翻译和保存方案，但要先在软件的“AE 版本”页做“自动识别与适配”。只有完整验证通过后才会开放实时写入，检测到版本号不代表已适配。

## 安装

在 [Releases](../../releases) 下载 `ae-localizer-setup-<版本>.exe`（安装包）。装好后软件会在启动时检查这里的新版本，有更新时提示一键下载安装。

- 安装到当前用户目录，不需要管理员权限。
- 需要 Windows 10 / 11（64 位）。

### AE 后台连接（推荐）

读取或应用参数名称时，软件要把一段脚本交给 AE 执行。没装后台连接时，软件用 `AfterFX.exe -r` 转交。这会让最大化的 AE 窗口被还原（AE 自身的行为），软件会立即把窗口恢复原样，但能看到一下闪动。

在“设置与日志 → 适配范围 → AE后台连接”，或者“AE 版本 → 自动识别与适配”窗口的第 4 步，点“安装后台连接”，再重启一次 AE。更新到新版本后，如果还没安装，软件会提醒一次。安装后：软件会把一个看不见的 CEP 扩展（源码在 [`cep/`](cep)）复制到 `%APPDATA%\Adobe\CEP\extensions`，之后的读写都由它在 AE 内部执行，不再启动第二个 AE 进程，窗口和焦点都不动。扩展只运行它自带的、已签名的脚本，只接受软件已知的几种请求；没加载或没及时响应时，软件自动改回原来的方式。

安装时会同时开启 Adobe 扩展调试模式（当前用户注册表 `HKCU\Software\Adobe\CSXS.10`～`13` 的 `PlayerDebugMode`），保证扩展一定能被加载。调试模式下 AE 不检查扩展签名，对你所有 Adobe 扩展都生效。移除后台连接时，本软件改过的值会恢复原样，原来就开着的不动。

## 局限

- 中文只写在正在运行的 AE 里，不改插件文件。AE 重启后会恢复原名，需要打开本软件连接 AE 重新应用（效果名称可以自动重新应用）。
- 参数名称每次都要在参数页读取后应用，不会自动重新应用。
- 下拉菜单选项和插件自绘的界面无法汉化。
- 名称有长度上限：参数名称最多约 10 个汉字，效果名称约 15 个汉字。
- “效果和预设”列表要手动刷新才显示新名称；图层上的效果标题会随工程保存。

## 遇到问题时

出错提示会尽量写明原因，并在末尾给出诊断文件的路径。诊断文件只记录版本、数量、状态和插件名称，不含工程内容或个人信息，可以在 Issues 里附上：

- **自动适配自检失败**（例如“完整效果目录向量未找到唯一证据”）：`<软件目录>\ae-bridge\adapter-qa\<编号>\catalog-diagnostic.json`。
- **AE 参数操作超时**：`<软件目录>\ae-bridge\<编号>\timeout-diagnostic.json`（装了后台连接时在 `%APPDATA%\AE-Localizer\bridge\<编号>\`）。提示会说明是 AE 根本没执行脚本、执行了没完成，还是 AE 与本软件的管理员权限不同。

- **译名含系统编码无法表示的字符**（AE 2025 等按系统编码保存名称的版本，中文系统为 GBK，没有 ™ ® © • ² 等字符）：这些译名会被跳过并写明是哪一项，诊断在 `%LOCALAPPDATA%\AE名称汉化验证\diagnostics\name-encoding-*.json`。

- **提示 AE 的效果模块（FLT.dll）与已验证的版本不同**：说明你的 AE 版本号虽然在支持列表里，但文件和原版不同，一般是被补丁、精简版或重新打包修改过。可以右键 `Support Files\FLT.dll` → 属性 → 数字签名，看有没有有效的 Adobe Inc. 签名。在“AE 版本”页做一次“自动识别与适配”，自检通过后就能在这台电脑上使用。

软件可以放在路径带空格的文件夹里。AE 不会执行从带空格路径转交的脚本，软件会自动改用不含空格的临时位置。

## 隐私

- 翻译时只把所选效果 / 参数的名称、内部标识和分类发给你配置的翻译服务，不发送工程、画面或参数值。
- API Key 默认只在本次运行中使用；勾选后用 Windows 当前用户加密保存。
- 检查更新只读取本仓库 Releases 中的 `update.json`（连不上时改读 GitHub API 上同一个 Release 的版本信息），不发送本机或工程信息，可在设置中关闭。依次尝试系统代理、自动代理脚本（PAC）和直连；失败时会写明原因。证书除了 Windows 系统根证书，还信任随软件附带的 Mozilla 根证书（certifi），校验依然严格。

## 从源码构建

需要 Python 3.12（Windows x64）。原生模块用 pip 安装的 Zig 编译，不需要另装编译器。编译时固定按通用 x86-64 指令集（`-mcpu=baseline`），不随编译机器的 CPU 变化。`make_installer.py` 打包前会用 `cpu_check.py` 检查一遍，模块里有新指令（GFNI、AVX 等）就拒绝打包。1.2.0 以前的模块按开发机 CPU 编译，在 AMD Zen 1～3 和较早的 Intel CPU 上会让自检卡住或 AE 崩溃。

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe build.py
```

AE 后台连接的扩展需要签名后才打进包里：把 Adobe 官方的 [ZXPSignCmd](https://github.com/Adobe-CEP/CEP-Resources/tree/master/ZXPSignCMD) 放到 `build/tools/zxpsign/ZXPSignCmd.exe`（或设环境变量 `ZXPSIGNCMD`），`build.py` 会在 `build/signing` 生成本机自签名证书并签名（证书不要提交）。没有 ZXPSignCmd 时照常构建，只是不带这个扩展。

运行测试（先运行一次 `build.py` 或 `build.py --native-only` 编译原生模块，个别测试需要它；装了 Node.js 时还会用模拟的 CEP 环境检查扩展脚本）：

```bash
.venv\Scripts\python.exe run_tests.py
```

每个测试模块用独立进程并行运行，全部约 1 分钟，测试期间不联网。只跑某几个模块：`run_tests.py test_updater test_gui`。

打安装包需要 [Inno Setup 6](https://jrsoftware.org/isinfo.php)：

```bash
.venv\Scripts\python.exe make_installer.py
```

## 致谢

AE 效果名称实时汉化的可行性最早由「帧绘剪工作室」验证跑通。本软件在此基础上独立实现，谨此致谢。

## 许可证

[MIT](LICENSE)
