"""Prepares (and on request uploads) an update release for the app's update check.

python publish_update.py [--notes 更新说明.txt] [--upload]

Without --notes the release text is this version's entry in release_notes.py (what the app shows once after
updating), so the two never disagree. Takes release/AE汉化工具_<version>_安装包.exe made by make_installer.py, copies it to an ASCII
asset name (GitHub renames non-ASCII asset names), writes update.json beside it in release/update-<version>/,
and prints the gh command. Only --upload runs that command, creating release v<version> in the public
release repository named by updater.UPDATE_REPO; the repository must already exist."""
from __future__ import annotations
import datetime,json,shutil,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent;sys.path.insert(0,str(root))
import release_notes,updater
from make_installer import NAME,version

def main():
    args=sys.argv[1:]
    if '--notes' in args and args.index('--notes')+1>=len(args):raise SystemExit('用法：python publish_update.py [--notes 更新说明.txt] [--upload]')
    if not updater.configured():raise SystemExit('updater.py 里的 UPDATE_REPO 还没有设置为发布仓库（owner/name），请先建好仓库再填写')
    version_=version();source=root/'release'/f'{NAME}_{version_}_安装包.exe'
    if '--notes' in args:notes=Path(args[args.index('--notes')+1]).read_text(encoding='utf-8-sig').strip()
    else:
        entry=[e for e in release_notes.NOTES if e[0]==version_]
        if not entry:raise SystemExit(f'release_notes.py 里还没有 {version_} 的更新内容，请先补上（或用 --notes 指定）')
        notes=release_notes.text(entry,headings=False)
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
