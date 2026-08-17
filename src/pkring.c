#include <windows.h>
#include "pkring.h"

unsigned int pk_ring_fill(unsigned char *base, unsigned int totalsize,
                          pk_fetch_fn fetch, void *ctx,
                          volatile long *cancel, pk_log_fn log)
{
    unsigned int total = 0, iter = 0, lastread = 0xFFFFFFFFu;
    unsigned int magic = *(DWORD *)(base + HDR_MAGIC);
    unsigned int npkt  = *(DWORD *)(base + HDR_RINGSZ);
    unsigned int pktsz = *(DWORD *)(base + HDR_PKTSZ);
    unsigned char *data = *(unsigned char **)(base + HDR_DATAPTR);
    volatile DWORD *reading = (volatile DWORD *)(base + HDR_READING);
    volatile DWORD *writing = (volatile DWORD *)(base + HDR_WRITING);
    volatile DWORD *thresh  = (volatile DWORD *)(base + HDR_TRIGGER);
    volatile BYTE *stopflag = (volatile BYTE *)(base + HDR_STOP);
    volatile BYTE *inprog   = (volatile BYTE *)(base + HDR_INPROG);
    volatile BYTE *overflow = (volatile BYTE *)(base + HDR_OVERFLW);
    HANDLE hpkt = *(HANDLE *)(base + HDR_EVENT);
    int degraded = 0;

    if (magic != 0x38 || data != base + RING_HDR || !npkt || !pktsz
        || (size_t)npkt * pktsz > totalsize - RING_HDR) {
        if (log) log("pkring: header unexpected (magic %x npkt %u pktsz %u data %p"
                     " base %p) -- plain fill\n", magic, npkt, pktsz, data, base);
        data = base + RING_HDR;
        npkt = 1; pktsz = totalsize - RING_HDR;
        degraded = 1;
    }

    /* uiGetCorrections blocks on this byte before it does anything at all. */
    *inprog = 1;
    *overflow = 0;
    if (log) log("pkring: npkt=%u pktsz=%u thresh=%u hEvent=%p TransferInProgress=1\n",
                 npkt, pktsz, *thresh, hpkt);

    while (!*cancel) {
        unsigned int w  = degraded ? (total / pktsz) % npkt : *writing;
        unsigned int rd = degraded ? 0 : *reading;
        unsigned int used = (w >= rd) ? (w - rd) : (npkt - rd + w);
        unsigned int freep = (npkt - 1) - used;   /* leave one slot: avail is mod N,
                                                     so a full ring reads as empty */
        unsigned int contig = npkt - w;
        unsigned int n, want;
        int got;

        if (*stopflag) {
            if (log) log("pkring: StopTransfer set by app\n");
            break;
        }
        if (!freep) {                 /* consumer is behind -- tell it, don't clobber */
            *overflow = 1;
            Sleep(1);
            continue;
        }
        *overflow = 0;

        n = freep < contig ? freep : contig;
        if (n > RING_BURST) n = RING_BURST;
        want = n * pktsz;

        got = fetch(ctx, data + (size_t)w * pktsz, want);
        if (got < 0) break;
        if ((unsigned)got < pktsz) { Sleep(1); continue; }   /* whole packets only */

        n = (unsigned)got / pktsz;
        total += n * pktsz;
        w += n;
        if (w >= npkt) w -= npkt;
        if (!degraded) *writing = w;
        if (hpkt) SetEvent(hpkt);     /* EventScanPacketReady */

        if (++iter <= 3 || (iter % 64) == 0 || rd != lastread) {
            if (log) log("pkring: it=%u W=%u R=%u used=%u free=%u +%upkt total=%u\n",
                         iter, w, rd, used, freep, n, total);
            lastread = rd;
        }
    }
    *inprog = 0;                      /* the transfer really has ended */
    return total;
}
