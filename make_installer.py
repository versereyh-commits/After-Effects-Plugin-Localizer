"""Builds the Windows installer from a fresh build, carrying nothing from this computer.

python make_installer.py [--skip-build]

Steps: a clean PyInstaller build into build/installer, a scan of every file for local paths
or user names, a short user guide and third-party notices, an icon drawn from the app's logo,
then Inno Setup 6 (ISCC.exe) writes release/AE汉化工具_<version>_安装包.exe.
The installer is per user (no administrator rights): the app keeps its ae-bridge exchange
folder beside the EXE, so it must live somewhere the user can write."""
from pathlib import Path
from importlib.metadata import distribution
import hashlib,json,os,re,shutil,subprocess,sys

root=Path(__file__).resolve().parent
stage=root/'build'/'installer'
app=stage/'AE汉化工具'
release=root/'release'
NAME='AE汉化工具'
OLD_NAMES=('AE插件名称汉化','AE 插件名称汉化')    # EXE and shortcut names before the rename

def version()->str:
    match=re.search(r"APP_VERSION\s*=\s*'([^']+)'",(root/'app_live.py').read_text(encoding='utf8'))
    if not match:raise RuntimeError('app_live.py 中找不到 APP_VERSION')
    return match.group(1)

def iscc()->Path:
    places=[(os.environ.get('LOCALAPPDATA'),'Programs'),(os.environ.get('ProgramFiles(x86)'),''),(os.environ.get('ProgramFiles'),'')]
    for base,sub in places:
        path=Path(base or '.')/sub/'Inno Setup 6'/'ISCC.exe'
        if base and path.is_file():return path
    found=shutil.which('iscc')
    if found:return Path(found)
    raise RuntimeError('没有找到 Inno Setup 6（ISCC.exe），请先安装：winget install JRSoftware.InnoSetup')

# Anything that would identify this computer or its user must not reach the package.
def private_patterns():
    words={os.environ.get('USERNAME',''),os.environ.get('COMPUTERNAME',''),str(root),str(Path.home())}
    found=[]
    for word in sorted(w for w in words if w and len(w)>=2):
        for encoding in ('utf-8','utf-16-le'):
            found.append(re.compile(re.escape(word.encode(encoding)),re.IGNORECASE))
    # The user name alone also appears inside generic words; only path-shaped uses count.
    user=os.environ.get('USERNAME','')
    if user:
        found=[p for p in found if p.pattern not in (re.escape(user.encode('utf-8')),re.escape(user.encode('utf-16-le')))]
        for sep in ('\\','/'):
            for encoding in ('utf-8','utf-16-le'):found.append(re.compile(re.escape(('Users'+sep+user).encode(encoding)),re.IGNORECASE))
    return found

def scan(folder:Path):
    patterns=private_patterns();hits=[]
    for path in folder.rglob('*'):
        if path.is_file():
            data=path.read_bytes()
            if any(p.search(data) for p in patterns):hits.append(path.relative_to(folder))
    if hits:raise RuntimeError('安装包内容含本机信息，已停止：'+'、'.join(map(str,hits[:10])))

def check_cpu(folder:Path):
    """Our native helpers must run on any x86-64 CPU: a build for the build machine's own CPU once shipped GFNI/AVX2
    code that crashed AE on AMD Zen 1-3 and older Intel CPUs."""
    from cpu_check import extended_instructions
    bad={p.name:found for p in (folder/'_internal'/'native').glob('*.dll') if (found:=extended_instructions(p))}
    if bad:raise RuntimeError('原生模块含部分 CPU 不支持的指令，已停止：'+'；'.join(f'{name} {found}' for name,found in bad.items()))

GUIDE='''AE汉化工具 {version} 使用说明

这是什么
  在 Adobe After Effects 运行时，把“效果和预设”中的效果名称、以及“效果控件”里的参数名称实时显示为中文。
  数值仍用 AE 原有控件调节；软件不修改插件文件、工程内容或任何数值。

开始使用
  1. 先打开 After Effects。
  2. 打开本软件，在左下角选择你的 AE 版本，点“连接 AE”。
  3. 在“效果名称”页选中或勾选效果，在右侧详情里点“翻译所选项 / 翻译勾选项”（需要先在“设置与日志”里
     配置翻译服务和自己的 API Key），确认中文后点“应用到 AE”。有勾选时只处理勾选项，没有勾选时处理所选行。
     完成后在 AE 的“效果和预设”面板菜单里点“刷新列表”。
  4. 参数名称在“参数名称”页读取、翻译并应用。读取范围默认是 AE 当前合成里选中的图层，也可以改为整个工程；
     每次启动 AE 后的第一次读取较慢（需要识别结构），之后切换图层或范围通常一秒左右。
  5. “翻译方案”页可以保存、切换、编辑和导入 / 导出整套汉化。

支持的 AE 版本
  只有经过验证的具体构建可以实时写入。其他版本可以扫描、翻译和保存方案；
  也可以在“AE 版本”页用“自动识别与适配…”在本机完成自检和核对后启用。

恢复原名
  “效果名称”页“恢复 ▾ → 一键恢复全部默认（含参数）”把本软件改过的名称全部恢复。
  “设置与日志”里的“临时翻译”默认关闭：关闭软件后中文名称留在 AE 中，直到 AE 退出；开启后，关闭软件时自动恢复原名。
  AE 每次启动都会从插件文件读回原名（本软件不改插件文件）。打开本软件并连接 AE 后，会自动把上次应用的效果名称写回；
  参数名称需要在“参数名称”页重新应用。此功能可在“设置与日志 → 退出与恢复”关闭。

深色模式
  “设置与日志 → 界面”里的“深色模式”默认关闭，勾选后整个窗口以短暂渐变换成深色（关闭界面动效时直接切换），下次启动保持。

AE后台连接（可选）
  读取或应用参数名称时，软件要把一段脚本交给 AE 执行。默认方式会让最大化的 AE 窗口被还原一下（软件会立即恢复）。
  在“设置与日志 → 适配范围 → AE后台连接”点“安装后台连接”，再重启一次 AE：之后读写都在 AE 后台进行，不再启动第二个
  AE 进程，AE 窗口和焦点保持不变。扩展装在你的用户目录（%APPDATA%\\Adobe\\CEP\\extensions），不需要管理员权限。
  安装时会同时开启 Adobe 扩展调试模式（让 AE 一定加载这个扩展；调试模式下 AE 不检查扩展签名，对所有 Adobe 扩展生效）。
  可随时在同一处移除，移除时本软件开启的调试模式会恢复原样，原来就开着的不动。

软件更新
  启动时每天最多联网检查一次新版本（只读取 GitHub 公开发布页，不发送本机或工程信息），可在“设置与日志 → 软件更新”关闭，
  也可以随时点“检查更新”。发现新版本时左侧出现“有新版本”，点开可看更新说明；下载后核对文件大小和 SHA256，
  再经你确认才会关闭本软件并打开安装向导。
  网络不稳定时会自动重试并从中断处接着下载，没下完的部分会保留，下次再点下载时接着下载。仍然下载失败时，
  可以在提示里点“打开下载页面”，用浏览器或下载工具下载安装包，双击运行即可安装。

数据与隐私
  设置、中文候选、方案和恢复记录保存在 %LOCALAPPDATA%\\AE名称汉化验证 。
  API Key 默认只在本次运行中使用；勾选“加密保存”后用当前 Windows 用户的 DPAPI 加密保存。
  翻译只把效果 / 参数名称、内部标识和分类发送给你选择的翻译服务。
  卸载本软件不会删除上述数据目录；如需清除，请手动删除该文件夹。

建议先在不重要的工程中试用，并保留工程备份。
'''

def notices(folder:Path):
    folder.mkdir(parents=True,exist_ok=True);site=root/'.venv'/'Lib'/'site-packages'
    for package in ('pyside6_essentials-6.12.0','shiboken6-6.12.0','pyinstaller-6.22.3','comtypes-1.4.17','certifi-2026.7.22'):
        source=site/(package+'.dist-info')/'licenses'
        for file in (source.rglob('*') if source.is_dir() else []):
            if file.is_file():
                target=folder/package/file.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
    for package in ('pefile','capstone'):
        installed=distribution(package)
        for entry in installed.files or []:
            if Path(entry).name.upper().startswith(('LICENSE','COPYING')):
                target=folder/package/Path(entry).name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(installed.locate_file(entry),target)
    (folder/'组件来源.txt').write_text('PySide6 / Shiboken 6.12.0 (LGPL-3.0): https://doc.qt.io/qtforpython-6/\nPyInstaller 6.22.3 (GPL-2.0 with bootloader exception): https://pyinstaller.org/\n'
        'comtypes 1.4.17 (MIT): https://github.com/enthought/comtypes\npefile (MIT): https://github.com/erocarrera/pefile\nCapstone (BSD): https://github.com/capstone-engine/capstone\n'
        'Windows 系统 DLL 不在本安装包的再分发文件内。原生模块由 Zig 构建，编译器不随包分发。\n',encoding='utf-8')

def icon(path:Path):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(root))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage
    app_=QApplication.instance() or QApplication([])
    import ui_kit,struct,io
    from PySide6.QtCore import QBuffer,QByteArray,QIODevice
    images=[]
    for size in (16,24,32,48,64,128,256):
        data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly);ui_kit.logo(size).toImage().save(buffer,'PNG');images.append((size,bytes(data)))
    # An .ico is a directory of PNG images; written by hand so no image plugin is needed.
    head=struct.pack('<HHH',0,1,len(images));offset=6+16*len(images);entries=b'';body=b''
    for size,png in images:
        entries+=struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(png),offset+len(body));body+=png
    path.write_bytes(head+entries+body)

MESSAGES='''[Messages]
SetupAppTitle=安装
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载
UninstallAppFullTitle=卸载 %1
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&R)...
ButtonNewFolder=新建文件夹(&M)
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。现在退出将不会安装本软件。%n%n确定要退出安装吗？
WelcomeLabel1=欢迎安装 [name]
WelcomeLabel2=将在你的电脑上安装 [name/ver]。%n%n建议先关闭其他应用程序再继续。
WizardSelectDir=选择安装位置
SelectDirDesc=要把 [name] 安装到哪里？
SelectDirLabel3=安装程序将把 [name] 安装到下面的文件夹。
SelectDirBrowseLabel=点“下一步”继续。如需选择其他文件夹，请点“浏览”。
DiskSpaceMBLabel=至少需要 [mb] MB 可用空间。
WizardSelectTasks=选择附加任务
SelectTasksDesc=要执行哪些附加任务？
SelectTasksLabel2=选择安装 [name] 时要执行的附加任务，然后点“下一步”。
WizardReady=准备安装
ReadyLabel1=已准备好在你的电脑上安装 [name]。
ReadyLabel2a=点“安装”开始安装；如需查看或更改设置，请点“上一步”。
ReadyMemoDir=安装位置：
ReadyMemoTasks=附加任务：
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候。
FinishedHeadingLabel=[name] 安装完成
FinishedLabelNoIcons=[name] 已安装到你的电脑。
FinishedLabel=[name] 已安装到你的电脑，可通过已创建的快捷方式启动。
ClickFinish=点“完成”退出安装程序。
StatusExtractFiles=正在解压文件...
StatusCreateIcons=正在创建快捷方式...
StatusUninstalling=正在卸载 %1...
ConfirmUninstall=确定要完全删除 %1 及其组件吗？
UninstalledAll=%1 已从你的电脑中删除。
UninstallStatusLabel=正在从你的电脑中删除 %1，请稍候。
WizardUninstalling=卸载状态
SetupAlreadyRunning=安装程序已在运行。
DirExists=文件夹：%n%n%1%n%n已经存在。仍要安装到该文件夹吗？
DirDoesntExist=文件夹：%n%n%1%n%n不存在。要创建该文件夹吗？
BrowseDialogTitle=浏览文件夹
BrowseDialogLabel=在下面的列表中选择一个文件夹，然后点“确定”。
NewFolderName=新建文件夹
'''

def iss(version_:str,source:Path,guide:Path,notice:Path,ico:Path)->str:
    return f'''; Generated by make_installer.py — edit the script, not this file.
#define AppName "AE汉化工具"
#define AppVersion "{version_}"
[Setup]
AppId={{{{8E3C7A52-6B1D-4F0E-9C2A-3D7F5B9E1A64}}
AppName={{#AppName}}
AppVersion={{#AppVersion}}
AppVerName={{#AppName}} {{#AppVersion}}
DefaultDirName={{localappdata}}\\Programs\\{NAME}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={release}
OutputBaseFilename={NAME}_{version_}_安装包
SetupIconFile={ico}
UninstallDisplayIcon={{app}}\\app.ico
UninstallDisplayName={{#AppName}} {{#AppVersion}}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=no

{MESSAGES}
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{source}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{guide}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{notice}\\*"; DestDir: "{{app}}\\第三方组件说明"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{ico}"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{autoprograms}}\\{{#AppName}}"; Filename: "{{app}}\\{NAME}.exe"; IconFilename: "{{app}}\\app.ico"
Name: "{{autodesktop}}\\{{#AppName}}"; Filename: "{{app}}\\{NAME}.exe"; IconFilename: "{{app}}\\app.ico"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{NAME}.exe"; Description: "立即运行 {{#AppName}}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; Before the rename the EXE and shortcuts had another name; an upgrade (same AppId, same folder) removes them.
Type: files; Name: "{{app}}\\{OLD_NAMES[0]}.exe"
Type: files; Name: "{{autoprograms}}\\{OLD_NAMES[1]}.lnk"
Type: files; Name: "{{autodesktop}}\\{OLD_NAMES[1]}.lnk"

[UninstallDelete]
; The exchange folder the app creates beside its EXE while it talks to AE.
Type: filesandordirs; Name: "{{app}}\\ae-bridge"

[Code]
// After Effects keeps the app's parameter helper DLL loaded until it quits, so that file can be
// neither replaced (install over an older copy) nor removed (uninstall) while AE runs.
function AERunning(): Boolean;
var Locator,Service,Found: Variant;
begin
  Result:=False;
  try
    Locator:=CreateOleObject('WbemScripting.SWbemLocator');Service:=Locator.ConnectServer('.','root\\CIMV2');
    Found:=Service.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE Name="AfterFX.exe"');
    Result:=Found.Count>0;
  except
  end;
end;
function AskAboutAE(Action: String): Boolean;
begin
  Result:=True;
  while AERunning() do
  begin
    case SuppressibleMsgBox('After Effects 正在运行，它可能正在使用本软件的参数模块，' + Action + '时该文件无法替换或删除。'#13#10#13#10'请先在 AE 中保存工程并关闭 AE，然后点“重试”。'#13#10'点“忽略”继续（该文件会留下，可在关闭 AE 后手动删除）。',mbConfirmation,MB_ABORTRETRYIGNORE,IDIGNORE) of
      IDABORT: begin Result:=False; Exit; end;
      IDIGNORE: Exit;
    end;
  end;
end;
function InitializeSetup(): Boolean;
begin
  Result:=AskAboutAE('安装或更新');
end;
function InitializeUninstall(): Boolean;
begin
  Result:=AskAboutAE('卸载');
end;
'''

def main():
    version_=version();compiler=iscc()
    if '--skip-build' not in sys.argv:
        if stage.exists():shutil.rmtree(stage)
        subprocess.run([sys.executable,str(root/'build.py'),'--dist-dir',str(stage.relative_to(root))],check=True,cwd=root)
    if not (app/f'{NAME}.exe').is_file():raise RuntimeError('缺少新构建：'+str(app))
    extra=[p for p in app.iterdir() if p.name not in (f'{NAME}.exe','_internal')]
    if extra:raise RuntimeError('构建目录中有多余内容，拒绝打包：'+'、'.join(p.name for p in extra))
    support=stage/'support';shutil.rmtree(support,ignore_errors=True);support.mkdir(parents=True)
    guide=support/'使用说明.txt';guide.write_text(GUIDE.format(version=version_),encoding='utf-8-sig')
    notice=support/'第三方组件说明';notices(notice)
    ico=support/'app.ico';icon(ico)
    scan(app);scan(support);check_cpu(app)
    script=stage/'installer.iss';script.write_text(iss(version_,app,guide,notice,ico),encoding='utf-8-sig')
    release.mkdir(exist_ok=True)
    subprocess.run([str(compiler),'/Q',str(script)],check=True)
    output=release/f'{NAME}_{version_}_安装包.exe'
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    print(json.dumps({'installer':output.name,'bytes':output.stat().st_size,'sha256':digest},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
