#!/usr/bin/env bash
# Provision a USB disk for Survey365 resilient mode.
# Example:
#   sudo bash scripts/enable-resilient-usb.sh --device=/dev/sda --user=jaredirby --force --reboot

set -euo pipefail
PATH="/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()   { err "$*"; exit 1; }

if [[ $EUID -ne 0 ]]; then
    die "Run as root: sudo bash scripts/enable-resilient-usb.sh ..."
fi

DEVICE=""
TARGET_USER=""
DATA_ROOT="/srv/survey365"
FORCE=false
REBOOT_AFTER=false

for arg in "$@"; do
    case "$arg" in
        --device=*) DEVICE="${arg#--device=}" ;;
        --user=*) TARGET_USER="${arg#--user=}" ;;
        --data-root=*) DATA_ROOT="${arg#--data-root=}" ;;
        --force) FORCE=true ;;
        --reboot) REBOOT_AFTER=true ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

[[ -n "$DEVICE" ]] || die "Missing --device=/dev/sdX"
[[ -b "$DEVICE" ]] || die "Block device not found: $DEVICE"
[[ -n "$TARGET_USER" ]] || die "Missing --user=<username>"
id "$TARGET_USER" >/dev/null 2>&1 || die "User '$TARGET_USER' does not exist"
[[ "$FORCE" == "true" ]] || die "Refusing to wipe $DEVICE without --force"

if [[ "$DEVICE" == /dev/mmcblk0* || "$DEVICE" == /dev/nvme0n1* ]]; then
    die "Refusing to wipe a likely system disk: $DEVICE"
fi

TRANSPORT=$(lsblk -dn -o TRAN "$DEVICE" 2>/dev/null || true)
if [[ "$TRANSPORT" != "usb" ]]; then
    die "$DEVICE is not detected as USB storage"
fi

FSTYPE=$(lsblk -dn -o FSTYPE "$DEVICE" 2>/dev/null || true)
LABEL=$(lsblk -dn -o LABEL "$DEVICE" 2>/dev/null || true)
SIZE=$(lsblk -dn -o SIZE "$DEVICE" 2>/dev/null || true)

warn "About to wipe $DEVICE ($SIZE, fstype='${FSTYPE:-unknown}', label='${LABEL:-none}')"

while read -r path mnt; do
    [[ -n "$path" && -n "$mnt" ]] || continue
    if [[ "$path" == "$DEVICE" || "$path" == "$DEVICE"* ]]; then
        die "Refusing to wipe mounted path $path mounted at $mnt"
    fi
done < <(findmnt -rn -o SOURCE,TARGET)

info "Removing existing signatures from $DEVICE..."
wipefs -a "$DEVICE" >/dev/null 2>&1 || true
while read -r child; do
    [[ "$child" == "$DEVICE" ]] && continue
    wipefs -a "$child" >/dev/null 2>&1 || true
done < <(lsblk -ln -o PATH "$DEVICE")

info "Creating GPT and single ext4 partition..."
printf 'label: gpt\n, ,L\n' | sfdisk "$DEVICE" >/dev/null
partprobe "$DEVICE"
sleep 2

PARTITION="$(lsblk -ln -o PATH,TYPE "$DEVICE" | awk '$2 == "part" { print $1; exit }')"
[[ -n "$PARTITION" ]] || die "Failed to discover new partition on $DEVICE"

info "Formatting $PARTITION as ext4..."
mkfs.ext4 -F -E nodiscard -L survey365-data "$PARTITION" >/dev/null

mkdir -p "$DATA_ROOT"
if mountpoint -q "$DATA_ROOT"; then
    umount "$DATA_ROOT"
fi

info "Mounting $PARTITION at $DATA_ROOT..."
mount "$PARTITION" "$DATA_ROOT"

UUID="$(blkid -s UUID -o value "$PARTITION")"
[[ -n "$UUID" ]] || die "Failed to read UUID from $PARTITION"
ok "Mounted resilient data disk UUID=$UUID"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_SCRIPT="$(cd "$SCRIPT_DIR" && pwd)/setup-pi.sh"

info "Enabling resilient mode via scripts/setup-pi.sh..."
"$INSTALL_SCRIPT" --user="$TARGET_USER" --resilient --data-root="$DATA_ROOT" --data-device="UUID=$UUID"

if [[ "$REBOOT_AFTER" == "true" ]]; then
    warn "Rebooting to enter resilient mode..."
    systemctl reboot
else
    warn "Reboot required before resilient mode takes effect"
fi
