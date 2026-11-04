# Examples

* `station.simulator.yaml` — four simulated fixtures, demo profile, label
  fallback into `station-data/labels`, no printer or MES.
* `station.real.example.yaml` — production template with four ST-LINK probes,
  a Zebra printer and an optional MES endpoint.
* `profile-sc767-demo/` — a released demo profile with pinned test, boot and
  application images, an EOL test plan, calibration, identity, protection and a
  ZPL label template. The images are tiny deterministic placeholders for the
  simulator; replace them with real HWVAL/BOOT/application builds when moving to
  hardware.

The simulator station is used by:

```
hwval-factory-ui --station examples/station.simulator.yaml
hwval-factory --cli --station examples/station.simulator.yaml \
  --fixture F1 --pcb-serial PCB-0001
```
