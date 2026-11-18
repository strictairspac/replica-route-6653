# 站点读流量路由演练

栖原站点运维组（11人）在交接班前，用节点位点与会话时序复核读写路由，明确何时读副本、何时回主库、何时必须拒绝读取；并按键范围在线迁移分片，验证搬迁不丢写、旧拷贝不覆盖新写、迟到写有明确去向。

## 场景与模块

原先值班员在配置表和聊天记录中核对副本延迟，以“延迟很低”推断写后读安全。痛点：低延迟不等于已应用会话写位点、故障时回退策略不清、决策无逐请求证据。上游输入为人工确认的节点状态和请求轨迹，本站承接路由规则演练与证据留档，下游为站点值班与变更复核人员。

三个核心实体：复制节点 Node（主副角色/在线状态/日志谱系/位点/延迟），会话请求集 RequestSet（带时序的读写、复制应用、故障恢复事件），路由试验 Trial（冻结输入、策略参数与结果）。4业务页+登录页，另有管理员审计。

**v1.1 新增按键范围在线迁移**：MigrationPlan（键范围、两端分片、初始数据、搬迁时序脚本）与 Migration（冻结快照与逐事件证据）。新增「搬迁脚本 / 搬迁执行 / 搬迁证据」三页，与原有路由试验并列、互不影响。

**这是受控复制路由模型，不连接真实主从集群。** MySQL真实保存节点、请求、任务及证据；Python真实逐事件计算策略。日志位点采用命名谱系中的单调整数，未实现完整GTID集合/真实复制协议。

## 快速启动

```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

前端 http://localhost:18247 ，后端 http://localhost:19247/docs ，MySQL localhost:17747。

|角色|账号|密码|权限|
|---|---|---|---|
|admin|admin@qiyuan.test|Demo2026!|全部业务写入、运行与审计|
|operator|operator@qiyuan.test|Demo2026!|节点/请求编辑、运行、导出|
|viewer|viewer@qiyuan.test|Demo2026!|只读和导出，写API403|

## 一天的工作剧本

09:00 operator登录；09:05查看harbor-primary位点105、north-replica位点104/150ms、south-replica位点105/20ms；09:10打开 `QY-1117-A` 请求集；09:15确认八事件时序；09:20以100ms延迟门槛和500ms粘滞执行 `shift-20261117`；09:25检查：写入主库位点106、100ms读仍走主库、600ms南站追平106、700ms读走南站、800ms南站故障、900ms回主库、1000ms主库故障、1100ms拒绝读取；09:30导出route-1.json；09:40 viewer核验拒绝理由；10:00 admin看审计。

另有 `QY-1117-B` 高延迟副本回退主库、`QY-1117-C` 主库故障且副本未追平的拒绝案例。所有试验使用冻结快照，不会把模型故障写回节点台账。

## 真实计算规则

输入上限12节点、100事件；时间0–86400000ms，事件按at_ms非递减，时间相同按输入顺序执行。必须有且仅一个主节点，同谱系副本初始位点不能超过主库。事件字段：

```json
[
  {"at_ms":0,"kind":"write","session":"lab-A"},
  {"at_ms":100,"kind":"read","session":"lab-A"},
  {"at_ms":600,"kind":"replicate","node":"south-replica","position":106,"delay_ms":20},
  {"at_ms":700,"kind":"read","session":"lab-A"}
]
```

- write：主库在线才成功；主库位点+1，记录该会话required_position和last_write_ms。失败写入不推进会话位点。
- read：若距最近成功写入 `< sticky_ms`，必须主库在线、同谱系且位点满足；否则拒绝。到达边界即结束粘滞。
- 粘滞期外：副本必须在线、同谱系、position≥会话required_position、delay_ms≤max_delay_ms，四条件同时成立。多个候选按延迟升序、位点降序、名称字典序选择。无副本达标则回主库；主库也失效则拒绝。
- replicate：只允许在线同谱系副本；位点不能回退、不能超过主库已提交位点。node_down/node_up修改模型状态。未知节点或非法复制在入队前422。
- 每个会话独立维护位点；从未成功写入的会话required=0，允许最终一致副本读取，不承诺全局强一致。低延迟绝不覆盖落后位点。

输出每事件目标/拒绝、会话所需位点、实际位点、各副本四项资格、最终节点、会话状态、汇总和SHA256。相同输入产生相同摘要；不依赖随机数或外部网络。拒绝不安全读是正常业务结果，不算任务失败。快照与结果保存在MySQL，Redis只传递任务ID。

## 按键范围在线迁移规则（v1.1）

搬迁脚本声明键范围 `key_start..key_end`、源/目标分片、两端初始键值与最多100个时序事件。事件按 `at_ms` 非递减，同一时刻按输入顺序执行，用来表达**存量拷贝、增量复制与业务写入的并发交错**。事件类型：

- `write` / `read`：请求带键，write 带 value；`route_hint=old` 表示切流后仍走旧路由表的迟到写。
- `copy_snapshot`：存量拷贝批次（key/value/version），只写目标分片。
- `stream`：增量复制包（key/value/version），与拷贝、写入可交错。
- `cutover`：路由归属切换；`source_down/up`、`target_down/up`：分片故障与恢复。

**版本闸（防覆盖、防重复）**：每个键维护单调整数版本，任何复制包仅当 `incoming_version > 目标本地版本` 才应用。因此迟到的旧存量拷贝不会覆盖目标已有的新写；同版本重复投递（故障恢复重试）幂等跳过，数据不重复应用。

**切流闸门（未补齐拒绝切流）**：cutover 时目标必须在线，且对源分片每一个已接受的范围键，目标版本不低于源已提交版本（版本相等时值须一致；目标版本更高代表已接管新写，允许）。不满足则拒绝切流，证据逐键列出缺口原因，归属保留源分片；补齐后可再次 cutover。

**迟到写去向**：切流后范围键归属目标。旧路由迟到写在源分片仍可达时由源转发目标落新版本；源或目标不可达时**明确拒绝并给出原因**，绝不回写旧主形成双主，也不静默丢弃。

**故障与重试**：目标故障窗内的复制包不应用、不丢版本语义，目标恢复后重发；版本闸保证重试只生效一次。搬迁中源分片持续接写，写入产生新版本并进入追赶清单。最终证据对每个源已提交键做无丢写校验（`no_data_loss`/`data_loss_keys`），并输出两端内容、键归属、每次拒绝原因和 SHA256。

**可用性代价**：搬迁期间需要双份存储与复制带宽；切流前源分片不可用会拒绝范围写（不向未接管目标盲写）；目标故障窗口复制与新路由写被拒；切流后需要源分片保持短暂可达以转发旧路由迟到写，源下线后的迟到写只能拒绝。拒绝不安全操作是预期安全结果，不是任务失败。

种子含三个脚本：`MG-1117-A` 全链路（并发交错/目标故障/拒绝切流/重试幂等/旧包不覆盖新写/迟到写转发与拒绝）、`MG-1117-B` 未补齐拒绝切流、`MG-1117-C` 故障恢复重试幂等。后端测试：`python -m tests.test_migration` 与 `python -m tests.test_migration_integration`；前端冒烟：`node --test frontend/tests/*.test.js`。

## 工程说明

|服务|技术|职责|
|---|---|---|
|frontend|原生HTML/CSS/JS + Nginx|路由4页+迁移3页、编辑表单、共享组件、/api代理|
|backend|Python3.11/FastAPI/SQLAlchemy/PyJWT|校验、权限、审计、快照与导出|
|worker|Python3.11|串行消费路由队列与搬迁队列，逐事件重放两套确定性算法|
|db|MySQL8.0|真实持久化业务和JSON证据|
|redis|Redis7|持久队列（路由/搬迁两条）|

各实体贯穿 `backend/src/{models,schemas,services,routes}` 和 `frontend/src/{api,stores,pages}`；其余强制目录含controllers/middlewares/config/constants/utils、components/common/hooks/types/router。三组件table/badge/frame共用；hooks/session/feedback和utils/forms/html共用；request_id/errors双中间件。

共享枚举角色admin/operator/viewer与任务queued/running/completed/failed位于 `backend/src/constants/enums.py` / `frontend/src/constants/enums.js`，security/trials、frame/badge消费。节点角色primary/replica和状态healthy/down由node schema及前端表单定义，事件类型由request_set schema及路由算法定义。

环境变量见.env.example：COMPOSE_PROJECT_NAME、三端口、DB_PASSWORD、JWT_SECRET、SEED_DEMO。默认密码仅本地演示、JWT8小时；seed自然键仅补缺失，不覆盖修改。schema由metadata.create_all初始建表，无在线升级引擎。5服务均健康检查，backend/worker依赖健康db/redis，frontend依赖backend；数据库、Redis、worker产物使用命名卷。

本地开发：Python3.11安装backend/requirements.txt、配置DATABASE_URL/REDIS_URL，在backend运行 `uvicorn src.main:app --reload` 和 `python -m src.worker`，前端经Nginx同源代理。停止清空：`docker compose down -v --remove-orphans`。

限制：单worker串行、无生产任务重试/崩溃续跑；不模拟网络随机抖动、自动选主或完整GTID集合。Google Fonts离线回退，业务不依赖字体网络。

## License

MIT。源码外 `../../test-artifacts/gb-4108/` 含真实测试、截图与结果。
