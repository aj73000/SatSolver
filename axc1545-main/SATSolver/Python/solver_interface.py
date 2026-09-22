"""
solver_interface.py — Interface between the PySide6 GUI and the SAT solver.
"""

from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence

from PySide6.QtCore import QObject, QProcess, Signal

# Standard SAT exit codes.
EXIT_SAT = 10
EXIT_UNSAT = 20
EXIT_UNKNOWN = 0

POLICIES = ["none", "fixed", "geometric", "luby", "glucose"]

POLICY_HELP = {
    "none": "Never restarts. Use as the baseline for every comparison.",
    "fixed": "Restart every N conflicts.",
    "geometric": "Interval grows by a constant factor after each restart.",
    "luby": "N x luby(2, i) — the MiniSat default; provably good worst case.",
    "glucose": "Dynamic: restart when recent LBD is worse than the running average.",
}


# ---------------------------------------------------------------------------
# CNF helpers
# ---------------------------------------------------------------------------

def to_dimacs(clauses: Sequence[Sequence[int]], num_vars: Optional[int] = None) -> str:
    if num_vars is None:
        num_vars = max((abs(l) for c in clauses for l in c), default=0)
    lines = [f"p cnf {num_vars} {len(clauses)}"]
    lines += [" ".join(str(l) for l in clause) + " 0" for clause in clauses]
    return "\n".join(lines) + "\n"


def parse_dimacs(text: str) -> tuple[List[List[int]], int]:
    clauses: List[List[int]] = []
    current: List[int] = []
    declared = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] == "c":
            continue
        if line[0] == "%":
            break
        if line[0] == "p":
            parts = line.split()
            if len(parts) >= 4 and parts[1] == "cnf":
                declared = int(parts[2])
            continue
        for token in line.split():
            value = int(token)
            if value == 0:
                if current:
                    clauses.append(current)
                current = []
            else:
                current.append(value)

    if current:
        clauses.append(current)

    largest = max((abs(l) for c in clauses for l in c), default=0)
    return clauses, max(declared, largest)


# ---------------------------------------------------------------------------
# Solver configuration
# ---------------------------------------------------------------------------

@dataclass
class SolverConfig:
    # restart defaults
    restart: str = "luby"
    restart_unit: int = 100
    geo_factor: float = 1.5
    glucose_k: float = 0.8
    block_restarts: bool = False

    phase_saving: bool = True
    minimize: bool = True
    reduce_db: bool = True
    var_decay: float = 0.95
    random_freq: float = 0.0

    # limits
    budget: int = 0
    seed: int = 91648253

    def to_args(self) -> List[str]:
        args = [
            f"--restart={self.restart}",
            f"--restart-unit={self.restart_unit}",
            f"--geo-factor={self.geo_factor}",
            f"--glucose-k={self.glucose_k}",
            f"--var-decay={self.var_decay}",
            f"--random-freq={self.random_freq}",
            f"--seed={self.seed}",
        ]
        if self.budget > 0:
            args.append(f"--budget={self.budget}")
        if self.block_restarts:
            args.append("--block-restarts")
        if not self.phase_saving:
            args.append("--no-phase-saving")
        if not self.minimize:
            args.append("--no-minimize")
        if not self.reduce_db:
            args.append("--no-reduce")
        return args


# -----------------------------------------------------------------------

STAT_LABELS = [
    ("conflicts", "Conflicts"),
    ("decisions", "Decisions"),
    ("propagations", "Propagations"),
    ("restarts", "Restarts"),
    ("blocked", "Blocked restarts"),
    ("learnt", "Learnt clauses"),
    ("deleted", "Deleted clauses"),
    ("avg_lbd", "Average LBD"),
    ("avg_len", "Average learnt length"),
    ("max_level", "Max decision level"),
    ("seconds", "CPU time (s)"),
]


@dataclass
class SolverResult:
    status: str = "ERROR" # SAT / UNSAT / UNKNOWN / ERROR
    stats: dict = field(default_factory=dict) # csv column -> string
    model: List[int] = field(default_factory=list)
    restart_log: List[int] = field(default_factory=list)

    # Output of solver
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1

    @property
    def ok(self) -> bool:
        return self.status in ("SAT", "UNSAT", "UNKNOWN")

    def assignment_text(self) -> str:
        if not self.model:
            return ""
        return "\n".join(f"x{abs(l)} = {'True' if l > 0 else 'False'}" for l in self.model)


def parse_output(stdout: str, stderr: str, exit_code: int) -> SolverResult:
    result = SolverResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    header: Optional[List[str]] = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("policy,"):
            header = line.split(",")
            continue

        if header and not result.stats and "," in line and not line.startswith("v "):
            values = line.split(",")
            if len(values) == len(header):
                result.stats = dict(zip(header, values))
                result.status = result.stats.get("result", "ERROR")
                continue

        # human readable mode
        if line.startswith("s "):
            result.status = line[2:].strip()
        elif line.startswith("v "):
            result.model = [int(t) for t in line[2:].split() if t != "0"]
        elif line.startswith("c restart log:"):
            result.restart_log = [int(t) for t in line.split(":", 1)[1].split()]

    if result.status == "ERROR":
        result.status = {
            EXIT_SAT: "SAT",
            EXIT_UNSAT: "UNSAT",
            EXIT_UNKNOWN: "UNKNOWN",
        }.get(exit_code, "ERROR")

    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class SolverRunner(QObject):
    # SolverResult
    finished = Signal(object)
    failed = Signal(str)
    started = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self._process is not None

    def run(self, exe: str, config: SolverConfig,
            cnf_path: Optional[str] = None,
            cnf_text: Optional[str] = None,
            extra_args: Sequence[str] = ()) -> None:
        if self.running:
            self.failed.emit("Can't solve! Solver already running!")
            return

        args: List[str] = []
        if cnf_path:
            args.append(cnf_path)
        args += list(extra_args)
        args += config.to_args()
        args += ["--csv", "--model", "--restart-log"]

        self._cancelled = False
        proc = QProcess(self)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._process = proc

        proc.start(exe, args)
        if not proc.waitForStarted(3000):
            self._process = None
            self.failed.emit(f"Could not start solver:\n{exe}")
            return

        self.started.emit()

        if cnf_text is not None:
            proc.write(cnf_text.encode())
        proc.closeWriteChannel()

    def cancel(self) -> None:
        if self._process:
            self._cancelled = True
            self._process.kill()

    def _on_error(self, _error) -> None:
        if self._process is None:
            return
        message = self._process.errorString()
        self._process = None
        if not self._cancelled:
            self.failed.emit(message)

    def _on_finished(self, exit_code: int, _status) -> None:
        proc = self._process
        if proc is None:
            return
        self._process = None

        if self._cancelled:
            self.failed.emit("Run cancelled.")
            return

        stdout = bytes(proc.readAllStandardOutput()).decode(errors="replace")
        stderr = bytes(proc.readAllStandardError()).decode(errors="replace")
        self.finished.emit(parse_output(stdout, stderr, exit_code))


# ---------------------------------------------------------------------------
# Experiment sweep
# ---------------------------------------------------------------------------

@dataclass
class SweepSpec:
    variables: int = 200
    clauses: int = 852
    first_seed: int = 1000
    instances: int = 20
    k: int = 3
    policies: List[str] = field(default_factory=lambda: list(POLICIES))

    def jobs(self, config: SolverConfig):
        for i in range(self.instances):
            seed = self.first_seed + i
            gen = f"--gen={self.variables},{self.clauses},{seed},{self.k}"
            for policy in self.policies:
                yield seed, policy, replace(config, restart=policy), gen
