"""Finds machine instructions a plain x86-64 CPU cannot run in our native helpers.

The helpers once came out of `zig cc` built for the build machine's own CPU (an i9-13900HX): GFNI, AVX2, BMI and
MOVBE instructions throughout, so on an AMD Zen 1-3 or an older Intel CPU AE hit an illegal instruction while
hashing its modules -- a self-check that "timed out" or an AE that crashed. build.py now asks for the baseline
(SSE2) instruction set; make_installer and the tests use this to refuse any helper that has more.

python cpu_check.py native/*.dll"""
from __future__ import annotations
import collections,sys
from pathlib import Path

# Mnemonics outside baseline x86-64 that a compiler emits only when told the CPU has them.
# Whole names: andnps/andnpd (SSE) and register pextrw (SSE2) only share a prefix. tzcnt is absent on purpose:
# it is encoded as rep bsf, which an older CPU runs as bsf with the same result, and LLVM emits it for baseline.
# lzcnt is not: an older CPU runs it as bsr, which gives a different answer.
_BMI={'andn','bextr','blsi','blsmsk','blsr','bzhi','pdep','pext','rorx','sarx','shlx','shrx','lzcnt'}
_EXTENSIONS=(('gfni',('gf2p8',)),
             ('popcnt',('popcnt',)),('movbe',('movbe',)),('adx',('adcx','adox')),('aes',('aes','pclmul')),('sha',('sha1','sha256')),
             ('sse4.2',('crc32','pcmpgtq','pcmpestr','pcmpistr')),
             ('sse4.1',('pblend','blendp','blendvp','ptest','pinsrb','pinsrd','pinsrq','pextrb','pextrd','pextrq','roundp','rounds','pmovzx','pmovsx',
                        'pcmpeqq','pmulld','pminsb','pminsd','pminud','pminuw','pmaxsb','pmaxsd','pmaxud','pmaxuw','insertps','extractps','dpp','mpsadbw','packusdw','phminposuw','pmuldq')),
             ('ssse3',('pshufb','palignr','pmaddubsw','phadd','phsub','pabs','psign','pmulhrsw')))
_BASELINE_V=('verr','verw')   # the only baseline mnemonics that start with "v"

def kind(mnemonic:str)->str|None:
    """The instruction-set extension a mnemonic needs, or None for baseline x86-64."""
    m=mnemonic.lower()
    if m.startswith(('gf2p8','vgf2p8')):return 'gfni'
    if m.startswith('v') and m not in _BASELINE_V:return 'avx'
    if m in _BMI:return 'bmi'
    for name,prefixes in _EXTENSIONS:
        if m.startswith(prefixes):return name
    return None

def extended_instructions(path:Path)->dict[str,int]:
    """Counts of non-baseline instructions in a DLL's executable sections, by extension; empty when it is baseline."""
    import capstone,pefile
    pe=pefile.PE(str(path),fast_load=True);found=collections.Counter()
    try:
        md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);md.skipdata=True
        for section in pe.sections:
            if not section.Characteristics&0x20000000:continue   # IMAGE_SCN_MEM_EXECUTE
            for _,_,mnemonic,_ in md.disasm_lite(section.get_data(),section.VirtualAddress):
                if (name:=kind(mnemonic)):found[name]+=1
    finally:pe.close()
    return dict(found)

def main(paths):
    bad=0
    for path in paths:
        found=extended_instructions(Path(path));bad+=bool(found)
        print(f"{Path(path).name}: {'通用 x86-64' if not found else '含新指令 '+str(found)}")
    sys.exit(1 if bad else 0)

if __name__=='__main__':main(sys.argv[1:])
