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
- 深色模式、可自定义快捷键。

## 支持的 AE 版本

实时写入只对已验证的 AE 构建开放：

| AE 版本 | 构建号 |
|---|---|
| After Effects 2026 | 26.0.0.67 |
| After Effects 2025 | 25.3.0.71 |
| After Effects 2021 | 18.2.1.8 |

其他构建可以扫描、翻译和保存方案，但要先在软件的“AE 版本”页做“自动识别与适配”。只有完整验证通过后才会开放实时写入，检测到版本号不代表已适配。

## 安装

在 [Releases](../../releases) 下载 `ae-localizer-setup-<版本>.exe`（安装包）。装好后软件会在启动时检查这里的新版本，有更新时提示一键下载安装。

- 安装到当前用户目录，不需要管理员权限。
- 需要 Windows 10 / 11（64 位）。

## 局限

- 中文只写在正在运行的 AE 里，不改插件文件。AE 重启后会恢复原名，需要打开本软件连接 AE 重新应用（效果名称可以自动重新应用）。
- 参数名称每次都要在参数页读取后应用，不会自动重新应用。
- 下拉菜单选项和插件自绘的界面无法汉化。
- 名称有长度上限：参数名称最多约 10 个汉字，效果名称约 15 个汉字。
- “效果和预设”列表要手动刷新才显示新名称；图层上的效果标题会随工程保存。

## 隐私

- 翻译时只把所选效果 / 参数的名称、内部标识和分类发给你配置的翻译服务，不发送工程、画面或参数值。
- API Key 默认只在本次运行中使用；勾选后用 Windows 当前用户加密保存。
- 检查更新只读取本仓库 Releases 中的 `update.json`，不发送本机或工程信息，可在设置中关闭。

## 从源码构建

需要 Python 3.12（Windows x64）。原生模块用 pip 安装的 Zig 编译，不需要另装编译器。

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe build.py
```

运行测试（先运行一次 `build.py` 或 `build.py --native-only` 编译原生模块，个别测试需要它）：

```bash
.venv\Scripts\python.exe -m unittest discover -s tests
```

打安装包需要 [Inno Setup 6](https://jrsoftware.org/isinfo.php)：

```bash
.venv\Scripts\python.exe make_installer.py
```

## 致谢

AE 效果名称实时汉化的可行性最早由「帧绘剪工作室」验证跑通。本软件在此基础上独立实现，谨此致谢。

## 许可证

[MIT](LICENSE)
