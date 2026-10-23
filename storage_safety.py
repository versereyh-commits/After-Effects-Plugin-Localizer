"""Read local JSON without silently overwriting damaged or inaccessible originals."""
import json,os,time,uuid
from pathlib import Path
_notices={}

def read_json(path,validate=None):
    path=Path(path)
    try:
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value,dict):raise ValueError('记录必须是对象')
        if validate is not None and not validate(value):raise ValueError('记录格式不正确')
        return value,''
    except FileNotFoundError:return {},_notices.get(str(path.absolute()),'')
    except (ValueError,UnicodeError) as error:
        backup=path.with_name(path.stem+'.corrupt-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+path.suffix)
        try:os.replace(path,backup)
        except OSError as failure:raise OSError('记录损坏且无法备份，已禁止覆盖：'+str(path)) from failure
        warning='记录损坏，原文件已保留：'+str(backup);_notices[str(path.absolute())]=warning
        return {},warning
