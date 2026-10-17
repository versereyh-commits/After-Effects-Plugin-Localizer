"""Runs the test suite in several processes at once, or only the given test modules.

python run_tests.py                         # every tests/test_*.py
python run_tests.py test_updater test_gui   # just these modules
python run_tests.py --workers 4

Every module runs in a fresh process, several at once, offscreen and with the network blocked. Running the
whole suite in one process takes 12-15 minutes because windows and timers from earlier GUI tests pile up and
slow every later test; one module per process the same tests take about 85 seconds in a row, under a minute here.
Exit code 0 only when every module passed."""
from __future__ import annotations
import os,re,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

root=Path(__file__).resolve().parent;tests=root/'tests'

def environment()->dict:
    return {**os.environ,'PYTHONIOENCODING':'utf-8','QT_QPA_PLATFORM':'offscreen','PYTHONPATH':str(root),
            # Unit tests must never reach the network; a dead local proxy makes any attempt fail fast.
            'HTTPS_PROXY':'http://127.0.0.1:9','HTTP_PROXY':'http://127.0.0.1:9'}

def run(modules:list[str])->tuple[list[str],int,str]:
    result=subprocess.run([sys.executable,'-m','unittest',*modules],cwd=tests,env=environment(),capture_output=True,text=True,encoding='utf-8',errors='replace')
    return modules,result.returncode,result.stderr+result.stdout

def main():
    args=sys.argv[1:];workers=min(8,os.cpu_count() or 1)
    if '--workers' in args:workers=max(1,int(args[args.index('--workers')+1]));del args[args.index('--workers'):args.index('--workers')+2]
    modules=[a.removesuffix('.py').split('/')[-1].split('\\')[-1] for a in args] or sorted(p.stem for p in tests.glob('test_*.py'))
    # Largest first so the long modules start at once and the short ones fill in around them.
    plan=[[m] for m in sorted(modules,key=lambda m:(tests/f'{m}.py').stat().st_size,reverse=True)]
    started=time.monotonic();total=skipped=0;failed=[]
    with ThreadPoolExecutor(min(workers,len(plan))) as pool:
        for batch,code,output in pool.map(run,plan):
            ran=re.search(r'^Ran (\d+) test',output,re.M);total+=int(ran[1]) if ran else 0
            skip=re.search(r'skipped=(\d+)',output);skipped+=int(skip[1]) if skip else 0
            if code:failed.append(batch);print(output[-6000:])
    seconds=time.monotonic()-started
    print(f"{total} 项测试，{len(plan)} 个模块，用时 {seconds:.0f} 秒"+(f"，跳过 {skipped}" if skipped else '')+('' if not failed else f"；失败：{[b[0] for b in failed]}"))
    print('OK' if not failed else 'FAILED')
    sys.exit(1 if failed else 0)

if __name__=='__main__':main()
