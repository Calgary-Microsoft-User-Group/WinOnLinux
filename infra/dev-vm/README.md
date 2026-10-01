# WinOnLinux Azure dev VM

Provisions the Linux desktop dev/test machine required by the spec (NFR-7 platform baseline, E-8 test
matrix): **Ubuntu 24.04 LTS + GNOME**, reached over **xrdp** from a Windows machine, pre-loaded with the
decided stack — Python 3 + GTK4/libadwaita/PyGObject (D-16), GNOME Keyring/libsecret (D-2), Flatpak +
flatpak-builder (D-3), and all build dependencies for FreeRDP ≥ 3.30.0.

Target subscription: **AZ-PRI-01** (`7a713a84-…`). Every script hard-fails if `az` is logged into anything
else. Defaults: resource group `rg-winonlinux-dev`, VM `vm-wol-dev`, region `canadacentral`,
size `Standard_D4s_v4` (4 vCPU / 16 GB).

## Scripts

| Script | What it does |
| --- | --- |
| `deploy.ps1` | Creates the RG and deploys `main.bicep`. NSG admits SSH/RDP only from your current public IP (override with `-AllowedIp`). Prompts for the admin password (12–72 chars, 3 of 4 character classes). |
| `stop-vm.ps1` | **Everyday off-switch.** Deallocates the VM ($0 compute) and downgrades the OS disk to Standard HDD (~$6/mo). ~1 min each way. |
| `start-vm.ps1` | Re-tiers the disk to Standard SSD and starts the VM. |
| `archive-to-cool.ps1` | **Long-idle off-switch.** Snapshots the OS disk, copies it into a **Cool-tier block blob**, then deletes the VM + disk (network kept, IP stays). ~$1.50/mo storage for 128 GB. Asks for confirmation; destructive by design. |
| `restore-from-cool.ps1` | Rebuilds the managed disk and VM from the cool blob. Takes ~15–30 min (blob→page-blob copy + disk import). |

A daily auto-shutdown at **19:00 Mountain** is deployed with the VM as a forgot-to-turn-it-off backstop
(deallocate only — it does not re-tier the disk; run `stop-vm.ps1` for that).

Note on "cool storage": Azure managed disks have no cool tier — cool is a blob-storage concept, and page
blobs (the VHD export format) cannot be tiered either. `archive-to-cool.ps1` therefore converts the disk
to a **block** blob during the server-side copy, which is what makes the Cool tier applicable, and
`restore-from-cool.ps1` converts it back to a page blob before the managed-disk import.

## First login

1. `./deploy.ps1` — note the public IP it prints.
2. Wait for provisioning: `ssh woldev@<ip> cloud-init status --wait` (desktop install takes ~10–20 min).
3. `mstsc /v:<ip>` → xrdp login with the same username/password → full GNOME (Xorg) session.

Sanity checks on the box:

```bash
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); print('GTK4 + libadwaita OK')"
secret-tool store --label=test wol test <<< "x" && secret-tool lookup wol test   # keyring reachable
flatpak remotes   # flathub present
```

## FreeRDP 3.30.0+ (manual step, deliberately)

The distro `xfreerdp` is far below the NFR-8 floor of 3.30.0; build from source (all build deps are
pre-installed). Per the repo's "verify, don't assert" rule, the 3.30.0 tag, its contents, and the §5.5
flag names are July–Aug 2026 claims that Stage 0 must re-verify — check them on the box rather than
trusting this file:

```bash
git clone --branch 3.30.0 --depth 1 https://github.com/FreeRDP/FreeRDP.git ~/FreeRDP
cmake -S ~/FreeRDP -B ~/FreeRDP/build -GNinja -DCMAKE_BUILD_TYPE=Release \
      -DWITH_SERVER=OFF -DWITH_FFMPEG=ON -DCHANNEL_URBDRC=OFF
cmake --build ~/FreeRDP/build && sudo cmake --install ~/FreeRDP/build
xfreerdp --version && xfreerdp --help   # verify version floor and §5.5 flag names here
```

(Adjust `-D` options against the FreeRDP 3.x build docs if cmake reports missing optional deps.)

## Costs (canadacentral, approximate)

| State | Compute | Storage | Total order-of-magnitude |
| --- | --- | --- | --- |
| Running | ~$0.19/hr (D4s_v4) | Standard SSD 128 GB ~$10/mo | ~$45/mo at 8 h/day |
| Stopped (`stop-vm.ps1`) | $0 | HDD ~$6/mo + IP ~$4/mo | ~$10/mo |
| Archived (`archive-to-cool.ps1`) | $0 | Cool blob ~$1.50/mo + IP ~$4/mo | ~$5.50/mo |

## Caveats

- **xrdp sessions are Xorg only.** That matches the Phase 1 reference platform (D-20, §5.6), but the
  Wayland half of the E-8 test matrix cannot be exercised over xrdp — that needs local hardware or a
  Hyper-V/console session later. Don't let green results here read as Wayland coverage.
- **No GPU** on D-series: fine for GTK dev and protocol work, not valid for NFR-6 60 fps measurement (E-9).
- **E-7 still needs a Windows host** running Windows App ≥ 2.0.804.0 for the Stage 1 capture — this VM
  does not cover that prerequisite.
- The NSG pins access to the IP you deployed from. If your home IP changes:
  `az network nsg rule update -g rg-winonlinux-dev --nsg-name nsg-vm-wol-dev -n Allow-SSH-From-Home --source-address-prefixes <new-ip>`
  (and the same for `Allow-RDP-From-Home`).
- `restore-from-cool.ps1` keeps the cool archive blob after a successful restore; delete it manually once
  the VM checks out.
