/* Exercise upstream's real decoder after another format has used its storage.
   Compiled only in the build stage, against the pinned gzip source. */
#include "config.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "unlzh.c"

DECLARE (uch, inbuf, INBUFSIZ + INBUF_EXTRA);
DECLARE (ush, prev, 1L << BITS);
unsigned insize, inptr;
static int input_reads;

static void
require_clean_state (void)
{
  for (unsigned i = 0; i < 2 * NC - 1; i++)
    if (left[i] || right[i])
      exit (42);
}

int
fill_inbuf (int eof_ok)
{
  (void) eof_ok;
  /* The reset must happen before reading any attacker-controlled table. */
  require_clean_state ();
  input_reads++;
  return EOF;
}

int
main (void)
{
  for (unsigned repeat = 0; repeat < 2; repeat++)
    {
      memset (prev, 0xa5, sizeof prev);
      huf_decode_start ();
      require_clean_state ();
    }
  if (!input_reads)
    return 43;
  puts ("GZIP_LZH_STATE_RESET_OK");
  return 0;
}
