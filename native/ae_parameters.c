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

typedef struct { union {long integer;double number;char *string;void *object;} data;long type,filler;} TaggedData;
typedef struct {union {wchar_t small[8];wchar_t *pointer;} data;uint64_t length,capacity;} WideString;
#define API __declspec(dllexport)
static DWORD script_thread;
static int profile_ok;
/* map: where a named group's stream list starts (its head; the count getter reads the size 8 bytes later).
   It was vector+8 up to AE 26.0; AE 26.5 moved it, so a dynamic adapter passes it explicitly. */
typedef struct {const char *flt,*tdb;uintptr_t root,vector;int named_vector,legacy;uintptr_t map;} Adapter;
static const Adapter adapters[]={
 {"68b229950e95dedf630f5d09653715268e8beba4830590433abe7cb58e78a357","8e1ea3d5e08d91dd25e899d43e0ad69a525b161716bb4c29250e3711b2591c80",0xc8e80,0x220,0,0,0x228},
 {"a4896d34e20f1fb08ba9afd0fc6e428081bd51dba6abb39e87aa6342c973a183","6d8f80729ef02538415bb6578399cee667101b6c218ea73d85d74357558b660e",0xce2f0,0x220,0,1,0x228},
 {"abf6673b380450cc98549876c3738135bc025345b619cc4cba9d17bf98d05bc9","2214da04c37c197f8cefb3cc825eaab6f45efa3c1bdd300e2157842e4b38113d",0x8f0f0,0x190,1,1,0x198}
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
/* Breadcrumb for a call that never returns (AE 2025 25.6 hung while hashing FLT.dll): the step reached is written
   to %TEMP%\AE-Localizer-native-step.txt, which the program reads when a request times out. Only a step name. */
static void step(const char *name) {
    wchar_t path[MAX_PATH+40];DWORD n=GetTempPathW(MAX_PATH,path);
    if(!n||n>MAX_PATH)return;
    wcscat(path,L"AE-Localizer-native-step.txt");
    HANDLE file=CreateFileW(path,GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
    if(file==INVALID_HANDLE_VALUE)return;
    DWORD written=0;WriteFile(file,name,(DWORD)strlen(name),&written,NULL);CloseHandle(file);
}
/* SHA-256 (FIPS 180-4) computed here rather than through Windows CNG (bcrypt), so hashing AE's modules depends on
   nothing but reading the file: on that AE 2025 25.6 the first module hash never returned. */
typedef struct {uint32_t h[8];uint64_t length;unsigned char block[64];size_t used;} Sha256;
static const uint32_t sha_k[64]={
 0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,
 0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
 0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,
 0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
 0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
 0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
#define ROR32(x,n) (((x)>>(n))|((x)<<(32-(n))))
static void sha_block(Sha256 *s,const unsigned char *p) {
    uint32_t w[64],v[8];
    for(int i=0;i<16;i++)w[i]=(uint32_t)p[i*4]<<24|(uint32_t)p[i*4+1]<<16|(uint32_t)p[i*4+2]<<8|(uint32_t)p[i*4+3];
    for(int i=16;i<64;i++)w[i]=w[i-16]+(ROR32(w[i-15],7)^ROR32(w[i-15],18)^(w[i-15]>>3))+w[i-7]+(ROR32(w[i-2],17)^ROR32(w[i-2],19)^(w[i-2]>>10));
    memcpy(v,s->h,sizeof(v));
    for(int i=0;i<64;i++){
        uint32_t t1=v[7]+(ROR32(v[4],6)^ROR32(v[4],11)^ROR32(v[4],25))+((v[4]&v[5])^(~v[4]&v[6]))+sha_k[i]+w[i];
        uint32_t t2=(ROR32(v[0],2)^ROR32(v[0],13)^ROR32(v[0],22))+((v[0]&v[1])^(v[0]&v[2])^(v[1]&v[2]));
        v[7]=v[6];v[6]=v[5];v[5]=v[4];v[4]=v[3]+t1;v[3]=v[2];v[2]=v[1];v[1]=v[0];v[0]=t1+t2;
    }
    for(int i=0;i<8;i++)s->h[i]+=v[i];
}
static void sha_init(Sha256 *s) {
    static const uint32_t iv[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    memcpy(s->h,iv,sizeof(iv));s->length=0;s->used=0;
}
static void sha_update(Sha256 *s,const unsigned char *p,size_t n) {
    s->length+=n;
    while(n){size_t take=64-s->used;if(take>n)take=n;memcpy(s->block+s->used,p,take);s->used+=take;p+=take;n-=take;if(s->used==64){sha_block(s,s->block);s->used=0;}}
}
static void sha_final(Sha256 *s,unsigned char out[32]) {
    uint64_t bits=s->length*8;s->block[s->used++]=0x80;
    if(s->used>56){memset(s->block+s->used,0,64-s->used);sha_block(s,s->block);s->used=0;}
    memset(s->block+s->used,0,56-s->used);
    for(int i=0;i<8;i++)s->block[56+i]=(unsigned char)(bits>>(56-8*i));
    sha_block(s,s->block);
    for(int i=0;i<8;i++){out[i*4]=(unsigned char)(s->h[i]>>24);out[i*4+1]=(unsigned char)(s->h[i]>>16);out[i*4+2]=(unsigned char)(s->h[i]>>8);out[i*4+3]=(unsigned char)s->h[i];}
}
/* Hashing a module file runs on a worker thread that reads it through a read-only file mapping (no ReadFile), and is
   given up after HASH_WAIT. On one AE 2025 25.6, ReadFile on AE's own FLT.dll never returned inside AE (reading the same
   file from another process worked) -- something in that process intercepts file reads. A mapping usually takes another
   path; if even that stalls, AE is not held: the step is marked ":timeout" and the check fails as unverified. The job
   lives on the heap and is deliberately leaked on a timeout (the stuck worker still owns it), and the helper pins itself
   so it is never unloaded under that worker. Nothing large sits on the script thread's stack. */
#ifndef HASH_WAIT
#define HASH_WAIT 10000
#endif
typedef struct {wchar_t path[32768];char crumb[64];size_t base;unsigned char digest[32];int ok;volatile LONG abandoned;} HashJob;
/* A worker that was given up writes no more steps, so the "+timeout" mark stays the last one. */
static void job_step(HashJob *j,const char *sub){if(InterlockedCompareExchange(&j->abandoned,0,0))return;strcpy(j->crumb+j->base,sub);step(j->crumb);}
static DWORD WINAPI hash_worker(LPVOID parameter) {
    HashJob *j=(HashJob*)parameter;
    job_step(j,":open");
    HANDLE file=CreateFileW(j->path,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,OPEN_EXISTING,0,NULL);
    if(file==INVALID_HANDLE_VALUE)return 0;
    LARGE_INTEGER size;HANDLE map=NULL;const unsigned char *view=NULL;
    job_step(j,":map");
    if(!GetFileSizeEx(file,&size)||size.QuadPart<=0||(uint64_t)size.QuadPart>(uint64_t)SIZE_MAX)goto done;
    map=CreateFileMappingW(file,NULL,PAGE_READONLY,0,0,NULL);if(!map)goto done;
    view=(const unsigned char*)MapViewOfFile(map,FILE_MAP_READ,0,0,0);if(!view)goto done;
    job_step(j,":hash");
    Sha256 s;sha_init(&s);sha_update(&s,view,(size_t)size.QuadPart);sha_final(&s,j->digest);j->ok=1;
done:
    if(view)UnmapViewOfFile(view);if(map)CloseHandle(map);CloseHandle(file);return 0;
}
/* Waits for the worker while letting messages other threads *send* to this thread through: code injected into AE
   that stalls the worker until AE's main window answers a SendMessage would otherwise deadlock with this wait.
   Posted messages stay queued and nothing is dispatched (PM_NOREMOVE | PM_QS_SENDMESSAGE only runs sent ones). */
static int wait_worker(HANDLE worker,DWORD limit) {
    ULONGLONG until=GetTickCount64()+limit;
    for(;;){
        ULONGLONG now=GetTickCount64();DWORD left=now>=until?0:(DWORD)(until-now);
        DWORD r=MsgWaitForMultipleObjects(1,&worker,FALSE,left,QS_SENDMESSAGE);
        if(r==WAIT_OBJECT_0)return 1;
        if(r!=WAIT_OBJECT_0+1)return 0;   /* timeout or failure */
        MSG msg;PeekMessageW(&msg,NULL,0,0,PM_NOREMOVE|PM_QS_SENDMESSAGE);
    }
}
static int module_hash(HMODULE module,const char *expected,const char *label) {
    static int pinned;HMODULE self;
    if(!pinned&&GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,(LPCWSTR)(void*)hash_worker,&self))pinned=1;
    if(!pinned||strlen(label)>40)return 0;
    HashJob *j=(HashJob*)HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,sizeof(HashJob));if(!j)return 0;
    strcpy(j->crumb,"hash");strcat(j->crumb,label);j->base=strlen(j->crumb);
    if(!GetModuleFileNameW(module,j->path,32768)){HeapFree(GetProcessHeap(),0,j);return 0;}
    HANDLE worker=CreateThread(NULL,0,hash_worker,j,0,NULL);
    if(!worker){HeapFree(GetProcessHeap(),0,j);return 0;}
    if(!wait_worker(worker,HASH_WAIT)){
        /* The worker may still write into j: it is left to it. The crumb names where it stopped, then ":timeout". */
        InterlockedExchange(&j->abandoned,1);
        char crumb[80];strcpy(crumb,j->crumb);strcat(crumb,"+timeout");step(crumb);CloseHandle(worker);return 0;
    }
    CloseHandle(worker);
    int ok=0;
    if(j->ok){char hex[65];static const char digits[]="0123456789abcdef";
        for(int i=0;i<32;i++){hex[i*2]=digits[j->digest[i]>>4];hex[i*2+1]=digits[j->digest[i]&15];}hex[64]=0;ok=!strcmp(hex,expected);}
    HeapFree(GetProcessHeap(),0,j);return ok;
}
static int profile(void) {
    HMODULE t=GetModuleHandleW(L"TDB.dll"),f=GetModuleHandleW(L"FLT.dll");
    if(!t||!f)return 0;
    for(unsigned i=0;i<sizeof(adapters)/sizeof(adapters[0]);i++)
        if(module_hash(t,adapters[i].tdb,"TDB")&&module_hash(f,adapters[i].flt,"FLT")){adapter=&adapters[i];return 1;}
    return 0;
}
API long ESGetVersion(void){return 1;}
API char *ESInitialize(TaggedData *a,long count){
    (void)a;(void)count;script_thread=GetCurrentThreadId();
#ifdef AE_DYNAMIC
    /* Loading does as little as possible: a dynamic adapter is set (and its modules hashed) by "configure";
       the built-in builds use the v8 helper. */
    profile_ok=0;
#else
    profile_ok=profile();
#endif
    return "setLabel_sssss";}
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
    /* The string is built in a static block far larger than the verified 32-byte layout, the rest filled with a
       canary: a build whose string is bigger then writes into our own spare bytes, not over the stack, and is
       refused as unsupported instead of crashing AE. */
    static union {WideString value;unsigned char raw[256];} block;
    memset(block.raw,0xA5,sizeof(block.raw));memset(&block.value,0,sizeof(block.value));block.value.capacity=7;
    WideString *value=&block.value;
    if(convert(value,sample)!=value)return 0;
    for(size_t i=sizeof(WideString);i<sizeof(block.raw);i++)if(block.raw[i]!=0xA5)return 0;
    if(value->capacity>7||value->length>6||value->data.small[value->length])return 0;
    if(value->length==2&&value->data.small[0]==0x4e2d&&value->data.small[1]==0x4e2d)return 1;
    wchar_t expected[8];int n=MultiByteToWideChar(CP_ACP,0,sample,-1,expected,8);
    return n>0&&!wcscmp(value->data.small,expected)?2:0;
}
static int configure(TaggedData *a) {
    uintptr_t v[7];char *end=NULL,*pos=a[3].data.string;
    for(int i=0;i<7;i++){
        v[i]=(uintptr_t)_strtoui64(pos,&end,16);if(end==pos)return 0;
        if(i<6){if(*end!=',')return 0;pos=end+1;}else if(*end)return 0;
    }
    uintptr_t root=v[0],vector=v[1],map=v[2],named=v[3],parent_get=v[4],match_get=v[5],count_get=v[6];
    if(vector<0x80||vector>0x800||vector%8||named>1)return 0;
    if(named?map!=vector+8:(map%8||map<=vector||map>0x800))return 0;
    HMODULE f=GetModuleHandleW(L"FLT.dll"),t=GetModuleHandleW(L"TDB.dll"),u=GetModuleHandleW(L"U.dll");
    if(strlen(a[1].data.string)!=64||strlen(a[2].data.string)!=64||strlen(a[4].data.string)!=64)return 0;
    step("hashFLT");if(!module_hash(f,a[1].data.string,"FLT"))return 0;
    step("hashTDB");if(!module_hash(t,a[2].data.string,"TDB"))return 0;
    step("hashU");if(!module_hash(u,a[4].data.string,"U"))return 0;
    step("checks");if(!primary_root(f,root))return 0;
    if(!module_range(t,parent_get,5)||memcmp((void*)((uintptr_t)t+parent_get),"\x48\x8b\x41\x20\xc3",5)||
        !module_range(t,match_get,5)||memcmp((void*)((uintptr_t)t+match_get),"\x48\x8d\x41\x08\xc3",5))return 0;
    UCHAR *indexed=(UCHAR*)GetProcAddress(t,"?GetNumStreams@TDB_IndexedStreamGroup@@UEBAIXZ");
    if(!indexed||!readable(indexed,19)||memcmp(indexed,"\x48\x8b\x81",3)||memcmp(indexed+7,"\x48\x2b\x81",3)||
        *(uint32_t*)(indexed+3)!=vector+8||*(uint32_t*)(indexed+10)!=vector||memcmp(indexed+14,"\x48\xc1\xf8\x03\xc3",5))return 0;
    if(!module_range(t,count_get,named?19:7))return 0;
    UCHAR *count=(UCHAR*)((uintptr_t)t+count_get);
    if(named){if(memcmp(count,indexed,19))return 0;}
    else if(memcmp(count,"\x8b\x81",2)||*(uint32_t*)(count+2)!=map+8||count[6]!=0xc3)return 0;
    step("codecCall");int codec=codec_probe(u);step(codec?"codecReturned":"codecRefused");if(!codec)return 0;
    dynamic_adapter.root=root;dynamic_adapter.vector=vector;dynamic_adapter.map=map;dynamic_adapter.named_vector=(int)named;dynamic_adapter.legacy=codec==2;
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
        uintptr_t head=*(uintptr_t*)(parent+adapter->map);
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
