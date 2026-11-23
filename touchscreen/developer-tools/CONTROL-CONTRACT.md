# Controls, availability and operations

This UI uses composition and explicit data contracts rather than widget subclasses.
The Python service is authoritative; the C renderer consumes its availability map.
Fresh printer/configuration checks and the existing Klipper/CFS macros remain in place.

## Action contract

`backend/action_policy.py` defines immutable `ActionSpec` records. Every action
has a label, resource set, idle requirement, readiness requirement, interrupt flag,
and completion-reporting flag. Parameterized actions resolve their actual heater,
fan or preference resource before admission. Unknown actions fail closed.

`Service.submit()` reserves resources under the service lock before starting work.
Conflicting requests are rejected, never queued for later movement. An expected
`ActionBusy` response refreshes availability without creating a modal fault.
There are at most six independent operations plus one explicit print interruption
or restart. A failing thread start or execution always releases its reservation.
There is no worker-pool framework, subclass hierarchy, or hidden deferred jog queue.

| Action | Owned resources |
| --- | --- |
| XY/Z jog or absolute position | Motion |
| Motors on/off | Motion and filament |
| Manual extrusion | Motion, filament and nozzle |
| Homing, CFS load/unload | Motion, filament, nozzle and part fan |
| Calibration | Motion, filament, all heaters and controllable fans |
| Start print | Motion, filament, heaters, part fan, metadata and `file:<path>` |
| Save configuration | All mutable controls |
| Temperature or fan | Only the selected heater/channel |
| Filament editing and CFS preferences | Filament; preferences also serialize persistence |
| Display preferences / time zone | Preference persistence |
| File list / metadata | Coalesced read / selected metadata and `file:<path>` |
| File deletion | Storage and the selected `file:<path>` |
| Recording deletion / USB export | Recording/storage resources, as declared by the action policy |
| Restart or print interruption | Reserved interrupt channel; confirmation/state guards still apply |

Only independent requests can be outstanding together. Klipper may itself serialize
G-code execution; this design promises responsive navigation, not parallel mechanics.
The UI does not invent a Cancel button for an operation the firmware cannot abort.

Completed results retain their request IDs in a bounded 32-entry list, so concurrent
completions cannot overwrite each other's UI transition. History is separate from
in-flight state. A late filament-save result does not pull a user out of another tab.

## Native control contract

Use `button()`, `button_fixed()` or `row()`, supplying geometry, caption/icon,
action and selection state. Both button factories share styling, binding, minimum
touch behavior and policy handling. `button_fixed()` uses a larger caption for
tall footer/card actions. All geometry comes from the native source.

`views_control_key()` maps UI-only aliases to the public action key. Serialized
backend requests derive their keys automatically. Navigation, browsing and drafts
are local actions; mutation controls bind to their corresponding backend action.
Custom controls such as fan sliders must also consume `ui_control_reason()`.
The final send path checks the policy again, and the service revalidates fresh state.

Policy-owned disabled state is tagged independently from numeric validity and
missing-axis/cold-extrusion state. Availability changes update an already open
dialog; unlocking a resource cannot accidentally enable an invalid numeric value.
Decorative rings, swatches and icons are not independent touch targets.

## View contract

`src/view_inputs.c` declares each view's snapshot dependencies in `ViewSpec`.
Only relevant changes rebuild a view. Live coordinates and heater labels update in
place; local draft changes explicitly mark the view dirty. Unregistered pages use
a conservative full-snapshot fallback rather than silently failing to refresh.

The activity indicator occupies the otherwise unused bottom of the navigation
rail. It persists across tabs without covering controls or changing content layout.
Tapping it opens current operations and status history. Errors and destructive
confirmations still use dialogs. Navigation remains available during work.

When adding a control/action, add its action declaration and binding key, retain
fresh backend validation, and test its conflicting and independent operation cases.
When adding a view, declare its input paths and test snapshot updates while pressed
or scrolling. The fixture preview sends no printer commands.

## Screen power and idle timers

Screen off, light off and idle shutdown are grouped on the timers page. Screen
sleep uses UI inactivity and stays inhibited during printing, paused jobs,
operations and pending notices. The first touch is consumed only to wake the
screen; the service publishes awake state before the physical power command
finishes. Power commands serialize, and stale sleep requests are discarded.
Print/pause status and urgent faults wake the screen without a touch.

Snapshots show actual Klipper idle/light timer values when available, falling
back to saved preferences and loaded configuration. Changing the light timer
re-arms or cancels its existing countdown while idle. Idle shutdown retains the
printer's configured idle G-code, including its pause-specific behavior. Klipper
requires a positive timeout: the previous idle `Never` value (0, implemented as
86400 seconds) is preserved and accurately displayed as `24 hours`. Screen and
light `Never` settings still disable their respective timers.

## File deletion and activity captions

Files and time-lapse recording lists expose a trash icon on each row, with a
filename-specific Cancel/OK confirmation. The print-file preview has no Delete action.
The service requires explicit confirmation, validates a relative G-code path and
rechecks the active print filename before delegating to Moonraker. Other files
can be deleted while printing or paused; the active file cannot. Start, metadata
selection and deletion reserve a per-path resource so a pending print start also
protects its file. Deletion additionally reserves storage. Errors leave the list
intact; success clears only the matching selection, cache and metadata.

File-list reads serialize with deletion, and old thumbnail workers cannot publish
into a newer list generation. Missing or unreadable thumbnails clear stale cached
previews. The list row and its separate trash target must not overlap: a disabled
trash control must never open the print-file screen underneath it.

Navigation remains a fixed group above a separate activity area. Activity uses
short explicit captions limited to two lines (e.g. Loading / filament), never an
unbounded operation description. Existing status history remains unchanged.

Moonraker file-delete API reference:
https://moonraker.readthedocs.io/en/latest/external_api/file_manager/#file-delete


## Material compatibility

Suggestions, picker selection and print start must respect a slicer-declared
material type. Python `model.material_matches()` and the small native comparator
ignore case and ASCII whitespace; variants/additives remain distinct (PLA versus
PLA+, PETG versus PETG-CF). Colors may differ. Keep both comparators in sync and
exercise UI and service rejection tests when changing this rule. If the slicer
omits its material type, manual mapping remains available without claiming a
verified match.

The picker header states Select filament: <material>. Incompatible rows stay
visible but are muted and cannot be selected; repeated warning labels are omitted. A stale mapping shows a red border and Material mismatch;
Start print remains disabled. The service revalidates against fresh slot data
before sending any G-code. There is no override dialog or silent material fallback.

Recording deletion uses Moonraker's published timelapse root and rechecks the
completed-recordings list before deleting. The bundled time-lapse renderer writes
in its temporary directory and publishes videos to that root after rendering.
Only MP4/MKV filenames are eligible. Completed recordings can be deleted during
printing; the shared action policy serializes deletion with recording reads and
USB export. The list uses separate, borderless save/export and trash touch areas.


## Axis homing and motor controls

XY movement and absolute-position entry require both X and Y homed; Z requires
Z homed. UI availability has per-axis keys so open numeric dialogs disable Set
when homing is lost. The service independently rechecks fresh homing state.
Views rebuild when homing, axis-enable state or extrusion readiness changes.
Extrusion retains its separate idle, temperature, distance and operation guards.

The motor control uses Lock motors / Unlock motors and open/closed padlock
icons representing current axis enable state. It sends an explicit enable flag,
never a backend toggle inferred from possibly stale status. Unlock requires
confirmation and runs M84, which clears Klipper homing. Lock enables configured
XYZ steppers without homing or restoring position. Motor enable state considers
axis steppers, not the extruder's enable pin. No model-specific axis list or
motion limits are embedded in the service.

## Connection lifecycle

`ui_connection_mode()` is the single page-level gate: initial startup, ready, or
connection lost. Startup waits for connected Klipper, ready webhooks, populated
settings/heaters and the initial motor handshake when that capability exists.
Ordinary G-code failures with ready Klipper remain dismissible notices. Shutdown
or disconnection replaces the whole page and dismisses obsolete open dialogs.
Only restart actions and their confirmation/cancellation are available; commands
for navigation, movement, filament or temperature are rejected by the UI guard.
If the backend socket is unavailable, a separate service-reconnection screen
hides restart buttons until the service reconnects. Recovery returns to the previous route without issuing motion.
