/* See pkunlock.h for the disassembly this is derived from. */
#include "pkunlock.h"

/* Both gates sit in the same run of capability checks in the Scan Settings
 * dialog's initialiser.  Positive and B&W share ONE bit, so one byte ungreys
 * both; B&W C41 has its own.  Negative is never disabled and has no site. */
const struct pk_unlock_site PK_UNLOCK_SITES[PK_UNLOCK_NSITES] = {
    { 0x3ef6, 0x800000, 0x3f7, "Positive and B&W" },
    { 0x3ed2, 0x400000, 0x3f9, "B&W C41" },
};

#define OP_PUSH_IMM32 0x68
#define OP_JNE_REL8   0x75
#define OP_PUSH_EAX   0x50
#define OP_JMP_REL8   0xEB

int pk_unlock_state(const unsigned char *image, unsigned int size,
                    const struct pk_unlock_site *site)
{
    const unsigned char *p;
    unsigned int start;

    if (!image || !site || site->rva < PK_UNLOCK_BACK)
        return PK_UNLOCK_NOMATCH;
    start = site->rva - PK_UNLOCK_BACK;
    /* The window extends past the jump into the block it skips, so the site
     * needs PK_UNLOCK_FWD bytes beyond the opcode.  Checked before any
     * dereference, so a truncated or wrong module cannot be walked off. */
    if (size && site->rva + PK_UNLOCK_FWD >= size)
        return PK_UNLOCK_NOMATCH;

    p = image + start;
    if (p[0] != OP_PUSH_IMM32)
        return PK_UNLOCK_NOMATCH;
    if ((unsigned int)p[1]         != (site->bit & 0xFF) ||
        (unsigned int)p[2]         != ((site->bit >> 8) & 0xFF) ||
        (unsigned int)p[3]         != ((site->bit >> 16) & 0xFF) ||
        (unsigned int)p[4]         != ((site->bit >> 24) & 0xFF))
        return PK_UNLOCK_NOMATCH;
    if (p[5] != 0x8b || p[6] != 0xcf)          /* mov %edi,%ecx */
        return PK_UNLOCK_NOMATCH;
    if (p[7] != 0xe8)                          /* call rel32; target unchecked */
        return PK_UNLOCK_NOMATCH;
    if (p[12] != 0x85 || p[13] != 0xc0)        /* test %eax,%eax */
        return PK_UNLOCK_NOMATCH;

    if (p[14] != OP_JNE_REL8 && p[14] != OP_JMP_REL8)
        return PK_UNLOCK_NOMATCH;

    /* Past the jump, inside the block it skips: push %eax (the FALSE that
     * reaches EnableWindow) then push $ctrl.  This is what says the gate
     * guards the control we mean. */
    if (p[16] != OP_PUSH_EAX || p[17] != OP_PUSH_IMM32)
        return PK_UNLOCK_NOMATCH;
    if ((unsigned int)p[18] != (site->ctrl & 0xFF) ||
        (unsigned int)p[19] != ((site->ctrl >> 8) & 0xFF) ||
        (unsigned int)p[20] != ((site->ctrl >> 16) & 0xFF) ||
        (unsigned int)p[21] != ((site->ctrl >> 24) & 0xFF))
        return PK_UNLOCK_NOMATCH;

    return p[14] == OP_JNE_REL8 ? PK_UNLOCK_LOCKED : PK_UNLOCK_DONE;
}

int pk_unlock_apply(unsigned char *image, unsigned int size,
                    const struct pk_unlock_site *site)
{
    /* Re-checked here rather than trusted from the caller: apply() is the only
     * function that writes, so the guard belongs where the write is. */
    if (pk_unlock_state(image, size, site) != PK_UNLOCK_LOCKED)
        return 0;
    image[site->rva] = OP_JMP_REL8;
    return 1;
}
