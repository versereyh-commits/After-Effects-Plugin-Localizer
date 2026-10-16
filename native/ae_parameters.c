/* Loaded through Adobe ExtendScript's documented ExternalObject ABI.
   ABI reference: Adobe-CEP/CEP-Resources, SoSharedLibDefs.h (ab5e4e3).
   The TDB adapters are PRIVATE and pinned to three exact FLT/TDB hash pairs.
   Runs only on AE's scripting/main thread. Only the inline PF name buffer is
   written, followed by the host's display-name setter/notification. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <bcrypt.h>

typedef struct { union {long integer;double number;char *string;void *object;} data;long type,filler;} TaggedData;
typedef struct {union {wchar_t small[8];wchar_t *pointer;} data;uint64_t length,capacity;} WideString;
#define API __declspec(dllexport)
static DWORD script_thread;
static int profile_ok;
typedef struct {const char *flt,*tdb;uintptr_t root,vector;int named_vector,legacy;} Adapter;
static const Adapter adapters[]={
 {"68b229950e95dedf630f5d09653715268e8beba4830590433abe7cb58e78a357","8e1ea3d5e08d91dd25e899d43e0ad69a525b161716bb4c29250e3711b2591c80",0xc8e80,0x220,0,0},
 {"a4896d34e20f1fb08ba9afd0fc6e428081bd51dba6abb39e87aa6342c973a183","6d8f80729ef02538415bb6578399cee667101b6c218ea73d85d74357558b660e",0xce2f0,0x220,0,1},
 {"abf6673b380450cc98549876c3738135bc025345b619cc4cba9d17bf98d05bc9","2214da04c37c197f8cefb3cc825eaab6f45efa3c1bdd300e2157842e4b38113d",0x8f0f0,0x190,1,1}
};
static const Adapter *adapter;
#ifdef AE_DYNAMIC
static Adapter dynamic_adapter;
#endif
static int readable(const void *address,SIZE_T size) {
    MEMORY_BASIC_INFORMATION m;
    return address&&VirtualQuery(address,&m,sizeof(m))&&m.State==MEM_COMMIT&&!(m.Protect&(PAGE_GUARD|PAGE_NOACCESS))&&
        (uintptr_t)address+size<=(uintptr_t)m.BaseAddress+m.RegionSize;
}
static int module_hash(HMODULE module,const char *expected) {
    wchar_t path[32768];if(!GetModuleFileNameW(module,path,32768))return 0;
    HANDLE file=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,OPEN_EXISTING,0,NULL);
    if(file==INVALID_HANDLE_VALUE)return 0;
    BCRYPT_ALG_HANDLE alg=NULL;BCRYPT_HASH_HANDLE hash=NULL;UCHAR digest[32],buffer[65536];DWORD read=0;int ok=0;
    if(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,NULL,0)<0)goto done;
    if(BCryptCreateHash(alg,&hash,NULL,0,NULL,0,0)<0)goto done;
    for(;;){if(!ReadFile(file,buffer,sizeof(buffer),&read,NULL))goto done;if(!read)break;if(BCryptHashData(hash,buffer,read,0)<0)goto done;}
    if(BCryptFinishHash(hash,digest,32,0)<0)goto done;
    char hex[65];static const char digits[]="0123456789abcdef";
    for(int i=0;i<32;i++){hex[i*2]=digits[digest[i]>>4];hex[i*2+1]=digits[digest[i]&15];}hex[64]=0;ok=!strcmp(hex,expected);
done:
    if(hash)BCryptDestroyHash(hash);if(alg)BCryptCloseAlgorithmProvider(alg,0);CloseHandle(file);return ok;
}
static int profile(void) {
    HMODULE t=GetModuleHandleW(L"TDB.dll"),f=GetModuleHandleW(L"FLT.dll");
    if(!t||!f)return 0;
    for(unsigned i=0;i<sizeof(adapters)/sizeof(adapters[0]);i++)
        if(module_hash(t,adapters[i].tdb)&&module_hash(f,adapters[i].flt)){adapter=&adapters[i];return 1;}
    return 0;
}
API long ESGetVersion(void){return 1;}
API char *ESInitialize(TaggedData *a,long count){(void)a;(void)count;script_thread=GetCurrentThreadId();profile_ok=profile();return "setLabel_sssss";}
API void ESTerminate(void){}
API void ESFreeMem(void *p){free(p);}
static int hexval(char c){if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;if(c>='A'&&c<='F')return c-'A'+10;return -1;}
#ifdef AE_DYNAMIC
static int module_range(HMODULE module,uintptr_t offset,SIZE_T size) {
    if(!module||!readable(module,4096))return 0;
    IMAGE_DOS_HEADER *dos=(IMAGE_DOS_HEADER*)module;
    IMAGE_NT_HEADERS64 *nt=(IMAGE_NT_HEADERS64*)((uintptr_t)module+dos->e_lfanew);
    return dos->e_magic==IMAGE_DOS_SIGNATURE&&nt->Signature==IMAGE_NT_SIGNATURE&&
        nt->FileHeader.Machine==IMAGE_FILE_MACHINE_AMD64&&offset<nt->OptionalHeader.SizeOfImage&&
        size<=nt->OptionalHeader.SizeOfImage-offset&&readable((void*)((uintptr_t)module+offset),size);
}
static int primary_root(HMODULE f,uintptr_t root) {
    if(root<8||!module_range(f,root-8,16))return 0;
    uintptr_t col=*(uintptr_t*)((uintptr_t)f+root-8);
    if(col<(uintptr_t)f||!module_range(f,col-(uintptr_t)f,24))return 0;
    uint32_t *c=(uint32_t*)col;
    if(c[0]!=1||c[1]!=0||c[5]!=col-(uintptr_t)f||!module_range(f,c[3],64))return 0;
    return !memcmp((void*)((uintptr_t)f+c[3]+16),".?AVFLT_FCSeqSpec@@",19);
}
static int codec_probe(HMODULE u) {
    typedef WideString *(*Convert)(WideString*,const char*);
    Convert convert=(Convert)GetProcAddress(u,"?U_MBToUTF16String@@YA?AV?$basic_string@_WU?$char_traits@_W@std@@U?$STLAllocator@_W@allocator@dvacore@@@std@@PEBD@Z");
    if(!convert)return 0;
    /* A short, valid UTF-8 sequence which also forms valid pairs in ACP 936.
       Both interpretations stay in the verified MSVC small-string layout. */
    const char sample[]="\xe4\xb8\xad\xe4\xb8\xad";
    WideString value;memset(&value,0,sizeof(value));value.capacity=7;
    if(convert(&value,sample)!=&value||value.capacity>7||value.length>6||value.data.small[value.length])return 0;
    if(value.length==2&&value.data.small[0]==0x4e2d&&value.data.small[1]==0x4e2d)return 1;
    wchar_t expected[8];int n=MultiByteToWideChar(CP_ACP,0,sample,-1,expected,8);
    return n>0&&!wcscmp(value.data.small,expected)?2:0;
}
static int configure(TaggedData *a) {
    uintptr_t v[7];char *end=NULL,*pos=a[3].data.string;
    for(int i=0;i<7;i++){
        v[i]=(uintptr_t)_strtoui64(pos,&end,16);if(end==pos)return 0;
        if(i<6){if(*end!=',')return 0;pos=end+1;}else if(*end)return 0;
    }
    uintptr_t root=v[0],vector=v[1],map=v[2],named=v[3],parent_get=v[4],match_get=v[5],count_get=v[6];
    if(vector<0x80||vector>0x800||vector%8||map!=vector+8||named>1)return 0;
    HMODULE f=GetModuleHandleW(L"FLT.dll"),t=GetModuleHandleW(L"TDB.dll"),u=GetModuleHandleW(L"U.dll");
    if(strlen(a[1].data.string)!=64||strlen(a[2].data.string)!=64||strlen(a[4].data.string)!=64||
        !module_hash(f,a[1].data.string)||!module_hash(t,a[2].data.string)||!module_hash(u,a[4].data.string)||!primary_root(f,root))return 0;
    if(!module_range(t,parent_get,5)||memcmp((void*)((uintptr_t)t+parent_get),"\x48\x8b\x41\x20\xc3",5)||
        !module_range(t,match_get,5)||memcmp((void*)((uintptr_t)t+match_get),"\x48\x8d\x41\x08\xc3",5))return 0;
    UCHAR *indexed=(UCHAR*)GetProcAddress(t,"?GetNumStreams@TDB_IndexedStreamGroup@@UEBAIXZ");
    if(!indexed||!readable(indexed,19)||memcmp(indexed,"\x48\x8b\x81",3)||memcmp(indexed+7,"\x48\x2b\x81",3)||
        *(uint32_t*)(indexed+3)!=vector+8||*(uint32_t*)(indexed+10)!=vector||memcmp(indexed+14,"\x48\xc1\xf8\x03\xc3",5))return 0;
    if(!module_range(t,count_get,named?19:7))return 0;
    UCHAR *count=(UCHAR*)((uintptr_t)t+count_get);
    if(named){if(memcmp(count,indexed,19))return 0;}
    else if(memcmp(count,"\x8b\x81",2)||*(uint32_t*)(count+2)!=vector+16||count[6]!=0xc3)return 0;
    int codec=codec_probe(u);if(!codec)return 0;
    dynamic_adapter.root=root;dynamic_adapter.vector=vector;dynamic_adapter.named_vector=(int)named;dynamic_adapter.legacy=codec==2;
    adapter=&dynamic_adapter;profile_ok=1;return codec;
}
#endif
static int pf_encode(const wchar_t *text,char *out,int size) {
    BOOL fallback=FALSE;
    int n=WideCharToMultiByte(adapter->legacy?CP_ACP:CP_UTF8,adapter->legacy?WC_NO_BEST_FIT_CHARS:0,text,-1,out,size,NULL,adapter->legacy?&fallback:NULL);
    return n>0&&!fallback;
}
static int child_attached(uintptr_t parent,uintptr_t address,const UCHAR *guid) {
    if(adapter->named_vector){
        uintptr_t begin=*(uintptr_t*)(parent+adapter->vector),end=*(uintptr_t*)(parent+adapter->vector+8);
        if(end<begin||(end-begin)%8||end-begin>8192*8||!readable((void*)begin,end-begin))return 0;
        for(uintptr_t at=begin;at<end;at+=8)if(*(uintptr_t*)at==address)return 1;
    }else{
        uintptr_t head=*(uintptr_t*)(parent+adapter->vector+8);
        if(!readable((void*)head,40))return 0;
        uintptr_t node=*(uintptr_t*)head;
        for(unsigned i=0;node!=head&&i<8192;i++){
            if(!readable((void*)node,40))return 0;
            if(*(uintptr_t*)(node+32)==address&&!memcmp((void*)(node+16),guid,16))return 1;
            node=*(uintptr_t*)node;
        }
    }
    return 0;
}
API long setLabel(TaggedData *a,long count,TaggedData *out) {
    out->type=123;out->data.integer=0;
    if((count!=5&&count!=7)||GetCurrentThreadId()!=script_thread)return 32;
    for(int i=0;i<count;i++)if(a[i].type!=4||!a[i].data.string)return 47;
#ifdef AE_DYNAMIC
    if(!strcmp(a[0].data.string,"configure")){if(count!=5)return 32;int codec=configure(a);out->data.integer=codec;return codec?0:32;}
#endif
    if(!profile_ok)return 32;
    char *end=NULL;uintptr_t address=(uintptr_t)_strtoui64(a[0].data.string,&end,16);if(!address||*end)return 20;
    uintptr_t parent=(uintptr_t)_strtoui64(a[2].data.string,&end,16);if(*end)return 20;
    if(strlen(a[1].data.string)!=32||!readable((void*)address,0x240))return 45;
    UCHAR guid[16];for(int i=0;i<16;i++){int h=hexval(a[1].data.string[i*2]),l=hexval(a[1].data.string[i*2+1]);if(h<0||l<0)return 20;guid[i]=(UCHAR)((h<<4)|l);}
    if(memcmp((void*)(address+8),guid,16)||*(uintptr_t*)(address+0x20)!=parent)return 45;
    /* The stream must still be attached to a currently published effect root.
       A stale undo/copy-on-write object is not a valid mutation target. */
    SIZE_T root_size=adapter->vector+24>0x240?adapter->vector+24:0x240;
    if(!readable((void*)parent,root_size)||*(uintptr_t*)parent!=(uintptr_t)GetModuleHandleW(L"FLT.dll")+adapter->root)return 45;
    uintptr_t parade=*(uintptr_t*)(parent+0x20);
    if(!readable((void*)parade,adapter->vector+24))return 45;
    uintptr_t begin=*(uintptr_t*)(parade+adapter->vector),end_ptr=*(uintptr_t*)(parade+adapter->vector+8);
    if(end_ptr<begin||(end_ptr-begin)%8||end_ptr-begin>8192*8||!readable((void*)begin,end_ptr-begin))return 45;
    int attached=0;for(uintptr_t pos=begin;pos<end_ptr;pos+=8)if(*(uintptr_t*)pos==parent){attached=1;break;}
    if(!attached)return 45;
    if(!child_attached(parent,address,guid))return 45;
    uintptr_t vt=*(uintptr_t*)address;HMODULE owner=NULL;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(LPCWSTR)vt,&owner))return 45;
    if(owner!=GetModuleHandleW(L"TDB.dll")&&owner!=GetModuleHandleW(L"FLT.dll"))return 45;
    typedef const WideString *(*GetName)(const void *);
    GetName get=(GetName)GetProcAddress(GetModuleHandleW(L"TDB.dll"),"?GetDisplayName@TDB_StreamBase@@QEBAAEBV?$basic_string@_WU?$char_traits@_W@std@@U?$STLAllocator@_W@allocator@dvacore@@@std@@XZ");
    if(!get)return 32;
    const WideString *current=get((void*)address);
    if(current->capacity>4096||current->length>current->capacity)return 45;
    const wchar_t *text=current->capacity<=7?current->data.small:current->data.pointer;
    if(!readable(text,(current->length+1)*2)||text[current->length])return 45;
    wchar_t before[512],next[512];
    if(!MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,a[3].data.string,-1,before,512)||
       !MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,a[4].data.string,-1,next,512))return 20;
    wchar_t pf_next[512],expected_display[512];
    if(count==7){
        if(!MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,a[5].data.string,-1,pf_next,512)||
           !MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,a[6].data.string,-1,expected_display,512))return 20;
        if(wcscmp(text,expected_display))return 45;
    }else wcscpy(pf_next,next);
    /* UI cache can lag behind PF metadata after an undo or value edit. The
       exact PF name below is authoritative; SetDisplayName re-syncs the cache. */
    size_t length=wcslen(next);if(!length||strlen(a[4].data.string)>31)return 41;
    char expected_pf[32],next_pf[32];
    if(!pf_encode(before,expected_pf,32)||!pf_encode(pf_next,next_pf,32))return 41;
    /* Inline PF_ParamDef.name is a second, independently verified name source.
       Never touch a value, a pointer, extended strings or any other PF field. */
    char *pf_name=(char*)(address+0x40);char saved[32];
    if(!memchr(pf_name,0,32)||strcmp(pf_name,expected_pf))return 45;
    memcpy(saved,pf_name,32);
    WideString value;memset(&value,0,sizeof(value));value.length=length;
    if(length<=7){memcpy(value.data.small,next,(length+1)*2);value.capacity=7;}
    else{value.data.pointer=next;value.capacity=length;}
    typedef void (*SetName)(void *,const WideString *);
    SetName set=(SetName)GetProcAddress(GetModuleHandleW(L"TDB.dll"),"?SetDisplayName@TDB_StreamBase@@QEAAXAEBV?$basic_string@_WU?$char_traits@_W@std@@U?$STLAllocator@_W@allocator@dvacore@@@std@@@Z");
    if(!set)return 32;
    memset(pf_name,0,32);memcpy(pf_name,next_pf,strlen(next_pf));
    set((void*)address,&value);
    text=current->capacity<=7?current->data.small:current->data.pointer;
    if(current->length!=length||wcscmp(text,next)){memcpy(pf_name,saved,32);return 32;}
    out->data.integer=1;return 0;
}
