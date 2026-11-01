# GNU tar archive safety backports

The application uses Debian Trixie tar 1.35; PostgreSQL uses Debian Bookworm
tar 1.34. Both packages keep `--disable-acl` and their existing Debian source
archives, pinned in `deploy/acl_tools_sources.sha256`.

The profile patches backport the upstream changes shipped by AlmaLinux in
`tar-1.35-13.el10_2` and `tar-1.34-13.el9_8`. `sources.json` records the exact
vendor commits, patch order, URLs, SHA-256 hashes and before/after source hashes.
These are source backports; RPM binaries are not installed in Debian images.

The three issues covered are:

- [CVE-2026-5704](https://security-tracker.debian.org/tracker/CVE-2026-5704):
  inconsistent archive traversal between listing and extraction. Includes the
  vendor's follow-ups for nonzero-sized special members and hardlinks.
- [CVE-2026-18508](https://security-tracker.debian.org/tracker/CVE-2026-18508):
  hardlink targets outside `--one-top-level`. Includes the extraction-directory
  confinement prerequisite, its `openat2` support, and absolute-directory fixes.
- [CVE-2026-18477](https://security-tracker.debian.org/tracker/CVE-2026-18477):
  incremental dumpdir `X` temporary directories. Includes `mkdtempat` and the
  upstream cyclic-rename tests for `-C` and `--one-top-level` restores.

Primary vendor references:

- [AlmaLinux 10 advisory](https://errata.almalinux.org/10/ALSA-2026-61586.html),
  [source commit](https://git.almalinux.org/rpms/tar/commit/d00c4815b2e9ecb0858bb5aa1dbe11b2827e913f).
- [AlmaLinux 9 advisory](https://errata.almalinux.org/9/ALSA-2026-61581.html),
  [source commit](https://git.almalinux.org/rpms/tar/commit/f932969b23b8929177b96a46a9f56bdadca279bb).

The compiled C/gnulib/configuration changes preserve the vendor logic. The
small Debian-context adaptations retain Debian's statistics/atime handling and
its wildcard-exclusion context while passing the vendor's descriptor/base-name
arguments and updated `chdir_do` signature. Trixie includes upstream prerequisite
`1e6ce98e3a4ef5c807458a35973af7e3503c678c` for intermediate-directory tracking.
Test registration is adapted to Debian's test list; every added Autotest is
registered once. Vendor NEWS/THANKS/manual hunks are omitted; this file records
the provenance and changed option behavior. Each complete profile patch applies
without fuzz to the exact pinned Debian source and is tracked in its quilt stack.

`tar_security.py` first builds a separate pristine control. All three bounded
regressions must reproduce their specific failure with exit 42. The patched
package must then pass the complete Debian/native tar suite as a nonroot user
and the same bounded probes without skips. The dumpdir probe demonstrates
temporary-directory confinement; it does not attempt a live privilege-escalation
race. Archive payloads are never executed. Additional upstream cyclic-rename and
special-member regressions run in the complete native suite.

The package revision is `+~scoreitnoacl2`, above the previous local build and
below the next official Debian security revision. Native source/control/probe
evidence is saved in `tar-security-build.json`, bound to the actual packaged
ELF hash. Release checks compare every saved layer and the running tar hash,
require all runtime probes under UID 10001/999, and preserve the ACL-free checks.
Release reports include `security/app-tar-build.json` and
`security/postgres-tar-build.json`.

All compilation and network package downloads take place in disposable build
containers before deployment. The server receives completed images. Raw
vulnerability reports remain unchanged; a matching custom package version alone
does not establish that a finding is fixed.
