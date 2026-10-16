/* Read-only Windows process/module diagnostics. Never requests VM_WRITE rights. */
#define UNICODE
#define _UNICODE
#define PSAPI_VERSION 1
#include <windows.h>
#include <tlhelp32.h>
#include <psapi.h>
#include <stdio.h>
#include <stdint.h>
#include <stdarg.h>
#include <string.h>
#include <wchar.h>

typedef struct { char *buffer; size_t capacity, used; int failed; } Json;
static void emit(Json *j, const char *format, ...) {
    if(j->failed) return;
    va_list args; va_start(args,format);
    int n=vsnprintf(j->buffer+j->used,j->capacity-j->used,format,args); va_end(args);
    if(n<0 || (size_t)n>=j->capacity-j->used) { j->failed=1; return; }
    j->used+=(size_t)n;
}
static void string(Json *j,const wchar_t *value) {
    char text[32768];
    int count=WideCharToMultiByte(CP_UTF8,0,value,-1,text,sizeof(text),NULL,NULL);
    if(!count) { emit(j,"null"); return; }
    emit(j,"\"");
    for(int i=0;i<count-1;i++) {
        unsigned char c=(unsigned char)text[i];
        if(c=='"' || c=='\\') emit(j,"\\%c",c);
        else if(c<32) emit(j,"\\u%04x",c);
        else emit(j,"%c",c);
    }
    emit(j,"\"");
}
static void version(Json *j,const wchar_t *path) {
    DWORD ignored=0,size=GetFileVersionInfoSizeW(path,&ignored);
    if(!size) { emit(j,"null"); return; }
    void *data=HeapAlloc(GetProcessHeap(),0,size); VS_FIXEDFILEINFO *info=NULL; UINT length=0;
    if(data && GetFileVersionInfoW(path,0,size,data) && VerQueryValueW(data,L"\\",(void**)&info,&length))
        emit(j,"\"%u.%u.%u.%u\"",HIWORD(info->dwFileVersionMS),LOWORD(info->dwFileVersionMS),HIWORD(info->dwFileVersionLS),LOWORD(info->dwFileVersionLS));
    else emit(j,"null");
    if(data) HeapFree(GetProcessHeap(),0,data);
}
static void string_hits(Json *j,HANDLE process,uintptr_t base,DWORD size) {
    static const unsigned char narrow[]={'S','_','G','l','o','w',0};
    static const unsigned char wide[]={'S',0,'_',0,'G',0,'l',0,'o',0,'w',0,0,0};
    unsigned char buffer[65536+14]; uintptr_t cursor=base,end=base+size;
    unsigned narrow_count=0,wide_count=0; size_t bytes_read=0;
    uintptr_t offsets[12]; unsigned saved=0;
    while(cursor<end) {
        MEMORY_BASIC_INFORMATION mbi;
        if(!VirtualQueryEx(process,(void*)cursor,&mbi,sizeof(mbi))) break;
        uintptr_t region_end=(uintptr_t)mbi.BaseAddress+mbi.RegionSize;
        if(region_end>end) region_end=end;
        if(region_end<=cursor) break;
        DWORD p=mbi.Protect&0xff;
        int readable=(p==PAGE_READONLY || p==PAGE_READWRITE || p==PAGE_WRITECOPY || p==PAGE_EXECUTE_READ || p==PAGE_EXECUTE_READWRITE || p==PAGE_EXECUTE_WRITECOPY);
        if(mbi.State==MEM_COMMIT && readable && !(mbi.Protect&PAGE_GUARD)) {
            for(uintptr_t at=cursor;at<region_end;at+=65536) {
                SIZE_T request=region_end-at;
                if(request>sizeof(buffer)) request=sizeof(buffer);
                SIZE_T got=0;
                if(!ReadProcessMemory(process,(void*)at,buffer,request,&got)) continue;
                size_t limit=(size_t)(region_end-at); if(limit>65536) limit=65536;
                bytes_read+=got;
                for(size_t i=0;i<limit;i++) {
                    if(i+sizeof(narrow)<=got && !memcmp(buffer+i,narrow,sizeof(narrow))) {
                        narrow_count++; if(saved<12) offsets[saved++]=at+i-base;
                    }
                    if(i+sizeof(wide)<=got && !memcmp(buffer+i,wide,sizeof(wide))) wide_count++;
                }
            }
        }
        cursor=region_end;
    }
    emit(j,",\"asciiNameHits\":%u,\"utf16NameHits\":%u,\"readBytesIncludingOverlap\":%llu,\"firstAsciiOffsets\":[",narrow_count,wide_count,(unsigned long long)bytes_read);
    for(unsigned i=0;i<saved;i++) emit(j,"%s%llu",i?",":"",(unsigned long long)offsets[i]);
    emit(j,"]");
}
__declspec(dllexport) int ae_file_version_json(const wchar_t *path,char *output,uint32_t capacity) {
    if(!path || !output || capacity<32) return -1;
    Json j={output,capacity,0,0}; version(&j,path); return j.failed?-1:(int)j.used;
}
__declspec(dllexport) int ae_probe_json(char *output,uint32_t capacity) {
    if(!output || capacity<32) return -1;
    Json j={output,capacity,0,0};
    emit(&j,"{\"schemaVersion\":1,\"mode\":\"read_only\",\"runtimeRenameEnabled\":false,\"memoryWrites\":0,\"processes\":[");
    HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);
    PROCESSENTRY32W entry; memset(&entry,0,sizeof(entry)); entry.dwSize=sizeof(entry);
    unsigned process_count=0;
    if(snapshot!=INVALID_HANDLE_VALUE && Process32FirstW(snapshot,&entry)) do {
        if(_wcsicmp(entry.szExeFile,L"AfterFX.exe")) continue;
        HANDLE process=OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,FALSE,entry.th32ProcessID);
        if(!process) {
            DWORD error=GetLastError();
            /* Toolhelp can retain an exited -r relay while enumerating the
               primary host. Exclude only a proven ended process; inaccessible
               live AE instances must still block an ambiguous write. */
            HANDLE query=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,entry.th32ProcessID);
            if(query){DWORD code=STILL_ACTIVE;BOOL known=GetExitCodeProcess(query,&code);CloseHandle(query);if(known&&code!=STILL_ACTIVE)continue;}
            else if(GetLastError()==ERROR_INVALID_PARAMETER)continue;
            emit(&j,"%s{\"pid\":%lu,\"error\":%lu}",process_count++?",":"",entry.th32ProcessID,error);continue;
        }
        DWORD code=STILL_ACTIVE;if(GetExitCodeProcess(process,&code)&&code!=STILL_ACTIVE){CloseHandle(process);continue;}
        emit(&j,"%s{\"pid\":%lu",process_count++?",":"",entry.th32ProcessID);
        wchar_t image[32768]; DWORD image_length=32768;
        emit(&j,",\"imagePath\":");
        if(QueryFullProcessImageNameW(process,0,image,&image_length)) { string(&j,image); emit(&j,",\"fileVersion\":"); version(&j,image); }
        else emit(&j,"null");
        HMODULE modules[4096]; DWORD needed=0; unsigned selected=0,loaded=0;
        emit(&j,",\"inspectedModules\":[");
        if(EnumProcessModulesEx(process,modules,sizeof(modules),&needed,LIST_MODULES_64BIT)) {
            unsigned count=needed/sizeof(HMODULE); if(count>4096) count=4096;
            for(unsigned i=0;i<count;i++) {
                wchar_t path[32768],name[512];
                if(!GetModuleFileNameExW(process,modules[i],path,32768)) continue;
                if(!GetModuleBaseNameW(process,modules[i],name,512)) continue;
                size_t len=wcslen(name); if(len>4 && !_wcsicmp(name+len-4,L".aex")) loaded++;
                if(_wcsicmp(name,L"AfterFXLib.dll") && _wcsicmp(name,L"AfterFX.exe") && _wcsicmp(name,L"S_Glow.aex")) continue;
                MODULEINFO info; if(!GetModuleInformation(process,modules[i],&info,sizeof(info))) continue;
                emit(&j,"%s{\"name\":",selected++?",":""); string(&j,name);
                emit(&j,",\"path\":"); string(&j,path); emit(&j,",\"imageSize\":%lu",info.SizeOfImage);
                string_hits(&j,process,(uintptr_t)info.lpBaseOfDll,info.SizeOfImage);
                emit(&j,"}");
            }
        }
        emit(&j,"],\"loadedAexModuleCount\":%u}",loaded); CloseHandle(process);
    } while(Process32NextW(snapshot,&entry));
    if(snapshot!=INVALID_HANDLE_VALUE) CloseHandle(snapshot);
    emit(&j,"]}"); return j.failed?-1:(int)j.used;
}
