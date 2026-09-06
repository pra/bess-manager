# SSH into HA Green + back up BESS config (do this before switching anything)

> Verified working on this Green (HA Green, aarch64) 2026-09-06.
> Key gotchas that tripped us up are called out inline.

## Step 0 — Safest snapshot first (no SSH needed)

Settings → System → Backups → **Create backup** (include the BESS add-on).
Snapshots its `/data`, one-click restorable. Do this regardless of the below.

## Step 1 — Install the SSH add-on with the right access

Install **"Advanced SSH & Web Terminal"** (Frenck's community add-on). The
*official* "Terminal & SSH" cannot reach another add-on's `/data`, so it won't
work for this.

Configuration — the options are nested under an **`ssh:`** block, and the login
user is **`hassio`** by default (NOT root). Set at least one auth method:

```yaml
ssh:
  username: hassio          # <-- you log in as THIS user, not root
  password: ""
  authorized_keys:
    - "ssh-ed25519 AAAA...your key on one line... you@host"
  sftp: false               # note: this being false is why scp fails (see Step 4)
  compatibility_mode: false
```

Gotchas:
- Neither key nor password set → the add-on refuses to start
  (`init-ssh: command exited 1`, "set at least an SSH password or one
  authorized key").
- The public key must be the whole line, unbroken. Paste-truncation = auth fails.
- **Save AND restart** the add-on after any auth change.
- To expose network SSH (vs only the in-UI Web Terminal), set a port (e.g. 22)
  in the add-on's **Network** box. If it's blank, port 22 is refused.

## Step 2 — Connect (as hassio, not root)

Find the Green's IP: Settings → System → Network (or your router).

```bash
ssh hassio@<green-ip>
```

Connecting as `root` gives `Permission denied (publickey)` because the key is
authorized for `hassio`. Or just use the add-on's built-in **Web Terminal** in
the HA UI (runs as root, no key/port needed).

## Step 3 — Find BESS and copy its /data out (docker needs sudo)

```bash
sudo docker ps --format '{{.Names}}' | grep -i bess
# -> app_<hash>_bess_manager   (on this system: app_faaac926_bess_manager)

sudo docker cp app_faaac926_bess_manager:/data /share/bess-data-backup
sudo chown -R hassio /share/bess-data-backup     # so you can read/copy it
ls -la /share/bess-data-backup
```

As `hassio` the Docker socket is root-only, so prefix docker with `sudo`.
Use `/share/...` as the target (writable, and also visible over Samba).

## Step 4 — Pull the files to your Mac (use tar, NOT scp)

`scp`/`sftp` fail here — `subsystem request failed on channel 0` — because the
add-on has `sftp: false`. Stream over a plain SSH channel with tar instead:

```bash
mkdir -p /Users/pra/src/energy/ha
ssh hassio@<green-ip> "cd /share && tar czf - bess-data-backup" \
  | tar xzf - -C /Users/pra/src/energy/ha
```

(If you'd rather use scp: set `sftp: true` in the add-on config and restart,
then `scp -r hassio@<green-ip>:/share/bess-data-backup .` — but tar needs no
config change.)

## What the files are

- `bess_settings.json` — everything from the BESS UI (battery, home,
  electricity_price, energy_provider, growatt, inverter, sensors). Copy this
  into the fork add-on's `/data` to reuse your setup without the wizard.
- `options.json` — the InfluxDB block (url/bucket/username/password). Confirm
  bucket is `homeassistant/autogen` (no underscore).
- `logs/` — daily rotating logs (`bess-YYYY-MM-DD.log[.zip]`, 7-day retention).

## Later — seed the fork add-on with the saved config (one-time)

After installing "BESS Manager (fork)" and starting it once (so its `/data`
exists), find the new container and copy the file in, then restart it:

```bash
sudo docker ps --format '{{.Names}}' | grep -i bess     # the NEW fork container
cat bess_settings.json | sudo docker exec -i <fork-container> sh -c 'cat > /data/bess_settings.json'
# then restart the fork add-on from the HA UI
```

(InfluxDB creds you just re-type in the fork add-on's Configuration tab.)
