# Perl 5.36 Bookworm backports

These patches apply to the verified Debian source `5.36.0-7+deb12u4`.
The interpreter ABI and module API versions are retained. All four runtime
packages are rebuilt together, using revision `+scoreit1`.

- `archive-tar-size-cap.patch` adapts upstream
  [f9af014](https://github.com/jib/archive-tar-new/commit/f9af01426038e29d9578825a0cd3626946ab08c7).
  The default entry limit is 1 GiB; setting `MAX_FILE_SIZE` to zero retains
  upstream's explicit unlimited option.
- `archive-tar-stream-alignment.patch` is a project backport based on the
  proposed [upstream PR #53](https://github.com/jib/archive-tar-new/pull/53),
  head `aeb258562a758400fb0e11f5944d06f749ac413a`. That proposal was **not merged**
  when this backport was prepared. It consumes a refused entry in at most
  32 KiB chunks and stops at a short read. The first size-cap patch alone
  fails the alignment/warning-count control.
- `pod-text-margins.patch` adapts upstream
  [7051017](https://github.com/rra/podlators/commit/70510174f69eb54aa6d617bde4e1402cd9b7c61f)
  to Text, Color, Overstrike and Termcap in the bundled podlators version.

The build checks source and patch SHA-256 pins, runs the normal Debian
static/shared suites as an unprivileged user, and runs bounded negative
controls and regression probes. Optional platform-specific skips in the normal
Perl suites are not described as zero-skipped coverage. The additional probe
must complete every tested case.

Runtime evidence checks all saved layers, package revisions, five module
hashes, actual module loading and PostgreSQL command wrappers. Test sources,
control copies and build dependencies remain outside the final image.
Raw scanner findings are retained even when Debian's database does not
recognize the project backport.
