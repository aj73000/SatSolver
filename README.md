A conflict-driven clause-learning solver for solving the satisfiability problem, used to test restart policies.

# Setup

This project consists of a Python interface and a C++ solver.

```text
project/
├── python/
│   ├── main.py
│   └── solver_interface.py
│
└── cpp/
    └── SatSolver/
        ├── solver.exe
        ├── main.cpp
        ├── solver.cpp
        └── solver.h
```

## 1. Download the solver

The Python code does not requires the "SatSolver" exe files to run but if you want to run tests then you will need one. This can be done by either compiling the 2 cpp and 1 .h file into an exe or by taking the .exe provided.

> **Important:** You only need to download the `SatSolver` folder containing the C++ solver and python GUI. You do **not** need to download the entire repository.

Place the downloaded `SatSolver` folder inside the `cpp` folder:

```text
project/
└── cpp/
    └── SatSolver/
```

## 2. Use the precompiled solver

The `SatSolver` folder may already contain a precompiled `solver.exe`.

If `solver.exe` is present, you can use it directly without compiling the C++ source code.

```text
cpp/
└── SatSolver/
    ├── solver.exe
    ├── main.cpp
    ├── solver.cpp
    └── solver.h
```

In this case, no C++ compiler is required.

## 3. Compile the solver yourself

Alternatively, you can compile the C++ source code yourself instead of using the provided `solver.exe`.

From the `SatSolver` directory, compile:

```bash
g++ main.cpp solver.cpp -o solver.exe
```

This will create:

```text
SatSolver/
├── solver.exe
├── main.cpp
├── solver.cpp
└── solver.h
```

You can then run the Python project using the newly compiled `solver.exe`.

## 4. Running the Python project

Once `solver.exe` is available in the expected `SatSolver` directory, run:

```bash
python python/main.py
```

The Python files communicate with the C++ solver through `solver.exe`.
