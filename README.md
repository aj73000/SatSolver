# CDCL SAT Solver

A **conflict-driven clause-learning (CDCL) SAT solver** for solving the satisfiability problem, used to test **restart policies**.

## Features

-  **Two-watched-literal** unit propagation
-  **First-UIP** conflict analysis
-  **VSIDS** variable ordering using a binary max-heap
-  **Phase saving**
-  **LBD-based** learnt clause deletion

## Restart Policies
-  None
-  Fixed
-  Geometric
-  Luby
-  A dynamic LBD moving-average policy in the style of Glucose

# Setup

This project consists of a Python interface and a C++ solver.

```text
Solver/
├── python/
│   ├── main.py
│   └── solver_interface.py
│
└── cpp/
    ├── main.cpp
    ├── solver.cpp
    └── solver.h
```

## 1. Download the solver folder

The Python code does not requires the "SatSolver" exe file to run but if you want to run tests then you will need one.

## 2. Compile the solver

Compile from the C++ source code

From the `Cpp` directory, compile:

```bash
g++ main.cpp solver.cpp -o solver.exe
```

This will create:

```text
Cpp/
├── solver.exe
├── main.cpp
├── solver.cpp
└── solver.h
```

You can then run the Python main using the newly compiled `solver.exe`.

## 4. Running the Python project

Once `solver.exe` is available in the expected `SatSolver` directory, run:

```bash
python python/main.py
```

The Python files communicate with the C++ solver through `solver.exe`.
