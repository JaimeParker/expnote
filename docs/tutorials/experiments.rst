Record training runs and benchmarks
===================================

Record two training runs, query their results, and link them to a benchmark
matrix. This example uses synthetic metrics and takes a few minutes.

Install and set up a workspace
------------------------------

Use Python 3.11 or later and a Bash shell. From a checkout of the
`expnote repository <https://github.com/JaimeParker/expnote>`_, create a virtual
environment and install the CLI.

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install -e .

Run the remaining blocks in the same shell. They use a temporary configuration
and data directory. A workspace stores the SQLite database and the audit log.
A MOC (map of content) groups related topics. A topic groups runs that answer
the same experimental question.

.. literalinclude:: ../examples/experiments.sh
   :language: bash
   :start-after: # start workspace
   :end-before: # end workspace

Record and query training runs
------------------------------

Give each run a stable ID. An external tracker ID works well when you use one.
Record the purpose and parameters when training starts. After evaluation, put
the headline metric and evaluation scope in Result, and your observations in
Analysis. The square-bracket link in the PPO run opens the SAC run in the Web UI.

.. literalinclude:: ../examples/experiments.sh
   :language: bash
   :start-after: # start runs
   :end-before: # end runs

The query returns two finished runs for ``Pendulum-v1``. Add
``expnote run show sac00001 --workspace training-demo --json`` to inspect the
complete SAC record.

Build the benchmark matrix
--------------------------

A benchmark has tasks as rows and algorithms as columns. Link a finished or
failed run to each populated cell. Each cell holds one run and linking another
run to that cell replaces the link. Keep individual seeds as separate run
records. For a summary across seeds, use ``expnote doc add`` and link the runs
with ``expnote doc link``.

.. literalinclude:: ../examples/experiments.sh
   :language: bash
   :start-after: # start benchmark
   :end-before: # end benchmark

The Pendulum row shows ``sac00001`` and ``ppo00001``. The MountainCarContinuous
row is empty, so the matrix also shows which comparisons are still pending.

Inspect the records in the Web UI
---------------------------------

.. code-block:: bash

   expnote web --workspace training-demo --no-open

Open the localhost URL the command prints. Browse the **Control experiments**
MOC and open a run to inspect its metadata, Result, and Analysis. In **All
benchmarks**, open **Control reproduction** and select a populated cell to
jump to its run. Press **Ctrl+C** in the terminal when you finish.

For a larger record and tips for automation, see
:doc:`/templates/training-record-example` and :doc:`/agent-quickstart`. To manage
the research that produced a set of runs, continue with :doc:`research`.
