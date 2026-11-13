# M2 共享预算与调用状态设计

当前实现为离线执行骨架；真实 transport 不存在。部署迁移 `0003_shared_billing`、`0004_billing_guards`，原 41 张业务表以外增加两张平台表。

| 对象 | 职责 |
|---|---|
| billing.pools | 全 ScholarShip 研究账号/项目共用的活动预算池，唯一活动池、周期、上限、已花费、占用、真实调用开关和阻断标记 |
| billing.allocations | 调用与原业务 reservation 对应，持有者、池、模拟/真实标记、状态、报价快照、实际金额和回执摘要 |
| scholar.external_calls | 归属项目/步骤、供应商、幂等意图、请求哈希及安全回执 |
| scholar.budget_reservations / budget_accounts | 账号级预留及余额；与全局池同时准入 |
| scholar.usage_ledger / audit_events | 追加实际使用记录和状态审计 |

## 准入与恢复

`reserve_call` 先锁共享池再锁账号，原子检查有效 researcher、本人步骤、CNY 报价、周期、阻断、账号余额和共享余额。当前共享池 100 CNY/30 天，真实调用默认关闭；周期不会自动续期。reviewer 不能发起计费调用。

同一账号/项目/供应商的意图键只能对应同一请求、步骤、报价和模式；冲突拒绝。Python 金额向上取整到 0.000001 CNY。持久化状态为：

`held → sent → settled`，或 `sent → unknown → settled`；仅 `held → released` 可释放。发送前再次检查活动池、周期和开关。`sent/unknown` 不能由取消操作退还预留，不能盲目重发。

结算同事务转移预留为支出并追加 usage 与审计；同金额/回执重复结算不重复扣记，冲突回执拒绝。确认收到的实际费用高于预留时如实入账并阻断共享池，不抹掉真实费用。回执丢失先读取已持久化结果。禁用账号仍可完成历史调用结算或释放未发送预留。

## 权限与边界

运行角色只读池汇总，allocations 强制 owner RLS；撤销其对 external_calls、budget_reservations 和 usage_ledger 的直接写入，改用固定 search_path、撤销 PUBLIC EXECUTE 的具名数据库函数。事务仍依赖服务端可信设置 `app.owner_id`，不是针对泄露数据库凭据后可伪造会话上下文的隔离承诺。

所有将来的供应商任务、重试、OCR 调用必须接入该准入，不能通过状态页开关绕过。当前离线执行器仅绑定明确的向量、重排和问答适配操作；MinerU 价格未知，未提供真实 OCR 计费调度器。离线测试账目只进入独立测试库。

输入 Token 上界依赖未来业务规划器与供应商 tokenizer，当前保守字节限制不等于供应商计数。账务准入控制新请求并保存未知占额，**不承诺供应商最终账单绝对不超预算**。真实调用启用前还需校验计价规则、额度、并发和回执对账方式；异常成本必须停止后续准入并核对。
