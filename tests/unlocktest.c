/* Proof that the film-mode unlock changes exactly one byte, only where the
 * whole instruction pattern matches, and never anywhere else.
 *
 * The risk being tested is not "does it work" but "can it corrupt a binary it
 * does not recognise" -- a stray byte in the middle of unrelated code would
 * surface much later as something inexplicable. So most of this file is about
 * refusing to write.
 *
 *   i686-w64-mingw32-gcc -O2 -I../src -o unlocktest.exe unlocktest.c ../src/pkunlock.c
 *   wine unlocktest.exe
 */
#include <stdio.h>
#include <string.h>
#include "pkunlock.h"

#define IMG 0x8000u
static unsigned char img[IMG];
static int fails;

static void ok(int cond, const char *what)
{
    printf("  %-4s %s\n", cond ? "ok" : "FAIL", what);
    if (!cond)
        fails++;
}

/* Lay down the real pattern: the capability gate, then the head of the block
 * it skips, with the `jne` landing exactly on rva. */
static void plant(unsigned int rva, unsigned int bit, unsigned char jcc,
                  unsigned int ctrl)
{
    unsigned char *p = img + rva - PK_UNLOCK_BACK;
    *p++ = 0x68;
    *p++ = (unsigned char)(bit);
    *p++ = (unsigned char)(bit >> 8);
    *p++ = (unsigned char)(bit >> 16);
    *p++ = (unsigned char)(bit >> 24);
    *p++ = 0x8b; *p++ = 0xcf;                       /* mov %edi,%ecx */
    *p++ = 0xe8; *p++ = 0x9c; *p++ = 0x07;          /* call rel32, */
    *p++ = 0x00; *p++ = 0x00;                       /*   any target */
    *p++ = 0x85; *p++ = 0xc0;                       /* test %eax,%eax */
    *p++ = jcc;  *p++ = 0x29;                       /* jne/jmp rel8 */
    *p++ = 0x50;                                    /* push %eax (FALSE) */
    *p++ = 0x68;                                    /* push $ctrl */
    *p++ = (unsigned char)(ctrl);
    *p++ = (unsigned char)(ctrl >> 8);
    *p++ = (unsigned char)(ctrl >> 16);
    *p++ = (unsigned char)(ctrl >> 24);
}

int main(void)
{
    const struct pk_unlock_site *positive = &PK_UNLOCK_SITES[0];
    unsigned char before[IMG];
    unsigned int i, differ;

    printf("film-mode unlock\n");

    /* The table must describe the two sites we actually found, or the rest of
       this proves nothing about the real binary. */
    ok(PK_UNLOCK_SITES[0].rva == 0x3ef6 && PK_UNLOCK_SITES[0].bit == 0x800000
       && PK_UNLOCK_SITES[0].ctrl == 0x3f7,
       "site 0: 0x800000 gate at RVA 0x3ef6 guarding control 0x3f7");
    ok(PK_UNLOCK_SITES[1].rva == 0x3ed2 && PK_UNLOCK_SITES[1].bit == 0x400000
       && PK_UNLOCK_SITES[1].ctrl == 0x3f9,
       "site 1: 0x400000 gate at RVA 0x3ed2 guarding control 0x3f9");

    /* --- a matching, still-locked build --- */
    memset(img, 0xCC, IMG);
    plant(positive->rva, positive->bit, 0x75, positive->ctrl);
    ok(pk_unlock_state(img, IMG, positive) == PK_UNLOCK_LOCKED,
       "recognises the locked pattern");

    memcpy(before, img, IMG);
    ok(pk_unlock_apply(img, IMG, positive) == 1, "patches it");
    ok(img[positive->rva] == 0xEB, "the jne became an unconditional jmp");

    for (i = 0, differ = 0; i < IMG; i++)
        if (img[i] != before[i])
            differ++;
    ok(differ == 1, "EXACTLY ONE byte in the whole image changed");

    /* --- idempotent: running twice must not double-patch --- */
    ok(pk_unlock_state(img, IMG, positive) == PK_UNLOCK_DONE,
       "an already-patched build reads as done");
    ok(pk_unlock_apply(img, IMG, positive) == 0, "and is left alone");

    /* --- a build we do not recognise must be untouched --- */
    memset(img, 0xCC, IMG);
    plant(positive->rva, 0x123456, 0x75, positive->ctrl);            /* different capability bit */
    memcpy(before, img, IMG);
    ok(pk_unlock_state(img, IMG, positive) == PK_UNLOCK_NOMATCH,
       "a different capability bit is not our site");
    ok(pk_unlock_apply(img, IMG, positive) == 0 &&
       memcmp(img, before, IMG) == 0, "...and nothing is written");

    memset(img, 0xCC, IMG);
    plant(positive->rva, positive->bit, 0x74, positive->ctrl);       /* jz, not jnz */
    memcpy(before, img, IMG);
    ok(pk_unlock_state(img, IMG, positive) == PK_UNLOCK_NOMATCH,
       "a different jump opcode is not our site");
    ok(pk_unlock_apply(img, IMG, positive) == 0 &&
       memcmp(img, before, IMG) == 0, "...and nothing is written");

    /* The gate is right but it guards a DIFFERENT control: this is the case
       the control id was added to catch -- another build could plausibly test
       the same capability here for another reason. */
    memset(img, 0xCC, IMG);
    plant(positive->rva, positive->bit, 0x75, 0x401);
    memcpy(before, img, IMG);
    ok(pk_unlock_state(img, IMG, positive) == PK_UNLOCK_NOMATCH,
       "a matching gate guarding another control is not our site");
    ok(pk_unlock_apply(img, IMG, positive) == 0 &&
       memcmp(img, before, IMG) == 0, "...and nothing is written");

    memset(img, 0xCC, IMG);                          /* no pattern at all */
    memcpy(before, img, IMG);
    ok(pk_unlock_state(img, IMG, positive) == PK_UNLOCK_NOMATCH,
       "junk is not our site");
    ok(pk_unlock_apply(img, IMG, positive) == 0 &&
       memcmp(img, before, IMG) == 0, "...and nothing is written");

    /* --- refuse to read or write outside the image --- */
    memset(img, 0xCC, IMG);
    plant(positive->rva, positive->bit, 0x75, positive->ctrl);
    ok(pk_unlock_state(img, positive->rva, positive) == PK_UNLOCK_NOMATCH,
       "a site at the very end of a short image is refused");
    ok(pk_unlock_state(img, 4, positive) == PK_UNLOCK_NOMATCH,
       "a site past the end of a short image is refused");
    ok(pk_unlock_apply(img, 4, positive) == 0, "...and is not written");

    printf(fails ? "\nUNLOCKTEST FAILED\n" : "\nUNLOCKTEST PASSED\n");
    return fails ? 1 : 0;
}
