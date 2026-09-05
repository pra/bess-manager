# Deploying your fork to Home Assistant (aarch64 / HA Green)

This fork ships its own add-on image to **your** GHCR
(`ghcr.io/pra/bess-manager-aarch64`) and installs on the Green like any add-on.
An add-on **update never wipes `/data`**, so you configure BESS **once** and every
later deploy keeps all your settings.

## One-time setup

1. **Merge this to `main`.** HA only ever reads `config.yaml` from the fork's
   default branch (`main`) — there is no branch picker. So the deploy machinery
   and the `image:` pointer must live on `main`.

2. **Make the GHCR package public.** After the first deploy runs, go to
   github.com/pra?tab=packages → `bess-manager-aarch64` → Package settings →
   Change visibility → **Public**. HA pulls it anonymously; private needs
   registry auth the add-on store can't easily provide.

3. **Add the repo to HA.** Settings → Add-ons → Add-on Store → ⋮ →
   Repositories → add `https://github.com/pra/bess-manager`.
   "**BESS Manager (fork)**" appears in the store.

4. **Install it and configure once.** Install, then either:
   - re-run the setup wizard + re-enter the 4 InfluxDB fields (no shell needed), or
   - copy the old add-on's `/data/bess_settings.json` (+ InfluxDB options) over
     via the Advanced SSH add-on if you want the exact existing setup.
   This is the *only* time you configure it.

   > Tip: set the InfluxDB **bucket** to `homeassistant/autogen` (no underscore) —
   > the default in this file is the buggy `home_assistant/autogen` (see PR #434).

## Deploy loop (every time after)

1. Push the branch you want to ship.
2. GitHub → **Actions** → **Deploy to fork (aarch64)** → **Run workflow** →
   pick the branch (optionally type a version) → Run.
   (~a few minutes; builds on GitHub, not the Green.)
3. On the Green: **BESS Manager (fork) → ⋮ → Rebuild/Reload**, then **Update**.

`/data` (all your settings) carries across untouched. Not mission-critical:
if a build is bad, just deploy the previous good branch/version and roll forward.

## Notes

- The workflow auto-bumps `version:` in `config.yaml` on `main` each run
  (`9.8.1-dev.<run number>`, monotonic so HA always sees an update). Pass an
  explicit version in the Run-workflow form if you want a clean number.
- Only **aarch64** is built (the Green). `arch:` is set accordingly.
- Keeping your fork current with upstream is easy — this diverges from upstream
  by just `config.yaml`'s `name`/`image`/`arch` lines plus this workflow + doc.
