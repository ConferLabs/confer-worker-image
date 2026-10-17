# Confer Worker Image

Confidential GCP worker image for document and code generation.

## Overview

This repository uses Nix and mkosi to build a minimal, reproducible Ubuntu 24.04
image. It includes:

- a unified kernel image (UKI) and dm-verity-protected SquashFS root;
- a standard-library Python listener that reserves exactly one connection;
- OpenSSH in inetd mode for the reserved, unprivileged worker session;
- Python document libraries, PDF tools, and fonts; and
- an ephemeral tmpfs workspace with measured network and process restrictions.

The VM has no administrative SSH service or external IP. It stops accepting new
work after a randomly selected 18–20-hour window so workers rotate gradually.

## Architecture

1. The worker advertises health while its one-shot listener is available.
2. The controller reserves it and relays a challenge and the proxy's ephemeral
   SSH client key.
3. The worker extends RTMR3, which is zero at boot, with a digest of this
   reservation's challenge and both SSH keys, then returns a hardware
   attestation quote, its ephemeral SSH host key, and its exact Sigstore
   manifest and inclusion bundle from instance metadata.
4. The proxy verifies the quote, signed measurements, signing identity, and
   inclusion proof before beginning SSH on the same connection.
5. OpenSSH accepts only the attested client key and disables passwords, PTYs,
   forwarding, and user startup files. The single attested connection may
   carry up to nine concurrent channels: eight document extractions alongside
   command execution or file transfer.

The quote binds the challenge and both SSH keys. Any change to the measured boot
chain changes the attestation measurements and is rejected by the proxy. The
proxy also requires RTMR3 to equal exactly one extension with its own
reservation, which proves that no earlier client, and nothing else since boot,
has extended it: the proxy is the first to ever connect.

## Document worker

`document-worker/` is the sandboxed document extraction and retrieval worker.
The build installs its source and its hash-pinned, binary-only runtime under
`/usr/lib/confer-document-worker` and runs the worker's self-check, so the
measured image contains exactly the parser code that runs.

The proxy reaches it by running the constant command
`/usr/local/libexec/confer-document-session` on an SSH channel of the attested
connection. That command relays its stdin and stdout to
`confer-document-worker.socket`, which starts a fresh `confer-document-worker@`
process for each connection: no network, a read-only filesystem without the job
workspace, an 8 GiB and five-minute limit per process, and a shared
`confer-document.slice` memory limit that leaves headroom for command
execution on the same worker. Document bytes travel over stdin, never in the
command line.

Run its tests with `pytest` from `document-worker/` after installing the
package's `test` extra.

## Building

Prerequisite: Nix with flakes enabled on Linux.

```bash
nix develop
make test
make build
make gcp-archive
make prepare-release
```

The build produces `mkosi.output/confer-worker-image_<version>.raw` and a
Compute Engine archive named `confer-worker-image_<version>.tar.gz`.

The release workflow additionally runs `sudo make test-linux-policy` on Linux.

## Measurements and releases

The release workflow derives every signed measurement from checked-in inputs:

- MRTD and RTMR0 use the reviewed GCP firmware value and normalized CCEL event
  fixture in the checked-in platform profile;
- RTMR1 is calculated from the disk's GPT, fallback UKI, embedded Linux EFI
  application, and EDK2 boot transcript; and
- RTMR2 is empty for this direct-UKI boot configuration.

`scripts/measure-release-image` validates the disk and emits the four
measurements. A reviewer can reproduce them with:

```bash
nix develop --command scripts/measure-release-image \
  mkosi.output/confer-worker-image_<version>.raw
```

