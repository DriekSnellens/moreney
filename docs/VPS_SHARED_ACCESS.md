# Shared VPS: Cursor SSH without Moreney access

Two surfaces on one machine:

| Path | Mount | Who |
|---|---|---|
| `/opt/moreney` | **Dedicated** Moreney filesystem | `moreney` + operator `ubuntu` only |
| `/opt/shared/...` | VPS **root** disk | Cursor collaborator SSH user |

The collaborator drives **Cursor via Remote SSH** on `/opt/shared`. They never get
the `moreney` / `sudo` / `docker` groups, so `/opt/moreney` stays `Permission denied`.

## Threat model (honest)

| Collaborator has… | Moreney? | Cursor Remote SSH? |
|---|---|---|
| SSH user + group `shared`, **no** sudo/docker/moreney | **No** | **Yes** on `/opt/shared` |
| `sudo` or `docker` | **Yes** (effectively) | — don't grant these |
| Access to open `/opt/moreney` in Cursor | blocked by FS mode `2770` | — |

Operator `ubuntu` stays in `moreney` (bots/agents) and `shared` (can help on collab projects).

## One-time host prep

```bash
sudo deploy/scripts/setup_moreney_mount.sh --harden-only --apply   # if not already
sudo deploy/scripts/setup_shared_ssh_user.sh --prepare-host
```

### Moreney on its own mount (if not done yet)

```bash
sudo deploy/scripts/setup_moreney_mount.sh --loop-size 40G --dry-run
sudo deploy/scripts/setup_moreney_mount.sh --loop-size 40G --apply
# or: --device /dev/sdb1 --apply
```

## Create the Cursor collaborator

```bash
sudo deploy/scripts/setup_shared_ssh_user.sh \
  --user collega \
  --pubkey 'ssh-ed25519 AAAA... collega@laptop' \
  --cursor
```

What that does:

- user in group `shared` only (not `moreney` / `sudo` / `docker`)
- workspace ` /opt/shared/<user>` (+ `~/shared` symlink)
- `~/.cursor-server` ready for Cursor IDE Remote SSH
- sshd Match: TCP forwarding **on** (Cursor ports), agent forwarding **off**

### Connect as `ekompassie` (live on this VPS)

**Host:** `192.99.54.178`  
**User:** `ekompassie`  
**Auth:** SSH key only (password login disabled)  
**Key comment on server:** `ekompassie` · fingerprint `SHA256:troyX8G+ffJNJgOmHA1HHewFoaIMdUWxQCbR+RAAVlY`

#### 1) One-time SSH config on their laptop

Put this in `~/.ssh/config` (macOS/Linux) or `%USERPROFILE%\.ssh\config` (Windows):

```sshconfig
Host vps-ekompassie
    HostName 192.99.54.178
    User ekompassie
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

Use the private key that matches the pubkey that was installed (comment `ekompassie`).  
Test in a terminal:

```bash
ssh vps-ekompassie
```

You should land in `/home/ekompassie` without a password prompt.

#### 2) Open Cursor on the shared workspace

1. Cursor → **Remote-SSH: Connect to Host…** → choose `vps-ekompassie`
2. After the window reloads: **File → Open Folder…**
3. Open exactly: `/opt/shared/ekompassie`  
   (shortcut on the server: `~/shared`)

Do **not** open `/opt/moreney` — that is a private mount (`Permission denied` is correct).

#### 3) Quick checks after first login

```bash
whoami                    # ekompassie
pwd                       # /home/ekompassie
ls /opt/shared/ekompassie # ok
ls /opt/moreney           # Permission denied
```

Put **their** git repos under `/opt/shared/...` — never under `/opt/moreney`
(that mount is private and nested sites inherit Moreney ACLs).

### Optional: their own My Machines worker

Remote SSH is enough for day-to-day Cursor. For a separate cloud worker on
**their** tree (not Moreney), they install the agent under their account with
**their** API key:

```bash
curl https://cursor.com/install -fsS | bash
agent worker --worker-dir /opt/shared/collega --name vps-collega start
```

Do **not** point their worker at `/opt/moreney` or reuse `ubuntu`'s
`CURSOR_API_KEY` / `cursor-moreney-worker.service`.

## Checklist

- [ ] `findmnt /opt/moreney` is a dedicated source (loop or disk)
- [ ] `stat -c '%a' /opt/moreney` ends with `0`
- [ ] Collaborator `id` has `shared`, not `moreney` / `sudo` / `docker`
- [ ] Collaborator: `ls /opt/moreney` → Permission denied
- [ ] Collaborator: can write `/opt/shared/<user>`
- [ ] Cursor Remote SSH opens `/opt/shared/...` successfully

## Rollback Moreney mount

```bash
sudo systemctl stop 'moreney-*'
sudo umount /opt/moreney
sudo rmdir /opt/moreney
sudo mv /opt/moreney.root-bak /opt/moreney
# remove the Moreney line from /etc/fstab
sudo systemctl start moreney-marketdata 'moreney-live@micro'
```
