记录训练、实验与 benchmark
===========================

:doc:`English </tutorials/experiments>`

这篇教程使用合成指标。你会记录两次训练，查询实验结果，再把它们关联到
benchmark 矩阵。

安装并建立工作区
----------------

使用 Python 3.11 或更新版本和 Bash。在检出的
`expnote 仓库 <https://github.com/JaimeParker/expnote>`_ 中，创建虚拟环境并安装 CLI。

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install -e .

在同一个 shell 中运行后续命令。示例把配置和数据放在临时目录里。工作区保存
SQLite 数据库和审计日志。MOC（内容索引）组织相关主题，每个主题收录围绕
同一个实验问题的训练记录。

.. literalinclude:: ../../examples/experiments.sh
   :language: bash
   :start-after: # start workspace
   :end-before: # end workspace

记录并查询训练
--------------

为每次训练选一个稳定的 ID。如果使用了外部实验跟踪器，可以沿用它的 run ID。
训练开始时写入目的和参数。评估结束后，在 Result 中写入主要指标及其评估范围，
在 Analysis 中写观察。PPO 记录里的双方括号链接可以在 Web UI 中打开 SAC 记录。

.. literalinclude:: ../../examples/experiments.sh
   :language: bash
   :start-after: # start runs
   :end-before: # end runs

查询返回两条在 ``Pendulum-v1`` 上完成的训练。用
``expnote run show sac00001 --workspace training-demo --json`` 查看完整的 SAC 记录。

建立 benchmark 矩阵
-------------------

矩阵的行是任务，列是算法。每个单元格关联一条状态为 finished 或 failed 的
训练记录。给同一单元格关联另一条记录会更新链接。每个 seed 各留一条训练
记录。需要汇总多个 seed 时，用 ``expnote doc add`` 建立分析文档，再用
``expnote doc link`` 关联各条记录。

.. literalinclude:: ../../examples/experiments.sh
   :language: bash
   :start-after: # start benchmark
   :end-before: # end benchmark

Pendulum 行显示 ``sac00001`` 和 ``ppo00001``。MountainCarContinuous 行为空，
表示这些比较还待完成。

在 Web UI 中查看
----------------

.. code-block:: bash

   expnote web --workspace training-demo --no-open

打开命令打印的 localhost 地址，进入 **Control experiments** MOC，查看某条记录的
参数、Result 和 Analysis。在 **All benchmarks** 中打开 **Control reproduction**，
点击有记录的单元格即可跳转。完成后在终端按 **Ctrl+C**。

完整的单条记录示例和自动化用法见 :doc:`/templates/training-record-example`
和 :doc:`/agent-quickstart` （英文）。接下来可以用 :doc:`research` 管理一组
实验背后的研究问题。
