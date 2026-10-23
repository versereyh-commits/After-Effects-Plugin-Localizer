/* Read-only ExtendScript helper: no AE layout, project or mutation APIs. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>
#include <stdio.h>
typedef struct {union {long integer;double number;char *string;void *object;} data;long type,filler;} TaggedData;
#define API __declspec(dllexport)
API long ESGetVersion(void){return 1;}
API char *ESInitialize(TaggedData *a,long n){(void)a;(void)n;return "identity";}
API void ESTerminate(void){}
API void ESFreeMem(void *p){free(p);}
API long identity(TaggedData *a,long n,TaggedData *out){
    (void)a;(void)n;wchar_t path[32768];FILETIME c,e,k,u;
    DWORD length=GetModuleFileNameW(NULL,path,32768);
    if(!length||length>=32768||!GetProcessTimes(GetCurrentProcess(),&c,&e,&k,&u))return 32;
    char *json=malloc(200000);if(!json)return 32;
    int pos=sprintf(json,"{\"pid\":%lu,\"creationTime\":\"%08lx%08lx\",\"image\":\"",GetCurrentProcessId(),c.dwHighDateTime,c.dwLowDateTime);
    for(int i=0;path[i];i++)pos+=sprintf(json+pos,"\\u%04x",(unsigned)path[i]);
    sprintf(json+pos,"\"}");out->type=4;out->data.string=json;return 0;
}
