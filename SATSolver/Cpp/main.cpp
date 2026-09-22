#include "SatSolver.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

//-----------------------------------------------------------------
// DIMACS
//-----------------------------------------------------------------
static bool readDimacs(std::istream& in, SatSolver::CNF& cnf, int& numVars)
{
    numVars = 0;
    cnf.clear();

    std::string line;
    SatSolver::Clause current;

    while (std::getline(in, line))
    {
        if (line.empty() || line[0] == 'c')
            continue;
        if (line[0] == '%')
            break;

        if (line[0] == 'p')
        {
            std::istringstream ss(line);
            std::string p, cnfTok;
            int nc = 0;
            ss >> p >> cnfTok >> numVars >> nc;
            cnf.reserve(nc);
            continue;
        }

        std::istringstream ss(line);
        int lit;
        while (ss >> lit)
        {
            if (lit == 0)
            {
                cnf.push_back(current);
                current.clear();
            }
            else
            {
                current.push_back(lit);
            }
        }
    }

    if (!current.empty()) cnf.push_back(current);

    for (const auto& c : cnf)
        for (const int l : c)
            if (std::abs(l) > numVars)
                numVars = std::abs(l);

    return true;
}

//-----------------------------------------------------------------
// Uniform random k-SAT
//-----------------------------------------------------------------
static SatSolver::CNF generateRandomKSat(int vars, int clauses, int k, uint64_t seed)
{
    std::mt19937_64 rng(seed);
    std::uniform_int_distribution<int> pickVar(1, vars);
    std::uniform_int_distribution<int> pickSign(0, 1);

    SatSolver::CNF cnf;
    cnf.reserve(clauses);

    while (static_cast<int>(cnf.size()) < clauses)
    {
        SatSolver::Clause c;
        while (static_cast<int>(c.size()) < k)
        {
            const int v = pickVar(rng);
            bool clash = false;
            for (const int l : c) if (std::abs(l) == v) { clash = true; break; }
            if (clash) continue;
            c.push_back(pickSign(rng) ? v : -v);
        }
        cnf.push_back(std::move(c));
    }
    return cnf;
}

//-----------------------------------------------------------------
// Reporting
//-----------------------------------------------------------------
static const char* resultName(SatSolver::Result r)
{
    switch (r)
    {
        case SatSolver::Result::Sat:   return "SAT";
        case SatSolver::Result::Unsat: return "UNSAT";
        default:                       return "UNKNOWN";
    }
}

static void printCsvHeader()
{
    std::printf("policy,unit,vars,clauses,result,conflicts,decisions,propagations,"
                "restarts,blocked,learnt,avg_lbd,avg_len,deleted,max_level,seconds\n");
}

static void printCsvRow(const SatSolver& s, SatSolver::Result r, int vars, int clauses)
{
    const auto& st = s.stats();
    std::printf("%s,%d,%d,%d,%s,%llu,%llu,%llu,%llu,%llu,%llu,%.2f,%.2f,%llu,%llu,%.4f\n",
                SatSolver::policyName(s.config().restart), s.config().restartUnit,
                vars, clauses, resultName(r),
                (unsigned long long)st.conflicts, (unsigned long long)st.decisions,
                (unsigned long long)st.propagations, (unsigned long long)st.restarts,
                (unsigned long long)st.blockedRestarts, (unsigned long long)st.learnedClauses,
                st.avgLBD(), st.avgLearntLength(),
                (unsigned long long)st.deletedClauses,
                (unsigned long long)st.maxDecisionLevel, st.seconds);
}

static void printHuman(const SatSolver& s, SatSolver::Result r)
{
    const auto& st = s.stats();
    std::printf("s %s\n", resultName(r));
    std::printf("c restart policy   : %s (unit %d)\n",
                SatSolver::policyName(s.config().restart), s.config().restartUnit);
    std::printf("c conflicts        : %llu\n", (unsigned long long)st.conflicts);
    std::printf("c decisions        : %llu\n", (unsigned long long)st.decisions);
    std::printf("c propagations     : %llu\n", (unsigned long long)st.propagations);
    std::printf("c restarts         : %llu (blocked %llu)\n",
                (unsigned long long)st.restarts, (unsigned long long)st.blockedRestarts);
    std::printf("c learnt clauses   : %llu (deleted %llu)\n",
                (unsigned long long)st.learnedClauses, (unsigned long long)st.deletedClauses);
    std::printf("c avg LBD / length : %.2f / %.2f\n", st.avgLBD(), st.avgLearntLength());
    std::printf("c max level        : %llu\n", (unsigned long long)st.maxDecisionLevel);
    std::printf("c cpu time         : %.4f s\n", st.seconds);
}

static void printModel(const SatSolver& s, int vars)
{
    std::printf("v");
    for (int v = 1; v <= vars; ++v) std::printf(" %d", s.model()[v] ? v : -v);
    std::printf(" 0\n");
}

//-----------------------------------------------------------------

static bool argVal(const char* arg, const char* name, const char** out)
{
    const size_t n = std::strlen(name);
    if (std::strncmp(arg, name, n) == 0 && arg[n] == '=') { *out = arg + n + 1; return true; }
    return false;
}

int main(int argc, char** argv)
{
    SatSolver::Config cfg;
    const char* file       = nullptr;
    const char* genSpec    = nullptr;
    const char* sweepSpec  = nullptr;
    bool csv = false, showModel = false, showLog = false;

    for (int i = 1; i < argc; ++i)
    {
        const char* a = argv[i];
        const char* v = nullptr;

        if (argVal(a, "--restart", &v))
        {
            if (!SatSolver::parsePolicy(v, cfg.restart))
            {
                std::fprintf(stderr, "unknown restart policy: %s\n", v);
                return 2;
            }
        }
        else if (argVal(a, "--restart-unit", &v)) cfg.restartUnit     = std::atoi(v);
        else if (argVal(a, "--geo-factor",   &v)) cfg.geometricFactor = std::atof(v);
        else if (argVal(a, "--glucose-k",    &v)) cfg.glucoseK        = std::atof(v);
        else if (argVal(a, "--var-decay",    &v)) cfg.varDecay        = std::atof(v);
        else if (argVal(a, "--random-freq",  &v)) cfg.randomVarFreq   = std::atof(v);
        else if (argVal(a, "--budget",       &v)) cfg.conflictBudget  = std::strtoull(v, nullptr, 10);
        else if (argVal(a, "--seed",         &v)) cfg.randomSeed      = std::strtoull(v, nullptr, 10);
        else if (argVal(a, "--gen",          &v)) genSpec             = v;
        else if (argVal(a, "--sweep",        &v)) sweepSpec           = v;
        else if (!std::strcmp(a, "--block-restarts"))  cfg.blockRestarts  = true;
        else if (!std::strcmp(a, "--no-phase-saving")) cfg.phaseSaving    = false;
        else if (!std::strcmp(a, "--no-minimize"))     cfg.minimizeLearnt = false;
        else if (!std::strcmp(a, "--no-reduce"))       cfg.reduceDB       = false;
        else if (!std::strcmp(a, "--csv"))             csv        = true;
        else if (!std::strcmp(a, "--model"))           showModel  = true;
        else if (!std::strcmp(a, "--restart-log"))     showLog    = true;
        else if (a[0] == '-') { std::fprintf(stderr, "unknown option: %s\n", a); return 2; }
        else file = a;
    }

    if (sweepSpec)
    {
        int vars = 0, clauses = 0, seed0 = 0, n = 0;

        if (std::sscanf(sweepSpec, "%d,%d,%d,%d", &vars, &clauses, &seed0, &n) != 4)
        {
            std::fprintf(stderr, "bad --sweep spec\n");
            return 2;
        }

        const SatSolver::RestartPolicy policies[] =
        {
            SatSolver::RestartPolicy::None,
            SatSolver::RestartPolicy::Fixed,
            SatSolver::RestartPolicy::Geometric,
            SatSolver::RestartPolicy::Luby,
            SatSolver::RestartPolicy::Glucose
        };

        std::printf("instance,");
        printCsvHeader();
        for (int i = 0; i < n; ++i)
        {
            const uint64_t seed = static_cast<uint64_t>(seed0) + i;
            SatSolver::CNF cnf = generateRandomKSat(vars, clauses, 3, seed);

            for (const auto p : policies)
            {
                SatSolver::Config c = cfg;
                c.restart = p;
                SatSolver solver(c);
                const auto r = solver.solve(cnf, vars);
                if (r == SatSolver::Result::Sat && !SatSolver::verify(cnf, solver.model()))
                {
                    std::fprintf(stderr, "MODEL CHECK FAILED (seed %llu)\n",(unsigned long long)seed);
                    return 1;
                }
                std::printf("%llu,", (unsigned long long)seed);
                printCsvRow(solver, r, vars, static_cast<int>(cnf.size()));
            }
        }
        return 0;
    }


    SatSolver::CNF cnf;
    int numVars = 0;

    if (genSpec)
    {
        int vars = 0, clauses = 0, k = 3;
        unsigned long long seed = 1;
        const int got = std::sscanf(genSpec, "%d,%d,%llu,%d", &vars, &clauses, &seed, &k);

        if (got < 2)
        {
            std::fprintf(stderr, "bad --gen spec\n");
            return 2;
        }

        cnf = generateRandomKSat(vars, clauses, k, seed);
        numVars = vars;
    }
    else if (file)
    {
        std::ifstream in(file);

        if (!in)
        {
            std::fprintf(stderr, "cannot open %s\n", file);
            return 2;
        }

        readDimacs(in, cnf, numVars);
    }
    else
    {
        readDimacs(std::cin, cnf, numVars);
    }

    SatSolver solver(cfg);
    const auto r = solver.solve(cnf, numVars);

    if (r == SatSolver::Result::Sat && !SatSolver::verify(cnf, solver.model()))
    {
        std::fprintf(stderr, "INTERNAL ERROR: model does not satisfy the formula\n");
        return 1;
    }

    if (csv)
    {
        printCsvHeader();
        printCsvRow(solver, r, numVars, (int)cnf.size());
    }
    else
        printHuman(solver, r);


    if (showModel && r == SatSolver::Result::Sat)
        printModel(solver, numVars);

    if (showLog)
    {
        std::printf("c restart log:");

        for (const auto c : solver.restartLog())
            std::printf(" %llu", (unsigned long long)c);

        std::printf("\n");
    }

    return r == SatSolver::Result::Sat ? 10 : (r == SatSolver::Result::Unsat ? 20 : 0);
}