#!/usr/bin/env bash
# Create (or update) an SSH user who can drive Cursor on this VPS
# WITHOUT access to the Moreney mount.
#
# Cursor Remote SSH uses the shared root disk (/opt/shared/...), never /opt/moreney.
#
# Example:
#   sudo ./setup_shared_ssh_user.sh --prepare-host
#   sudo ./setup_shared_ssh_user.sh --user collega --pubkey 'ssh-ed25519 AAAA... comment' --cursor
#   sudo ./setup_shared_ssh_user.sh --user collega --pubkey-file /tmp/collega.pub --cursor
#
# Guarantees with --cursor:
#   - no sudo / docker / moreney group membership (cannot read /opt/moreney)
#   - member of group `shared`, home + /opt/shared writable
#   - SSH allows TCP forwarding (needed for Cursor Remote)
#   - ~/.cursor-server prepared for Cursor IDE Remote SSH
set -euo pipefail

GUEST_USER=""
PUBKEY=""
PUBKEY_FILE=""
SHELL_PATH="/bin/bash"
CREATE_SSH_MATCH=1
CURSOR_MODE=0
PREPARE_HOST_ONLY=0
SSHD_DROPIN="/etc/ssh/sshd_config.d/70-cursor-collab.conf"
SHARED_GROUP="${SHARED_GROUP:-shared}"
SHARED_ROOT="${SHARED_ROOT:-/opt/shared}"
DRY_RUN=0

log() { printf '[shared-ssh] %s\n' "$*"; }
die() { printf '[shared-ssh] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  sudo setup_shared_ssh_user.sh --prepare-host
  sudo setup_shared_ssh_user.sh --user NAME --pubkey 'ssh-ed25519 AAAA...' --cursor
  sudo setup_shared_ssh_user.sh --user NAME --pubkey-file PATH --cursor
  sudo setup_shared_ssh_user.sh --user NAME --pubkey-file PATH --cursor --dry-run

--cursor   Cursor Remote SSH collaborator: /opt/shared access, no Moreney
--prepare-host   only create group shared + /opt/shared (no user yet)
EOF
  exit "${1:-0}"
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "run as root (sudo)"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --user) GUEST_USER="${2:-}"; shift 2 ;;
      --pubkey) PUBKEY="${2:-}"; shift 2 ;;
      --pubkey-file) PUBKEY_FILE="${2:-}"; shift 2 ;;
      --shell) SHELL_PATH="${2:-}"; shift 2 ;;
      --cursor) CURSOR_MODE=1; shift ;;
      --prepare-host) PREPARE_HOST_ONLY=1; shift ;;
      --no-ssh-match) CREATE_SSH_MATCH=0; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h|--help) usage 0 ;;
      *) die "unknown arg: $1" ;;
    esac
  done

  if [[ "$PREPARE_HOST_ONLY" -eq 1 ]]; then
    return
  fi
  [[ -n "$GUEST_USER" ]] || die "--user required (or use --prepare-host)"
  if [[ -n "$PUBKEY_FILE" ]]; then
    [[ -f "$PUBKEY_FILE" ]] || die "missing pubkey file: $PUBKEY_FILE"
    PUBKEY="$(tr -d '\n' < "$PUBKEY_FILE")"
  fi
  [[ -n "$PUBKEY" ]] || die "--pubkey or --pubkey-file required"
  [[ "$CURSOR_MODE" -eq 1 ]] || die "pass --cursor (this script is for Cursor collab without Moreney access)"
}

deny_groups=(sudo docker moreney root admin lxd)
blocked_group_csv="sudo,docker,moreney"

prepare_host() {
  log "ensuring group $SHARED_GROUP and $SHARED_ROOT"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "would: groupadd -f $SHARED_GROUP; mkdir $SHARED_ROOT mode 2775"
    return
  fi
  getent group "$SHARED_GROUP" >/dev/null || groupadd --system "$SHARED_GROUP"
  # Operator ubuntu can help manage shared projects.
  if id -u ubuntu >/dev/null 2>&1; then
    usermod -aG "$SHARED_GROUP" ubuntu
  fi
  install -d -m 2775 -o root -g "$SHARED_GROUP" "$SHARED_ROOT"
  # Drop-in README so Remote SSH has an obvious folder to open.
  if [[ ! -f "$SHARED_ROOT/README.md" ]]; then
    cat > "$SHARED_ROOT/README.md" <<EOF
# Shared Cursor workspace

Projects here live on the **VPS root disk**, not the Moreney mount.

- Open this folder (or a subfolder) in Cursor via Remote SSH.
- Do **not** open \`/opt/moreney\` — that mount is private (\`Permission denied\` is expected).
EOF
    chown root:"$SHARED_GROUP" "$SHARED_ROOT/README.md"
    chmod 664 "$SHARED_ROOT/README.md"
  fi
}

ensure_user() {
  if id -u "$GUEST_USER" >/dev/null 2>&1; then
    log "user $GUEST_USER already exists"
  else
    log "creating user $GUEST_USER"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      return
    fi
    useradd -m -s "$SHELL_PATH" -G "$SHARED_GROUP" "$GUEST_USER"
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi

  usermod -aG "$SHARED_GROUP" "$GUEST_USER"

  # Strip dangerous / Moreney groups if present.
  local g
  for g in "${deny_groups[@]}"; do
    if getent group "$g" >/dev/null 2>&1 && id -nG "$GUEST_USER" | tr ' ' '\n' | grep -qx "$g"; then
      log "removing $GUEST_USER from group $g"
      gpasswd -d "$GUEST_USER" "$g"
    fi
  done
}

install_pubkey() {
  local home auth
  home="$(getent passwd "$GUEST_USER" | cut -d: -f6)"
  [[ -n "$home" ]] || die "no home for $GUEST_USER"
  auth="$home/.ssh/authorized_keys"
  log "installing SSH pubkey into $auth"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi
  install -d -m 700 -o "$GUEST_USER" -g "$GUEST_USER" "$home/.ssh"
  touch "$auth"
  chown "$GUEST_USER:$GUEST_USER" "$auth"
  chmod 600 "$auth"
  if ! grep -Fqx "$PUBKEY" "$auth"; then
    printf '%s\n' "$PUBKEY" >> "$auth"
  fi
}

prepare_cursor_home() {
  local home
  home="$(getent passwd "$GUEST_USER" | cut -d: -f6)"
  log "preparing Cursor Remote SSH dirs under $home"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi
  install -d -m 755 -o "$GUEST_USER" -g "$GUEST_USER" "$home/.cursor-server"
  install -d -m 755 -o "$GUEST_USER" -g "$GUEST_USER" "$home/.cursor"
  install -d -m 755 -o "$GUEST_USER" -g "$GUEST_USER" "$home/.cache"
  # Personal project shortcut on the shared root mount.
  install -d -m 2775 -o "$GUEST_USER" -g "$SHARED_GROUP" "$SHARED_ROOT/$GUEST_USER"
  if [[ ! -e "$home/shared" ]]; then
    ln -s "$SHARED_ROOT/$GUEST_USER" "$home/shared"
    chown -h "$GUEST_USER:$GUEST_USER" "$home/shared"
  fi
  # Shell hint on login.
  local rc="$home/.bashrc"
  if [[ -f "$rc" ]] && ! grep -q 'CURSOR_COLLAB_SHARED' "$rc"; then
    cat >> "$rc" <<EOF

# CURSOR_COLLAB_SHARED
export SHARED_ROOT=$SHARED_ROOT
cd_shared() { cd "\$SHARED_ROOT/\$USER" || cd "\$SHARED_ROOT"; }
EOF
    chown "$GUEST_USER:$GUEST_USER" "$rc"
  fi
}

write_sshd_match() {
  [[ "$CREATE_SSH_MATCH" -eq 1 ]] || return 0
  log "writing $SSHD_DROPIN"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi
  # TCP forwarding ON — Cursor Remote SSH port-forward / forwarded ports need it.
  # Agent forwarding OFF — do not borrow ubuntu's keys.
  cat > "$SSHD_DROPIN" <<EOF
# Cursor collaborator(s): Remote SSH OK, Moreney still filesystem-isolated.
Match User $GUEST_USER
    AllowAgentForwarding no
    AllowTcpForwarding yes
    X11Forwarding no
    PermitTunnel no
EOF
  sshd -t
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
}

assert_moreney_private() {
  local mode
  mode="$(stat -c '%a' /opt/moreney 2>/dev/null || true)"
  if [[ -z "$mode" ]]; then
    log "WARN: /opt/moreney missing"
    return
  fi
  if [[ "${mode: -1}" != "0" ]]; then
    log "WARN: /opt/moreney mode is $mode (others can still access). Run:"
    log "  sudo deploy/scripts/setup_moreney_mount.sh --harden-only --apply"
  else
    log "/opt/moreney mode $mode looks private to owner/group"
  fi
  if id -nG "$GUEST_USER" 2>/dev/null | tr ' ' '\n' | grep -qx moreney; then
    die "user still in group moreney — abort"
  fi
  if [[ "$DRY_RUN" -eq 0 ]]; then
    if sudo -u "$GUEST_USER" test -r /opt/moreney 2>/dev/null; then
      die "$GUEST_USER can still read /opt/moreney"
    fi
    log "confirmed: $GUEST_USER cannot read /opt/moreney"
    if ! sudo -u "$GUEST_USER" test -w "$SHARED_ROOT/$GUEST_USER"; then
      die "$GUEST_USER cannot write $SHARED_ROOT/$GUEST_USER"
    fi
    log "confirmed: $GUEST_USER can write $SHARED_ROOT/$GUEST_USER"
  fi
}

print_cursor_instructions() {
  cat <<EOF

Cursor Remote SSH (collega):
  1. In Cursor / VS Code: Remote-SSH → ssh ${GUEST_USER}@<host>
  2. Open folder: ${SHARED_ROOT}/${GUEST_USER}   (or ${SHARED_ROOT}/<project>)
  3. Do NOT open /opt/moreney

Optional My Machines worker for THEIR projects (their own API key):
  curl https://cursor.com/install -fsS | bash
  agent worker --worker-dir ${SHARED_ROOT}/${GUEST_USER} --name vps-${GUEST_USER} start

Keep ${GUEST_USER} out of groups: ${blocked_group_csv}
EOF
}

main() {
  parse_args "$@"
  require_root
  prepare_host

  if [[ "$PREPARE_HOST_ONLY" -eq 1 ]]; then
    log "host prepared: group=$SHARED_GROUP root=$SHARED_ROOT"
    exit 0
  fi

  ensure_user
  if [[ "$DRY_RUN" -eq 0 ]]; then
    install_pubkey
    prepare_cursor_home
    write_sshd_match
    assert_moreney_private
  else
    log "DRY-RUN ok for user=$GUEST_USER cursor=$CURSOR_MODE"
  fi
  log "done. Login: ssh ${GUEST_USER}@<host>"
  print_cursor_instructions
}

main "$@"
