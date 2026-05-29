# ops/ — runtime self-update for Hermes Gateway in Incus

This directory implements the contract:

> The Incus image is thin. The container pulls the current Hermes code from
> the fork repository every time `hermes-gateway.service` (re)starts, gated
> by a `py_compile` sanity check. New commits propagate at the next restart;
> broken commits never crashloop.

## Files

| File | Where it lives in the container | Owner |
|------|---------------------------------|-------|
| `update-from-repo.sh`     | `/opt/hermes-agent/ops/update-from-repo.sh` | runtime: invoked as `ExecStartPre` |
| `hermes-gateway.service`  | `/etc/systemd/system/hermes-gateway.service` | systemd unit |
| `build-base-image.sh`     | host-side (Incus target host) | image build pipeline |

## How updates flow

1. Developer pushes to `main` on `Jhonnyr97/hermes-agent`.
2. Operator (or the UI) triggers `systemctl restart hermes-gateway` on the
   target customer container(s).
3. `ExecStartPre=/opt/hermes-agent/ops/update-from-repo.sh` runs:
   - clones the repo if `.git` is missing (first-boot path),
   - `git fetch --depth 1 origin <HERMES_REF>` (default: `main`),
   - `git reset --hard FETCH_HEAD`,
   - `python -m py_compile` on the boot-critical files.
4. If the sanity gate fails, `ExecStartPre` exits non-zero → systemd refuses
   to start Hermes. The previous Hermes process keeps running until somebody
   pushes a working commit; no crashloop.
5. If the sanity gate passes, `ExecStart` runs the new code.

## Pinning / rollback a single container

```
sudo systemctl edit hermes-gateway   # creates a drop-in
# add:
[Service]
Environment=HERMES_REF=afb43827f
# save & quit, then:
sudo systemctl restart hermes-gateway
```

To unpin, delete `/etc/systemd/system/hermes-gateway.service.d/override.conf`
and `daemon-reload + restart`.

## Building a new base image

Run on the Incus target host (as root, with `incus` CLI configured):

```
sudo REF=main /opt/hermes-agent/ops/build-base-image.sh
```

Knobs (all env vars):

| var | default | purpose |
|-----|---------|---------|
| `REPO_URL`   | `https://github.com/Jhonnyr97/hermes-agent.git` | source repo |
| `REF`        | `main` | which ref to seed the venv from |
| `BASE_IMAGE` | `images:debian/12` | Incus base image |
| `IMAGE_ALIAS`| `hermes` | alias to swap to the new image |
| `BUILD_NAME` | `hermes-base-build-<stamp>` | temporary container name |
| `NEW_ALIAS`  | `hermes-base-<stamp>` | unique alias for the new image |

What the script does:

- Launches a fresh container from `BASE_IMAGE`.
- `apt install` of OS prereqs (`git`, `python3`, `python3-venv`, …).
- Clones `$REF` once to resolve & install deps into the venv.
- Strips the working copy bare — only `venv/`, `ops/`, `.git/` remain.
- Stops + `incus publish` as `NEW_ALIAS`.
- Atomically swings `IMAGE_ALIAS` to point at the new image; the previous
  image keeps its own fingerprint and stays available for rollback.

Output ends with the exact command to rollback the alias if anything goes wrong.

## Migrating existing containers (one-time, after this change is merged)

These are the two containers currently in prod that pre-date the contract.
They keep running their hand-edited code until somebody runs the migration.

### Customer container (e.g. `hermes-customer-5-...`)
```bash
# On the Incus host, as root:
INST=hermes-customer-5-42f6c1
incus file push ops/hermes-gateway.service $INST/etc/systemd/system/hermes-gateway.service
incus exec $INST -- mkdir -p /opt/hermes-agent/ops
incus file push ops/update-from-repo.sh $INST/opt/hermes-agent/ops/update-from-repo.sh
incus exec $INST -- chmod 755 /opt/hermes-agent/ops/update-from-repo.sh
incus exec $INST -- systemctl daemon-reload
incus exec $INST -- systemctl restart hermes-gateway
incus exec $INST -- journalctl -u hermes-gateway -n 50 --no-pager
```

`update-from-repo.sh` will see the missing `.git` (the existing customer
containers were snapshotted without it), clone the fork fresh, and reset to
`main`. Any hand-edits in the working copy are replaced by the clean commit.

### Admin container (`hermes`)

Same as above with `INST=hermes`. The container already has a `.git`, so the
clone branch in the script is skipped — only `fetch + reset --hard` runs.

### Verifying

```
incus exec $INST -- systemctl is-active hermes-gateway        # active
incus exec $INST -- bash -c 'cd /opt/hermes-agent && git log --oneline -1'
curl -sS -H "Authorization: Bearer <KEY>" http://<host>:<port>/health
```

## Trade-offs & gotchas

- **Startup cost.** Each restart pays one `git fetch --depth 1` (~1–2s) plus
  `py_compile` on a handful of files (~ms). On boot, plus a clone the first
  time (~5–10s). Acceptable for a gateway that runs for hours/days at a time.
- **No network at boot.** If the container can't reach GitHub, `ExecStartPre`
  fails and Hermes does NOT start. Set `SKIP_UPDATE=1` in the override to
  bypass once, then fix the network and remove the override.
- **Venv drift.** `update-from-repo.sh` does not run `pip install` — if the
  fork ships a new dependency in `pyproject.toml`, the venv will be missing
  it and Hermes will fail at import (caught by the sanity gate, hopefully).
  Solution: rebuild the base image (`build-base-image.sh`) when deps change.
- **Sanity gate scope.** It currently `py_compile`s two files:
  `gateway/platforms/api_server.py` and `cron/scheduler.py`. Add more entries
  to the `SANITY_FILES` array in the script when you find new boot-time
  imports worth guarding.
- **The image alias swap is not transactional.** Brief window where the alias
  doesn't exist; `incus launch hermes` during that window fails. New customer
  provisioning during a base-image rebuild may need to be paused.
