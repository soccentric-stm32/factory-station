"""Home page: the session bar, the eight bench actions and the activity log."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hostcore import soc_widgets
from hwval_factory.bench import actions
from hwval_factory.bench.actions import ActionResult, TaskRunner
from hwval_factory.core import generators
from hwval_factory.ui.home.action_card import ActionCard
from hwval_factory.ui.home.run_hero import RunHero

# step -> (glyph, caption); the caption is the card's resting hint.
_STEPS: dict[int, tuple[str, str]] = {
    1: ("probe", "List the ST-LINK probes and the serial ports"),
    2: ("boot", "Write the bootloader to the board"),
    3: ("factory", "Write the factory image to slot A"),
    4: ("info", "Read the identity and the firmware versions"),
    5: ("list", "Generate, write and read back .sys"),
    6: ("sliders", "Generate, write and read back .cal"),
    7: ("pulse", "Generate, write and read back .diag"),
    8: ("check", "Run the read-only checks and the self-test"),
}


class HomePage(QWidget):
    """The one main page from requirement R5."""

    progress_signal = Signal(int, int, str)
    done_signal = Signal(int, object)

    def __init__(self, state: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.runner = TaskRunner()
        self.cards: dict[int, ActionCard] = {}
        self._syncing = False
        self._current = 0
        self._sequence: list[int] = []
        self._sequencing = False
        self._build()
        self.progress_signal.connect(self._on_progress)
        self.done_signal.connect(self._on_done)
        self.refresh()
        self.state.changed.connect(self.on_state_changed)

    # -- construction --------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root.addWidget(scroll)
        host = QWidget()
        scroll.setWidget(host)

        outer = QVBoxLayout(host)
        outer.setContentsMargins(24, 14, 24, 18)
        outer.setSpacing(16)

        self.hero = RunHero()
        self.sequence_button = self.hero.button
        self.sequence_button.setToolTip("Run the eight steps in order and stop on the first failure")
        self.sequence_button.clicked.connect(self._run_sequence)
        outer.addWidget(self.hero)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._procedure(), 1, Qt.AlignmentFlag.AlignTop)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(16)
        side.addWidget(self._session_card())
        self.log_panel = soc_widgets.OutputPanel("Activity log", "Action output appears here")
        self.log_view = self.log_panel.console
        self.log_view.setMinimumHeight(120)
        side.addWidget(self.log_panel, 1)
        side_host = QWidget()
        side_host.setLayout(side)
        side_host.setFixedWidth(340)
        body.addWidget(side_host)
        outer.addLayout(body, 1)
        self._update_hero()

    def _procedure(self) -> QWidget:
        self.done_label = soc_widgets.label("0 of 8 done", "Hint")
        card = soc_widgets.Card("Procedure", trailing=self.done_label)
        card._box.addWidget(soc_widgets.hairline())
        for number, title, _fn in actions.ACTIONS:
            glyph, hint = _STEPS[number]
            row = ActionCard(number, title, hint, glyph)
            row.run_requested.connect(self._start)
            self.cards[number] = row
            card.add_widget(row)
        return card

    def _session_card(self) -> QWidget:
        self.probe_combo = QComboBox()
        self.port_combo = QComboBox()
        self.unit_combo = QComboBox()
        self.unit_combo.setEditable(True)
        self.key_edit = QLineEdit()
        self.key_edit.editingFinished.connect(self._apply_key)

        browse = soc_widgets.icon_button("folder", "Choose the signing key")
        browse.clicked.connect(self._choose_key)
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(6)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(browse)

        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        column.addLayout(soc_widgets.field(soc_widgets.label("Unit", "RowLabel"), self.unit_combo))
        column.addLayout(soc_widgets.field(soc_widgets.label("Probe", "RowLabel"), self.probe_combo))
        column.addLayout(soc_widgets.field(soc_widgets.label("Serial port", "RowLabel"), self.port_combo))
        key_field = QVBoxLayout()
        key_field.setSpacing(4)
        key_field.addWidget(soc_widgets.label("Signing key", "RowLabel"))
        key_field.addLayout(key_row)
        column.addLayout(key_field)

        card = soc_widgets.Card("Session", trailing=self._session_tools())
        card.add_widget(body, padded=True)
        self.probe_combo.currentIndexChanged.connect(self._apply_probe)
        self.port_combo.currentIndexChanged.connect(self._apply_port)
        self.unit_combo.currentTextChanged.connect(self._apply_unit)
        return card

    def _session_tools(self) -> QWidget:
        tools = QWidget()
        row = QHBoxLayout(tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.simulate_switch = soc_widgets.Switch()
        self.simulate_switch.toggled.connect(self._set_simulate)
        row.addWidget(soc_widgets.label("Simulate", "RowLabel"))
        row.addWidget(self.simulate_switch)
        refresh = soc_widgets.icon_button("refresh", "List the probes and serial ports again")
        refresh.clicked.connect(self._refresh_detection)
        row.addWidget(refresh)
        return tools

    def _update_hero(self) -> None:
        """Summarise the whole run: one state word, one detail line, one counter."""
        states = {n: card.state for n, card in self.cards.items()}
        done = sum(1 for value in states.values() if value == "ok")
        self.done_label.setText(f"{done} of {len(states)} done")
        unit = self.state.unit_id or "no unit selected"
        running = next((n for n, value in states.items() if value == "running"), 0)
        failed = next((n for n, value in states.items() if value == "failed"), 0)
        if running:
            self.hero.show_state(
                f"Step {running} of {len(states)}",
                f"{unit} · {card_title(running)}",
                "running",
                done,
            )
        elif failed:
            self.hero.show_state(
                f"Step {failed} failed",
                f"{unit} · {self.cards[failed].detail.fullText()}",
                "x",
                done,
            )
        elif done == len(states):
            self.hero.show_state("All steps passed", f"{unit} · ready to ship", "check", done)
        else:
            self.hero.show_state("Ready", f"{unit} · {done} of {len(states)} steps done", "zap", done)

    # -- state ---------------------------------------------------------
    def refresh(self) -> None:
        self._syncing = True
        self.simulate_switch.setChecked(bool(self.state.simulate))
        self.key_edit.setText(str(self.state.key_path))
        self._fill_combos()
        self._fill_units()
        self._syncing = False
        self._refresh_staleness()

    def on_state_changed(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        self._fill_combos()
        self.simulate_switch.setChecked(bool(self.state.simulate))
        self._syncing = False
        self._refresh_staleness()

    def _fill_combos(self) -> None:
        detected = self.state.detected
        self.probe_combo.clear()
        for probe in detected.probes:
            self.probe_combo.addItem(probe.label, probe.serial)
        index = self.probe_combo.findData(self.state.probe_serial)
        self.probe_combo.setCurrentIndex(index if index >= 0 else (0 if self.probe_combo.count() == 1 else -1))
        self.port_combo.clear()
        for port in detected.ports:
            self.port_combo.addItem(port.label, port.device)
        index = self.port_combo.findData(self.state.port)
        self.port_combo.setCurrentIndex(index if index >= 0 else (0 if self.port_combo.count() == 1 else -1))

    def _fill_units(self) -> None:
        current = self.state.unit_id
        self.unit_combo.blockSignals(True)
        self.unit_combo.clear()
        path = self.state.workspace.csv_path_path("sys")
        if not path.is_file():
            fallback = Path(self.state.csv_path)
            path = fallback if fallback.is_file() else path
        try:
            data = generators.GENERATORS["sys"].load(path)
            self.unit_combo.addItems(data.ids)
        except Exception:
            pass
        if current:
            if self.unit_combo.findText(current) < 0:
                self.unit_combo.addItem(current)
            self.unit_combo.setCurrentText(current)
        elif self.unit_combo.count():
            self.unit_combo.setCurrentIndex(0)
            self.state.set_unit(self.unit_combo.currentText())
        self.unit_combo.blockSignals(False)

    def _refresh_staleness(self) -> None:
        """Mark SYS/CAL/DIAG cards when the generated file is missing or older."""
        for number, kind in ((5, "sys"), (6, "cal"), (7, "diag")):
            card = self.cards[number]
            if self.runner.running:
                continue
            if not self.state.unit_id:
                card.mark_blocked("select a unit")
                continue
            csv = self.state.workspace.csv_path_path(kind)
            out = self.state.unit_file(kind)
            if not csv.is_file():
                card.mark_blocked(f"no {kind}.csv")
            elif not out.is_file():
                card.mark_stale(f"stale: generate {kind.upper()}")
            elif csv.stat().st_mtime > out.stat().st_mtime:
                card.mark_stale(f"stale: {kind}.csv changed")
            else:
                card.reset_button()
                card.detail.setText(f"ready · {out.name}")

    def _apply_probe(self) -> None:
        if not self._syncing:
            self.state.select(probe_serial=self.probe_combo.currentData() or "")

    def _apply_port(self) -> None:
        if not self._syncing:
            self.state.select(port=self.port_combo.currentData() or "")

    def _apply_unit(self, text: str) -> None:
        if not self._syncing:
            self.state.set_unit(text)

    def _apply_key(self) -> None:
        self.state.remember("key_path", self.key_edit.text().strip())

    def _choose_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Signing key", self.key_edit.text(), "PEM (*.pem);;All files (*)")
        if path:
            self.key_edit.setText(path)
            self._apply_key()

    def _set_simulate(self, value: bool) -> None:
        if not self._syncing:
            self.state.set_simulate(value)
            self.refresh()

    def _refresh_detection(self) -> None:
        self.state.refresh_detection()

    # -- actions -------------------------------------------------------
    def _start(self, number: int) -> None:
        if self.runner.running:
            self.log("another action is running")
            return
        self._current = number
        card = self.cards[number]
        card.set_running()
        self.hero.track.set_state(number, "running", 0.0)
        self._update_hero()
        for other in self.cards.values():
            other.button.setEnabled(False)
        self.sequence_button.setEnabled(False)
        fn = dict((n, f) for n, _t, f in actions.ACTIONS)[number]
        title = card_title(number)
        self.log(f"{number}. {title}")
        self.log_panel.set_status(f"{number}. {title}", "accent")

        def progress(percent: int, text: str) -> None:
            self.progress_signal.emit(number, percent, text)

        def done(result: ActionResult) -> None:
            self.done_signal.emit(number, result)

        if not self.runner.start(fn, self.state, progress, done):
            self.log("another action is running")

    def _on_progress(self, number: int, percent: int, text: str) -> None:
        self.cards[number].set_progress(percent, text)
        self.hero.track.set_state(number, "running", percent / 100)
        if text:
            self.log_panel.set_status(text, "accent")
            self.log(f"  {text}")

    def _on_done(self, number: int, result: object) -> None:
        assert isinstance(result, ActionResult)
        card = self.cards[number]
        card.set_result(result)
        self.log(("done · " if result.ok else "failed · ") + result.summary)
        if result.details:
            for line in result.details.splitlines():
                self.log(f"    {line}")
        self.hero.track.set_state(number, "ok" if result.ok else "failed")
        self.log_panel.set_status(result.summary, "success" if result.ok else "danger")
        self.state.last_results[number] = result
        for other in self.cards.values():
            other.button.setEnabled(True)
        self.sequence_button.setEnabled(True)
        self._refresh_staleness()
        self._update_hero()
        if self._sequencing:
            self._next_in_sequence(result)

    def _run_sequence(self) -> None:
        if self.runner.running:
            return
        self._sequence = list(range(1, 9))
        self._sequencing = True
        for card in self.cards.values():
            card.reset()
        self.hero.track.clear()
        self.sequence_button.setText("Running")
        self.log("sequence · steps 1 → 8")
        self._next_in_sequence(None)

    def _next_in_sequence(self, previous: ActionResult | None) -> None:
        if previous is not None and not previous.ok:
            self.log("sequence stopped on the first failure")
            self.log_panel.set_status("Sequence stopped on the first failure", "danger")
            self._sequence = []
            self._sequencing = False
            self.sequence_button.setText("Start run")
            return
        if not self._sequence:
            self._sequencing = False
            if previous is not None:
                self.log_panel.set_status("Sequence finished", "success")
            self.sequence_button.setText("Start run")
            return
        number = self._sequence.pop(0)
        self._start(number)

    # -- log -----------------------------------------------------------
    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in str(text).splitlines():
            self.log_panel.append(f"[{stamp}] {line}")


def card_title(number: int) -> str:
    for value, title, _fn in actions.ACTIONS:
        if value == number:
            return title
    return str(number)


__all__ = ["HomePage"]
