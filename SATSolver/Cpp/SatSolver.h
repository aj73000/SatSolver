
#ifndef SATSOLVER_SATSOLVER_H
#define SATSOLVER_SATSOLVER_H

#include <cstdint>
#include <memory>
#include <vector>

class SatSolver
{
public:
    using Clause = std::vector<int>;   // literals; +v or -v
    using CNF    = std::vector<Clause>;

    enum class Result { Unsat = 0, Sat = 1, Unknown = 2 };


    // Restart policies --------------------------------------------
    enum class RestartPolicy
    {
        None,       // never restart
        Fixed,      // restart every `restartUnit` conflicts
        Geometric,  // limit *= geometricFactor after each restart
        Luby,       // restartUnit * luby(2, i)
        Glucose     // dynamic: restart when recent LBD is worse than average
    };

    struct Config
    {
    // --- restarts ---------------------------------------------------
    RestartPolicy restart = RestartPolicy::Luby;

    // Base number of conflicts between restarts for static/geometric policies.
    int restartUnit = 100;

    // Multiplicative growth factor for geometric restart intervals.
    double geometricFactor = 1.5;

    // Controls the sensitivity of the glucose-style restart heuristic.
    double glucoseK = 0.8;

    // Fast EMA smoothing factor for recent LBD measurements.
    double emaFastAlpha = 1.0 / 50;

    // Slow EMA smoothing factor for long-term LBD measurements.
    double emaSlowAlpha = 1.0 / 5000;

    // Minimum confidence/measurement count before glucose restarts are used.
    int glucoseMinConf = 50;

    // Prevents a restart when the restart-blocking heuristic says progress is good.
    bool blockRestarts = false;

    // Threshold/factor used by the restart-blocking heuristic.
    double blockR = 1.4;

    // EMA smoothing factor for tracking the assignment trail length.
    double emaTrailAlpha = 1.0 / 5000;

    // Minimum confidence/measurement count before restart blocking is enabled.
    int blockMinConf = 10000;

    // --- things that interact with restarts -------------------------------------

    // Reuse the previous phase/value of a variable when it is branched on again.
    bool phaseSaving = true;

    // Initial/default phase used for variables that have no saved phase yet.
    bool defaultPhase = false;

    // Amount by which variable activity is decayed after activity updates.
    double varDecay = 0.95;

    // Probability/frequency of choosing a random branching variable.
    double randomVarFreq = 0.0;

    // Try to minimise learnt clauses before adding them to the database.
    bool minimizeLearnt = true;

    // --- learnt clause database -------------------------------------

    // Periodically remove less useful learnt clauses.
    bool reduceDB = true;

    // Number of conflicts before the first learnt-clause database reduction.
    int reduceFirst = 2000;

    // Number of additional conflicts between subsequent database reductions.
    int reduceInc = 300;

    // --- limits ------------------------------------------------------

    // Maximum number of conflicts allowed; 0 means no conflict limit.
    uint64_t conflictBudget = 0;

    // Seed used by the solver's pseudo-random number generator.
    uint64_t randomSeed = 91648253;
    };

    struct Stats
    {
        uint64_t decisions = 0;
        uint64_t propagations = 0;
        uint64_t conflicts = 0;
        uint64_t restarts = 0;
        uint64_t blockedRestarts = 0;
        uint64_t learnedClauses  = 0;
        uint64_t learnedLiterals = 0;
        uint64_t lbdSum = 0;
        uint64_t deletedClauses = 0;
        uint64_t maxDecisionLevel = 0;

        // Number of assignments made at decision level zero.
        uint64_t rootAssignments = 0;

        double seconds = 0.0;

        double avgLBD() const { return learnedClauses ? double(lbdSum) / learnedClauses : 0.0; }

        // Average number of literals in each learnt clause.
        double avgLearntLength() const { return learnedClauses ? double(learnedLiterals) / learnedClauses : 0.0; }
    };

    SatSolver();
    explicit SatSolver(const Config& cfg);

    Result solve(const CNF& cnf, int numVars);

    const std::vector<char>& model() const { return model_; }

    const Stats&  stats()  const { return stats_; }
    const Config& config() const { return config_; }
    Config& config() { return config_; }

    const std::vector<uint64_t>& restartLog() const { return restartLog_; }

    // Verify that a model satisfies the supplied CNF formula.
    static bool verify(const CNF& cnf, const std::vector<char>& model);

    static const char* policyName(RestartPolicy p);
    static bool parsePolicy(const char* name, RestartPolicy& out);

private:
    struct SolverClause
    {
        Clause lits;

        // VSIDS-style activity score used when deciding which clauses to remove.
        double activity = 0.0;

        // Literal Block Distance score; lower values suggest
        // more useful learnt clauses.
        int lbd = 0;

        // True when this clause was learnt during search rather than
        // being part of the original problem. An example would be if A becomes implicitly true then (a) is a learnt clause
        bool learnt = false;
    };

    // -----------------------------------------------------------------
    // Variable decision heap.
    // Maintains variables ordered by their activity scores.
    // Structured as a binary tree heap for efficient ordering and removal
    // -----------------------------------------------------------------
    struct VarHeap
    {
        // Binary heap containing variable indices.
        std::vector<int> heap;

        // Position of each variable inside heap; -1 means not present.
        // This is to remove needing of searching heap, making O(n) to constant.
        std::vector<int> pos;

        // Points at the solver's variable-activity array.
        const std::vector<double>* act = nullptr;

        void init(int numVars, const std::vector<double>* activity);
        bool empty() const { return heap.empty(); }
        bool contains(int v)  const { return pos[v] >= 0; }
        void insert(int v);

        // Notify the heap that a variable's activity has increased.
        void bumped(int v);

         // Remove and return the highest-activity variable.
        int  removeMax();

    private:
        // Return true or false dependent of if variable is ranked above variable b.
        bool higher(int a, int b) const { return (*act)[a] > (*act)[b]; }

        // Restore heap ordering by moving an item towards the root.
        void siftUp(int i);

        // Same as siftUp but moving items down the tree.
        void siftDown(int i);
    };

    // -----------------------------------------------------------------
    // Setup / problem initialisation.
    // -----------------------------------------------------------------

    // Reset all solver state for a new problem with numVars variables.
    void reset(int numVars);

    // Add an problem clause to the solver.
    bool addClause(Clause c);

    // Add a clause's watched literals to the watch lists.
    void attach(SolverClause* c);

    // Rebuild all watched-literal lists from the current clauses.
    void rebuildWatches();

    // -----------------------------------------------------------------
    // Core CDCL search.
    // -----------------------------------------------------------------

    Result search();

    // Perform Boolean constraint propagation.
    // Returns the conflicting clause, or nullptr when propagation succeeds.
    SolverClause* propagate();

     // Analyse a conflict and produce a learnt clause and backtrack level.
    void analyze(SolverClause* confl, Clause& out, int& btLevel, int& lbd);

    // Undo assignments until the specified decision level is reached.
    void backtrack(int level);

    int pickBranchVar();

    // -----------------------------------------------------------------
    // Assignment / trail helpers.
    // -----------------------------------------------------------------

    // Current number of active decision levels.
    int decisionLevel() const { return static_cast<int>(trailLim_.size()); }

     // Start a new decision level at the current end of the assignment trail.
    void newDecisionLevel() { trailLim_.push_back(static_cast<int>(trail_.size())); }

    // Assign a literal and record the clause that implied it.
    void enqueue(int lit, SolverClause* reason);

    // Return the current truth value of a literal.
    // The sign of lit determines whether the stored variable value is returned directly or inverted.
    int8_t value(int lit) const
    {
        const int8_t v = assign_[lit > 0 ? lit : -lit];
        return lit > 0 ? v : static_cast<int8_t>(-v);
    }

    // Convert a literal into the index used by the watched-literal array.
    static int watchIndex(int lit)
    {
        return lit > 0 ? (2 * lit) : (2 * (-lit) + 1);
    }

    // -----------------------------------------------------------------
    // Variable / clause activity heuristics.
    // -----------------------------------------------------------------

    // Increase the activity of a variable after it participates in a conflict.
    void bumpVar(int v);

    // Decay variable activities so newer conflicts have more influence.
    void decayVarActivity() { varInc_ /= config_.varDecay; }

    // Increase the activity of a learnt clause.
    void bumpClause(SolverClause& c);

    // Decay learnt-clause activities.
    void decayClauseActivity() { clauseInc_ /= 0.999; }

    // -----------------------------------------------------------------
    // Restart management.
    // -----------------------------------------------------------------

    // Check whether the solver has reached the next restart point.
    bool restartDue() const;

    // Update solver state after performing a restart.
    void onRestart();

    // Calculate the next restart limit for static restart policies.
    uint64_t staticRestartLimit() const;

    // Return the x-th value in the Luby restart sequence scaled by y.
    static double luby(double y, int x);

    // -----------------------------------------------------------------
    // Learnt-clause database management.
    // -----------------------------------------------------------------

    // Remove low-value learnt clauses from the database.
    void reduceDB();

    // Check whether a learnt clause is currently used as a reason.
    bool locked(const SolverClause* c) const;

    // Calculate the Literal Block Distance (LBD) of a clause.
    int computeLBD(const Clause& c);


    // Generate the next pseudo-random value.
    uint32_t nextRandom();

    //-----------------------------------------------------------------
    // State
    //-----------------------------------------------------------------

    Config config_;
    Stats stats_;

    int numVars_ = 0;
    bool unsat_ = false;

    // -----------------------------------------------------------------
    // Clause storage.
    // -----------------------------------------------------------------

    std::vector<std::unique_ptr<SolverClause>> problem_;
    std::vector<std::unique_ptr<SolverClause>> learnts_;

    // -----------------------------------------------------------------
    // Boolean propagation.
    // -----------------------------------------------------------------

    // Watched-literal lists used for efficient unit propagation.
    std::vector<std::vector<SolverClause*>> watches_;

    // Current assignment for each variable.
    std::vector<int8_t> assign_;

    // Decision level at which each variable was assigned.
    std::vector<int> level_;

    // Clause responsible for implying each variable's current assignment.
    std::vector<SolverClause*> reason_;

    // Saved/default phase for each variable.
    std::vector<char> phase_;

    // Current VSIDS-style activity score for each variable.
    std::vector<double> activity_;

    // Final satisfying assignment returned to the caller.
    std::vector<char> model_;

    // -----------------------------------------------------------------
    // Assignment trail.
    // -----------------------------------------------------------------

    // Variables/literals assigned in chronological order.
    std::vector<int> trail_;

    // Trail positions at which each decision level begins.
    std::vector<int> trailLim_;

    // Index of the next trail entry that still needs propagation.
    size_t qhead_ = 0;

    // -----------------------------------------------------------------
    // Conflict analysis scratch state.
    // -----------------------------------------------------------------
    
    // Marks variables that have already been visited during conflict analysis.
    std::vector<char> seen_;

    // Variables that need their temporary 'seen' state cleared.
    std::vector<int> toClear_;

    // Stamps used while calculating LBD scores.
    std::vector<int> lbdStamp_;

    // Current stamp/counter for LBD calculation.
    int lbdCounter_ = 0;

    // -----------------------------------------------------------------
    // Branching heuristic state.
    // -----------------------------------------------------------------

    // Heap containing variables ordered by activity.
    VarHeap order_;

    // Increment used when bumping variable activities.
    double varInc_ = 1.0;

    // Increment used when bumping learnt-clause activities.
    double clauseInc_ = 1.0;

    // -----------------------------------------------------------------
    // Restart bookkeeping.
    // -----------------------------------------------------------------

    // Number of conflicts since the most recent restart.
    uint64_t conflictsSinceRestart_ = 0;

    // Index/number of the next restart in the selected restart sequence.
    uint64_t restartIndex_ = 0;

    // Current conflict limit for geometric/static restarts.
    double geoLimit_ = 0.0;

    // Fast exponential moving average of recent learnt-clause LBD values.
    double emaFastLBD_ = 0.0;

    // Slow exponential moving average of learnt-clause LBD values.
    double emaSlowLBD_ = 0.0;

    // Exponential moving average of trail/assignment size.
    double emaTrail_ = 0.0;

    // Records the restart limit/conflict count associated with each restart.
    std::vector<uint64_t> restartLog_;

    // -----------------------------------------------------------------
    // Learnt-clause database bookkeeping.
    // -----------------------------------------------------------------
    int reduceLimit_ = 0;

    // -----------------------------------------------------------------
    // Random-number generator state.
    // -----------------------------------------------------------------
    uint64_t rngState_ = 0;
};

#endif // SATSOLVER_SATSOLVER_H