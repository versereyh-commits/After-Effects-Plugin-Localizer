"""What is new in each version: shown once when a new version is first opened, offered again from the settings
page, and used by publish_update.py as the release text. Newest first; add an entry with every version."""
from __future__ import annotations
from updater import parse_version

NOTES:list[tuple[str,list[str]]]=[
    ('1.1.5',[
        '修复翻译大量效果时报“译文校验失败：返回了重复或未知标识”的问题：发给翻译模型的不再是插件内部标识（模型会把 CS Composite 之类看着像笔误的标识“纠正”掉），改为每批的序号。',
        '自动适配自检创建的 QA 工程改用软件渲染，测试效果优先用 AE 自带的高斯模糊，避免部分显卡驱动在自检时让 AE 崩溃。',
        '参数辅助模块核对 AE 名称编码时更加稳妥：遇到字符串格式不同的 AE 版本会报“不支持”，不再可能让 AE 崩溃。',
        '修复按系统编码保存名称的 AE 版本（如 AE 2025）应用方案时，只要有一个译名含 ™ ® © • ² 等系统编码无法表示的字符，就整批报“此 AE 的旧式编码无法表示该名称”的问题：现在只跳过这几项并写明是哪个效果、哪个字符，其余照常应用，同时生成诊断文件。',
        '修复同一插件装了两份（例如 AE 的 Plug-ins 和 MediaCore 公共插件目录里各一份）时，自动适配自检报“宿主目录不能作为唯一完整对照”的问题：完全相同的重复项按一份对比；名称不同的重复项会被列出并生成诊断文件。',
    ]),
    ('1.1.4',[
        '“AE 参数操作超时”的提示会写明 AE 停在哪一步（如加载参数辅助模块、核对 AE 模块），并给出这一步常见的原因，例如安全软件拦截了辅助模块的加载。',
    ]),
    ('1.1.3',[
        '修复软件放在带空格的文件夹（例如“E:\\ae O.o  2026”）时，没装后台连接就读取或应用参数一直“AE 参数操作超时”的问题：AE 不会执行从带空格路径转交的脚本，现在会改用不含空格的路径。',
        '自动适配自检在“完整效果目录向量未找到唯一证据”处失败时，提示会写明哪里不一致，并在本次自检目录生成诊断文件 catalog-diagnostic.json（只含插件名称和数量），可发给开发者排查。',
        '插件名称以系统编码保存时（例如汉化版 BCC），列表里显示正确的中文，不再是乱码。',
        '读取或应用参数时出现“AE 参数操作超时”，提示会写明 AE 是否开始执行了脚本、AE 与本程序的管理员权限是否不同，并生成诊断文件 timeout-diagnostic.json，可发给开发者排查。',
    ]),
    ('1.1.2',[
        '修复装有内部标识带中文的插件时，“连接 AE”报 ascii 编码无法解码的问题；参数页跳过这类效果或参数，不再整页报错。',
        '修复装有汉化版 BCC 等名称编码特殊的插件时，自动适配自检报“完整效果目录向量未找到唯一证据”的问题；这类名称按 AE 自己的显示方式读取和比对，仍逐字节严格核对，恢复时原样写回。',
    ]),
    ('1.1.1',[
        '修复部分电脑上“检查更新”无法连接的问题：依次尝试系统代理、自动代理脚本（PAC）和直连，仍连不上时改从 GitHub API 读取版本信息。',
        '修复系统缺少 GitHub 所用根证书时报“证书校验失败”的问题：除 Windows 系统证书外，还信任随软件附带的 Mozilla 根证书，校验仍然严格。',
        '检查更新失败时写明具体原因（如域名解析失败、连接超时、代理连不上），便于排查网络问题。',
    ]),
    ('1.1',[
        '无感读写：在“设置与日志 → 适配范围”点“安装后台连接”并重启一次 AE，之后读取和应用参数时不再启动第二个 AE 进程，AE 窗口和焦点保持不变。安装时会开启 Adobe 扩展调试模式，移除时恢复原样。',
        '没装后台连接时，读写过程中 AE 的最大化 / 最小化窗口也会立即恢复原样，不再被切成小窗。',
        '允许多个效果使用同一个中文名称（例如 BCC 转场的两份插件，或与中文版 AE 自带效果同名），只在运行日志里提示，不再整批拒绝。',
        '软件更新下载支持断点续传：网络中断时自动重试并接着下载；仍失败时可在提示里打开下载页面，用浏览器下载。',
        '翻译 token 用量改用 k（千）为单位，窄窗口下统计卡片不再截断数字。',
        '首次打开新版本时显示一次更新内容，也可在“设置与日志 → 软件更新”中再次查看。',
    ]),
    ('1.0',[
        '程序更名为“AE汉化工具”，从测试版转为正式版，并在 GitHub 开源（MIT 许可证）。',
        '软件更新：启动时每天最多检查一次新版本，发现后可一键下载、核对并安装；可在“设置与日志 → 软件更新”关闭。',
        '深色模式：在“设置与日志 → 界面”中开启，切换时带渐变动画。',
        'AE 重启后自动重新应用上次的效果名称。',
        '双语写入（“英文原名 (中文)”）、翻译 token 用量统计、多批并行翻译、参数名称专用提示词。',
    ]),
]


def newer(seen:str|None,current:str)->list[tuple[str,list[str]]]:
    """The entries to show for `current` when `seen` was the last version opened: every version after `seen` up
    to `current`, newest first; just `current` when nothing was seen; none when `current` has been seen."""
    try:now=parse_version(current)
    except ValueError:return []
    try:last=parse_version(seen) if seen else None
    except ValueError:last=None
    if last is not None and last>=now:return []
    entries=[]
    for version,items in NOTES:
        try:value=parse_version(version)
        except ValueError:continue
        if value<=now and (value==now if last is None else value>last):entries.append((version,items))
    return entries

def html(entries:list[tuple[str,list[str]]])->str:
    """The same for the in-app notice: a list whose wrapped lines stay under their bullet, items a little apart."""
    from html import escape
    blocks=[]
    for version,items in entries:
        heading=f'<p style="margin:10px 0 2px 0;font-weight:600">{escape(version)}</p>' if len(entries)>1 else ''
        rows=''.join(f'<li style="margin-bottom:6px">{escape(item)}</li>' for item in items)
        blocks.append(f'{heading}<ul style="margin:0 0 0 -24px">{rows}</ul>')
    return ''.join(blocks)

def text(entries:list[tuple[str,list[str]]],headings:bool|None=None)->str:
    """Plain text for a dialog or a release; version headings only when there is more than one entry."""
    headings=len(entries)>1 if headings is None else headings
    blocks=[]
    for version,items in entries:
        lines=[f'· {item}' for item in items]
        blocks.append('\n'.join(([version] if headings else [])+lines))
    return '\n\n'.join(blocks)
