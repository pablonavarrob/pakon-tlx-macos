/* pkring -- the driver side of TLB.dll's scan-line ring.
 *
 * Layout and ownership are read out of TLB.dll, not chosen by us:
 *   allocator            0x10028af0  writes magic/sizes/event/data pointer
 *   consumer "MustWait"  0x1002f020  avail = (R>=W)? N-R+W : W-R;  wait = avail < thresh
 *   uiGetCorrections     0x1001cea0  spins on TransferInProgress(+0x31) before anything else
 *   uiGetScanLines       0x1002f550  waits on the event handle at +0x2c
 *
 * Reading/Writing are PACKET INDICES into a ring of hdr[0x0c] packets of
 * hdr[0x24] bytes; the data area begins at base+0x1000.
 */
#ifndef PKRING_H
#define PKRING_H

#define RING_HDR    4096    /* the first page is the control block */
#define HDR_MAGIC   0x00    /* = 0x38, header size */
#define HDR_TOTAL   0x04
#define HDR_RINGSZ  0x0c    /* packet COUNT */
#define HDR_READING 0x14    /* consumer tail  (app owns) */
#define HDR_TOREAD  0x18    /* app owns */
#define HDR_WRITING 0x1c    /* producer head  (DRIVER owns) */
#define HDR_PKTSZ   0x24    /* packet size in bytes */
#define HDR_TRIGGER 0x28    /* threshold in packets */
#define HDR_EVENT   0x2c    /* HANDLE EventScanPacketReady (DRIVER signals) */
#define HDR_STOP    0x30    /* byte, app asks us to stop */
#define HDR_INPROG  0x31    /* byte, DRIVER owns */
#define HDR_OVERFLW 0x32    /* byte, DRIVER owns */
#define HDR_DATAPTR 0x34

#define RING_BURST  8       /* packets fetched per iteration */

/* Fill dst with up to `want` bytes. Return bytes produced (0 = nothing ready
 * yet, caller will retry), or -1 for a fatal transport error. */
typedef int (*pk_fetch_fn)(void *ctx, unsigned char *dst, unsigned int want);
typedef void (*pk_log_fn)(const char *fmt, ...);

/* Runs until *cancel is set, the app raises StopTransfer, or fetch fails.
 * Returns total bytes published. Sets/clears TransferInProgress itself. */
unsigned int pk_ring_fill(unsigned char *base, unsigned int totalsize,
                          pk_fetch_fn fetch, void *ctx,
                          volatile long *cancel, pk_log_fn log);

#endif
