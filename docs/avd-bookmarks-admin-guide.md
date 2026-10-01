# AVD bookmarks: admin provisioning guide (Phase 0)

## What is an AVD bookmark, and why does Phase 0 need them?

WinOnLinux enumerates Windows 365 Cloud PCs directly through Microsoft Graph
(`GET /me/cloudPCs`) -- an ordinary end user has enough delegated permission for that call to
work on its own.

Azure Virtual Desktop (AVD) is different. AVD workspaces, desktops, and RemoteApps live under
Azure Resource Manager (ARM), not Graph, and discovering which ones a given user is assigned to
normally requires talking to the **AVD workspace feed** -- a protocol this client has not yet
reverse-mapped (that work is tracked separately, in `add-avd-feed-provider`). Until that lands,
an ordinary end user has no supported way to self-enumerate their AVD resources: Graph has no
delegated permission that exposes them, and the ARM APIs that do (`Get-AzWvdWorkspace` and
friends) require Azure RBAC roles an ordinary end user does not hold.

So for this phase ("Phase 0"), AVD support in WinOnLinux is **admin-provisioned bookmarks**: a
tenant admin -- who *does* have the ARM permissions to look these IDs up -- supplies a small,
static list of `{workspaceId, resourceId, displayName, kind}` entries. WinOnLinux renders them
grouped by workspace, alongside the Cloud PCs it enumerated itself.

Two things fall directly out of this being a workaround rather than the real thing:

- **Web launch only.** A Phase 0 bookmark's native (FreeRDP) connect option is disabled, with
  the reason "Connection configuration cannot be acquired for this resource yet" -- Phase 0 has
  no way to obtain the `.rdpw` connection configuration a native launch needs (that requires the
  same feed access), so it falls back to opening the resource in the AVD web client instead.
- **IDs are opaque to the client.** WinOnLinux does not validate a bookmark's `workspaceId` or
  `resourceId` against anything -- see [Wrong or stale IDs](#wrong-or-stale-ids-not-a-client-bug)
  below.

## Where do `workspaceId` and `resourceId` come from?

Both are ARM object IDs (GUIDs), not display names. An admin with the `Az.DesktopVirtualization`
PowerShell module and appropriate RBAC access to the AVD host pool(s) in question can retrieve
them as follows.

**Workspace ID** -- run `Get-AzWvdWorkspace` (scoped to the resource group, or across the
subscription) and copy the `Id` (or `.Id`) property of the workspace object the desktop/RemoteApp
in question is registered to. This is the `workspaceId` for every bookmark drawn from that
workspace.

**Resource ID, and desktop vs. RemoteApp** -- an AVD workspace's application group(s) are what
distinguish a full desktop from an individual published app:

- Run `Get-AzWvdApplicationGroup` (scoped to the workspace) and look at each group's
  `ApplicationGroupType`. A `Desktop`-type group publishes a session desktop; a
  `RemoteApp`-type group publishes individual applications.
- For a `Desktop`-type application group, run `Get-AzWvdDesktop` against that group and copy the
  desktop object's `Id` -- that is the bookmark's `resourceId`, with `kind: "desktop"`.
- For a `RemoteApp`-type application group, run `Get-AzWvdApplication` against that group and
  copy the `Id` of each published application -- each one is a separate bookmark's `resourceId`,
  with `kind: "remoteapp"`.

Copy the object's friendly name (`FriendlyName`, falling back to `Name`) into `displayName` --
that is purely cosmetic and is what WinOnLinux shows in its UI; it does not need to match
anything server-side.

*(This describes the general shape of the lookup, not verbatim runnable PowerShell -- exact
cmdlet parameters and output property casing should be confirmed against the `Az.DesktopVirtualization`
module version actually installed, per the repository's "verify, don't assert" discipline.)*

## The JSON shape

`BookmarkStore` persists one JSON document at
`$XDG_STATE_HOME/winonlinux/avd_bookmarks.json` (falling back to
`~/.local/state/winonlinux/avd_bookmarks.json` when `XDG_STATE_HOME` is unset), shaped as:

```json
{
  "schemaVersion": 1,
  "bookmarks": [
    {
      "workspaceId": "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.DesktopVirtualization/workspaces/<workspace-name>",
      "resourceId": "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.DesktopVirtualization/applicationgroups/<ag-name>/desktops/SessionDesktop",
      "displayName": "Engineering Desktop",
      "kind": "desktop"
    },
    {
      "workspaceId": "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.DesktopVirtualization/workspaces/<workspace-name>",
      "resourceId": "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.DesktopVirtualization/applicationgroups/<ag-name-2>/applications/notepad",
      "displayName": "Notepad",
      "kind": "remoteapp"
    }
  ]
}
```

Every entry requires all four fields. `kind` must be exactly `"desktop"` or `"remoteapp"` --
anything else (including a typo) is skipped when the client loads the file, with a warning
logged for that one entry only; the rest of the list still loads normally.

`schemaVersion` is managed by `BookmarkStore`/`StateStore` (D-13) -- an admin hand-editing this
file should leave it as `1` (the current version) rather than guessing at a migration.

## Wrong or stale IDs: not a client bug

WinOnLinux does not validate `workspaceId` or `resourceId` against Azure in any way -- it treats
them as opaque strings and only ever hands them to Microsoft's own AVD web client at launch time
(`https://windows.cloud.microsoft/webclient/avd/<workspaceId>/<resourceId>`). If an ID is wrong,
stale (the resource was deleted or renamed), or the signed-in user is no longer assigned to it,
the failure surfaces **in the browser**, as whatever error Microsoft's web client shows for that
condition -- not as an error from this client.

This is a deliberate design trade-off (see `openspec/changes/add-cloudpc-enumeration/design.md`),
not a defect: validating these IDs client-side would require the same ARM/feed access this whole
Phase 0 workaround exists because end users don't have. If a bookmark stops working, the fix is
to re-run the lookup above and correct the stored entry -- it is not something to file against
WinOnLinux itself.
