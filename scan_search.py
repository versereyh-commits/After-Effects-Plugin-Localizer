"""Optional bounded local-buffer native search, with a complete Python fallback."""
import ctypes as C
from runtime import asset_root

_library=None

def native_function():
    global _library
    if _library is None:
        try:
            library=C.CDLL(str(asset_root()/'native'/'ae_scan_v1.dll'))
            f=library.ae_scan_u64
            f.argtypes=[C.c_void_p,C.c_size_t,C.c_size_t,C.c_size_t,C.c_uint64,C.c_size_t,C.POINTER(C.c_size_t),C.c_size_t,C.POINTER(C.c_size_t),C.POINTER(C.c_size_t)]
            f.restype=C.c_int;_library=library
        except (OSError,AttributeError):return None
    return _library.ae_scan_u64

def matches(data,valid,owned,base,pattern,*,native=False,stats=None,capacity=4096):
    if not 0<=owned<=valid<=len(data):raise ValueError('搜索边界无效')
    if capacity<1:raise ValueError('搜索输出容量无效')
    if native:
        try:
            function=native_function()
            if function is None:raise RuntimeError('native search unavailable')
            buffer=(C.c_ubyte*len(data)).from_buffer(data);output=(C.c_size_t*capacity)();written=C.c_size_t();next_offset=C.c_size_t();start=0
            while start<owned:
                result=function(buffer,valid,owned,base%8,int.from_bytes(pattern,'little'),start,output,capacity,C.byref(written),C.byref(next_offset))
                if result not in (0,1) or written.value>capacity or not start<=next_offset.value<=owned or (result==1 and (next_offset.value<=start or written.value!=capacity)):raise RuntimeError('native search invalid output')
                batch=list(output[:written.value]);previous=start-1
                for hit in batch:
                    if not previous<hit<owned or hit+8>valid or (base+hit)%8 or data[hit:hit+8]!=pattern:raise RuntimeError('native search invalid match')
                    previous=hit
                if result==1 and batch and next_offset.value<=batch[-1]:raise RuntimeError('native search invalid resume')
                yield from batch
                if result==0:return
                start=next_offset.value
            return
        except (OSError,RuntimeError,ValueError,TypeError):
            if stats is not None:stats['scanEngine']='reuse-fallback'
            # Replay the whole buffer; the caller deduplicates candidates.
    start=0
    while True:
        hit=data.find(pattern,start,valid)
        if hit<0 or hit>=owned:return
        start=hit+1;yield hit
