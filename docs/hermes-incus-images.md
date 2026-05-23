# Hermes Incus Images

Hermes customer runtimes should be provisioned from a prebuilt Incus image named
`hermes`. Keep the image name product-neutral so the same artifact can survive a
future product rename.

## Registry

GitHub is the image registry of record:

- GitHub Actions builds the Incus image files on GitHub-hosted
  `ubuntu-latest`.
- The workflow publishes the Incus image files as GitHub Release assets.
- If `INCUS_HOST` and `INCUS_SSH_KEY` secrets are configured, the workflow also
  imports those files into the production Incus host and moves the `hermes`
  alias to the new image.
- The Incus host keeps local aliases such as `hermes` and `hermes-<git-sha>` for
  immediate provisioning.

Use GitHub Releases for native Incus image artifacts. GHCR is an OCI container
registry, while Incus imports a metadata archive plus a rootfs image.

## Build

The workflow lives at `.github/workflows/hermes-incus-image.yml`.

Manual dispatch defaults:

```text
image_name=hermes
release=noble
architecture=x86_64
```

The builder script is `scripts/incus/build-hermes-image.sh`. It creates an
Ubuntu rootfs, installs the Hermes runtime from this repository, writes
safe placeholder config, and emits:

- `incus.tar.xz`
- `rootfs.squashfs`
- `SHA256SUMS`
- `IMPORT.md`

Import on an Incus host with:

```bash
incus image import incus.tar.xz rootfs.squashfs --alias hermes --alias hermes-<git-sha>
```

## Required GitHub Configuration

Repository variables:

- `INCUS_PROJECT`: Incus project to import into. Defaults to `default`.

Repository secrets:

- `INCUS_HOST`: production Incus host.
- `INCUS_USER`: SSH user. Optional; defaults to `root`.
- `INCUS_SSH_KEY`: private key allowed to import Incus images on the host.

## Provisioning Contract

`hermes-ui` already provisions customer environments through
`TargetServer#image_alias`. For prebuilt images, set the target server image to:

```text
hermes
```

This is the normal production contract: `hermes` always points to the latest
image imported by GitHub Actions. Pinning a specific build is only for rollback
or diagnosis:

```text
hermes-<git-sha>
```

Customer-specific files still belong outside the image and are written during
provisioning:

- `/etc/hermes-agent/config.yaml`
- `/etc/hermes-agent/.env`
- customer sessions, logs, auth state, and runtime state

The image should contain only reusable runtime code, dependencies, service
files, and safe placeholder config.
