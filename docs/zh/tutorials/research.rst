用 Research 开展科研
====================

这篇教程用两条简短的合成损失曲线，走完定义比较、记录决策、整理证据和审阅
结论的流程。用 CLI 提交草案，用 Research Web UI 完成审批和审阅。

建立研究项目
------------

使用 Python 3.11 或更新版本和 Bash。在检出的
`expnote 仓库 <https://github.com/JaimeParker/expnote>`_ 中，创建虚拟环境并安装
Research 和绘图依赖。

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install -e '.[research,charts]'

后续 CLI 命令在同一个 shell 中运行。vault 保存你编写的计划、执行计划、证据
和结论 Markdown 笔记。关联的工作区用 SQLite 保存训练记录。Research 的决策
数据库和索引位于 vault 外，并把决策备份导出到 ``ops/decisions.jsonl``。

.. literalinclude:: ../../examples/research.sh
   :language: bash
   :start-after: # start project
   :end-before: # end project

在同一个 shell 中启动后台 Web UI，打开
``http://localhost:8765/research/`` 并选择 **Warmup study**。
如果 8765 已被占用，可以用 ``--port`` 指定另一个端口，并修改浏览器地址。

.. code-block:: bash

   expnote web --workspace warmup-runs --detach --no-open

提出并审批计划
--------------

在计划里写清研究问题、两个实验组和判定标准。``new plan`` 先预览写入，
退出码 4 表示命令等待应用。第二条命令实际写入。随后填充笔记正文，保留自动生成
的 frontmatter。

.. literalinclude:: ../../examples/research.sh
   :language: bash
   :start-after: # start plan
   :end-before: # end plan

在 Research 的 **Waiting on you** 中打开 **Approve the warmup study**。
阅读计划，点击 **Approve** 并确认预览。在新项目里，这条决策编号为
``WARMUP-DEC-0001``。

冻结执行计划
------------

执行计划写明要运行的步骤和判定规则。本例沿用获批计划中的标准，因此使用
``--self-approve --no-new-rules``。如果实际研究增加了判定标准，请由用户
确认新规则，再通过 ``--rules-decision`` 冻结执行计划。示例在修改临时 vault
后使用 ``--no-quiet``，方便连续执行。

.. literalinclude:: ../../examples/research.sh
   :language: bash
   :start-after: # start execution
   :end-before: # end execution

登记训练并整理证据
------------------

在关联的 SQLite 工作区为每个实验组登记一条 run。用 Python 把合成数值绘制
成 PNG，保存在 vault 中。证据笔记展示图和两个峰值，附录记录 run ID、原始
数值和检查结果。

.. literalinclude:: ../../examples/research.sh
   :language: bash
   :start-after: # start runs
   :end-before: # end runs

.. literalinclude:: ../../examples/research.sh
   :language: bash
   :start-after: # start evidence
   :end-before: # end evidence

在 Research 中打开 **Peak loss**。查看图，在 **Value** 列分别为 **No warmup**
和 **Linear warmup** 填入 ``1.20`` 和 ``0.67``。点击 **Save and mark labelled**
并确认预览。证据状态变为 ``labelled``。如果界面提示文件刚被修改，按提示等待后重试。

提交结论并结项
--------------

按预先确定的标准写结论，引用证据并写清适用范围。提交后结论进入
``first-read``。满足阶段检查时，计划进入 ``read-out``。

.. literalinclude:: ../../examples/research.sh
   :language: bash
   :start-after: # start result
   :end-before: # end result

从 **Waiting on you** 打开结论，核对引用的证据。点击 **Pass review** 并确认
预览，结论状态变为 ``confirmed``。

.. literalinclude:: ../../examples/research.sh
   :language: bash
   :start-after: # start close
   :end-before: # end close

在 Research 中打开计划，用 **Close plan** 填写结项理由，核对预览并确认。
再次运行 ``expnote check``。项目应报告零项失败，计划状态为 ``closed``。
完成后，用 ``kill PID`` 停止后台 Web UI。PID 使用启动命令打印的进程号。

项目配置、偏差记录和备份见 :doc:`/research-vault` （英文）。
给 agent 的 CLI 流程可用 ``expnote guide research`` 查看。
