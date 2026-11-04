# HWVAL Factory — operator quick guide

One page per screen. The station is normally in **kiosk mode**, so only the
station view is visible.

## Sign in

1. Scan your badge, or choose **ID and PIN**, enter your user name and PIN, and
   press **Sign in**.
2. The header always shows the active product profile and version, your user
   name, the work order, pass/fail counts, FPY and the MES queue length.
3. The session locks after the configured idle time; sign in again to unlock.

## Station view

Each fixture is one large tile:

| Tile shows | Meaning |
|---|---|
| IDLE / LOAD / RUNNING / ACTION / PASS / FAIL / ERROR | State, with an icon, a word and a colour |
| Step name and progress bar | Current EOL step |
| Elapsed time | Cycle time of the current run |
| PCB serial / unit serial | Identity of the board |
| Red tile with "Failed at …" | The failing step and reason |

* Press **Start** on a tile (or **F1–F8**) to start that fixture.
* When the tile says **Waiting for scan**, scan the board barcode. The scan
  goes to the waiting fixture automatically; no field needs focus.
* Press **Abort** on the tile (or the same function key) to abort at the next
  safe point. The run is recorded as ABORTED and the probe is released.
* **Start all** starts every configured fixture. **Abort all** stops all runs.
* **Detail** opens the live step list, measured test values, OpenOCD log and
  label preview.
* When the firmware requests an operator action, a large dialog names the
  fixture and the test. Follow it and press the requested choice.

A PASS tile is green and beeps once. A FAIL tile is red, names the failing
step, and beeps three times. Do not remove a board until the tile reaches PASS,
FAIL or ERROR.

## Labels

* A configured printer receives the ZPL label over TCP port 9100 or a local
  print queue.
* Without a printer, Factory saves `.zpl`, `.png` and `.pdf` under the labels
  directory and marks the run **label pending**. The label can be printed later
  by a supervisor.
* A reprint always requires supervisor approval and is recorded.

## Escalation

* **FAIL / ERROR** — leave the board in the fixture, call a supervisor, and
  note the run number shown in the status bar.
* **ACTION** — the test plan is waiting for you; read the dialog.
* **No fixture waiting** — press Start on the wanted tile first.
* **Probe warning** — a configured ST-LINK is missing or an unconfigured probe
  is attached; call an engineer.
