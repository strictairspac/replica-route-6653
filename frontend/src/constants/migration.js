export const MIGRATION_PHASES={preparing:'准备中',copying:'存量拷贝',catching_up:'增量追赶',cutover_blocked:'切流被拒',cutover:'切流中',completed:'迁移完成',failed:'迁移失败'};
export const MIGRATION_DECISIONS={applied:'已应用',routed:'已路由',skipped:'跳过',rejected:'已拒绝'};
export const MIGRATION_KIND_HINTS={write:'写入',read:'读取',copy_snapshot:'存量拷贝',stream:'增量复制',cutover:'切流',target_down:'目标故障',target_up:'目标恢复',source_down:'源故障',source_up:'源恢复'};
