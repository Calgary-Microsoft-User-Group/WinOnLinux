# Release checklist

Per-release steps recorded by the `add-flatpak-packaging` change (tasks 5.2/5.3) and D-23.
Items marked **[open decision]** need an owner call before the first distributed build — which
is also gated on **LG-1** (the written legal position precedes any distribution, spec.md §11.1).

## Every release

1. **Re-pin the FreeRDP module** (`packaging/flatpak/freerdp-module.yml`): pick the release
   tag, update `tag:` and `commit:` TOGETHER (`git ls-remote --tags
   https://github.com/FreeRDP/FreeRDP.git`), and confirm the tag still meets the ≥ 3.30.0
   floor (§2.2). The runtime probe (NFR-8) backstops a bad bundle but must never be the first
   line of defense. *(Phase 1 onward — the Phase 0 manifest omits the module.)*
2. **Regenerate `constraints.txt` deliberately** (D-23): `pip-compile --strip-extras
   --no-header -o constraints.txt requirements.in` from the pyproject floors; review the
   `pip-audit` CI output for anything that appeared since the last regeneration.
3. **Add the release to the AppStream metadata**
   (`data/...metainfo.xml` `<releases>`), version and date matching the tag.
4. **Bump `version`** in `pyproject.toml`; tag the repo.
5. **Confirm CI is green across the matrix AND the flatpak job** for the release commit.

## Before the FIRST distributed build (one-time)

- **[open decision] Distribution channel** — Flathub vs a repo-hosted Flatpak remote
  (proposal task 5.2). Consequences to weigh:
  - *Flathub*: discovery and updates for free, but requires **vendored (offline) build
    sources** — replace the manifest's `--share=network` pip install with
    `flatpak-pip-generator`-generated pinned sources from the same `constraints.txt` — plus
    Flathub review and publisher verification questions (ties into D-19's registration
    ownership).
  - *Repo-hosted remote*: ships as-is with the current manifest, no review, but users must
    add the remote and discovery is nil.
- **Gate LG-1** must have its written legal position recorded before either channel ships
  anything (spec.md §11.1) — distribution, not development, is what the gate blocks.
- **Replace the placeholder icon** (`data/icons/.../*.svg`) with designed artwork before any
  store listing.
- **Gate STACK V2** (`add-flatpak-packaging` tasks 3.x) runs against the first real build:
  Secret Service portal on GNOME and KDE, OpenURI/filesystem portal costs, X11-socket
  grantability for the bundled `xfreerdp`, proxy exposure (§5.9). Findings go to spec.md
  §14.4 and can fire D-3's revisit trigger.

## Secondary formats (deb/rpm — proposal tasks 4.x, not yet built)

Ship only for distributions whose native FreeRDP satisfies ≥ 3.30.0 (survey at release
time; the set changes per distro release). Hard versioned dependency, same application tree,
no AppImage.
