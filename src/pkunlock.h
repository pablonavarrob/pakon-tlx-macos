/* pkunlock -- re-enable the film modes TLXClientDemo.exe greys out.
 *
 * The client offers Negative, Positive, B&W and B&W C41 in its Scan Settings
 * dialog, but disables all but Negative.  None of them carries WS_DISABLED in
 * the dialog RESOURCE -- the greying is done at runtime, by this pattern,
 * repeated once per capability (addresses from TLXClientDemo.exe 106496 bytes):
 *
 *   403ee8:  68 00 00 80 00   push  $0x800000        ; the capability bit
 *   403eed:  8b cf            mov   %edi,%ecx
 *   403eef:  e8 9c 07 00 00   call  0x404690         ; (caps & bit) == bit ?
 *   403ef4:  85 c0            test  %eax,%eax
 *   403ef6:  75 29            jne   0x403f21         ; capable -> skip the disable
 *   403ef8:  50               push  %eax             ; eax is 0 here, i.e. FALSE
 *   403ef9:  68 f7 03 00 00   push  $0x3f7           ; IDC, "Positive"
 *   403efe:  8b ce            mov   %esi,%ecx
 *   403f00:  e8 4b 8a 00 00   call  0x40c950         ; GetDlgItem
 *   403f05:  8b c8            mov   %eax,%ecx
 *   403f07:  e8 50 8a 00 00   call  0x40c95c         ; EnableWindow(FALSE)
 *   ... and the same again for 0x3f8, "B&W"
 *
 * and 0x404690 is nothing but a bitmask test against a DWORD in the object:
 *
 *   mov 0x4(%esp),%eax ; mov 0x28(%ecx),%ecx ; and %eax,%ecx
 *   cmp %ecx,%eax ; sete %dl ; mov %edx,%eax ; ret $0x4
 *
 * So the lock is a flag, not a capability the code lacks.  Turning the `jne`
 * into an unconditional `jmp` skips the disabling block entirely -- including
 * both of its pushes, so the stack stays balanced -- and since the controls are
 * created ENABLED, not disabling them is all that is needed.  One byte per
 * site, 0x75 -> 0xEB.
 *
 * WHY A SIGNATURE AND NOT AN OFFSET.  A raw offset applied to a different OEM
 * build writes a byte into the middle of unrelated code, and the failure would
 * appear much later as something inexplicable.  Every site is therefore matched
 * on the whole 16-byte instruction pattern INCLUDING the capability bit, and an
 * unrecognised build is left completely alone.
 */
#ifndef PKUNLOCK_H
#define PKUNLOCK_H

struct pk_unlock_site {
    unsigned int rva;       /* the `jne` byte itself */
    unsigned int bit;       /* capability bit pushed 14 bytes earlier */
    unsigned int ctrl;      /* first control id the skipped block disables */
    const char  *unlocks;   /* what it ungreys, for the log */
};

#define PK_UNLOCK_NSITES 2
extern const struct pk_unlock_site PK_UNLOCK_SITES[PK_UNLOCK_NSITES];

/* The matched window runs from the `push` to the control id in the block the
 * jump skips: bytes rva-14 through rva+7. */
#define PK_UNLOCK_PATTERN 22
/* How far before the `jne` the `push` sits. */
#define PK_UNLOCK_BACK 14
/* Last byte of the window, measured forward from the `jne`. */
#define PK_UNLOCK_FWD 7

enum {
    PK_UNLOCK_NOMATCH = 0,  /* not this build -- do not touch it */
    PK_UNLOCK_LOCKED,       /* pattern present, still `jne`: patchable */
    PK_UNLOCK_DONE          /* pattern present, already `jmp` */
};

/* Classify a site. `size` bounds the image; 0 means "not known, trust the rva". */
int pk_unlock_state(const unsigned char *image, unsigned int size,
                    const struct pk_unlock_site *site);

/* Flip `jne` to `jmp`. Re-checks the signature itself, so it can never write
 * into a build it does not recognise. 1 = a byte changed, 0 = nothing done. */
int pk_unlock_apply(unsigned char *image, unsigned int size,
                    const struct pk_unlock_site *site);

#endif
