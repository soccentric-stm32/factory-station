# HWVAL Factory — supervisor guide

Supervisors can run units, select work orders, approve retests and reworks,
reprint labels, run self-tests, and view traceability.

## Work orders

**Work Order → Select…** asks for:

* work order name;
* the profile (the active profile must match);
* quantity;
* serial range (start/end) and optional prefix;
* MAC range when the profile enables Ethernet.

Factory loads the serial and MAC pools atomically in SQLite. A serial is
**reserved** when step 7 starts, **consumed** only after the provisioning
record is read back and checked, and returned to the pool if the run fails
before that point. The work order dialog warns at 90 % remaining and the run
stops when the range is exhausted. **Work Order → Close** closes the order and
audits the action.

## Retest and rework

* **Retest** — a board with the same PCB serial may be retested. After the
  configured number of failures (default 2), a supervisor PIN is required.
  Right-click the fixture tile → **Retest…**.
* **Rework** — a unit already programmed is recognised by its chip UID; the
  existing serial number and MAC are reused, the provisioning record revision
  is incremented, and the old record remains in the database. Rework requires a
  supervisor PIN and a second confirmation. If the chip is read-protected,
  Factory lowers RDP to level 0, which mass-erases all flash; the dialog states
  this explicitly.

Every retest, rework, reprint and protection change is written to the audit
log.

## Labels

**Tools → Label printer…** configures the printer (engineer permission).
**Reprint** is available from **Unit history** or **File → Print certificate**
for the selected run. A reprint requires supervisor approval, uses the stored
label data, and is recorded with the user and time. Without a printer the
label is saved as ZPL/PNG/PDF and the run stays **label pending**.

## Self-test and fixture health

**Station → Self-test…** runs a golden-board check on one fixture and records
the result. The station warns when no passing self-test was recorded within
the configured interval (default 24 h). **Station → Fixtures…** shows detected
and missing ST-LINK probes, insertion counters, maintenance status and
self-test age.

## Yield and traceability

* **Dashboard** — FPY, units per hour, average cycle, Pareto by step and test
  ID, and cycle time per fixture and profile.
* **Unit history** — search by PCB serial, chip UID, serial number, MAC or run
  ID. Selecting a run shows every run of that unit, including reworks.
* **File → Export CSV / JSON** and **Print certificate…** produce per-work-order
  or per-unit records.
* The header shows the MES queue length. Runs are queued locally when the MES
  is offline and retried with back-off; the local SQLite database remains the
  source of record.

## Users

Administrators manage users in **Tools → Users…** (roles: operator,
supervisor, engineer, administrator). PINs are stored as salted scrypt hashes.
Use **Tools → Change PIN…** to change your own PIN.

## Recovery and read-protected boards

Supervisors can use the recovery tools instead of ad-hoc OpenOCD commands:

```bash
hwval-factory tool recover --fixture F1 --profile profiles/sc767-1.1.0 catch
hwval-factory tool recover --fixture F1 --profile profiles/sc767-1.1.0 unstick
hwval-factory tool recover --fixture F1 --profile profiles/sc767-1.1.0 \
    unlock-rdp --reason "customer return" --confirm
hwval-factory tool recover --fixture F1 --profile profiles/sc767-1.1.0 connect
```

`unlock-rdp` lowers RDP 1 to 0 and mass-erases the chip. It is audited with
your user name and the reason code. RDP level 2 is never attempted.

## Signed profiles and the anti-rollback floor

`hwval-profile verify profiles/sc767-1.1.0` prints the signing key id, the
security counter, whether the BOOT image contains that public key, and
whether it is the published development key. A release profile must use the
product key. A rework is refused before erasing anything when the profile's
security counter is below the unit's confirmed floor; use a newer profile or
record a supervisor decision.
