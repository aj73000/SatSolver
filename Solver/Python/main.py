"""
main.py — PySide6 GUI for the CDCL solver.
"""

import statistics
import sys
from pathlib import Path
import csv

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from solver_interface import (
    POLICIES,
    POLICY_HELP,
    STAT_LABELS,
    SolverConfig,
    SolverResult,
    SolverRunner,
    SweepSpec,
    parse_dimacs,
    to_dimacs,
)

DEFAULT_EXE = ""


# ===========================================================================
# Solver options
# ===========================================================================

class SettingsPanel(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        # ---- restarts -----------------------------------------------------
        restart_box = QGroupBox("Restarts")
        restart_form = QFormLayout(restart_box)

        self.enable_restarts = QCheckBox("Enable restarts")
        self.enable_restarts.setChecked(True)
        self.enable_restarts.toggled.connect(self._sync_enabled)
        restart_form.addRow(self.enable_restarts)

        self.policy = QComboBox()
        for name in POLICIES:
            if name != "none":
                self.policy.addItem(name)
        self.policy.setCurrentText("luby")
        self.policy.currentTextChanged.connect(self._sync_enabled)
        restart_form.addRow("Policy", self.policy)

        self.policy_help = QLabel()
        self.policy_help.setWordWrap(True)
        self.policy_help.setStyleSheet("color: palette(mid);")
        restart_form.addRow(self.policy_help)

        self.restart_unit = QSpinBox()
        self.restart_unit.setRange(1, 1_000_000)
        self.restart_unit.setValue(100)
        self.restart_unit.setSuffix(" conflicts")
        restart_form.addRow("Base interval", self.restart_unit)

        self.geo_factor = QDoubleSpinBox()
        self.geo_factor.setRange(1.01, 10.0)
        self.geo_factor.setSingleStep(0.05)
        self.geo_factor.setValue(1.5)
        restart_form.addRow("Geometric factor", self.geo_factor)

        self.glucose_k = QDoubleSpinBox()
        self.glucose_k.setRange(0.1, 2.0)
        self.glucose_k.setSingleStep(0.05)
        self.glucose_k.setValue(0.8)
        restart_form.addRow("Glucose K", self.glucose_k)

        self.block_restarts = QCheckBox("Block restarts on long trails")
        restart_form.addRow(self.block_restarts)

        layout.addWidget(restart_box)

        # ---- heuristics ---------------------------------------------------
        heur_box = QGroupBox("Heuristics / ablations")
        heur_form = QFormLayout(heur_box)

        self.phase_saving = QCheckBox("Phase saving")
        self.phase_saving.setChecked(True)
        self.phase_saving.setToolTip(
            "Remember polarities across restarts. Turn this off and restarts "
            "lose most of their value — useful as a control."
        )
        heur_form.addRow(self.phase_saving)

        self.minimize = QCheckBox("Minimise learnt clauses")
        self.minimize.setChecked(True)
        heur_form.addRow(self.minimize)

        self.reduce_db = QCheckBox("Delete learnt clauses")
        self.reduce_db.setChecked(True)
        self.reduce_db.setToolTip(
            "Clause deletion interacts with restarts. Turn it off to remove "
            "that confound from a comparison."
        )
        heur_form.addRow(self.reduce_db)

        self.var_decay = QDoubleSpinBox()
        self.var_decay.setRange(0.5, 0.999)
        self.var_decay.setDecimals(3)
        self.var_decay.setSingleStep(0.01)
        self.var_decay.setValue(0.95)
        heur_form.addRow("VSIDS decay", self.var_decay)

        self.random_freq = QDoubleSpinBox()
        self.random_freq.setRange(0.0, 1.0)
        self.random_freq.setDecimals(3)
        self.random_freq.setSingleStep(0.01)
        self.random_freq.setValue(0.0)
        heur_form.addRow("Random decisions", self.random_freq)

        layout.addWidget(heur_box)

        # ---- limits -------------------------------------------------------
        limit_box = QGroupBox("Limits")
        limit_form = QFormLayout(limit_box)

        self.budget = QSpinBox()
        self.budget.setRange(0, 100_000_000)
        self.budget.setValue(0)
        self.budget.setSpecialValueText("unlimited")
        self.budget.setSuffix(" conflicts")
        limit_form.addRow("Conflict budget", self.budget)

        self.seed = QSpinBox()
        self.seed.setRange(1, 2_000_000_000)
        self.seed.setValue(91648253)
        limit_form.addRow("Solver seed", self.seed)

        layout.addWidget(limit_box)

        self.command_preview = QLabel()
        self.command_preview.setWordWrap(True)
        self.command_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.command_preview.setStyleSheet("color: palette(mid); font-family: monospace;")
        layout.addWidget(self.command_preview)

        layout.addStretch(1)

        for widget in (self.restart_unit, self.geo_factor, self.glucose_k,
                       self.var_decay, self.random_freq, self.budget, self.seed):
            widget.valueChanged.connect(self.changed)
        for widget in (self.enable_restarts, self.block_restarts, self.phase_saving,
                       self.minimize, self.reduce_db):
            widget.toggled.connect(self.changed)
        self.policy.currentTextChanged.connect(self.changed)

        self._sync_enabled()

    def _sync_enabled(self):
        on = self.enable_restarts.isChecked()
        policy = self.policy.currentText()

        self.policy.setEnabled(on)
        self.block_restarts.setEnabled(on)
        self.restart_unit.setEnabled(on and policy in ("fixed", "geometric", "luby"))
        self.geo_factor.setEnabled(on and policy == "geometric")
        self.glucose_k.setEnabled(on and policy == "glucose")

        self.policy_help.setText(
            POLICY_HELP["none"] if not on else POLICY_HELP.get(policy, "")
        )

    def config(self) -> SolverConfig:
        return SolverConfig(
            restart=self.policy.currentText() if self.enable_restarts.isChecked() else "none",
            restart_unit=self.restart_unit.value(),
            geo_factor=self.geo_factor.value(),
            glucose_k=self.glucose_k.value(),
            block_restarts=self.block_restarts.isChecked(),
            phase_saving=self.phase_saving.isChecked(),
            minimize=self.minimize.isChecked(),
            reduce_db=self.reduce_db.isChecked(),
            var_decay=self.var_decay.value(),
            random_freq=self.random_freq.value(),
            budget=self.budget.value(),
            seed=self.seed.value(),
        )


# ===========================================================================
# Result display
# ===========================================================================

class ResultPanel(QWidget):

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        self.status = QLabel("No run yet")
        font = QFont()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        self.status.setFont(font)
        layout.addWidget(self.status)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        self.table = QTableWidget(len(STAT_LABELS), 2)
        self.table.setHorizontalHeaderLabels(["Statistic", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, (_key, label) in enumerate(STAT_LABELS):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            self.table.setItem(row, 1, QTableWidgetItem(""))
        splitter.addWidget(self.table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Model / messages"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("monospace"))
        right_layout.addWidget(self.detail)
        splitter.addWidget(right)
        splitter.setSizes([320, 480])

    def set_running(self):
        self.status.setText("Solving…")
        self.status.setStyleSheet("color: palette(mid);")
        self.detail.setPlainText("")

    def set_error(self, message: str):
        self.status.setText("Error")
        self.status.setStyleSheet("color: #c0392b;")
        self.detail.setPlainText(message)

    def show_result(self, result: SolverResult):
        colours = {"SAT": "#27ae60", "UNSAT": "#c0392b", "UNKNOWN": "#d35400"}
        self.status.setText(result.status)
        self.status.setStyleSheet(f"color: {colours.get(result.status, '#c0392b')};")

        for row, (key, _label) in enumerate(STAT_LABELS):
            self.table.item(row, 1).setText(result.stats.get(key, ""))

        parts = []
        if result.status == "SAT":
            parts.append("Satisfying assignment:\n" + result.assignment_text())
        elif result.status == "UNKNOWN":
            parts.append("Conflict budget exhausted before an answer was reached.")

        if result.restart_log:
            log = result.restart_log
            shown = ", ".join(str(c) for c in log[:40])
            more = "" if len(log) <= 40 else f", … ({len(log)} restarts total)"
            parts.append(f"Restarts at conflict:\n{shown}{more}")

        if result.stderr.strip():
            parts.append("stderr:\n" + result.stderr.strip())

        self.detail.setPlainText("\n\n".join(parts))


# ===========================================================================
# Build tab
# ===========================================================================

class ClauseWidget(QGroupBox):
    def __init__(self):
        super().__init__("Current clause")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 8)

        self.hint = QLabel("Add a variable to start building a clause.")
        self.hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.hint)

        self.rows = []
        self.boxes = []

    def add_variable(self, number: int):
        self.hint.hide()

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)

        combo = QComboBox()
        combo.addItems(["Ignore", "True", "False"])

        row_layout.addWidget(QLabel(f"x{number}"))
        row_layout.addWidget(combo, 1)

        self.layout().addWidget(row)
        self.rows.append(row)
        self.boxes.append(combo)

    def remove_variable(self):
        if not self.rows:
            return
        row = self.rows.pop()
        self.boxes.pop()
        self.layout().removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        if not self.rows:
            self.hint.show()

    def get_clause(self):
        clause = []
        for i, combo in enumerate(self.boxes):
            if combo.currentText() == "True":
                clause.append(i + 1)
            elif combo.currentText() == "False":
                clause.append(-(i + 1))
        return clause

    def reset(self):
        for combo in self.boxes:
            combo.setCurrentIndex(0)


class BuildTab(QWidget):

    solve_requested = Signal(str)

    def __init__(self):
        super().__init__()

        self.variables = []
        self.clauses = []

        splitter = QSplitter(Qt.Horizontal)
        outer = QVBoxLayout(self)
        outer.addWidget(splitter)

        # ---- left: variables and the clause under construction -------------
        left = QWidget()
        left_layout = QVBoxLayout(left)

        left_layout.addWidget(QLabel("Variables"))
        self.variable_list = QListWidget()
        left_layout.addWidget(self.variable_list)

        buttons = QHBoxLayout()
        add_button = QPushButton("Add variable")
        remove_button = QPushButton("Remove variable")
        add_button.clicked.connect(self.add_variable)
        remove_button.clicked.connect(self.remove_variable)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        left_layout.addLayout(buttons)

        self.current_clause = ClauseWidget()
        left_layout.addWidget(self.current_clause, 1)

        create_button = QPushButton("Create clause")
        create_button.clicked.connect(self.create_clause)
        left_layout.addWidget(create_button)

        splitter.addWidget(left)

        # ---- right: the formula --------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)

        right_layout.addWidget(QLabel("Clauses"))
        self.clause_list = QListWidget()
        right_layout.addWidget(self.clause_list, 1)

        clause_buttons = QHBoxLayout()
        delete_button = QPushButton("Delete selected")
        clear_button = QPushButton("Clear all")
        delete_button.clicked.connect(self.delete_clause)
        clear_button.clicked.connect(self.clear_clauses)
        clause_buttons.addWidget(delete_button)
        clause_buttons.addWidget(clear_button)
        right_layout.addLayout(clause_buttons)

        right_layout.addWidget(QLabel("Current CNF"))
        self.cnf_display = QTextEdit()
        self.cnf_display.setReadOnly(True)
        self.cnf_display.setMaximumHeight(120)
        right_layout.addWidget(self.cnf_display)

        action_buttons = QHBoxLayout()
        self.solve_button = QPushButton("Solve")
        export_button = QPushButton("Export DIMACS…")
        self.solve_button.clicked.connect(self._on_solve)
        export_button.clicked.connect(self.export_dimacs)
        action_buttons.addWidget(self.solve_button, 1)
        action_buttons.addWidget(export_button)
        right_layout.addLayout(action_buttons)

        splitter.addWidget(right)
        splitter.setSizes([380, 520])

    # ---- variables --------------------------------------------------------

    def add_variable(self):
        number = len(self.variables) + 1
        self.variables.append(number)
        self.variable_list.addItem(f"x{number}")
        self.current_clause.add_variable(number)

    def remove_variable(self):
        if not self.variables:
            QMessageBox.warning(self, "No variables", "There are no variables to remove.")
            return

        highest = self.variables[-1]
        if any(abs(l) == highest for clause in self.clauses for l in clause):
            QMessageBox.warning(
                self, "Variable in use",
                f"x{highest} appears in an existing clause. Delete those clauses first.",
            )
            return

        self.variables.pop()
        self.variable_list.takeItem(self.variable_list.count() - 1)
        self.current_clause.remove_variable()

    # ---- clauses ----------------------------------------------------------

    def create_clause(self):
        clause = self.current_clause.get_clause()
        if not clause:
            QMessageBox.warning(self, "Empty clause",
                                "A clause must contain at least one literal.")
            return
        self.clauses.append(clause)
        self.current_clause.reset()
        self.refresh()

    def delete_clause(self):
        row = self.clause_list.currentRow()
        if row == -1:
            QMessageBox.warning(self, "No selection", "Select a clause to delete.")
            return
        self.clauses.pop(row)
        self.refresh()

    def clear_clauses(self):
        if not self.clauses:
            return
        self.clauses.clear()
        self.refresh()

    # ---- display ----------------------------------------------------------

    def refresh(self):
        self.clause_list.clear()
        texts = [self.clause_to_text(c) for c in self.clauses]
        for text in texts:
            self.clause_list.addItem(text)
        self.cnf_display.setText(" ∧ ".join(texts))

    @staticmethod
    def clause_to_text(clause):
        literals = [f"x{l}" if l > 0 else f"¬x{-l}" for l in clause]
        return "(" + " ∨ ".join(literals) + ")"

    def dimacs(self) -> str:
        return to_dimacs(self.clauses, len(self.variables))

    # ---- actions ----------------------------------------------------------

    def _on_solve(self):
        if not self.clauses:
            QMessageBox.warning(self, "No clauses", "Create at least one clause first.")
            return
        self.solve_requested.emit(self.dimacs())

    def export_dimacs(self):
        if not self.clauses:
            QMessageBox.warning(self, "No clauses", "Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export DIMACS", "formula.cnf",
                                              "DIMACS CNF (*.cnf);;All files (*)")
        if path:
            Path(path).write_text(self.dimacs())

    def show_model(self, model):
        """Annotate the variable list with the satisfying assignment."""
        values = {abs(l): l > 0 for l in model}
        for row in range(self.variable_list.count()):
            var = row + 1
            if var in values:
                self.variable_list.item(row).setText(
                    f"x{var} = {'True' if values[var] else 'False'}"
                )

    def clear_model(self):
        for row in range(self.variable_list.count()):
            self.variable_list.item(row).setText(f"x{row + 1}")


# ===========================================================================
# Load tab
# ===========================================================================

class LoadTab(QWidget):

    solve_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        open_button = QPushButton("Open .cnf…")
        open_button.clicked.connect(self.open_file)
        top.addWidget(open_button)

        self.path_label = QLabel("No file loaded — you can also drop a .cnf here, or paste below.")
        self.path_label.setStyleSheet("color: palette(mid);")
        top.addWidget(self.path_label, 1)
        layout.addLayout(top)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("monospace"))
        self.editor.setPlaceholderText("p cnf 3 2\n1 -2 0\n2 3 0")
        self.editor.textChanged.connect(self.update_summary)
        layout.addWidget(self.editor, 1)

        bottom = QHBoxLayout()
        self.summary = QLabel("")
        bottom.addWidget(self.summary, 1)

        self.solve_button = QPushButton("Solve")
        self.solve_button.clicked.connect(self._on_solve)
        bottom.addWidget(self.solve_button)
        layout.addLayout(bottom)

    # ---- file handling ----------------------------------------------------

    def load_path(self, path: str):
        try:
            text = Path(path).read_text(errors="replace")
        except OSError as exc:
            QMessageBox.critical(self, "Could not open file", str(exc))
            return
        self.editor.setPlainText(text)
        self.path_label.setText(path)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open DIMACS CNF", "", "DIMACS CNF (*.cnf *.dimacs);;All files (*)")
        if path:
            self.load_path(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.load_path(urls[0].toLocalFile())

    # ---- content ----------------------------------------------------------

    def update_summary(self):
        text = self.editor.toPlainText()
        if not text.strip():
            self.summary.setText("")
            return
        try:
            clauses, num_vars = parse_dimacs(text)
        except ValueError:
            self.summary.setText("Could not parse — expected DIMACS integers.")
            return
        ratio = len(clauses) / num_vars if num_vars else 0.0
        self.summary.setText(
            f"{num_vars} variables, {len(clauses)} clauses  (ratio {ratio:.2f})"
        )

    def _on_solve(self):
        text = self.editor.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "Nothing to solve", "Load or paste a CNF first.")
            return
        self.solve_requested.emit(text)


# ===========================================================================
# Experiments tab
# ===========================================================================

SWEEP_COLUMNS = ["seed", "policy", "result", "conflicts", "decisions",
                 "propagations", "restarts", "learnt", "avg_lbd", "seconds"]

class ExperimentTab(QWidget):
    def __init__(self, get_exe, get_config):
        super().__init__()
        self.get_exe = get_exe
        self.get_config = get_config

        self.runner = SolverRunner(self)
        self.runner.finished.connect(self.on_run_finished)
        self.runner.failed.connect(self.on_run_failed)

        self._jobs = []
        self._index = 0
        self._rows = []

        layout = QVBoxLayout(self)

        # ---- instance family -------------------------------------------------
        spec_box = QGroupBox("Instance family (uniform random k-SAT)")
        spec_form = QFormLayout(spec_box)

        self.variables = QSpinBox()
        self.variables.setRange(3, 100_000)
        self.variables.setValue(150)
        self.variables.valueChanged.connect(self.update_ratio)
        spec_form.addRow("Variables", self.variables)

        self.clause_count = QSpinBox()
        self.clause_count.setRange(1, 5_000_000)
        self.clause_count.setValue(639)
        self.clause_count.valueChanged.connect(self.update_ratio)
        spec_form.addRow("Clauses", self.clause_count)

        self.k = QSpinBox()
        self.k.setRange(2, 10)
        self.k.setValue(3)
        spec_form.addRow("Literals per clause", self.k)

        self.ratio_label = QLabel()
        self.ratio_label.setStyleSheet("color: palette(mid);")
        spec_form.addRow(self.ratio_label)

        self.first_seed = QSpinBox()
        self.first_seed.setRange(0, 2_000_000_000)
        self.first_seed.setValue(1000)
        spec_form.addRow("First seed", self.first_seed)

        self.instances = QSpinBox()
        self.instances.setRange(1, 5000)
        self.instances.setValue(20)
        spec_form.addRow("Instances", self.instances)

        layout.addWidget(spec_box)

        # ---- policies --------------------------------------------------------
        policy_box = QGroupBox("Policies to compare")
        policy_layout = QHBoxLayout(policy_box)
        self.policy_boxes = {}
        for name in POLICIES:
            box = QCheckBox(name)
            box.setChecked(True)
            box.setToolTip(POLICY_HELP[name])
            policy_layout.addWidget(box)
            self.policy_boxes[name] = box
        layout.addWidget(policy_box)

        note = QLabel(
            "Everything else (base interval, ablations, budget) comes from the "
            "Solver options panel, so the policies differ only in the restart rule."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        # ---- controls --------------------------------------------------------
        controls = QHBoxLayout()
        self.run_button = QPushButton("Run sweep")
        self.run_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        self.save_button = QPushButton("Save CSV…")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_csv)
        controls.addWidget(self.run_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.save_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(SWEEP_COLUMNS))
        self.table.setHorizontalHeaderLabels(SWEEP_COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self.table)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setFont(QFont("monospace"))
        self.summary.setMaximumHeight(160)
        splitter.addWidget(self.summary)
        layout.addWidget(splitter, 1)

        self.update_ratio()

    def update_ratio(self):
        ratio = self.clause_count.value() / max(1, self.variables.value())
        note = "  (3-SAT phase transition is ~4.26)" if self.k.value() == 3 else ""
        self.ratio_label.setText(f"clause/variable ratio {ratio:.2f}{note}")

    # ---- running ----------------------------------------------------------

    def start(self):
        exe = self.get_exe()
        if not exe:
            QMessageBox.warning(self, "No solver", "Set the solver executable first.")
            return

        policies = [name for name, box in self.policy_boxes.items() if box.isChecked()]
        if not policies:
            QMessageBox.warning(self, "No policies", "Select at least one policy.")
            return

        spec = SweepSpec(
            variables=self.variables.value(),
            clauses=self.clause_count.value(),
            first_seed=self.first_seed.value(),
            instances=self.instances.value(),
            k=self.k.value(),
            policies=policies,
        )

        self._jobs = list(spec.jobs(self.get_config()))
        self._index = 0
        self._rows = []
        self.table.setRowCount(0)
        self.summary.setPlainText("")
        self.progress.setRange(0, len(self._jobs))
        self.progress.setValue(0)

        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.save_button.setEnabled(False)

        self.next_job()

    def stop(self):
        self._jobs = []
        self.runner.cancel()
        self.finish()

    def next_job(self):
        if self._index >= len(self._jobs):
            self.finish()
            return

        _seed, _policy, config, gen = self._jobs[self._index]

        # Run on job then move onto the next
        self.runner.run(self.get_exe(), config, extra_args=[gen])

    def on_run_finished(self, result: SolverResult):
        seed, policy, _config, _gen = self._jobs[self._index]

        row = {"seed": seed, "policy": policy, "result": result.status}
        for key in SWEEP_COLUMNS[3:]:
            row[key] = result.stats.get(key, "")
        self._rows.append(row)

        r = self.table.rowCount()
        self.table.insertRow(r)
        for col, key in enumerate(SWEEP_COLUMNS):
            self.table.setItem(r, col, QTableWidgetItem(str(row[key])))
        self.table.scrollToBottom()

        self._index += 1
        self.progress.setValue(self._index)
        self.next_job()

    def on_run_failed(self, message: str):
        if not self._jobs:
            return                       # cancelled deliberately
        QMessageBox.critical(self, "Sweep failed", message)
        self.stop()

    def finish(self):
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.save_button.setEnabled(bool(self._rows))
        self.progress.setValue(self.progress.maximum())
        self.summarise()

    def summarise(self):
        if not self._rows:
            return

        by_policy = {}
        for row in self._rows:
            by_policy.setdefault(row["policy"], []).append(row)

        lines = [f"{'policy':>10} {'runs':>5} {'median confl':>13} "
                 f"{'mean confl':>11} {'total s':>9} {'sat/unsat/unk':>14}"]
        for policy in POLICIES:
            rows = by_policy.get(policy)
            if not rows:
                continue
            conflicts = [float(r["conflicts"]) for r in rows if r["conflicts"]]
            seconds = [float(r["seconds"]) for r in rows if r["seconds"]]
            sat = sum(r["result"] == "SAT" for r in rows)
            unsat = sum(r["result"] == "UNSAT" for r in rows)
            unknown = len(rows) - sat - unsat
            lines.append(
                f"{policy:>10} {len(rows):>5} "
                f"{statistics.median(conflicts) if conflicts else 0:>13,.0f} "
                f"{statistics.fmean(conflicts) if conflicts else 0:>11,.0f} "
                f"{sum(seconds):>9.3f} {f'{sat}/{unsat}/{unknown}':>14}"
            )

        lines.append("")
        lines.append("Random k-SAT is high variance: treat differences under ~2x on "
                     "a handful of instances as noise, and split SAT from UNSAT.")
        self.summary.setPlainText("\n".join(lines))

    def save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save results", "sweep.csv",
                                              "CSV (*.csv);;All files (*)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(",".join(SWEEP_COLUMNS) + "\n")
            for row in self._rows:
                handle.write(",".join(str(row[key]) for key in SWEEP_COLUMNS) + "\n")


# ===========================================================================
# Main window
# ===========================================================================

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SAT Solver")
        self.resize(1920, 1080)
        self.last_result = None

        self.settings = QSettings("SatSolver", "SatSolverGUI")

        self.runner = SolverRunner(self)
        self.runner.finished.connect(self.on_finished)
        self.runner.failed.connect(self.on_failed)
        self.runner.started.connect(lambda: self.set_busy(True))

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ---- solver executable ------------------------------------------
        exe_row = QHBoxLayout()
        exe_row.addWidget(QLabel("Solver:"))
        self.exe_edit = QLineEdit(self.settings.value("exe", DEFAULT_EXE))
        self.exe_edit.textChanged.connect(self.on_exe_changed)
        exe_row.addWidget(self.exe_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_exe)
        exe_row.addWidget(browse)
        layout.addLayout(exe_row)

        # ---- tabs ---------------------------------------------------------
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.build_tab = BuildTab()
        self.build_tab.solve_requested.connect(self.solve_text)
        self.tabs.addTab(self.build_tab, "Build")

        self.load_tab = LoadTab()
        self.load_tab.solve_requested.connect(self.solve_text)
        self.tabs.addTab(self.load_tab, "Load")

        self.settings_panel = SettingsPanel()
        #self.settings_panel.changed.connect(self._update_preview)

        self.experiment_tab = ExperimentTab(
            get_exe=lambda: self.exe_edit.text().strip(),
            get_config=self.settings_panel.config,
        )
        self.tabs.addTab(self.experiment_tab, "Experiments")

        # ---- docks --------------------------------------------------------
        options_dock = QDockWidget("Solver options", self)
        options_dock.setWidget(self.settings_panel)
        options_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, options_dock)

        self.result_panel = ResultPanel()
        result_dock = QDockWidget("Result", self)
        result_dock.setWidget(self.result_panel)
        result_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.BottomDockWidgetArea, result_dock)

        # ---- cancel action ---------------------------------------------------
        self.cancel_action = QAction("Cancel run", self)
        self.cancel_action.setShortcut("Esc")
        self.cancel_action.setEnabled(False)
        self.cancel_action.triggered.connect(self.runner.cancel)
        self.addAction(self.cancel_action)

        self.statusBar().showMessage("Ready")

        #---- save last result action --------------------------------------------
        save_result_button = QPushButton("Save Last Result…")
        save_result_button.setEnabled(False)
        save_result_button.clicked.connect(self.save_last_result)

        exe_row.addWidget(save_result_button)

        self.save_result_button = save_result_button

    # ---- executable ------------------------------------------------------

    def browse_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select solver executable")
        if path:
            self.exe_edit.setText(path)

    def on_exe_changed(self, text):
        self.settings.setValue("exe", text)


    # ---- solving ---------------------------------------------------------

    def solve_text(self, cnf_text: str):
        exe = self.exe_edit.text().strip()
        if not exe or not Path(exe).exists():
            QMessageBox.critical(
                self, "Solver not found",
                f"No executable at:\n{exe}\n\nUse Browse… to point at your build.")
            return

        self.build_tab.clear_model()
        self.result_panel.set_running()
        self.runner.run(exe, self.settings_panel.config(), cnf_text=cnf_text)

    def set_busy(self, busy: bool):
        self.build_tab.solve_button.setEnabled(not busy)
        self.load_tab.solve_button.setEnabled(not busy)
        self.cancel_action.setEnabled(busy)
        self.statusBar().showMessage("Solving… (Esc to cancel)" if busy else "Ready")

    def on_finished(self, result: SolverResult):
        self.last_result = result
        self.save_result_button.setEnabled(True)
        self.set_busy(False)
        self.result_panel.show_result(result)
        if result.status == "SAT" and result.model:
            self.build_tab.show_model(result.model)
        self.statusBar().showMessage(
            f"{result.status} — {result.stats.get('conflicts', '?')} conflicts, "
            f"{result.stats.get('restarts', '?')} restarts")

    def on_failed(self, message: str):
        self.set_busy(False)
        self.result_panel.set_error(message)

    def save_last_result(self):
        if self.last_result is None:
            QMessageBox.warning(self, "No result", "There is no solver result to save.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save last result",
            "Results Data/solver_result.csv",
            "CSV (*.csv);;All files (*)",
        )

        if not path:
            return

        config = self.settings_panel.config()
        result = self.last_result

        row = {
            "result": result.status,
            **result.stats,

            "restart_policy": config.restart,
            "restart_unit": config.restart_unit,
            "geo_factor": config.geo_factor,
            "glucose_k": config.glucose_k,
            "block_restarts": config.block_restarts,
            "phase_saving": config.phase_saving,
            "minimize": config.minimize,
            "reduce_db": config.reduce_db,
            "var_decay": config.var_decay,
            "random_freq": config.random_freq,
            "budget": config.budget,
            "seed": config.seed,
        }

        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=row.keys())
                writer.writeheader()
                writer.writerow(row)

        except OSError as exc:
            QMessageBox.critical(
                self,
                "Could not save result",
                str(exc),
            )
            return

        QMessageBox.information(
            self,
            "Result saved",
            f"Saved result to:\n{path}",
        )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
