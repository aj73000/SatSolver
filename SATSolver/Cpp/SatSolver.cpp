#include "SatSolver.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>

//=====================================================
// VarHeap
//=====================================================

// Name: SatSolver::VarHeap::init
//
// Inputs: int numVars                         : total number of variables in the formula
//         const std::vector<double>* activity : pointer to the solver's VSIDS activity array
//
// Description: Initialises the heap with all variables inserted in order 1..numVars.
// Sets the internal activity pointer so the comparator can read scores.
// All positions are recorded in pos[] so every variable can be located in O(1).
//
// Return: void
void SatSolver::VarHeap::init(int numVars, const std::vector<double>* activity)
{
    act = activity;
    heap.clear();
    pos.assign(numVars + 1, -1);
    for (int v = 1; v <= numVars; ++v)
    {
        heap.push_back(v);
        pos[v] = v - 1;
    }
}

// Name: SatSolver::VarHeap::siftUp
//
// Inputs: int i : index in heap[] of the element to move upward
//
// Description: Restores the max-heap property after an element's activity has increased.
// Repeatedly swaps the element at i with its parent while it has higher
// activity, updating pos[] at every swap to keep the inverse map valid. O(log n).
//
// Return: void
void SatSolver::VarHeap::siftUp(int i)
{
    const int x = heap[i];
    while (i > 0)
    {
        const int p = (i - 1) >> 1;
        if (!higher(x, heap[p]))
            break;
        heap[i] = heap[p];
        pos[heap[i]] = i;
        i = p;
    }
    heap[i] = x;
    pos[x] = i;
}

// Name: SatSolver::VarHeap::siftDown
//
// Inputs: int i : index in heap[] of the element to move downward
//
// Description: Restores the max-heap property after the root has been replaced.
// Repeatedly swaps the element at i with its highest-activity child
// until the heap property holds, updating pos[] at every swap. O(log n).
//
// Return: void
void SatSolver::VarHeap::siftDown(int i)
{
    const int x = heap[i];
    const int n = static_cast<int>(heap.size());
    while (true)
    {
        int c = 2 * i + 1;
        if (c >= n) break;
        if (c + 1 < n && higher(heap[c + 1], heap[c]))
            ++c;
        if (!higher(heap[c], x))
            break;
        heap[i] = heap[c];
        pos[heap[i]] = i;
        i = c;
    }
    heap[i] = x;
    pos[x] = i;
}

// Name: SatSolver::VarHeap::insert
//
// Inputs: int v : variable number to insert into the heap
//
// Description: Inserts variable v if it is not already present. Appends it to the
// end of the array and sifts it upward to its correct position so the
// heap property is maintained in O(log n).
//
// Return: void
void SatSolver::VarHeap::insert(int v)
{
    if (contains(v)) return;
    heap.push_back(v);
    pos[v] = static_cast<int>(heap.size()) - 1;
    siftUp(pos[v]);
}

// Name: SatSolver::VarHeap::bumped
//
// Inputs: int v : variable whose VSIDS activity score has just been increased
//
// Description: Notifies the heap that variable v has a higher activity than before.
// Calls siftUp from v's current position so the heap property is restored
// without a full rebuild. O(log n).
//
// Return: void
void SatSolver::VarHeap::bumped(int v)
{
    if (contains(v))
        siftUp(pos[v]);
}

// Name: SatSolver::VarHeap::removeMax
//
// Inputs: No inputs
//
// Description: Removes and returns the variable with the highest VSIDS activity score.
// Replaces the root with the last element, shrinks the array, and sifts
// the new root downward to restore the heap property. O(log n).
//
// Return: int : variable number with the highest activity score
int SatSolver::VarHeap::removeMax()
{
    const int x = heap[0];
    pos[x] = -1;
    heap[0] = heap.back();
    heap.pop_back();
    if (!heap.empty())
    {
        pos[heap[0]] = 0;
        siftDown(0);
    }
    return x;
}

//=====================================================
// Construction / reset
//=====================================================

SatSolver::SatSolver() = default;

SatSolver::SatSolver(const Config& cfg) : config_(cfg) {}

// Name: SatSolver::reset
//
// Inputs: int numVars : number of propositional variables in the formula
//
// Description: Clears and re-initialises every data structure to a clean state for a
// new solve call. Resizes all per-variable arrays, clears the trail,
// watch lists, learnt clause store, and all restart and LBD bookkeeping.
// Must be called at the start of solve() before any clauses are added.
//
// Return: void
void SatSolver::reset(int numVars)
{
    numVars_ = numVars;
    unsat_ = false;
    stats_ = Stats{};

    problem_.clear();
    learnts_.clear();

    watches_.assign(2 * (numVars_ + 1), {});

    assign_.assign(numVars_ + 1, 0);
    level_.assign(numVars_ + 1, -1);
    reason_.assign(numVars_ + 1, nullptr);
    phase_.assign(numVars_ + 1, config_.defaultPhase ? 1 : 0);
    activity_.assign(numVars_ + 1, 0.0);
    model_.assign(numVars_ + 1, 0);

    trail_.clear();
    trailLim_.clear();
    qhead_ = 0;

    seen_.assign(numVars_ + 1, 0);
    toClear_.clear();
    lbdStamp_.assign(numVars_ + 1, 0);
    lbdCounter_ = 0;

    order_.init(numVars_, &activity_);
    varInc_ = 1.0;
    clauseInc_ = 1.0;

    conflictsSinceRestart_ = 0;
    restartIndex_ = 0;
    geoLimit_ = config_.restartUnit;
    emaFastLBD_ = emaSlowLBD_ = emaTrail_ = 0.0;
    restartLog_.clear();

    reduceLimit_ = config_.reduceFirst;
    rngState_ = config_.randomSeed ? config_.randomSeed : 91648253u;
}

// Name: SatSolver::nextRandom
//
// Inputs: No inputs
//
// Description: Generates the next pseudo-random 32-bit integer using xorshift64*.
// Advances the internal rngState_ on every call. Used for random
// branching decisions when randomVarFreq > 0.
//
// Return: uint32_t : pseudo-random unsigned 32-bit integer
uint32_t SatSolver::nextRandom()
{
    // xorshift64*, plenty good for a decision heuristic
    rngState_ ^= rngState_ >> 12;
    rngState_ ^= rngState_ << 25;
    rngState_ ^= rngState_ >> 27;
    return static_cast<uint32_t>((rngState_ * 2685821657736338717ULL) >> 32);
}

//=====================================================
// Clause input
//=====================================================

// Name: SatSolver::addClause
//
// Inputs: Clause c : a clause as a vector of non-zero integer literals
//
// Description: Normalises the clause (sorts, removes duplicates, checks for tautologies),
// simplifies against root-level assignments, and stores it in problem_.
// Unit clauses are directly enqueued at decision level 0 rather than stored.
// Returns false only if the clause reduces to the empty clause, which means
// the formula is immediately UNSAT.
//
// Return: bool : true if the formula remains potentially satisfiable, false if UNSAT
bool SatSolver::addClause(Clause c)
{
    // Normalise: sort by |literal| then sign, drop duplicates, drop tautologies.
    std::sort(c.begin(), c.end(), [](int a, int b) {
        const int aa = std::abs(a), bb = std::abs(b);
        return aa != bb ? aa < bb : a < b;
    });
    c.erase(std::unique(c.begin(), c.end()), c.end());

    for (size_t i = 0; i + 1 < c.size(); ++i)
        if (c[i] == -c[i + 1])
            return true;          // tautology: harmless, skip

    // Simplify against literals already fixed at the root.
    Clause lits;
    lits.reserve(c.size());
    for (const int l : c)
    {
        const int8_t v = value(l);
        if (v > 0) return true;                      // already satisfied
        if (v < 0) continue;                         // already false: drop literal
        lits.push_back(l);
    }

    if (lits.empty())
        return false;                 // empty clause -> UNSAT
    if (lits.size() == 1)
    {
        enqueue(lits[0], nullptr);                   // root-level unit
        return true;
    }

    auto sc = std::make_unique<SolverClause>();
    sc->lits   = std::move(lits);
    sc->learnt = false;
    attach(sc.get());
    problem_.push_back(std::move(sc));
    return true;
}

// Name: SatSolver::attach
//
// Inputs: SolverClause* c : pointer to the clause to register in the watch lists
//
// Description: Registers a clause under the two-watched-literal scheme by adding it
// to the watch list of lits[0] and lits[1]. Both watched literals are
// always at positions 0 and 1; this invariant is maintained by propagate().
//
// Return: void
void SatSolver::attach(SolverClause* c)
{
    watches_[watchIndex(c->lits[0])].push_back(c);
    watches_[watchIndex(c->lits[1])].push_back(c);
}

// Name: SatSolver::rebuildWatches
//
// Inputs: No inputs
//
// Description: Clears all watch lists and re-attaches every surviving clause in
// problem_ and learnts_. Called after reduceDB() deletes learnt clauses,
// since deleted clauses leave stale pointers in the watch lists.
//
// Return: void
void SatSolver::rebuildWatches()
{
    for (auto& w : watches_) w.clear();
    for (auto& c : problem_) attach(c.get());
    for (auto& c : learnts_) attach(c.get());
}

//=====================================================
// Assignment
//=====================================================

// Name: SatSolver::enqueue
//
// Inputs: int lit              : the literal to assign (positive = true, negative = false)
//         SolverClause* reason : clause that forced this assignment, or nullptr for decisions
//
// Description: Records the assignment of a literal on the trail and in the per-variable
// arrays. Sets assign_[], level_[], and reason_[] for the variable, then
// appends the literal to trail_. Does not perform any propagation itself;
// propagate() processes the queue via qhead_.
//
// Return: void
void SatSolver::enqueue(int lit, SolverClause* reason)
{
    const int v = std::abs(lit);
    assign_[v] = lit > 0 ? 1 : -1;
    level_[v]  = decisionLevel();
    reason_[v] = reason;
    trail_.push_back(lit);
    if (decisionLevel() == 0) ++stats_.rootAssignments;
}

// Name: SatSolver::backtrack
//
// Inputs: int level : the decision level to backtrack to (inclusive)
//
// Description: Undoes all assignments made above the given decision level by walking
// the trail backwards and resetting assign_[], level_[], and reason_[] for
// each variable. If phase saving is enabled, the variable's last polarity
// is saved to phase_[] before it is unassigned. Unassigned variables are
// reinserted into the VSIDS order heap. Adjusts trailLim_ and qhead_
// to reflect the new trail size.
//
// Return: void
void SatSolver::backtrack(int level)
{
    if (decisionLevel() <= level) return;

    for (int i = static_cast<int>(trail_.size()) - 1; i >= trailLim_[level]; --i)
    {
        const int v = std::abs(trail_[i]);
        if (config_.phaseSaving) phase_[v] = (assign_[v] > 0) ? 1 : 0;
        assign_[v] = 0;
        level_[v]  = -1;
        reason_[v] = nullptr;
        order_.insert(v);
    }

    trail_.resize(trailLim_[level]);
    trailLim_.resize(level);
    qhead_ = trail_.size();
}

// Name: SatSolver::propagate
//
// Inputs: No inputs
//
// Description: Performs Boolean Constraint Propagation (BCP) using the two-watched-literal
// scheme. Processes every literal on the trail that has not yet been propagated
// (from qhead_ to the end). For each falsified watch, tries to find a new
// non-false literal to watch; if none exists the clause is either unit
// (enqueue the remaining literal) or falsified (conflict detected).
//
// Return: SolverClause* : pointer to the conflicting clause, or nullptr if no conflict
SatSolver::SolverClause* SatSolver::propagate()
{
    SolverClause* conflict = nullptr;

    while (qhead_ < trail_.size())
    {
        const int p = trail_[qhead_++];
        ++stats_.propagations;

        auto& ws = watches_[watchIndex(-p)];    // clauses in which -p is watched
        size_t i = 0, j = 0;

        while (i < ws.size())
        {
            SolverClause* c = ws[i];

            // Keep the falsified watch at position 1.
            if (c->lits[0] == -p)
                std::swap(c->lits[0], c->lits[1]);

            const int first = c->lits[0];

            // Other watch already satisfies the clause: nothing to do.
            if (value(first) > 0)
            {
                ws[j++] = ws[i++];
                continue;
            }

            // Look for a non-false literal to watch instead.
            bool moved = false;
            for (size_t k = 2; k < c->lits.size(); ++k)
            {
                if (value(c->lits[k]) >= 0)
                {
                    std::swap(c->lits[1], c->lits[k]);
                    watches_[watchIndex(c->lits[1])].push_back(c);
                    ++i;
                    moved = true;
                    break;
                }
            }
            if (moved)
                continue;

            // No replacement: the clause is unit or falsified.
            ws[j++] = ws[i++];

            if (value(first) < 0)
            {
                conflict = c;
                qhead_ = trail_.size();
                while (i < ws.size())
                    ws[j++] = ws[i++];   // keep the rest intact
            }
            else
            {
                enqueue(first, c);
            }
        }

        ws.resize(j);
        if (conflict)
            break;
    }

    return conflict;
}

//=====================================================
// Conflict analysis — first UIP
//=====================================================

// Name: SatSolver::analyze
//
// Inputs: SolverClause* confl : the clause that triggered the conflict
//         Clause& out         : output vector that receives the learnt clause literals
//         int& btLevel        : output set to the backjump decision level
//         int& lbd            : output set to the Literal Block Distance of the learnt clause
//
// Description: Performs first-UIP conflict analysis by walking the trail backwards through
// the implication graph. Expands reason clauses and bumps VSIDS scores for all
// variables encountered. Applies optional self-subsuming minimisation to shorten
// the learnt clause. Sets btLevel to the second-highest decision level in the
// learnt clause and moves that literal to position 1 so that the watched-literal
// invariant holds immediately after attachment. Cleans the seen_[] scratch array
// before returning.
//
// Return: void
void SatSolver::analyze(SolverClause* confl, Clause& out, int& btLevel, int& lbd)
{
    int pathCount = 0;
    int p = 0;
    int index = static_cast<int>(trail_.size()) - 1;

    out.clear();
    out.push_back(0);

    do
    {
        SolverClause& c = *confl;
        if (c.learnt) bumpClause(c);

        // For a reason clause, lits[0] is the literal we resolved on: skip it.
        for (size_t k = (p == 0 ? 0 : 1); k < c.lits.size(); ++k)
        {
            const int q = c.lits[k];
            const int v = std::abs(q);

            if (!seen_[v] && level_[v] > 0)
            {
                seen_[v] = 1;
                toClear_.push_back(v);
                bumpVar(v);

                if (level_[v] >= decisionLevel())
                    ++pathCount;
                else
                    out.push_back(q);
            }
        }

        while (!seen_[std::abs(trail_[index])])
            --index;
        p = trail_[index--];
        confl = reason_[std::abs(p)];
        seen_[std::abs(p)] = 0;
        --pathCount;
    }
    while (pathCount > 0);

    out[0] = -p;

    // Basic clause minimisation: drop any literal whose reason is entirely
    // covered by literals already in the clause (self-subsuming resolution).
    if (config_.minimizeLearnt)
    {
        size_t j = 1;
        for (size_t i = 1; i < out.size(); ++i)
        {
            const SolverClause* r = reason_[std::abs(out[i])];
            bool redundant = (r != nullptr);
            if (r)
            {
                for (size_t k = 1; k < r->lits.size(); ++k)
                {
                    const int u = std::abs(r->lits[k]);
                    if (!seen_[u] && level_[u] > 0)
                    {
                        redundant = false;
                        break;
                    }
                }
            }
            if (!redundant) out[j++] = out[i];
        }
        out.resize(j);
    }

    // Backjump level = second highest level in the clause; put it to
    // position 1 so the watches are correct straight away.
    if (out.size() == 1)
    {
        btLevel = 0;
    }
    else
    {
        size_t maxI = 1;
        for (size_t i = 2; i < out.size(); ++i)
            if (level_[std::abs(out[i])] > level_[std::abs(out[maxI])])
                maxI = i;
        std::swap(out[1], out[maxI]);
        btLevel = level_[std::abs(out[1])];
    }

    lbd = computeLBD(out);

    for (const int v : toClear_)
        seen_[v] = 0;
    toClear_.clear();
}

// Name: SatSolver::computeLBD
//
// Inputs: const Clause& c : the clause whose LBD is to be computed
//
// Description: Computes the Literal Block Distance of the clause: the number of distinct
// decision levels among its literals, ignoring level 0. Uses a stamp-based
// approach so no array clearing is needed between calls; incrementing
// lbdCounter_ acts as a logical clear in O(1).
//
// Return: int : the Literal Block Distance (number of distinct decision levels)
int SatSolver::computeLBD(const Clause& c)
{
    ++lbdCounter_;
    int count = 0;
    for (const int l : c)
    {
        const int lv = level_[std::abs(l)];
        if (lv <= 0)
            continue;
        if (lbdStamp_[lv] != lbdCounter_)
        {
            lbdStamp_[lv] = lbdCounter_;
            ++count;
        }
    }
    return count;
}

//=====================================================
// Heuristics
//=====================================================

// Name: SatSolver::bumpVar
//
// Inputs: int v : variable number whose VSIDS activity is to be increased
//
// Description: Increases the VSIDS activity score of variable v by the current increment
// varInc_. If any score exceeds 1e100, all scores and the increment are
// rescaled by 1e-100 to prevent floating-point overflow. Notifies the heap
// so the variable's position is updated in O(log n).
//
// Return: void
void SatSolver::bumpVar(int v)
{
    activity_[v] += varInc_;
    if (activity_[v] > 1e100)
    {
        for (int i = 1; i <= numVars_; ++i)
            activity_[i] *= 1e-100;
        varInc_ *= 1e-100;
    }
    order_.bumped(v);
}

// Name: SatSolver::bumpClause
//
// Inputs: SolverClause& c : the learnt clause whose activity is to be increased
//
// Description: Increases the activity score of a learnt clause by clauseInc_. If any
// clause activity exceeds 1e20, all learnt clause activities and clauseInc_
// are rescaled by 1e-20 to prevent overflow. Used by reduceDB() to prefer
// keeping recently active clauses over older unused ones.
//
// Return: void
void SatSolver::bumpClause(SolverClause& c)
{
    c.activity += clauseInc_;
    if (c.activity > 1e20)
    {
        for (auto& l : learnts_) l->activity *= 1e-20;
        clauseInc_ *= 1e-20;
    }
}

// Name: SatSolver::pickBranchVar
//
// Inputs: No inputs
//
// Description: Selects the next unassigned variable to branch on. With probability
// randomVarFreq a random unassigned variable is chosen for search diversity.
// Otherwise, repeatedly extracts the highest-activity variable from the VSIDS
// heap until an unassigned one is found. Returns 0 if all variables are
// assigned, signalling that the formula is satisfied.
//
// Return: int : variable number to branch on, or 0 if all variables are assigned
int SatSolver::pickBranchVar()
{
    if (config_.randomVarFreq > 0.0 && !order_.empty())
    {
        if ((nextRandom() & 0xFFFFFF) < config_.randomVarFreq * 0x1000000)
        {
            const int v = 1 + static_cast<int>(nextRandom() % static_cast<uint32_t>(numVars_));
            if (assign_[v] == 0)
                return v;
        }
    }

    while (!order_.empty())
    {
        const int v = order_.removeMax();
        if (assign_[v] == 0)
            return v;
    }
    return 0;   // everything assigned
}

//=====================================================
// Restart policies
//=====================================================

// Name: SatSolver::luby
//
// Inputs: double y : the base of the Luby sequence (typically 2.0)
//         int x    : the position in the sequence (0-indexed)
//
// Description: Computes the x-th element of the Luby universal restart sequence in
// O(log x) using a closed-form tree-structure algorithm. The sequence is
// y^0, y^0, y^1, y^0, y^0, y^1, y^2, ... and is scaled by restartUnit
// in the caller to produce the conflict limit for each restart interval.
//
// Return: double : the Luby sequence value at position x
double SatSolver::luby(double y, int x)
{
    if (x < 1)
        return 1.0;

    int size = 1;
    int seq = 0;

    // Find the smallest full binary tree size that bounds x
    while (size < x + 1)
    {
        size = 2 * size + 1;
        ++seq;
    }

    while (size - 1 != x)
    {
        size = (size - 1) >> 1;
        --seq;
        x = x % size;
    }
    return std::pow(y, seq);
}

// Name: SatSolver::staticRestartLimit
//
// Inputs: No inputs
//
// Description: Returns the conflict limit for the current restart interval under the
// active static restart policy. For Fixed, returns restartUnit unchanged.
// For Geometric, returns geoLimit_ which grows after each restart. For Luby,
// multiplies restartUnit by the Luby value at the current restartIndex_.
// Not called for the None or Glucose policies.
//
// Return: uint64_t : number of conflicts allowed in the current restart interval
uint64_t SatSolver::staticRestartLimit() const
{
    switch (config_.restart)
    {
        case RestartPolicy::Fixed:
            return static_cast<uint64_t>(config_.restartUnit);
        case RestartPolicy::Geometric:
            return static_cast<uint64_t>(geoLimit_);
        case RestartPolicy::Luby:
            return static_cast<uint64_t>(config_.restartUnit * luby(2.0, static_cast<int>(restartIndex_)));
        default:
            return 0;
    }
}

// Name: SatSolver::restartDue
//
// Inputs: No inputs
//
// Description: Determines whether a restart should be triggered based on the active
// restart policy. For None, always returns false. For static policies
// (Fixed, Geometric, Luby), compares conflictsSinceRestart_ against the
// current limit from staticRestartLimit(). For Glucose, checks whether the
// fast LBD EMA exceeds the slow LBD EMA by the threshold factor K, subject
// to a minimum conflict count since the last restart.
//
// Return: bool : true if a restart should be performed now, false otherwise
bool SatSolver::restartDue() const
{
    switch (config_.restart)
    {
        case RestartPolicy::None:
            return false;

        case RestartPolicy::Fixed:
        case RestartPolicy::Geometric:
        case RestartPolicy::Luby:
            return conflictsSinceRestart_ >= staticRestartLimit();

        case RestartPolicy::Glucose:
            if (conflictsSinceRestart_ < static_cast<uint64_t>(config_.glucoseMinConf))
                return false;
            return emaFastLBD_ * config_.glucoseK > emaSlowLBD_;
    }
    return false;
}

// Name: SatSolver::onRestart
//
// Inputs: No inputs
//
// Description: Executes a restart: increments the restart counter, records the current
// global conflict count in restartLog_, resets conflictsSinceRestart_ to 0,
// and advances restartIndex_. For Geometric, multiplies geoLimit_ by the
// growth factor. For Glucose, resets the fast LBD EMA to the slow EMA to
// prevent an immediate second restart. Then calls backtrack(0) to undo all
// assignments above level 0 while preserving learned clauses and VSIDS scores.
//
// Return: void
void SatSolver::onRestart()
{
    ++stats_.restarts;
    restartLog_.push_back(stats_.conflicts);
    conflictsSinceRestart_ = 0;
    ++restartIndex_;

    if (config_.restart == RestartPolicy::Geometric)
        geoLimit_ *= config_.geometricFactor;

    if (config_.restart == RestartPolicy::Glucose)
        emaFastLBD_ = emaSlowLBD_;     // reset the trigger

    backtrack(0);
}

//=====================================================
// Learnt clause database reduction
//=====================================================

// Name: SatSolver::locked
//
// Inputs: const SolverClause* c : the learnt clause to test
//
// Description: Returns true if clause c is the reason for the current assignment of
// its asserting literal (lits[0]). A locked clause must not be deleted by
// reduceDB() because removing it would invalidate the reason pointer stored
// in reason_[] and break the implication graph.
//
// Return: bool : true if the clause is locked (in use as a reason), false if deletable
bool SatSolver::locked(const SolverClause* c) const
{
    const int v = std::abs(c->lits[0]);
    return reason_[v] == c && assign_[v] != 0 && value(c->lits[0]) > 0;
}

// Name: SatSolver::reduceDB
//
// Inputs: No inputs
//
// Description: Deletes approximately half of the learnt clauses to limit memory usage.
// Sorts learnts_ by quality (worst first: highest LBD, then lowest activity),
// then removes up to half, skipping any that are locked, have LBD <= 2
// (high quality), or are binary. Surviving clauses are compacted in place.
// Updates stats_.deletedClauses and calls rebuildWatches() to remove stale
// watch-list pointers left by the deleted clauses.
//
// Return: void
void SatSolver::reduceDB()
{
    // Worst first: high LBD, then low activity.
    std::sort(learnts_.begin(), learnts_.end(),
              [](const std::unique_ptr<SolverClause>& a,
                 const std::unique_ptr<SolverClause>& b) {
                  if (a->lbd != b->lbd)
                      return a->lbd > b->lbd;

                  return a->activity < b->activity;
              });

    const size_t target = learnts_.size() / 2;
    size_t removed = 0, keep = 0;

    for (size_t i = 0; i < learnts_.size(); ++i)
    {
        SolverClause* c = learnts_[i].get();
        const bool droppable = removed < target && c->lbd > 2 &&
                               c->lits.size() > 2 && !locked(c);
        if (droppable)
        {
            learnts_[i].reset();
            ++removed;
        }
        else
        {
            learnts_[keep++] = std::move(learnts_[i]);
        }
    }
    learnts_.resize(keep);
    stats_.deletedClauses += removed;

    rebuildWatches();
}

//=====================================================
// Main search loop
//=====================================================

// Name: SatSolver::search
//
// Inputs: No inputs
//
// Description: The main CDCL search loop. Repeatedly calls propagate() to derive forced
// assignments. On conflict: increments counters, updates LBD EMAs, runs
// conflict analysis, backtracks, and attaches the learnt clause. On no
// conflict: checks whether a restart or clause deletion is due, then picks
// the next branch variable. Returns Sat when all variables are assigned,
// Unsat when a conflict occurs at level 0, or Unknown when the conflict
// budget is exhausted.
//
// Return: Result : Sat, Unsat, or Unknown
SatSolver::Result SatSolver::search()
{
    Clause learnt;

    while (true)
    {
        SolverClause* confl = propagate();

        if (confl)
        {
            ++stats_.conflicts;
            ++conflictsSinceRestart_;

            if (decisionLevel() == 0)
                return Result::Unsat;

            int btLevel = 0, lbd = 0;
            analyze(confl, learnt, btLevel, lbd);

            // Restart signals
            emaFastLBD_ += config_.emaFastAlpha * (lbd - emaFastLBD_);
            emaSlowLBD_ += config_.emaSlowAlpha * (lbd - emaSlowLBD_);
            if (config_.blockRestarts)
            {
                const double t = static_cast<double>(trail_.size());
                if (stats_.conflicts > static_cast<uint64_t>(config_.blockMinConf) &&
                    t > config_.blockR * emaTrail_)
                {
                    emaFastLBD_ = emaSlowLBD_;       // block the next restart
                    ++stats_.blockedRestarts;
                }
                emaTrail_ += config_.emaTrailAlpha * (t - emaTrail_);
            }

            backtrack(btLevel);

            if (learnt.size() == 1)
            {
                enqueue(learnt[0], nullptr);         // root-level fact
            }
            else
            {
                auto sc = std::make_unique<SolverClause>();
                sc->lits = learnt;
                sc->learnt = true;
                sc->lbd = lbd;
                SolverClause* raw = sc.get();
                bumpClause(*raw);
                attach(raw);
                learnts_.push_back(std::move(sc));
                enqueue(learnt[0], raw);
            }

            ++stats_.learnedClauses;
            stats_.learnedLiterals += learnt.size();
            stats_.lbdSum += lbd;

            decayVarActivity();
            decayClauseActivity();

            if (config_.conflictBudget && stats_.conflicts >= config_.conflictBudget)
                return Result::Unknown;
        }
        else
        {
            if (restartDue())
            {
                onRestart();
                continue;
            }

            if (config_.reduceDB && static_cast<int>(learnts_.size()) >= reduceLimit_)
            {
                reduceDB();
                reduceLimit_ += config_.reduceInc;
            }

            const int v = pickBranchVar();
            if (v == 0)
            {
                for (int i = 1; i <= numVars_; ++i) model_[i] = (assign_[i] > 0);
                return Result::Sat;
            }

            newDecisionLevel();
            if (static_cast<uint64_t>(decisionLevel()) > stats_.maxDecisionLevel)
                stats_.maxDecisionLevel = decisionLevel();

            ++stats_.decisions;
            enqueue(phase_[v] ? v : -v, nullptr);
        }
    }
}

//=====================================================
// Entry point
//=====================================================

// Name: SatSolver::solve
//
// Inputs: const CNF& cnf : the formula as a vector of clauses (each clause is a vector of ints)
//         int numVars    : number of propositional variables (literals range over 1..numVars)
//
// Description: Entry point for a solve call. Resets all state, validates and
// adds each clause via addClause(), runs an initial propagation pass to catch
// root-level units and detect trivial UNSAT, then calls search(). Records
// wall-clock time via chrono::steady_clock bracketing the entire call
// including setup.
//
// Return: Result : Sat if a satisfying assignment was found, Unsat if unsatisfiable,
//                  or Unknown if the conflict budget was exhausted
SatSolver::Result SatSolver::solve(const CNF& cnf, int numVars)
{
    const auto start = std::chrono::steady_clock::now();
    reset(numVars);

    for (const Clause& c : cnf)
    {
        for (const int l : c)
            if (l == 0 || std::abs(l) > numVars_)
            {
                unsat_ = true;
                break;
            }
        if (unsat_)
            break;
        if (!addClause(c))
        {
            unsat_ = true;
            break;
        }
    }

    Result r;
    if (unsat_ || propagate() != nullptr)
        r = Result::Unsat;
    else r = search();

    const auto end = std::chrono::steady_clock::now();
    stats_.seconds = std::chrono::duration<double>(end - start).count();
    return r;
}

//=====================================================
// Utilities
//=====================================================

// Name: SatSolver::verify
//
// Inputs: const CNF& cnf              : the formula to check against
//         const std::vector<char>& model : the assignment to verify (indexed by variable number)
//
// Description: Independently verifies that every clause in the formula is satisfied by
// the provided model. Checks that at least one literal in each clause evaluates
// to true under the assignment. Used as a post-solve sanity check to confirm
// the solver has not produced an incorrect result.
//
// Return: bool : true if every clause is satisfied, false if any clause is violated
bool SatSolver::verify(const CNF& cnf, const std::vector<char>& model)
{
    for (const Clause& c : cnf)
    {
        bool ok = false;
        for (const int l : c)
        {
            const int v = std::abs(l);
            if (v >= static_cast<int>(model.size()))
                return false;
            if ((l > 0) == (model[v] != 0))
            {
                ok = true;
                break;
            }
        }
        if (!ok)
            return false;
    }
    return true;
}

// Name: SatSolver::policyName
//
// Inputs: RestartPolicy p : the restart policy enum value to convert
//
// Description: Returns a human-readable lowercase string name for the given restart
// policy. Used in CSV output and the human-readable solver output header
// to identify which policy was active during a run.
//
// Return: const char* : string name of the policy ("none", "fixed", "geometric", "luby", "glucose")
const char* SatSolver::policyName(RestartPolicy p)
{
    switch (p)
    {
        case RestartPolicy::None:      return "none";
        case RestartPolicy::Fixed:     return "fixed";
        case RestartPolicy::Geometric: return "geometric";
        case RestartPolicy::Luby:      return "luby";
        case RestartPolicy::Glucose:   return "glucose";
    }
    return "?";
}

// Name: SatSolver::parsePolicy
//
// Inputs: const char* name   : string name of the policy to parse (e.g. "luby", "geo")
//         RestartPolicy& out : output parameter set to the corresponding enum value if found
//
// Description: Attempts to match the given string to a known restart policy enum value.
// Accepts "geo" as a shorthand for "geometric". Sets out and returns true
// on a successful match. Returns false and leaves out unchanged if the name
// is not recognised.
//
// Return: bool : true if the name was recognised and out was set, false otherwise
bool SatSolver::parsePolicy(const char* name, RestartPolicy& out)
{
    if (!std::strcmp(name, "none"))      { out = RestartPolicy::None;      return true; }
    if (!std::strcmp(name, "fixed"))     { out = RestartPolicy::Fixed;     return true; }
    if (!std::strcmp(name, "geometric") ||
        !std::strcmp(name, "geo"))       { out = RestartPolicy::Geometric; return true; }
    if (!std::strcmp(name, "luby"))      { out = RestartPolicy::Luby;      return true; }
    if (!std::strcmp(name, "glucose"))   { out = RestartPolicy::Glucose;   return true; }
    return false;
}