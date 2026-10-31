"""Bounded in-memory read reports. Nothing here grants file-write authorization."""
from collections import OrderedDict
from copy import deepcopy
import json,threading,sys
from pathlib import Path
from panel_files import digest,inside

_reports=OrderedDict()
_lock=threading.Lock()
CAPACITY=16
MAX_BYTES=32*1024*1024

def weight(value):
    seen=set();stack=[value];total=0
    while stack:
        item=stack.pop();key=id(item)
        if key in seen:continue
        seen.add(key);total+=sys.getsizeof(item)
        if isinstance(item,dict):stack.extend(item.keys());stack.extend(item.values())
        elif isinstance(item,(tuple,list)):stack.extend(item)
    return total

def clear(key=None):
    with _lock:
        if key is None:_reports.clear()
        else:_reports.pop(key,None)

def signature(path):
    s=path.stat();return (s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino)

def context(files):
    return digest(json.dumps(files.state(),sort_keys=True,ensure_ascii=True).encode())

def get(key,root,files,rules,cancelled):
    with _lock:entry=deepcopy(_reports.get(key))
    if not entry or entry['rules']!=rules:return None
    try:
        if entry['context']!=context(files):clear(key);return None
        for rel,stamp in entry['stamps'].items():
            if cancelled():raise RuntimeError('已取消读取')
            p=inside(root,rel)
            if signature(p)!=stamp:clear(key);return None
            expected=entry['report']['hashes'].get(rel)
            # Hash even when timestamp/size are unchanged; no weak-metadata cache authorization.
            if expected is not None and digest(p.read_bytes())!=expected:clear(key);return None
        with _lock:
            if key in _reports:_reports.move_to_end(key)
        result=entry['report'];result['readMode']='cache';return result
    except (OSError,ValueError):clear(key);return None

def put(key,root,files,rules,report,stamps):
    # Reject a racing modification, instead of associating old report contents with new file stamps.
    for rel,stamp in stamps.items():
        p=inside(root,rel)
        if signature(p)!=stamp:return
        expected=report['hashes'].get(rel)
        if expected is not None and digest(p.read_bytes())!=expected:return
    entry=dict(context=context(files),rules=rules,report=deepcopy(report),stamps=deepcopy(stamps))
    entry['weight']=weight(entry)
    with _lock:
        if entry['weight']>MAX_BYTES:_reports.pop(key,None);return
        _reports[key]=entry;_reports.move_to_end(key)
        while len(_reports)>CAPACITY or sum(x['weight'] for x in _reports.values())>MAX_BYTES:_reports.popitem(last=False)
