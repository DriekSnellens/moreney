#!/usr/bin/env bash
# Move /opt/moreney onto a dedicated filesystem mount owned by user moreney.
#
# Modes:
#   --device /dev/DISK     use an attached block device / partition
#   --loop-size 40G       create a sparse image under /var/lib/moreney-disk
#   --harden-only         only tighten permissions / guest isolation (no remount)
#   --apply               actually stop services and cut over (required for migrate)
#   --dry-run             print the plan (default when --apply is omitted for migrate)
#
# Operator (ubuntu) stays in group moreney so Cursor/agents keep access.
# Guest SSH users must NOT be in group moreney, docker, or sudo.
set -euo pipefail

MOUNT_POINT="${MOUNT_POINT:-/opt/moreney}"
IMAGE_DIR="${IMAGE_DIR:-/var/lib/moreney-disk}"
IMAGE_PATH="${IMAGE_PATH:-$IMAGE_DIR/moreney.img}"
LABEL="${LABEL:-moreney}"
OWNER_USER="${OWNER_USER:-moreney}"
OWNER_GROUP="${OWNER_GROUP:-moreney}"
OPERATOR_USER="${OPERATOR_USER:-ubuntu}"
STAGING="${STAGING:-/mnt/moreney-new}"
BACKUP_PATH="${BACKUP_PATH:-/opt/moreney.root-bak}"

MODE=""
DEVICE=""
LOOP_SIZE=""
APPLY=0
DRY_RUN=1
HARDEN_ONLY=0

usage() {
  sed -n '1,20p' "$0" | tail -n +2
  exit "${1:-0}"
}

log() { printf '[moreney-mount] %s\n' "$*"; }
die() { printf '[moreney-mount] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "run as root (sudo)"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --device)
        MODE="device"
        DEVICE="${2:-}"
        [[ -n "$DEVICE" ]] || die "--device needs a path"
        shift 2
        ;;
      --loop-size)
        MODE="loop"
        LOOP_SIZE="${2:-}"
        [[ -n "$LOOP_SIZE" ]] || die "--loop-size needs a size (e.g. 40G)"
        shift 2
        ;;
      --harden-only)
        HARDEN_ONLY=1
        MODE="${MODE:-harden}"
        shift
        ;;
      --apply)
        APPLY=1
        DRY_RUN=0
        shift
        ;;
      --dry-run)
        DRY_RUN=1
        APPLY=0
        shift
        ;;
      -h|--help)
        usage 0
        ;;
      *)
        die "unknown arg: $1"
        ;;
    esac
  done
  [[ -n "$MODE" ]] || die "choose --device, --loop-size, or --harden-only"
}

active_moreney_units() {
  systemctl list-units --type=service --state=running --no-legend --no-pager \
    'moreney-*' 2>/dev/null | awk '{print $1}' || true
}

stop_services() {
  local units
  mapfile -t units < <(active_moreney_units)
  if [[ ${#units[@]} -eq 0 ]]; then
    log "no running moreney units"
    return
  fi
  log "stopping: ${units[*]}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi
  systemctl stop "${units[@]}"
}

start_services() {
  local units=("$@")
  if [[ ${#units[@]} -eq 0 ]]; then
    log "no units to restart"
    return
  fi
  log "starting: ${units[*]}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi
  systemctl start "${units[@]}"
}

ensure_owner_user() {
  if ! id -u "$OWNER_USER" >/dev/null 2>&1; then
    log "creating user $OWNER_USER"
    [[ "$DRY_RUN" -eq 1 ]] && return
    useradd -m -s /bin/bash "$OWNER_USER"
  fi
  if id -u "$OPERATOR_USER" >/dev/null 2>&1; then
    if ! id -nG "$OPERATOR_USER" | tr ' ' '\n' | grep -qx "$OWNER_GROUP"; then
      log "adding $OPERATOR_USER to group $OWNER_GROUP (operator access)"
      [[ "$DRY_RUN" -eq 1 ]] || usermod -aG "$OWNER_GROUP" "$OPERATOR_USER"
    fi
  fi
}

harden_tree() {
  local root="$1"
  [[ -d "$root" ]] || die "missing tree: $root"
  log "hardening permissions under $root"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "would: chown -R $OWNER_USER:$OWNER_GROUP $root"
    log "would: chmod -R o-rwx + setgid on dirs; secrets 640"
    return
  fi
  chown -R "$OWNER_USER:$OWNER_GROUP" "$root"
  # Strip world access; grant group rwx so operator (ubuntu in group moreney) can
  # develop/deploy. Guests must not be in group moreney.
  chmod -R o-rwx "$root"
  chmod -R g+rwX "$root"
  find "$root" -type d -exec chmod g+rwxs {} +
  chmod 2770 "$root"
  # Secrets: owner+group read/write; never world.
  [[ -f "$root/.env" ]] && chmod 660 "$root/.env"
  if [[ -d "$root/env" ]]; then
    find "$root/env" -type f -name '*.env' -exec chmod 660 {} +
  fi
  # Home for service user: private to owner/group.
  if [[ -d "/home/$OWNER_USER" ]]; then
    chown -R "$OWNER_USER:$OWNER_GROUP" "/home/$OWNER_USER"
    chmod 750 "/home/$OWNER_USER"
  fi
}

write_fstab() {
  local source="$1"
  local opts="$2"
  local line="$source $MOUNT_POINT ext4 $opts 0 2"
  if grep -Eq "^[^#].*[[:space:]]$MOUNT_POINT[[:space:]]" /etc/fstab; then
    log "fstab already has an entry for $MOUNT_POINT — leaving as-is"
    grep -E "^[^#].*[[:space:]]$MOUNT_POINT[[:space:]]" /etc/fstab || true
    return
  fi
  log "appending fstab: $line"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi
  printf '\n# Moreney isolated data mount\n%s\n' "$line" >> /etc/fstab
}

prepare_loop_image() {
  mkdir -p "$IMAGE_DIR"
  if [[ ! -f "$IMAGE_PATH" ]]; then
    log "creating sparse image $IMAGE_PATH ($LOOP_SIZE)"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      return
    fi
    truncate -s "$LOOP_SIZE" "$IMAGE_PATH"
    mkfs.ext4 -F -L "$LABEL" "$IMAGE_PATH"
  else
    log "reusing existing image $IMAGE_PATH"
  fi
  chown root:root "$IMAGE_DIR" "$IMAGE_PATH" 2>/dev/null || true
  chmod 750 "$IMAGE_DIR"
  chmod 600 "$IMAGE_PATH" 2>/dev/null || true
}

prepare_device() {
  [[ -b "$DEVICE" ]] || die "not a block device: $DEVICE"
  if blkid "$DEVICE" >/dev/null 2>&1; then
    log "device $DEVICE already has a filesystem: $(blkid -o value -s TYPE "$DEVICE" || true)"
  else
    log "formatting $DEVICE as ext4 label=$LABEL"
    [[ "$DRY_RUN" -eq 1 ]] && return
    mkfs.ext4 -F -L "$LABEL" "$DEVICE"
  fi
}

mount_source() {
  if [[ "$MODE" == "loop" ]]; then
    echo "$IMAGE_PATH"
  else
    echo "$DEVICE"
  fi
}

mount_opts() {
  if [[ "$MODE" == "loop" ]]; then
    echo "loop,defaults,nofail"
  else
    echo "defaults,nofail"
  fi
}

already_on_dedicated_mount() {
  local src
  src="$(findmnt -n -o SOURCE --target "$MOUNT_POINT" 2>/dev/null || true)"
  [[ -n "$src" && "$src" != "$(findmnt -n -o SOURCE --target / 2>/dev/null || true)" ]]
}

migrate() {
  local src opts units_before
  src="$(mount_source)"
  opts="$(mount_opts)"

  if already_on_dedicated_mount; then
    log "$MOUNT_POINT is already a dedicated mount ($(findmnt -n -o SOURCE --target "$MOUNT_POINT"))"
    harden_tree "$MOUNT_POINT"
    write_fstab "$src" "$opts"
    return
  fi

  [[ -d "$MOUNT_POINT" ]] || die "missing $MOUNT_POINT"

  mapfile -t units_before < <(active_moreney_units)

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY-RUN plan:"
    log "  1) prepare $src"
    log "  2) mount at $STAGING"
    log "  3) stop moreney units: ${units_before[*]:-none}"
    log "  4) rsync $MOUNT_POINT/ -> $STAGING/"
    log "  5) move $MOUNT_POINT -> $BACKUP_PATH"
    log "  6) mkdir $MOUNT_POINT && mount $src -> $MOUNT_POINT ($opts)"
    log "  7) fstab + harden + restart units"
    log "re-run with --apply to execute"
    return
  fi

  [[ "$APPLY" -eq 1 ]] || die "refusing migrate without --apply"

  if [[ "$MODE" == "loop" ]]; then
    prepare_loop_image
  else
    prepare_device
  fi

  mkdir -p "$STAGING"
  if ! mountpoint -q "$STAGING"; then
    mount -t ext4 -o "$opts" "$src" "$STAGING"
  fi

  stop_services
  log "rsync data to staging"
  rsync -aHAX --info=stats2 "$MOUNT_POINT"/ "$STAGING"/
  harden_tree "$STAGING"

  umount "$STAGING"
  rmdir "$STAGING" 2>/dev/null || true

  if [[ -e "$BACKUP_PATH" ]]; then
    die "backup path exists: $BACKUP_PATH (remove/rename first)"
  fi
  mv "$MOUNT_POINT" "$BACKUP_PATH"
  mkdir -p "$MOUNT_POINT"
  chown "$OWNER_USER:$OWNER_GROUP" "$MOUNT_POINT"
  chmod 2770 "$MOUNT_POINT"

  mount -t ext4 -o "$opts" "$src" "$MOUNT_POINT"
  write_fstab "$src" "$opts"
  harden_tree "$MOUNT_POINT"

  start_services "${units_before[@]}"
  log "cutover done. Old tree kept at $BACKUP_PATH — delete after you verify services."
  log "verify: findmnt $MOUNT_POINT && systemctl --failed 'moreney-*'"
}

main() {
  parse_args "$@"
  require_root
  ensure_owner_user

  if [[ "$HARDEN_ONLY" -eq 1 ]]; then
    harden_tree "$MOUNT_POINT"
    log "harden-only complete"
    exit 0
  fi

  migrate
}

main "$@"
