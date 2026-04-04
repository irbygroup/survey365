---
name: survey365-resilient-mode
description: Resilient mode architecture for Survey365's read-only rootfs with writable data volume. Use when dealing with filesystem permissions, mount issues, read-only root errors, fstab, tmpfs, SD card corruption prevention, or the maintenance remount workflow.
---

# Survey365 Resilient Mode

Resilient mode protects the Pi's SD card from corruption during unplanned power loss (common in field deployments). It makes the root filesystem read-only and isolates all writes to a separate data volume.

## Why It Exists

SD cards corrupt when write operations are interrupted. A field base station can lose power at any time — generator dies, battery runs out, cable gets kicked. Resilient mode ensures the OS survives by never writing to the root partition during normal operation.

## Filesystem Layout

```
/                          ← ext4, READ-ONLY (SD card root)
├── /boot/firmware         ← vfat, READ-ONLY (kernel, cmdline.txt)
├── /tmp                   ← tmpfs (64 MB)
├── /var/tmp               ← tmpfs (32 MB)
├── /var/log               ← tmpfs (48 MB) — journald Storage=volatile
├── /var/lib/nginx         ← tmpfs (8 MB)
├── /var/lib/sudo          ← tmpfs (1 MB)
├── /var/lib/chrony         ← tmpfs (1 MB)
├── /var/lib/tailscale     ← bind mount → /srv/survey365/tailscale
│
/srv/survey365             ← ext4, READ-WRITE (USB SSD, separate partition)
├── survey365.db           ← SQLite database
├── logs/                  ← Application logs (rotating)
├── rinex/                 ← Raw GNSS data files
├── tailscale/             ← Tailscale state (persisted across reboots)
├── systemd/random-seed    ← Symlinked from /var/lib/systemd/random-seed
└── rtklib/                ← RTKLIB runtime config (active-base.json)
```

## How It Works

### Boot sequence
1. `cmdline.txt` contains `ro` flag — kernel mounts rootfs read-only
2. `fstab` specifies `ro` for `/` and `/boot/firmware`
3. `fstab` mounts the data volume at `/srv/survey365`
4. `fstab` creates tmpfs mounts for volatile paths
5. `systemd-tmpfiles` creates nginx subdirectories on the tmpfs
6. `journald` writes to volatile storage only (RAM)
7. Tailscale state is bind-mounted from the data volume
8. `/etc/resolv.conf` is a symlink to `/run/NetworkManager/resolv.conf` (runtime-generated)
9. Survey365 starts, writes only to `/srv/survey365/`

### Key boot config
- `cmdline.txt`: `ro fsck.repair=yes` appended
- `fstab`: `/` and `/boot/firmware` have `ro` option
- `journald.conf.d/survey365-volatile.conf`: `Storage=volatile`
- Unattended upgrade timers disabled: `apt-daily.timer`, `apt-daily-upgrade.timer`, `man-db.timer`

## Helper Commands

These scripts are installed by `setup-pi.sh` at `/usr/local/bin/`:

| Command | What it does |
|---------|-------------|
| `sudo survey365-root-rw` | Remounts `/` read-write |
| `sudo survey365-root-ro` | Syncs and remounts `/` read-only |
| `sudo survey365-maint-rw` | Remounts `/` AND `/boot/firmware` read-write |
| `sudo survey365-maint-ro` | Syncs and remounts `/boot/firmware` then `/` read-only |

The `maint` variants are for apt upgrades or kernel changes that touch `/boot/firmware`.

## Common Agent Pitfalls

### ❌ "Permission denied" or "Read-only file system"
If you try to write any file on `/` while in resilient mode, it will fail. **You must remount first:**
```bash
sudo survey365-maint-rw
# ... make changes ...
sudo survey365-maint-ro
```

### ❌ Editing systemd units directly
Never edit `/etc/systemd/system/survey365*.service` directly on the Pi. Edit the templates in the repo's `systemd/` directory, then redeploy with `setup-pi.sh`.

### ❌ Installing packages without remounting
```bash
sudo survey365-maint-rw
sudo apt update && sudo apt install <package>
sudo reboot  # returns to read-only mode
```

### ❌ Forgetting to restore read-only
After any maintenance, always run `sudo survey365-maint-ro` (or reboot). Leaving the rootfs writable defeats the purpose of resilient mode.

## The `update.sh` + Resilient Mode Interaction

When `update.sh` runs on a resilient-mode Pi:
1. Detects read-only rootfs
2. Runs `sudo survey365-maint-rw` to remount writable
3. Performs git pull, pip install, `setup-pi.sh` redeploy
4. If `--os-upgrade`: runs `apt-get full-upgrade` then reboots (reboot restores read-only)
5. If no reboot: runs `sudo survey365-maint-ro` to restore read-only

## What Writes Where

### Always writable (data volume `/srv/survey365/`)
- `survey365.db` — all app state
- `logs/survey365.log` — rotating app log (5 MB × 3)
- `rinex/` — RTCM3 raw data files (gzipped after rotation)
- `tailscale/` — Tailscale node state
- `systemd/random-seed` — boot entropy
- `rtklib/active-base.json` — RTKLIB runtime config

### Volatile (tmpfs, lost on reboot)
- `/var/log/` — journald, nginx logs
- `/tmp/`, `/var/tmp/` — temporary files
- `/var/lib/nginx/` — nginx runtime state
- `/var/lib/sudo/` — sudo timestamp cache
- `/var/lib/chrony/` — NTP drift state

### Never written (read-only rootfs)
- All system binaries, libraries, Python packages
- Nginx config, systemd units, udev rules, sudoers
- The git repo itself (code is read-only at runtime)

## Enabling Resilient Mode

```bash
# Format and mount a USB disk, then enable resilient mode
sudo bash scripts/enable-resilient-usb.sh --device=/dev/sda --user=jaredirby --force --reboot

# Or enable on an already-mounted data partition
sudo bash scripts/setup-pi.sh --user=jaredirby --resilient --data-root=/srv/survey365
```

## Maintenance Pattern (apt upgrades)

```bash
sudo survey365-maint-rw
sudo apt update
sudo apt full-upgrade
sudo reboot   # reboot restores read-only mode cleanly
```

## Checking Current State

```bash
# Is rootfs read-only?
mount | grep "on / " | grep -o "ro\|rw"

# Is data volume mounted?
mountpoint -q /srv/survey365 && echo "mounted" || echo "NOT mounted"

# What filesystem is the data volume?
findmnt -no FSTYPE /srv/survey365

# Is the data volume writable?
touch /srv/survey365/.write-test && rm /srv/survey365/.write-test && echo "writable" || echo "NOT writable"
```
