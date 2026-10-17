"""What is new in each version: shown once when a new version is first opened, offered again from the settings
page, and used by publish_update.py as the release text. Newest first; add an entry with every version."""
from __future__ import annotations
from updater import parse_version

NOTES:list[tuple[str,list[str]]]=[
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
