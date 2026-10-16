"""Build in the isolated environment; no global compiler installation."""
from pathlib import Path
import subprocess
import sys

root=Path(__file__).parent
# --dist-dir lets a new build land beside a copy that is still running (its files are locked).
dist=root/(sys.argv[sys.argv.index("--dist-dir")+1] if "--dist-dir" in sys.argv else "dist")
native_source=root/"native"/"ae_probe.c";native_dll=root/"native"/"ae_probe.dll"
parameter_source=root/"native"/"ae_parameters.c";parameter_dll=root/"native"/"ae_parameters_v8.dll"
dynamic_dll=root/"native"/"ae_parameters_v9.dll"
scan_source=root/"native"/"ae_scan.c";scan_dll=root/"native"/"ae_scan_v1.dll"
if not scan_dll.exists() or scan_dll.stat().st_mtime<scan_source.stat().st_mtime:
    subprocess.run([sys.executable,"-m","ziglang","cc","-shared","-O3","-std=c11",str(scan_source),"-o",str(scan_dll)],check=True,cwd=root)
if not dynamic_dll.exists() or dynamic_dll.stat().st_mtime<parameter_source.stat().st_mtime:
    subprocess.run([sys.executable,"-m","ziglang","cc","-shared","-O2","-std=c11","-DAE_DYNAMIC=1",str(parameter_source),"-o",str(dynamic_dll),"-lbcrypt"],check=True,cwd=root)
if not parameter_dll.exists() or parameter_dll.stat().st_mtime<parameter_source.stat().st_mtime:
    subprocess.run([sys.executable,"-m","ziglang","cc","-shared","-O2","-std=c11",str(parameter_source),"-o",str(parameter_dll),"-lbcrypt"],check=True,cwd=root)
# Retain the preceding helpers for existing portable sessions. Never rewrite
# an old DLL that AE may still have loaded; current calls use v8/v9.
import shutil
legacy_parameter=root/'native'/'ae_parameters_v6.dll';legacy_dynamic=root/'native'/'ae_parameters_v7.dll'
for current,legacy in ((parameter_dll,legacy_parameter),(dynamic_dll,legacy_dynamic)):
    if not legacy.exists():shutil.copy2(current,legacy)
if not native_dll.exists() or native_dll.stat().st_mtime<native_source.stat().st_mtime or "--native-only" in sys.argv:
    subprocess.run([sys.executable,"-m","ziglang","cc","-shared","-O2","-std=c11",str(native_source),"-o",str(native_dll),"-lpsapi","-lversion"],check=True,cwd=root)
if "--native-only" not in sys.argv:
    subprocess.run([sys.executable,"-c","import comtypes.client; comtypes.client.GetModule(r'C:\\Windows\\System32\\UIAutomationCore.dll')"],check=True,cwd=root)
    # PyInstaller resolves _ssl/_hashlib dependencies along PATH. Git's mingw64
    # or Poppler's bin then supply foreign same-named OpenSSL/ICU/UCRT DLLs, so
    # the bundle changed with the shell. Pin Python's own DLLs and Windows only.
    import os
    system=Path(os.environ.get("SystemRoot",r"C:\Windows"))
    clean_path=os.pathsep.join(str(p) for p in (Path(sys.base_prefix)/"DLLs",Path(sys.base_prefix),Path(sys.executable).parent,system/"System32",system,system/"System32"/"Wbem"))
    subprocess.run([sys.executable,"-m","PyInstaller","--noconfirm","--clean","--windowed","--onedir","--distpath",str(dist),"--name","AE汉化工具","--add-data",str(root/"scripts")+";scripts","--add-binary",str(native_dll)+";native","--add-binary",str(scan_dll)+";native","--add-binary",str(parameter_dll)+";native","--add-binary",str(dynamic_dll)+";native","--add-binary",str(legacy_parameter)+";native","--add-binary",str(legacy_dynamic)+";native","--hidden-import","comtypes.gen.UIAutomationClient","--exclude-module","PySide6.QtWebEngineCore","--exclude-module","PySide6.QtWebEngineWidgets","--exclude-module","PySide6.QtQml",str(root/"app_live.py")],check=True,cwd=root,env={**os.environ,"PATH":clean_path})
    # Qt 6.12 on Windows 11 uses the OS ICU shim. PyInstaller picked Poppler's
    # same-named versioned ICU from the build host PATH; it has different exports.
    for name in ("icuuc.dll","icuin.dll","icudt.dll"):
        copied=dist/"AE汉化工具"/"_internal"/name
        if copied.exists():copied.unlink()
