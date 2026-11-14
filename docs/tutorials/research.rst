Run a research project
======================

Use expnote Research to define a comparison, record a decision, and take the
result through review. The example uses two short, synthetic loss traces. You'll
use the CLI for proposals and the Research Web UI for the review steps.

Set up a research project
-------------------------

Use Python 3.11 or later and Bash. From a checkout of the
`expnote repository <https://github.com/JaimeParker/expnote>`_, install the
Research and plotting extras in a virtual environment.

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install -e '.[research,charts]'

Run the rest of the CLI blocks in the same shell. The vault holds authored
plans, execution plans, evidence, and result notes as Markdown. The linked
workspace holds run records in SQLite. Research keeps its decision database
and index outside the vault and exports a decision backup to ``ops/decisions.jsonl``.

.. literalinclude:: ../examples/research.sh
   :language: bash
   :start-after: # start project
   :end-before: # end project

Start the Web UI in the background using the same shell. Open
``http://localhost:8765/research/`` and select **Warmup study**. Use an alternate
``--port`` and update the URL if 8765 is already in use.

.. code-block:: bash

   expnote web --workspace warmup-runs --detach --no-open

Propose and approve a plan
--------------------------

The plan defines the question, the two arms, and the criterion used to read
the results. ``new plan`` first previews the write. Exit status 4 means a
write is pending. The second command applies it. The shell block fills in
the note body and preserves the generated frontmatter.

.. literalinclude:: ../examples/research.sh
   :language: bash
   :start-after: # start plan
   :end-before: # end plan

Open the **Approve the warmup study** decision under **Waiting on you** in the
Research UI. Read the plan and select **Approve**, then confirm the preview.
The decision is ``WARMUP-DEC-0001`` in this new project.

Freeze the execution plan
-------------------------

An execution plan spells out what to run and records the rules used to judge the
result. Here the execution plan repeats the approved criterion, so the example
uses ``--self-approve --no-new-rules``. If you add a new criterion in a real study,
get the user's decision on the new rules and freeze with ``--rules-decision``.
The blocks use ``--no-quiet`` immediately after edits in the disposable vault
so you can run the steps consecutively.

.. literalinclude:: ../examples/research.sh
   :language: bash
   :start-after: # start execution
   :end-before: # end execution

Register runs and prepare evidence
----------------------------------

Register one run per arm in the linked SQLite workspace. Python plots the
synthetic values to a PNG inside the vault. The evidence note presents the
figure and the two peak values. Its appendix gives the run IDs, source
values, and checks.

.. literalinclude:: ../examples/research.sh
   :language: bash
   :start-after: # start runs
   :end-before: # end runs

.. literalinclude:: ../examples/research.sh
   :language: bash
   :start-after: # start evidence
   :end-before: # end evidence

Open **Peak loss** in the Research UI. Inspect the figure and enter ``1.20``
for **No warmup** and ``0.67`` for **Linear warmup** in **Value**. Select
**Save and mark labelled**, then confirm the preview. The evidence status
becomes ``labelled``. If the UI reports a quiet window after a file edit,
wait for the indicated interval and retry.

Submit and close the study
--------------------------

Write the result around the pre-registered criterion. Cite the evidence and
state the scope of the finding. Submitting the result moves it to
``first-read`` and advances the plan to ``read-out`` once its gates pass.

.. literalinclude:: ../examples/research.sh
   :language: bash
   :start-after: # start result
   :end-before: # end result

Open the result from **Waiting on you**. Check the cited evidence, select
**Pass review**, and confirm the preview. The result becomes ``confirmed``.

.. literalinclude:: ../examples/research.sh
   :language: bash
   :start-after: # start close
   :end-before: # end close

Open the plan in Research and use **Close plan** to record the closure reason,
review the preview and confirm. Run ``expnote check`` once more. The project
should report zero failures and the plan should show ``closed``. When done, stop the background Web UI with ``kill PID``, using the process
ID printed at startup.

For project profiles, deviations, and backup, see :doc:`/research-vault`.
For the CLI workflow that agents can consult, run ``expnote guide research``.
