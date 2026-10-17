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
def sign_bridge():
    """The background CEP extension (cep/ plus scripts/parameter_bridge.jsx), signed for AE with Adobe's ZXPSignCmd
    (set ZXPSIGNCMD, or put it at build/tools/zxpsign/ZXPSignCmd.exe) and a self-signed certificate kept in
    build/signing (generated on first use, never committed or shipped). Re-signed only when its content changes,
    so an installed copy stays current across builds. Returns the folder to bundle, or None without the tool."""
    import hashlib,secrets,zipfile
    ext_id="com.verse.aelocalizer.bridge";signed=root/"build"/"cep"/"signed"
    sources=[root/"cep"/"CSXS"/"manifest.xml",root/"cep"/"index.html",root/"cep"/"main.js",root/"scripts"/"parameter_bridge.jsx"]
    bridge_id=hashlib.sha256(b"".join(p.read_bytes() for p in sources)).hexdigest()[:16]
    if (signed/"bridge.id").is_file() and (signed/"bridge.id").read_text(encoding="ascii")==bridge_id and (signed/ext_id/"META-INF"/"signatures.xml").is_file():return signed
    import os
    tool=Path(os.environ.get("ZXPSIGNCMD") or root/"build"/"tools"/"zxpsign"/"ZXPSignCmd.exe")
    if not tool.is_file():print("未找到 ZXPSignCmd，跳过后台连接扩展（软件仍用命令行转交）");return None
    keys=root/"build"/"signing";keys.mkdir(parents=True,exist_ok=True);cert=keys/"cep-selfsigned.p12";secret=keys/"cep-selfsigned.key"
    if not cert.is_file():
        secret.write_text(secrets.token_urlsafe(24),encoding="ascii")
        subprocess.run([str(tool),"-selfSignedCert","CN","Beijing","verse","AE Localizer Bridge",secret.read_text(encoding="ascii"),str(cert),"-validityDays","36500"],check=True,stdout=subprocess.DEVNULL)
    stage=root/"build"/"cep"/"stage"/ext_id;shutil.rmtree(stage.parent,ignore_errors=True);(stage/"CSXS").mkdir(parents=True)
    shutil.copy2(sources[0],stage/"CSXS"/"manifest.xml");shutil.copy2(sources[1],stage/"index.html");shutil.copy2(sources[3],stage/"parameter_bridge.jsx")
    (stage/"main.js").write_text(sources[2].read_text(encoding="utf-8").replace("@@BRIDGE_ID@@",bridge_id),encoding="utf-8")
    package=root/"build"/"cep"/"bridge.zxp";package.unlink(missing_ok=True)
    subprocess.run([str(tool),"-sign",str(stage),str(package),str(cert),secret.read_text(encoding="ascii")],check=True,stdout=subprocess.DEVNULL)
    subprocess.run([str(tool),"-verify",str(package)],check=True,stdout=subprocess.DEVNULL)
    shutil.rmtree(signed,ignore_errors=True);(signed/ext_id).mkdir(parents=True)
    with zipfile.ZipFile(package) as archive:archive.extractall(signed/ext_id)
    (signed/"bridge.id").write_text(bridge_id,encoding="ascii")
    return signed

if "--native-only" not in sys.argv:
    bridge=sign_bridge()
    subprocess.run([sys.executable,"-c","import comtypes.client; comtypes.client.GetModule(r'C:\\Windows\\System32\\UIAutomationCore.dll')"],check=True,cwd=root)
    # PyInstaller resolves _ssl/_hashlib dependencies along PATH. Git's mingw64
    # or Poppler's bin then supply foreign same-named OpenSSL/ICU/UCRT DLLs, so
    # the bundle changed with the shell. Pin Python's own DLLs and Windows only.
    import os
    system=Path(os.environ.get("SystemRoot",r"C:\Windows"))
    clean_path=os.pathsep.join(str(p) for p in (Path(sys.base_prefix)/"DLLs",Path(sys.base_prefix),Path(sys.executable).parent,system/"System32",system,system/"System32"/"Wbem"))
    subprocess.run([sys.executable,"-m","PyInstaller","--noconfirm","--clean","--windowed","--onedir","--distpath",str(dist),"--name","AE汉化工具","--add-data",str(root/"scripts")+";scripts",*(["--add-data",str(bridge)+";cep_signed"] if bridge else []),"--add-binary",str(native_dll)+";native","--add-binary",str(scan_dll)+";native","--add-binary",str(parameter_dll)+";native","--add-binary",str(dynamic_dll)+";native","--add-binary",str(legacy_parameter)+";native","--add-binary",str(legacy_dynamic)+";native","--hidden-import","comtypes.gen.UIAutomationClient","--exclude-module","PySide6.QtWebEngineCore","--exclude-module","PySide6.QtWebEngineWidgets","--exclude-module","PySide6.QtQml",str(root/"app_live.py")],check=True,cwd=root,env={**os.environ,"PATH":clean_path})
    # Qt 6.12 on Windows 11 uses the OS ICU shim. PyInstaller picked Poppler's
    # same-named versioned ICU from the build host PATH; it has different exports.
    for name in ("icuuc.dll","icuin.dll","icudt.dll"):
        copied=dist/"AE汉化工具"/"_internal"/name
        if copied.exists():copied.unlink()
