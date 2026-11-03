/* Popup text only. Separate helper so old label callers keep their ABI.
   Equal byte lengths are essential: AE copies strings when editing/undoing. */
#define ESInitialize LabelInitialize
#define setLabel LabelConfigure
#include "ae_parameters.c"
#undef ESInitialize
#undef setLabel
API char *ESInitialize(TaggedData *a,long n){LabelInitialize(a,n);return "setLabel_sssss,setPopup_sssss";}
API long setLabel(TaggedData *a,long n,TaggedData *out){
    if(n!=5)return 32;
    for(int i=0;i<5;i++)if(a[i].type!=4||!a[i].data.string)return 47;
    return LabelConfigure(a,n,out);
}
static int number(const char *s,uintptr_t *v){char *end;*v=_strtoui64(s,&end,16);return *s&&!*end;}
API long setPopup(TaggedData *a,long n,TaggedData *out){
    out->type=123;out->data.integer=0;
    if(n!=9||!profile_ok||GetCurrentThreadId()!=script_thread)return 32;
    for(int i=0;i<9;i++)if(a[i].type!=4||!a[i].data.string)return 47;
    uintptr_t address,parent,pointer,flags,def;
    if(!number(a[0].data.string,&address)||!number(a[2].data.string,&parent)||!number(a[3].data.string,&pointer)||!number(a[7].data.string,&flags)||!number(a[8].data.string,&def))return 20;
    if(strlen(a[1].data.string)!=32||!readable((void*)address,0x240))return 45;
    UCHAR guid[16];for(int i=0;i<16;i++){int h=hexval(a[1].data.string[i*2]),l=hexval(a[1].data.string[i*2+1]);if(h<0||l<0)return 20;guid[i]=(h<<4)|l;}
    if(memcmp((void*)(address+8),guid,16)||*(uintptr_t*)(address+32)!=parent)return 45;
    SIZE_T size=adapter->vector+24>0x240?adapter->vector+24:0x240;
    if(adapter->map+16>size)size=adapter->map+16;
    if(!readable((void*)parent,size)||*(uintptr_t*)parent!=(uintptr_t)GetModuleHandleW(L"FLT.dll")+adapter->root)return 45;
    uintptr_t parade=*(uintptr_t*)(parent+32);if(!readable((void*)parade,adapter->vector+24))return 45;
    uintptr_t begin=*(uintptr_t*)(parade+adapter->vector),end=*(uintptr_t*)(parade+adapter->vector+8);
    if(end<begin||(end-begin)%8||end-begin>8192*8||!readable((void*)begin,end-begin))return 45;
    int attached=0;for(uintptr_t at=begin;at<end;at+=8)if(*(uintptr_t*)at==parent)attached=1;
    if(!attached||!child_attached(parent,address,guid))return 45;
    HMODULE owner=NULL;if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(LPCWSTR)*(uintptr_t*)address,&owner))return 45;
    if(owner!=GetModuleHandleW(L"TDB.dll")&&owner!=GetModuleHandleW(L"FLT.dll"))return 45;
    /* Script strings are UTF-8; PF strings use the verified host codec (legacy ACP or UTF-8). */
    wchar_t wide[8192];char before[8192],after[8192],pf_name[32];
    if(!MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,a[4].data.string,-1,wide,8192)||!pf_encode(wide,before,8192))return 41;
    if(!MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,a[5].data.string,-1,wide,8192)||!pf_encode(wide,after,8192))return 41;
    if(!MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,a[6].data.string,-1,wide,8192)||!pf_encode(wide,pf_name,32))return 41;
    size_t name_size=strlen(pf_name)+1;
    if(name_size>32||*(uint32_t*)(address+0x3c)!=7||memcmp((void*)(address+0x40),pf_name,name_size)||*(uint32_t*)(address+0x60)!=flags)return 45;
    unsigned count=1,after_count=1;for(char *s=a[4].data.string;*s;s++)if(*s=='|')count++;for(char *s=a[5].data.string;*s;s++)if(*s=='|')after_count++;
    size_t capacity=strlen(before)+1;
    if(capacity<4||capacity>8192||strlen(after)+1!=capacity||count<2||count>256||count!=after_count)return 20;
    if(*(uint16_t*)(address+0x6c)!=count||*(uint16_t*)(address+0x6e)!=def||def<1||def>count||*(uint32_t*)(address+0x68)<1||*(uint32_t*)(address+0x68)>count||*(uintptr_t*)(address+0x70)!=pointer)return 45;
    MEMORY_BASIC_INFORMATION m;
    if(!VirtualQuery((void*)pointer,&m,sizeof(m))||m.State!=MEM_COMMIT||m.Type!=MEM_PRIVATE||(m.Protect&(PAGE_GUARD|PAGE_NOACCESS))||((m.Protect&255)!=PAGE_READWRITE&&(m.Protect&255)!=PAGE_WRITECOPY)||pointer+capacity>(uintptr_t)m.BaseAddress+m.RegionSize)return 45;
    if(memcmp((void*)pointer,before,capacity))return 45;
    /* No allocation, pointer replacement, page-protection change or parameter value write. */
    memcpy((void*)pointer,after,capacity);
    if(memcmp((void*)pointer,after,capacity)){memcpy((void*)pointer,before,capacity);return 32;}
    out->data.integer=1;return 0;
}
