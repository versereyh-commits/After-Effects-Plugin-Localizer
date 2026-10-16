/* Local-buffer search only. No process handles, remote reads or writes. */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <emmintrin.h>
#define API __declspec(dllexport)
API int ae_scan_u64(const unsigned char *data,size_t valid,size_t owned,size_t base_mod8,
                   uint64_t target,size_t start,size_t *out,size_t capacity,size_t *written,size_t *next) {
    if(!data||!out||!written||!next||!capacity||base_mod8>7||owned>valid||start>owned)return -1;
    *written=0;*next=owned;
    size_t i=start,shift=(8-((base_mod8+(i&7))&7))&7;
    if(shift>owned-i)return 0;
    i+=shift;
    __m128i needle=_mm_set_epi32((int)(target>>32),(int)target,(int)(target>>32),(int)target);
    for(;i<owned && valid-i>=16 && owned-i>8;i+=16) {
        __m128i word=_mm_loadu_si128((const __m128i *)(data+i));
        int mask=_mm_movemask_epi8(_mm_cmpeq_epi32(word,needle));
        if((mask&255)==255) {
            out[(*written)++]=i;
            if(*written==capacity){*next=i+8<owned?i+8:owned;return 1;}
        }
        if((mask&65280)==65280) {
            out[(*written)++]=i+8;
            if(*written==capacity){*next=i+16<owned?i+16:owned;return 1;}
        }
    }
    for(;i<owned && valid-i>=8;i+=8) {
        uint64_t word;memcpy(&word,data+i,8);
        if(word==target) {
            out[(*written)++]=i;
            if(*written==capacity){*next=i+8<owned?i+8:owned;return 1;}
        }
    }
    return 0;
}
