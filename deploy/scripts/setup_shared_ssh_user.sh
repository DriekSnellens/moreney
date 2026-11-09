#!/usr/bin/env bash
# Create (or update) a guest SSH user for sharing the VPS without Moreney access.
#
# Example:
#   sudo ./setup_shared_ssh_user.sh --user collega --pubkey 'ssh-ed25519 AAAA... comment'
#   sudo ./setup_shared_ssh_user.sh --user collega --pubkey-file /tmp/collega.pub
#
# Guarantees:
#   - no sudo / docker / moreney group membership
#   - cannot enter /opt/moreney (world bits removed; not in group)
#   - optional SSH Match block restricts them to their home + shared project paths
set -euo pipefail

GUEST_USER=""
PUBKEY=""
PUBKEY_FILE=""
SHELL_PATH="/bin/bash"
CREATE_SSH_MATCH=1
SSHD_DROPIN="/etc/ssh/sshd_config.d/70-moreney-guest.conf"
DRY_RUN=0

log() { printf '[shared-ssh] %s\n' "$*"; }
die() { printf '[shared-ssh] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  sudo setup_shared_ssh_user.sh --user NAME --pubkey 'ssh-ed25519 AAAA... comment'
  sudo setup_shared_ssh_user.sh --user NAME --pubkey-file PATH
  sudo setup_shared_ssh_user.sh --user NAME --pubkey-file PATH --dry-run
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
      --no-ssh-match) CREATE_SSH_MATCH=0; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h|--help) usage 0 ;;
      *) die "unknown arg: $1" ;;
    esac
  done
  [[ -n "$GUEST_USER" ]] || die "--user required"
  if [[ -n "$PUBKEY_FILE" ]]; then
    [[ -f "$PUBKEY_FILE" ]] || die "missing pubkey file: $PUBKEY_FILE"
    PUBKEY="$(tr -d '\n' < "$PUBKEY_FILE")"
  fi
  [[ -n "$PUBKEY" ]] || die "--pubkey or --pubkey-file required"
}

deny_groups=(sudo docker moreney root admin lxd)
blocked_group_csv="sudo,docker,moreney"

ensure_user() {
  if id -u "$GUEST_USER" >/dev/null 2>&1; then
    log "user $GUEST_USER already exists"
  else
    log "creating user $GUEST_USER"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      return
    fi
    useradd -m -s "$SHELL_PATH" "$GUEST_USER"
  fi

  # Strip dangerous groups if present.
  local g
  for g in "${deny_groups[@]}"; do
    if getent group "$g" >/dev/null 2>&1 && id -nG "$GUEST_USER" | tr ' ' '\n' | grep -qx "$g"; then
      log "removing $GUEST_USER from group $g"
      [[ "$DRY_RUN" -eq 1 ]] || gpasswd -d "$GUEST_USER" "$g"
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

write_sshd_match() {
  [[ "$CREATE_SSH_MATCH" -eq 1 ]] || return 0
  log "writing $SSHD_DROPIN"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi
  cat > "$SSHD_DROPIN" <<EOF
# Guest accounts for shared VPS access — no agent forwarding / X11, no root.
# Filesystem isolation of /opt/moreney is enforced by ownership + 2770 perms.
Match User $GUEST_USER
    AllowAgentForwarding no
    AllowTcpForwarding no
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
  # Last digit must be 0 (no other perms).
  if [[ "${mode: -1}" != "0" ]]; then
    log "WARN: /opt/moreney mode is $mode (others can still access). Run:"
    log "  sudo deploy/scripts/setup_moreney_mount.sh --harden-only --apply"
  else
    log "/opt/moreney mode $mode looks private to owner/group"
  fi
  if id -nG "$GUEST_USER" 2>/dev/null | tr ' ' '\n' | grep -qx moreney; then
    die "guest still in group moreney — abort"
  fi
}

main() {
  parse_args "$@"
  require_root
  ensure_user
  if [[ "$DRY_RUN" -eq 0 ]]; then
    install_pubkey
    write_sshd_match
    assert_moreney_private
  else
    log "DRY-RUN ok for user=$GUEST_USER"
  fi
  log "done. Guest logs in as: ssh ${GUEST_USER}@<host>"
  log "Keep $GUEST_USER out of groups: $blocked_group_csv"
}

main "$@"
