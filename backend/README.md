# 后端说明

后端启动说明请查看项目根目录 README.md。`app/main.py` 提供 FastAPI 入口，默认 Mock 模式读取 `app/data/mock_tracks.json`。

在 PyCharm 终端中进入 `backend` 并激活 `.venv` 后启动：

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

API 文档：http://127.0.0.1:8001/docs。前端 `frontend/.env.local` 应设置 `VITE_API_BASE_URL=http://127.0.0.1:8001/api`。

## 实际工作流程

1. `recommendation_service.py` 为请求创建独立 `request_id`，读取手动偏好、最新反馈和带来源的学习画像，准备最多 3 首已喜欢／收藏歌曲作为种子。
2. `planner.py` 将本次文本、明确筛选、长期偏好、种子、目标数量及预算发送给 LLM。模型返回 `RetrievalPlan`：软偏好 `intent`、不可放宽条件 `hard_constraints`、简短策略、初始调用列表和可选澄清问题。显式筛选覆盖模型解析，语言在意图与硬条件中保持一致。
3. `tool_executor.py` 校验并执行白名单工具，同轮调用并发。相同工具及参数会去重，单纯提高 limit 不能绕过重复检查。未知种子和不合法调用不能执行。
4. `retrieval.py` 合并候选，按歌名和艺人去重，复用数据库标签与反馈；优先补充语言查询来源的缺标签歌曲。按语言标签、排除艺人／标签／歌曲、必须标签进行硬过滤，再按本次匹配、反馈和多样性排序。
5. 如果合格歌曲数量不足且预算允许，模型收到每轮原始数、去重数、合格数、过滤原因、工具结果、执行过的调用和剩余预算，返回 `RetrievalDecision`：`search` 加新调用，或 `finish`。此响应不能修改既定硬条件。
6. 达到目标、模型停止、无新查询、预算耗尽或检索超时时结束。检索时间耗尽时保留已经返回的候选，包括同一轮中先完成的工具结果；最终只返回经过硬过滤的歌曲，数量不足会提示。整个 API 超时则返回 504。
7. 正常结果写入歌曲与推荐历史，返回计划、逐轮轨迹及评估。澄清响应不执行音乐检索、不写推荐历史。

例如：用户选择华语，首轮取得 30 首歌曲但都没有华语标签，观测为 `raw_count=30, eligible_count=0`。LLM 可以下一轮选择 `mandopop` 等标签检索；后端仍按实际返回标签过滤，不会因候选来自某个查询就认定满足语言要求。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `app/agent/agent.py` | 保留 API 到推荐服务的入口 |
| `app/agent/planner.py` | 调用 LLM、验证结构化响应、应用显式条件、生成规则回退 |
| `app/agent/prompts.py` | 描述数据源能力、规划与迭代规则 |
| `app/schemas/agent.py` | 计划、决策、硬条件、工具参数的 Pydantic 契约 |
| `app/agent/tool_executor.py` | 白名单派发、种子校验、单次超时及调用日志 |
| `app/agent/retrieval.py` | 有界循环、候选合并、硬过滤、补充详情及评估 |
| `app/agent/state.py` | 本次请求的候选、轮次、调用和停止原因 |
| `app/agent/telemetry.py` | 请求上下文、JSONL 轮转日志和密钥脱敏 |
| `app/services/recommendation_service.py` | 读取偏好、组织流程、持久化、返回诊断信息 |
| `app/services/ranking_service.py` | 基于已取得证据的程序化评分及多样性排序 |
| `app/services/learning_service.py` | 从最新反馈和歌曲元数据重建长期画像 |
| `app/tools/music_tools.py` / `app/clients/lastfm_client.py` | 音乐数据访问与标准化 |
| `scripts/evaluate_agent.py` | 离线流程评估和运行日志汇总 |

旧 `intent_service.py` 的确定性解析仍供规则回退使用；主链路由 Planner 负责结构化规划。当前实现通过 OpenAI 兼容 `/chat/completions` 的 JSON 输出实现工具计划协议，由后端执行；没有使用供应商原生 `tool_calls` 协议，也没有引入新的 Agent 框架。最终排序仍由代码完成。

## 模型可选择的工具

| 工具 | 参数（另有 limit，1–50，默认 30） | 数据源能力 |
| --- | --- | --- |
| `top_tracks_by_tag` | `tag` | 单个 Last.fm 社区标签，不能做 AND 查询 |
| `search_tracks` | `query` | 明确歌名搜索，不是整段需求的语义搜索 |
| `similar_tracks` | `seed_id` | 仅引用提供的真实种子，后端查找艺人／歌名 |
| `chart_tracks` | 无其他参数 | 热门候选，仍需硬过滤 |

模型不能调用任意 Python 方法、URL 或数据库操作。LLM 返回未知字段、非法工具、无效 JSON、拒答或截断时进入规则回退；本次请求随后不再重试 LLM，下一请求会重新尝试。迭代时回退会继续执行尚未使用的规则查询。

## 配置与预算

`MOCK_MODE` 仅控制音乐源；`LLM_MOCK_MODE` 控制是否禁用真实模型。启用模型需要 `LLM_MOCK_MODE=false` 以及完整的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。两种开关独立。未配置或调用失败都会有回退日志和响应提示。配置读取规则与主 README 相同；修改 `.env` 后重启后端。

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `AGENT_MAX_ROUNDS` | 3 | 总轮数，包含首轮，允许 1–3 |
| `AGENT_CALLS_PER_ROUND` | 4 | 单轮检索调用上限，允许 1–8 |
| `AGENT_MAX_TOOL_CALLS` | 12 | 全请求检索调用预算 |
| `AGENT_TOOL_TIMEOUT_SECONDS` | 10 | 单检索调用／详情批次超时 |
| `AGENT_LLM_TIMEOUT_SECONDS` | 8 | 每次规划／决策调用超时 |
| `AGENT_RETRIEVAL_TIMEOUT_SECONDS` | 32 | 首次规划之后的整个检索循环，含后续 LLM 决策 |
| `AGENT_ENRICHMENT_LIMIT` | 30 | 全请求可尝试补充详情的歌曲数，独立于检索调用预算 |
| `AGENT_LOG_ENABLED` | true | 写入文件日志 |
| `AGENT_LOG_RAW_REQUESTS` | false | 是否记录原始请求文本 |
| `AGENT_LOG_DIR` | backend/logs 的绝对路径 | 日志目录，可覆盖 |

默认最多 1 次初始规划 + 2 次后续决策，若早停则更少。初始规划最多 8 秒、随后检索最多 32 秒，API 外层默认 45 秒，前端推荐请求 50 秒。修改预算时注意外层超时仍优先。旧 `LLM_TIMEOUT_SECONDS` 不控制新 Planner。

## 响应与日志

`POST /api/recommend` 保留原字段，增加：

- `retrieval_plan`：最终解析后的计划及初始工具调用。
- `agent_trace`：每个完整轮次的统计与工具结果摘要。
- `evaluation`：全流程统计、停止原因、耗时、检索调用与详情预算。
- `needs_clarification` / `clarification_questions`：需要用户说明的条件；此时 `status=needs_clarification`，歌曲为空。问题也通过已有 warnings 展示，当前需编辑条件后重新提交，尚无多轮澄清会话。
- `request_id`：关联推荐历史与日志。`parser_mode=llm` 表示首轮模型规划成功；后续模型可能回退，应同时查看 warnings 和 `llm_call` 事件。

`logs/agent.jsonl` 每行一条 JSON，包含 UTC 时间、请求 ID、事件名和相对耗时；单文件 5 MB，保留 3 份轮转备份。事件包括 `request`、`plan`、`llm_call`／`llm_skipped`、`tool_call`、`tool_rejected`、`enrichment`、`retrieval_round`、`decision`、`evaluation`、`completed`／`failed`／`cancelled`。错误只记录异常类型，不写供应商原始异常或带密钥的请求地址。

默认关闭原文记录，并移除事件值中逐字重复的请求及已配置密钥。但结构化偏好、标签和查询仍可能体现用户兴趣，这不是完全匿名化。数据库推荐历史仍按原有设计保留请求文本。日志写入失败不会中断推荐。

在 backend 终端查看：

```powershell
Get-Content .\logs\agent.jsonl -Tail 30
Get-Content .\logs\agent.jsonl | Select-String -SimpleMatch '替换为响应中的request_id'
```

## 评估与验证

每请求评估包括候选原始／去重／合格／返回数量、过滤原因计数、填充率、艺人数、与上一批重合数、平均本次匹配维度数、硬条件违规数、轮数、工具错误数及停止原因。过滤原因可以同时命中多个，因此计数之和不等于被过滤歌曲数。`hard_constraint_violations=0` 只表示代码根据现有标签规则检查通过，不证明歌词或音频真实满足条件。

`tool_calls` 计入已调度的检索调用；`completed_tool_calls` 包含成功和报错结束的调用；`unfinished_tool_calls` 表示因整体截止／中断未完成的调用。`detail_candidates_attempted` 是被分配详情预算的候选数，不保证全部实际发出 HTTP。详情批次记录补全后有／无标签的数量；`completed` 仅表示批次返回，不表示每首歌曲详情查询都成功。

从 backend 运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe scripts/evaluate_agent.py --output logs/evaluation-offline.json
.\.venv\Scripts\python.exe scripts/evaluate_agent.py --logs logs/agent.jsonl --output logs/evaluation-runtime.json
```

第三条需要先产生运行日志；可向 `--logs` 传入多个不同轮转文件。汇总包含请求完成率、检索达到目标率、平均填充率、停止原因、硬违规数、终态请求 P95 延迟、LLM 调用失败回退率、跳过调用数、供应商报告的 Token 总数及缺失用量次数。澄清请求不计入填充率与达到目标率；HTTP 成功返回少量或零首歌曲仍计为请求完成，不能将完成率视为推荐准确率。Token 缺失不表示免费；仅有日志开头而缺少终态的请求不会计入终态延迟。

离线脚本使用固定规划器与模拟工具，覆盖华语／日语重试、纯音乐证据和重复调用停止。单元测试另用模拟 HTTP 验证真实 Planner 请求／响应契约。它们不调用真实供应商，不衡量真实 LLM 理解能力或用户满意度。在线评估应在同一需求集合、相同预算下对比规则基线和 LLM 方案，结合人工相关性判断、语言核验、反馈、耗时与 Token 成本。
