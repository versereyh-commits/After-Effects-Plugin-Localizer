"""Prepares (and on request uploads) an update release for the app's update check.

python publish_update.py --notes 更新说明.txt [--upload]

Takes release/AE汉化工具_<version>_安装包.exe made by make_installer.py, copies it to an ASCII
asset name (GitHub renames non-ASCII asset names), writes update.json beside it in release/update-<version>/,
and prints the gh command. Only --upload runs that command, creating release v<version> in the public
release repository named by updater.UPDATE_REPO; the repository must already exist."""
from __future__ import annotations
import datetime,json,shutil,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent;sys.path.insert(0,str(root))
import updater
from make_installer import NAME,version

def main():
    args=sys.argv[1:]
    if '--notes' not in args or args.index('--notes')+1>=len(args):raise SystemExit('用法：python publish_update.py --notes 更新说明.txt [--upload]')
    notes=Path(args[args.index('--notes')+1]).read_text(encoding='utf-8-sig').strip()
    if not updater.configured():raise SystemExit('updater.py 里的 UPDATE_REPO 还没有设置为发布仓库（owner/name），请先建好仓库再填写')
    version_=version();source=root/'release'/f'{NAME}_{version_}_安装包.exe'
    if not source.is_file():raise SystemExit(f'缺少安装包，请先运行 make_installer.py：{source}')
    folder=root/'release'/f'update-{version_}';folder.mkdir(parents=True,exist_ok=True)
    installer=folder/f'ae-localizer-setup-{version_}.exe';shutil.copyfile(source,installer)
    manifest=updater.make_manifest(installer,version_,notes,datetime.date.today().isoformat())
    (folder/updater.MANIFEST).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    command=['gh','release','create',f'v{version_}',str(installer),str(folder/updater.MANIFEST),'--repo',updater.UPDATE_REPO,
             '--title',f'{NAME} {version_}','--notes',notes,'--latest']
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    print('上传命令：'+subprocess.list2cmdline(command))
    if '--upload' in args:subprocess.run(command,check=True)

if __name__=='__main__':main()
