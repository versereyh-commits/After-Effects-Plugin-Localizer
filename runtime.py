from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime


def asset_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def data_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))/"AE名称汉化验证"
    root.mkdir(parents=True, exist_ok=True)
    return root


def short_path(path: Path) -> str:
    """The 8.3 form of an existing path, or the path itself when Windows has none (8.3 names off on that drive)."""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)):return buffer.value
    except (AttributeError, OSError):pass
    return str(path)


def relay_script(folder: Path) -> Path:
    """Where to write a script handed to AfterFX.exe -r for the request in `folder`.

    AE silently runs nothing when that path has a space (AE 2024 checked: one space is enough; the exit code
    is the same either way), and users unpack the program anywhere, e.g. "E:\\ae O.o  2026". So: the request
    folder itself when its path, or its 8.3 form, has no space; otherwise <requestId>.jsx in the first writable
    folder without one (this program's data folder, temp, Public, ProgramData). Only the script moves; the
    output and permit stay in the request folder (paths inside the script are JSON strings, spaces are fine)."""
    for base in (folder, Path(short_path(folder))):
        if " " not in str(base):return base/"request.jsx"
    candidates = [lambda: data_root()/"relay"]
    candidates += [lambda name=name: Path(os.environ[name])/"AE-Localizer"/"relay" for name in ("TEMP", "PUBLIC", "ProgramData") if os.environ.get(name)]
    for candidate in candidates:
        try:
            base = candidate();base.mkdir(parents=True, exist_ok=True)
        except OSError:continue
        for form in (str(base), short_path(base)):
            if " " not in form:return Path(form)/f"{folder.name}.jsx"
    raise RuntimeError("软件所在路径含空格，AE 不会执行从这里转交的脚本，而且找不到不含空格的可写目录。请把软件放到不含空格的文件夹，或在“设置与日志 → 适配范围”安装后台连接后重试")


def write_json_atomic(path, value, *, ensure_ascii=False, indent=2):
    """Writes JSON beside the target under a per-process name, then os.replace: a crash or a full disk never leaves
    a half-written file, and a failed write removes its temporary file. Every saved JSON file goes through here."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=ensure_ascii, indent=indent), encoding="utf8")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


LOCALE_HINT="若 Windows 系统区域不是中文，请在“设置 → 时间和语言 → 语言和区域 → 管理语言设置 → 更改系统区域设置”中选择“中文(简体，中国)”并重启电脑后再试"


def legacy_text_problem(texts, encoding="mbcs") -> str | None:
    """None when every text fits the Windows legacy code page used by mbcs AE builds."""
    try:
        for text in texts:text.encode(encoding)
    except UnicodeEncodeError:
        return f"当前 Windows 系统编码（代码页 {ctypes.windll.kernel32.GetACP()}）无法表示中文，此 AE 版本按系统旧式编码保存名称，验证未写入任何内容。{LOCALE_HINT}。"
    return None


def native_probe() -> dict:
    dll = asset_root()/"native"/"ae_probe.dll"
    if not dll.exists():
        return {"error":"原生诊断模块不存在", "runtimeRenameEnabled":False}
    library = ctypes.CDLL(str(dll))
    library.ae_probe_json.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
    library.ae_probe_json.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(1024*1024)
    code = library.ae_probe_json(buffer, len(buffer))
    if code < 0:
        raise RuntimeError("原生模块的输出缓冲区不足")
    return json.loads(buffer.value.decode("utf-8"))


def file_version(exe: Path) -> str:
    library=ctypes.CDLL(str(asset_root()/"native"/"ae_probe.dll"))
    library.ae_file_version_json.argtypes=[ctypes.c_wchar_p,ctypes.c_char_p,ctypes.c_uint32]
    library.ae_file_version_json.restype=ctypes.c_int
    buffer=ctypes.create_string_buffer(128)
    if library.ae_file_version_json(str(exe),buffer,len(buffer))<0:
        raise RuntimeError("无法读取 AE 文件版本")
    return json.loads(buffer.value.decode("utf-8")) or "unknown"


def run_catalog_probe(exe: Path, mutate: bool = False, timeout: int = 180, folder: Path | None = None) -> dict:
    if not exe.is_file():
        raise ValueError("找不到 AfterFX.exe")
    if file_version(exe).split(".")[:2] != ["26","0"]:
        raise ValueError("第一阶段仅支持实际文件版本为 26.0 的 AE")
    # Validate the actual running process; never send commands to a different AE version.
    native = native_probe()
    running = native.get("processes", [])
    if len(running)>1:
        raise RuntimeError("检测到多个 AE 进程。请仅保留一个 AE 2026 进程再验证。")
    expected = str(exe.resolve()).casefold()
    if running and any(str(x.get("imagePath", "")).casefold() != expected for x in running):
        raise RuntimeError("另一个版本的 AE 正在运行。请关闭其他版本，仅保留 AE 2026。")
    folder = folder or data_root()/"reports"/datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True, exist_ok=True)
    output = folder/"catalog-result.json"
    script = folder/"catalog-probe.jsx"
    template = (asset_root()/"scripts"/"catalog_probe.jsx").read_text(encoding="utf-8")
    script.write_text(template.replace("@@OUTPUT@@",json.dumps(output.as_posix(),ensure_ascii=True)).replace("@@MUTATE@@", "true" if mutate else "false"),encoding="utf-8")
    with (folder/"launcher.log").open("w",encoding="utf-8") as log:
        subprocess.Popen([str(exe),"-r",str(script)],stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if output.exists():
            try:
                result = json.loads(output.read_text(encoding="utf-8-sig"))
                result["reportPath"] = str(output)
                result["nativeProbe"] = native
                (folder/"report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
                return result
            except (ValueError,OSError):
                pass
        time.sleep(0.25)
    raise TimeoutError("AE 在限定时间内没有返回结果。请查看 AE 是否被启动提示阻挡；程序不会自动更改其安全设置。报告目录："+str(folder))


def rename_gate(report: dict | None) -> tuple[bool,str]:
    # There is deliberately no unverified runtime writer in this build.
    if report and report.get("hostChangeObserved"):
        return False,"目录探针观察到变化，但原生列表、搜索、渲染和恢复尚未完成验收，不能启用实时更名。"
    return False,"尚无经过验证的 AE 26.0 全局实时更名适配器；本版本不会写入插件或 AE 内存。"
