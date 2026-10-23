"""Independent full-range parallel-reader candidate. Original implementation.
Design reference: ReClass.NET Scanner.cs partitioning/consolidation (MIT).
No copied code. No injected code, persistent addresses or additional write rights.
"""
from __future__ import annotations
import ctypes as C
import os,time,threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from types import SimpleNamespace
from parameter_scan import scan as serial_scan,METRICS, _kernel,LIMIT,BLOCK
from win_process import Process

JOB=16*1024*1024

def enumerate_regions(p,query=None,limit=LIMIT):
    from parameter_names import MBI
    if query is None:
        kernel=_kernel(MBI)
        def query(address):
            m=MBI();return m if kernel.VirtualQueryEx(p.handle,C.c_void_p(address),C.byref(m),C.sizeof(m)) else None
    address=0;regions=[];count=0;start=time.perf_counter();p.ensure_alive()
    while address<limit:
        m=query(address)
        if m is None:break
        base=m.BaseAddress or 0;end=base+m.RegionSize;count+=1
        if end<=address:raise RuntimeError('memory enumeration did not advance')
        address=end
        if m.State==0x1000 and m.Type==0x20000 and not m.Protect&0x100 and m.Protect&0xff in (4,8):regions.append((base,end))
    p.ensure_alive();return regions,count,(time.perf_counter()-start)*1000

class _BasicInfo(C.Structure):
    _fields_=[('ExitStatus',C.c_void_p),('PebBaseAddress',C.c_void_p),('AffinityMask',C.c_void_p),('BasePriority',C.c_void_p),('UniqueProcessId',C.c_void_p),('Parent',C.c_void_p)]

def default_heap(p):
    """PEB.ProcessHeap of the target (x64 PEB +0x30). Read with the query/read rights the handle already has."""
    ntdll=C.WinDLL('ntdll');ntdll.NtQueryInformationProcess.argtypes=[C.c_void_p,C.c_int,C.c_void_p,C.c_ulong,C.c_void_p]
    info=_BasicInfo()
    if ntdll.NtQueryInformationProcess(p.handle,0,C.byref(info),C.sizeof(info),None) or not info.PebBaseAddress:raise OSError('进程信息读取失败')
    return p.u64(info.PebBaseAddress+0x30)

def heap_regions(p,query=None,heap=None,limit=LIMIT):
    """Committed private read-write regions of the default process heap's segments, in address order.
    AE's effect and parameter objects come from U.dll's tracked allocator, which calls the CRT malloc, which
    uses this heap (AE 2021-2026; AfterFX.exe does not opt into the segment heap). An NT heap segment starts at its
    allocation base with HEAP_SEGMENT: signature 0xffeeffee at +0x10, owning heap at +0x28. Returns None whenever
    the heap cannot be identified, so the caller scans everything instead."""
    from parameter_names import MBI
    try:heap=heap or default_heap(p)
    except (OSError,RuntimeError,ValueError):return None
    if not heap:return None
    if query is None:
        kernel=_kernel(MBI)
        def query(address):
            m=MBI();return m if kernel.VirtualQueryEx(p.handle,C.c_void_p(address),C.byref(m),C.sizeof(m)) else None
    address=0;owners={};regions=[]
    while address<limit:
        m=query(address)
        if m is None:break
        base=m.BaseAddress or 0;end=base+m.RegionSize
        if end<=address:raise RuntimeError('memory enumeration did not advance')
        address=end
        if not (m.State==0x1000 and m.Type==0x20000 and not m.Protect&0x100 and m.Protect&0xff in (4,8)):continue
        start=m.AllocationBase or 0
        if start not in owners:
            try:
                header=p.read(start,0x30)
                owners[start]=int.from_bytes(header[0x10:0x14],'little')==0xffeeffee and int.from_bytes(header[0x28:0x30],'little')==heap
            except (OSError,RuntimeError):owners[start]=False
        if owners[start]:regions.append((base,end))
    # The heap's first segment is the heap itself: no match means the layout is not the one described above.
    if not owners.get(heap):return None
    return regions

def consolidate(regions,merge):
    spans=[]
    for start,end in regions:
        if merge and spans and spans[-1][1]==start and start%8==0:spans[-1]=(spans[-1][0],end)
        else:spans.append((start,end))
    return spans

def partition(spans,workers,job=JOB):
    jobs=[]
    for start,end in spans:
        if start%8 or job%8:jobs.append((start,end));continue
        jobs.extend((at,min(at+job,end)) for at in range(start,end,job))
    bins=[[] for _ in range(workers)];weights=[0]*workers
    for task in sorted(jobs,key=lambda x:(-(x[1]-x[0]),x[0])):
        index=min(range(workers),key=lambda i:weights[i]);bins[index].append(task);weights[index]+=task[1]-task[0]
    return [sorted(tasks) for tasks in bins if tasks]

def region_query(regions):
    it=iter(regions)
    def query(_):
        item=next(it,None)
        if item is None:return None
        start,end=item;return SimpleNamespace(BaseAddress=start,RegionSize=end-start,State=0x1000,Type=0x20000,Protect=4)
    return query

def same_host(main,worker):
    return main.pid==worker.pid and main.creation_time==worker.creation_time and os.path.normcase(os.path.abspath(main.image))==os.path.normcase(os.path.abspath(worker.image))

class Reader:
    def __init__(self,p,stop,original,record_failures):self.p=p;self.stop=stop;self.original=original;self.record_failures=record_failures;self.retry=set()
    def ensure_alive(self):
        if self.stop.is_set():raise RuntimeError('parallel scan canceled')
        self.p.ensure_alive()
    def read_into(self,address,buffer,size):
        if self.stop.is_set():raise RuntimeError('parallel scan canceled')
        try:return self.p.read_into(address,buffer,size)
        except (OSError,RuntimeError):
            if self.record_failures:
                self.retry.update((start,end) for start,end in self.original if start<address+size and end>address)
            raise
    def read(self,address,size):self.ensure_alive();return self.p.read(address,size)
    def u64(self,address):self.ensure_alive();return self.p.u64(address)

def scan_parallel(catalog,*,workers=2,merge=False,query=None,factory=None,job=JOB,block=BLOCK):
    if type(workers) is not int or workers not in (1,2,4):raise ValueError('worker count must be 1, 2 or 4')
    if type(job) is not int or job<=0 or job%8 or type(block) is not int or block<=0 or block%8:raise ValueError('job/block must be positive and 8-byte aligned')
    total=time.perf_counter();main=catalog.p;regions,region_count,enum_ms=enumerate_regions(main,query)
    spans=consolidate(regions,merge);bins=partition(spans,workers,job);stop=threading.Event();factory=factory or (lambda:Process(main.pid,write=False))
    from scan_search import native_function
    native_function() # Load before worker threads; missing helper uses the validated Python fallback.
    def worker(tasks):
        process=factory()
        try:
            if not same_host(main,process):raise RuntimeError('worker host identity mismatch')
            process.ensure_alive();reader=Reader(process,stop,regions,merge)
            local=SimpleNamespace(p=reader,profile=catalog.profile,flt=catalog.flt,names=catalog.names,timings={})
            result=serial_scan(local,optimized=True,native=True,query=region_query(tasks),block=block);parts=[dict(local.timings)]
            retries=sorted(reader.retry)
            if retries:
                local.p=Reader(process,stop,regions,False)
                result.update(serial_scan(local,optimized=True,native=True,query=region_query(retries),block=block));parts.append(dict(local.timings))
            process.ensure_alive();return result,parts,len(retries)
        finally:process.close()
    found=set();details=[];retry_count=0
    try:
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='AERead') as pool:
            futures=[pool.submit(worker,tasks) for tasks in bins]
            try:
                for future in as_completed(futures):
                    parents,stats,retries=future.result();found.update(parents);details.extend(stats);retry_count+=retries
            except BaseException:
                stop.set()
                for future in futures:future.cancel()
                raise
        main.ensure_alive()
        stats={key:sum(item.get(key,0) for item in details) for key in METRICS}
        stats.update(scanEnumMs=enum_ms,scanRegions=region_count,scanEligibleRegions=len(regions),scanMergedRegions=len(spans),scanJobs=sum(map(len,bins)),scanWorkerCount=len(bins),scanRetryRegions=retry_count,scanCoalesced=int(merge),scanBufferCapacityBytes=(block+7)*len(bins),scanTimingMode='sum_of_worker_durations',scanEngine='parallel-native' if all(x.get('scanEngine')=='native' for x in details) else 'parallel-fallback',scanMeasuredMs=(time.perf_counter()-total)*1000)
        catalog.timings.update(stats);return found
    finally:stop.set()


def scan_auto(catalog,full=True):
    """Bound concurrency by CPU count. Resource-only failures retain serial coverage.
    full=False scans only the default heap (heap_regions) and falls back to everything when it cannot be found;
    catalog.timings['scanTarget'] says which one ran ('heap' or 'full')."""
    regions=None if full else heap_regions(catalog.p)
    query=None if regions is None else region_query(regions)
    catalog.timings['scanTarget']='full' if regions is None else 'heap'
    cores=os.cpu_count() or 1
    if cores<2:
        result=serial_scan(catalog,optimized=True,native=True,query=query)
        catalog.timings.update(scanWorkerCount=1,scanCoalesced=0,scanTimingMode='serial')
        return result
    workers=4 if cores>=4 else 2;started=time.perf_counter()
    try:return scan_parallel(catalog,workers=workers,merge=True,query=query)
    except (OSError,MemoryError):
        # scan_parallel joins workers before raising. Never fall back on identity/runtime guards.
        catalog.p.ensure_alive();result=serial_scan(catalog,optimized=True,native=True,query=None if regions is None else region_query(regions))
        catalog.timings.update(scanWorkerCount=1,scanCoalesced=0,scanTimingMode='serial-resource-fallback',scanResourceFallback=1,scanMeasuredMs=(time.perf_counter()-started)*1000)
        return result
