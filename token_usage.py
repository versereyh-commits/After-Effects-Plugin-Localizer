"""Tokens the translation requests used, kept per local day so the window can show all-time, 30-day and 7-day totals.

Only counts reported by the service are stored (no names, keys or request contents)."""
from __future__ import annotations
import datetime,json,threading
from pathlib import Path
from runtime import write_json_atomic

FILE='token-usage.json'
_lock=threading.Lock()

def reply_tokens(payload)->int:
    """Input plus output tokens from an OpenAI-compatible or Anthropic reply; 0 when the service reports none."""
    usage=payload.get('usage') if isinstance(payload,dict) else None
    if not isinstance(usage,dict):return 0
    def number(*names):
        for name in names:
            value=usage.get(name)
            if isinstance(value,(int,float)) and not isinstance(value,bool) and value>0:return int(value)
        return 0
    total=number('total_tokens')
    return total or number('prompt_tokens','input_tokens')+number('completion_tokens','output_tokens')

def _read(path:Path)->dict[str,int]:
    try:days=json.loads(path.read_text(encoding='utf8')).get('days')
    except (OSError,ValueError,AttributeError):return {}
    if not isinstance(days,dict):return {}
    return {day:int(value) for day,value in days.items() if isinstance(day,str) and isinstance(value,int) and not isinstance(value,bool) and value>0}

def record(tokens:int,root:Path,today:datetime.date|None=None):
    """Adds one reply's tokens to today's total. Safe to call from worker threads."""
    if not isinstance(tokens,int) or tokens<=0:return
    day=(today or datetime.date.today()).isoformat();path=Path(root)/FILE
    with _lock:
        days=_read(path);days[day]=days.get(day,0)+tokens
        write_json_atomic(path,{'days':days},ensure_ascii=True)

def summary(root:Path,today:datetime.date|None=None)->dict[str,int]:
    """{'all','30d','7d'}; the 30 and 7 days include today."""
    today=today or datetime.date.today();days=_read(Path(root)/FILE);result={'all':0,'30d':0,'7d':0}
    for day,value in days.items():
        try:age=(today-datetime.date.fromisoformat(day)).days
        except ValueError:continue
        result['all']+=value
        if 0<=age<30:result['30d']+=value
        if 0<=age<7:result['7d']+=value
    return result

def compact(value:int)->str:
    """12,345 · 12.3万 · 1.2亿 — short enough for a metric tile."""
    if value<100000:return f'{value:,}'
    if value<100000000:return f'{value/10000:.1f}'.rstrip('0').rstrip('.')+'万'
    return f'{value/100000000:.1f}'.rstrip('0').rstrip('.')+'亿'
