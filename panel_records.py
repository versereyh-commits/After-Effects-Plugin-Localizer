"""Read-only history summaries. Never restores from untrusted history data or grants a write permit."""
from pathlib import Path
import re
from panel_files import document,digest,inside

def entries(folder):
    folder=Path(folder);result=[]
    if not folder.exists():return result
    for target in folder.iterdir():
        if not re.fullmatch('[0-9a-f]{64}',target.name) or target.is_symlink() or target.is_junction():continue
        recovery=target/'recovery'
        if not recovery.is_dir() or recovery.is_symlink() or recovery.is_junction():continue
        for path in sorted(recovery.glob('*.json')):
            if path.name!='transaction.json' and not re.fullmatch(r'history-[0-9a-f]{32}\.json',path.name):continue
            if path.is_symlink() or path.is_junction():continue
            summary=dict(key=target.name,name=target.name[:12],state='记录损坏',at='',files=[],backup=str(recovery),issues=[],current=path.name=='transaction.json')
            try:
                if path.stat().st_size>8*1024*1024:raise ValueError('记录过大')
                r=document(path);binding=r['binding'];files=r['files'];status=r['status']
                if not isinstance(files,list) or status not in ('applied','prepared','restored'):raise ValueError('记录格式不符')
                summary.update(name=str(r.get('targetName') or binding.get('id') or summary['name']),at=str(r.get('updatedAt') or r.get('createdAt') or ''),
                               state={'applied':'已汉化','prepared':'待恢复','restored':'已恢复'}[status] if summary['current'] else '历史快照')
                for x in files:
                    rel=x['path'];p=inside(binding['root'],rel);summary['files'].append(rel)
                    backup=x.get('backup')
                    if x.get('before') is not None:
                        if not isinstance(backup,str) or not re.fullmatch(r'[0-9a-f]{32}\.bin',backup):raise ValueError('备份路径不符')
                        b=inside(recovery,backup)
                        if not b.is_file() or digest(b.read_bytes())!=x['before']:summary['issues'].append(rel+'：备份缺失或哈希不符')
                    if summary['current'] and status!='restored':
                        actual=digest(p.read_bytes()) if p.exists() else None
                        expected=(x['after'],) if status=='applied' else (x['after'],x['before'],x.get('previous',x['before']))
                        if actual not in expected:summary['issues'].append(rel+'：目标文件已变化')
                if summary['issues'] and summary['current']:summary['state']='存在冲突'
            except (ValueError,OSError,KeyError,TypeError,AttributeError) as e:
                summary['state']='记录损坏';summary['issues'].append(str(e))
            result.append(summary)
    return sorted(result,key=lambda r:(r['at'],r['current']),reverse=True)
