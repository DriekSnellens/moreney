# Shared VPS access with isolated Moreney

Goal: let someone else use SSH on this VPS for other projects, while Moreney
(code, `.env`, live state, keys) stays on a **dedicated mount** owned by the
**`moreney` user/group**.

## Threat model (honest)

| Guest has… | Can they read Moreney? |
|---|---|
| Normal SSH user, **no** sudo/docker/moreney group | **No** (filesystem 2770 + not in group) |
| `sudo` or root | **Yes** — do not give guests sudo |
| `docker` group | **Effectively root** — do not add guests to docker |
| Same physical disk, only a loop mount | Logical isolation only; attach a real second volume for stronger separation |

Operator account `ubuntu` **stays** in group `moreney` so Cursor / deploy agents keep access.

## Current host shape

- App path: `/opt/moreney`
- Service user: `moreney` (systemd units already run as this user)
- Operator: `ubuntu` (group `moreney`, `docker`)
- Default disk: single root filesystem — use `--loop-size` until you attach a second cloud volume

## One-time setup

### 1) Put Moreney on its own mount

Prefer a real attached disk when available:

```bash
sudo deploy/scripts/setup_moreney_mount.sh --device /dev/sdb1 --dry-run
sudo deploy/scripts/setup_moreney_mount.sh --device /dev/sdb1 --apply
```

Otherwise create a sparse image (dedicated mount, still on the same disk):

```bash
sudo deploy/scripts/setup_moreney_mount.sh --loop-size 40G --dry-run
sudo deploy/scripts/setup_moreney_mount.sh --loop-size 40G --apply
```

`--apply` briefly stops running `moreney-*` units, rsyncs data, remounts at
`/opt/moreney`, writes `/etc/fstab`, hardens permissions, and restarts the
units that were running. Old tree is kept at `/opt/moreney.root-bak` until you
delete it.

Verify:

```bash
findmnt /opt/moreney
systemctl --failed 'moreney-*'
curl -sS http://127.0.0.1:8000/health || true
```

### 2) Harden without remounting

If the mount is already correct and you only need privacy:

```bash
sudo deploy/scripts/setup_moreney_mount.sh --harden-only --apply
```

### 3) Create the shared SSH user

```bash
sudo deploy/scripts/setup_shared_ssh_user.sh \
  --user collega \
  --pubkey 'ssh-ed25519 AAAA... collega@laptop'
```

This user is **not** in `sudo`, `docker`, or `moreney`. Install only their
pubkey; never share the `ubuntu` or `moreney` keys.

### 4) Where guests may work

Put shared projects outside Moreney, for example:

- `/opt/shared/<project>` owned by the guest (or a `shared` group)
- `/home/<guest>/...`

Do **not** nest other sites under `/opt/moreney` (they inherit Moreney ACLs).

## Day-2 checklist before handing out access

- [ ] `findmnt /opt/moreney` shows a non-root source (loop image or disk)
- [ ] `stat -c '%a' /opt/moreney` ends with `0` (no world access)
- [ ] `stat -c '%a' /opt/moreney/.env` → `660` (owner+group only)
- [ ] Operator `ubuntu` stays in group `moreney` (group-writable tree)
- [ ] Guest `id` has no `moreney` / `sudo` / `docker`
- [ ] Guest cannot `ls /opt/moreney` (expect Permission denied)
- [ ] Live units healthy: `systemctl status 'moreney-live@*'`

## Rollback

If cutover misbehaves:

```bash
sudo systemctl stop 'moreney-*'
sudo umount /opt/moreney
sudo rmdir /opt/moreney
sudo mv /opt/moreney.root-bak /opt/moreney
# remove the Moreney line from /etc/fstab
sudo systemctl start moreney-marketdata 'moreney-live@micro' # …other units
```
