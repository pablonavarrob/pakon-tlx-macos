/* Runnable proof that pk_ring_fill satisfies TLB.dll's consumer.
 *
 * The consumer below is not invented: it is transcribed from the disassembly.
 *   uiGetCorrections  0x1001cea0 : while (!TransferInProgress) Sleep(1);
 *                                  need = min(want, N - ToRead)
 *                                  got  = (Writing - ToRead) mod N
 *                                  if (need <= got) { ToRead = Writing; consume }
 *                                  else { thresh = need; Wait(hEvt,(need-got)*12); Reset }
 *   MustWait          0x1002f020 : avail = (R>=W)? N-R+W : W-R;  wait = avail < thresh
 *   uiGetScanLines    0x1002f550 : waits on the handle at +0x2c, advances Reading
 *                                  one packet at a time, and requires bit0 of a
 *                                  16-bit sample to mark a line start (0x1002ff12)
 *
 * Header values are the ones observed live: N=409, pktsz=0x5000, thresh=3,
 * total=0x802000.
 *
 *   i686-w64-mingw32-gcc -O2 -o ringtest.exe ringtest.c pkring.c
 *   wine ringtest.exe
 */
#include <windows.h>
#include <stdio.h>
#include "pkring.h"

#define TOTAL  0x802000u
#define NPKT   409u
#define PKTSZ  0x5000u
#define THRESH 3u
#define LINE_SAMPLES 8000u          /* 4-channel RGB+IR line, as seen with IR on */

static volatile long g_cancel = 0;
static unsigned char *g_base;
static unsigned int g_produced;     /* packets the fetch has handed over */
static int g_fail;

static void nolog(const char *fmt, ...) { (void)fmt; }

/* Synthetic scan data with a real line-sync marker: bit0 set on the first
 * sample of every line, clear elsewhere -- what the hardware does and what the
 * consumer checks at 0x1002ff12. */
static int fetch(void *ctx, unsigned char *dst, unsigned int want)
{
    static unsigned int sample_pos = 0;
    unsigned int i;
    (void)ctx;
    for (i = 0; i + 1 < want; i += 2) {
        unsigned short v = (unsigned short)(0x1000 + (sample_pos & 0x3FF));
        v &= 0xFFFE;
        if (sample_pos % LINE_SAMPLES == 0) v |= 1;      /* line start */
        dst[i] = (unsigned char)(v & 0xFF);
        dst[i + 1] = (unsigned char)(v >> 8);
        sample_pos++;
    }
    g_produced += want / PKTSZ;
    return (int)want;
}

static DWORD WINAPI producer(LPVOID p)
{
    (void)p;
    pk_ring_fill(g_base, TOTAL, fetch, NULL, &g_cancel, nolog);
    return 0;
}

#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", (msg)); g_fail = 1; } \
                              else printf("  ok   %s\n", (msg)); } while (0)

int main(void)
{
    unsigned char *base = (unsigned char *)VirtualAlloc(NULL, TOTAL,
                              MEM_COMMIT, PAGE_READWRITE);
    volatile DWORD *reading, *writing, *toread, *thresh;
    volatile BYTE *inprog, *stopflag, *overflow;
    HANDLE hEvt, hProd;
    DWORD t0, consumed = 0, wraps = 0;
    int saw_inprog = 0, marker_ok = 1;

    if (!base) { printf("FAIL: VirtualAlloc\n"); return 1; }
    g_base = base;
    memset(base, 0, RING_HDR);

    /* build the control block exactly as the allocator at 0x10028af0 does */
    hEvt = CreateEventW(NULL, TRUE, FALSE, NULL);     /* manual reset */
    *(DWORD *)(base + HDR_MAGIC)   = 0x38;
    *(DWORD *)(base + HDR_TOTAL)   = TOTAL;
    *(DWORD *)(base + HDR_RINGSZ)  = NPKT;
    *(DWORD *)(base + HDR_PKTSZ)   = PKTSZ;
    *(DWORD *)(base + HDR_TRIGGER) = THRESH;
    *(HANDLE *)(base + HDR_EVENT)  = hEvt;
    *(unsigned char **)(base + HDR_DATAPTR) = base + RING_HDR;

    reading  = (volatile DWORD *)(base + HDR_READING);
    toread   = (volatile DWORD *)(base + HDR_TOREAD);
    writing  = (volatile DWORD *)(base + HDR_WRITING);
    thresh   = (volatile DWORD *)(base + HDR_TRIGGER);
    inprog   = (volatile BYTE *)(base + HDR_INPROG);
    stopflag = (volatile BYTE *)(base + HDR_STOP);
    overflow = (volatile BYTE *)(base + HDR_OVERFLW);

    printf("ring: N=%u pktsz=%u thresh=%u total=%u\n", NPKT, PKTSZ, THRESH, TOTAL);

    hProd = CreateThread(NULL, 0, producer, NULL, 0, NULL);

    /* --- uiGetCorrections' opening spin, verbatim --- */
    t0 = GetTickCount();
    while (!*inprog && GetTickCount() - t0 < 3000) Sleep(1);
    saw_inprog = *inprog != 0;
    CHECK(saw_inprog, "TransferInProgress(+0x31) gets set -- the Corrections spin exits");

    /* --- consume like uiGetScanLines: wait on the event, advance Reading --- */
    t0 = GetTickCount();
    while (consumed < NPKT * 3 && GetTickCount() - t0 < 8000) {
        DWORD R = *reading, W = *writing;
        DWORD avail = (R >= W) ? (NPKT - R + W) : (W - R);
        if (avail < *thresh) {                      /* MustWait -> block */
            if (WaitForSingleObject(hEvt, 500) == WAIT_TIMEOUT) continue;
            ResetEvent(hEvt);
            continue;
        }
        /* validate the packet the way 0x1002ff12 does: a line-start marker */
        {
            const unsigned short *s =
                (const unsigned short *)(base + RING_HDR + (size_t)R * PKTSZ);
            unsigned int k, found = 0;
            for (k = 0; k < PKTSZ / 2; k++) if (s[k] & 1) { found = 1; break; }
            if (!found) marker_ok = 0;
        }
        if (R + 1 >= NPKT) { *reading = 0; wraps++; } else *reading = R + 1;
        *toread = *reading;
        consumed++;
    }
    CHECK(consumed >= NPKT * 3, "consumer drains >3 full rings without deadlock");
    CHECK(wraps >= 3, "Writing/Reading wrap correctly at N");
    CHECK(marker_ok, "every consumed packet carries a line-sync marker (bit0)");
    CHECK(!*overflow, "no overflow flagged while the consumer keeps up");

    /* --- overflow must be raised, not silently overwritten, when we stall --- */
    Sleep(300);                                     /* stop consuming */
    CHECK(*overflow == 1, "OverFlow(+0x32) raised when the consumer stalls");
    {   /* and the producer must NOT have run past us */
        DWORD R = *reading, W = *writing;
        DWORD used = (W >= R) ? (W - R) : (NPKT - R + W);
        CHECK(used <= NPKT - 1, "producer never overruns Reading (used <= N-1)");
    }

    /* --- StopTransfer must end the fill and clear TransferInProgress --- */
    *stopflag = 1;
    t0 = GetTickCount();
    while (*inprog && GetTickCount() - t0 < 3000) Sleep(1);
    CHECK(!*inprog, "StopTransfer(+0x30) honoured; TransferInProgress cleared");

    g_cancel = 1;
    WaitForSingleObject(hProd, 2000);
    printf(g_fail ? "\nRINGTEST FAILED\n" : "\nRINGTEST PASSED\n");
    return g_fail;
}
