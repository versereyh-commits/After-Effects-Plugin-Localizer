"""Instrumented full-range read-only scanning. No addresses or bytes are logged."""
from __future__ import annotations
import ctypes as C
import struct,time

BLOCK=4*1024*1024
LIMIT=0x7ffffffeffff
METRICS=('scanEnumMs','scanReadMs','scanSearchMs','scanValidateMs','scanBufferMs',
         'scanRegions','scanEligibleRegions','scanReadCalls','scanReadBytes','scanReadFailures',
         'scanHits','scanAlignedHits','scanParentChecks','scanRepeatedParents','scanValidationReads','scanBufferCapacityBytes')

def _kernel(mbi_type):
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.VirtualQueryEx.argtypes=[C.c_void_p,C.c_void_p,C.POINTER(mbi_type),C.c_size_t]
    kernel.VirtualQueryEx.restype=C.c_size_t
    return kernel

def scan(catalog,*,optimized=True,native=False,query=None,block=BLOCK,limit=LIMIT):
    from parameter_names import MBI
    p=catalog.p;pattern=struct.pack('<Q',catalog.flt+catalog.profile.root_vtable)
    stats={key:0 for key in METRICS};stats['scanEngine']='native' if native else 'reuse' if optimized else 'baseline'
    clock=time.perf_counter;total=clock();parades=set();confirmed=set();seen_parents=set()
    if query is None:
        kernel=_kernel(MBI)
        def query(address):
            mbi=MBI()
            return mbi if kernel.VirtualQueryEx(p.handle,C.c_void_p(address),C.byref(mbi),C.sizeof(mbi)) else None
    p.ensure_alive();scratch=None
    if optimized:
        tick=clock();scratch=bytearray(block+7);stats['scanBufferMs']+=(clock()-tick)*1000;stats['scanBufferCapacityBytes']=len(scratch)
    address=0;chunks=0
    try:
        while address<limit:
            tick=clock();m=query(address);stats['scanEnumMs']+=(clock()-tick)*1000
            if m is None:break
            stats['scanRegions']+=1;base=m.BaseAddress or 0;end=base+m.RegionSize
            if end<=address:raise RuntimeError('内存区域枚举未前进')
            address=end
            if m.State!=0x1000 or m.Type!=0x20000 or m.Protect&0x100 or m.Protect&0xff not in (4,8):continue
            stats['scanEligibleRegions']+=1
            for at in range(base,end,block):
                if chunks%16==0:p.ensure_alive()
                chunks+=1;size=min(block+7,end-at)
                if not optimized:
                    tick=clock();scratch=C.create_string_buffer(size);stats['scanBufferMs']+=(clock()-tick)*1000
                    stats['scanBufferCapacityBytes']=max(stats['scanBufferCapacityBytes'],size*2)
                tick=clock();stats['scanReadCalls']+=1
                try:p.read_into(at,scratch,size)
                except (OSError,RuntimeError):
                    stats['scanReadFailures']+=1;stats['scanReadMs']+=(clock()-tick)*1000;continue
                stats['scanReadMs']+=(clock()-tick)*1000;stats['scanReadBytes']+=size
                if optimized:data=scratch
                else:
                    tick=clock();data=scratch.raw;stats['scanBufferMs']+=(clock()-tick)*1000
                from scan_search import matches
                owned=min(block,end-at);hits=iter(matches(data,size,owned,at,pattern,native=native,stats=stats));seen_hits=set()
                while True:
                    tick=clock()
                    try:i=next(hits)
                    except StopIteration:
                        stats['scanSearchMs']+=(clock()-tick)*1000;break
                    stats['scanSearchMs']+=(clock()-tick)*1000
                    if i in seen_hits:continue
                    seen_hits.add(i);stats['scanHits']+=1
                    if (at+i)%8:continue
                    stats['scanAlignedHits']+=1;tick=clock()
                    try:
                        if optimized and i+40<=size:parent=struct.unpack_from('<Q',data,i+32)[0]
                        else:stats['scanValidationReads']+=1;parent=p.u64(at+i+32)
                        if not parent:continue
                        if parent in seen_parents:stats['scanRepeatedParents']+=1
                        seen_parents.add(parent)
                        if optimized and parent in confirmed:parades.add(parent);continue
                        stats['scanParentChecks']+=1;stats['scanValidationReads']+=1
                        if catalog.names.get(p.read(parent+8,16))=='ADBE Effect Parade':
                            parades.add(parent);confirmed.add(parent)
                    except (OSError,RuntimeError,ValueError):pass
                    finally:stats['scanValidateMs']+=(clock()-tick)*1000
        p.ensure_alive();return parades
    finally:
        stats['scanMeasuredMs']=(clock()-total)*1000;catalog.timings.update(stats)
