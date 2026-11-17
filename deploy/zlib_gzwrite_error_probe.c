/* A failed gzip write must never leave input in the caller's buffer. */
#include <signal.h>
#include "gzguts.h"

static unsigned char input[256 * 1024];

int main(int argc, char **argv)
{
    int fds[2], errnum, clean;
    gzFile file;
    gz_statep state;

    if (argc != 2 || (strcmp(argv[1], "wb0") && strcmp(argv[1], "wbT")))
        return 10;
    if (signal(SIGPIPE, SIG_IGN) == SIG_ERR || pipe(fds) != 0 || close(fds[0]) != 0)
        return 11;
    file = gzdopen(fds[1], argv[1]);
    if (file == NULL || gzbuffer(file, 64) != 0)
        return 12;
    memset(input, 0x5a, sizeof(input));
    if (gzwrite(file, input, sizeof(input)) != 0)
        return 13;
    (void)gzerror(file, &errnum);
    if (errnum != Z_ERRNO)
        return 14;
    state = (gz_statep)file;
    clean = state->strm.avail_in == 0 && state->strm.next_in == state->in;
    (void)gzclose_w(file);
    if (!clean)
        return 42;
    printf("ZLIB_GZWRITE_ERROR_STATE_OK mode=%s\n", argv[1]);
    return 0;
}
