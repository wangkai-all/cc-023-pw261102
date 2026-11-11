import React, { useMemo, useState } from 'react'

const FILTERS = [
  { key: 'all', label: '全部记录' },
  { key: 'included', label: '仅纳入' },
  { key: 'excluded', label: '仅排除' },
  { key: 'diverge', label: '仅正确/错误判定不一致' },
]

/**
 * Per-record lineage table. Every revision of every event is shown with:
 *  - whether the point-in-time-correct logic includes it, and why
 *  - what the naive "join all history" logic would do with the same row
 * Rows where the two disagree are highlighted as leakage evidence.
 */
export default function LineageTable({ rows, kind, reasons }) {
  const [filter, setFilter] = useState('all')

  const filtered = useMemo(() => {
    switch (filter) {
      case 'included':
        return rows.filter((r) => r.correct_included)
      case 'excluded':
        return rows.filter((r) => !r.correct_included)
      case 'diverge':
        return rows.filter(
          (r) => r.correct_included !== r.naive_included,
        )
      default:
        return rows
    }
  }, [rows, filter])

  const isPayment = kind === 'payment'

  return (
    <div className="lineage">
      <div className="filter-bar">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={filter === f.key ? 'chip chip-active' : 'chip'}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
        <span className="muted small">
          共 {rows.length} 行，当前显示 {filtered.length} 行
        </span>
      </div>

      <table className="lineage-table">
        <thead>
          <tr>
            <th>事件编号</th>
            <th>修订版本</th>
            <th>业务发生时间</th>
            <th>系统可用时间</th>
            <th>状态</th>
            {isPayment && <th>金额</th>}
            <th>正确逻辑判定（原因）</th>
            <th>错误基线判定</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((r) => {
            const diverges = r.correct_included !== r.naive_included
            return (
              <tr
                key={`${r.event_id}-${r.version}`}
                className={diverges ? 'row-diverge' : ''}
              >
                <td className="mono">{r.event_id}</td>
                <td>v{r.version}</td>
                <td className="mono">{r.event_time}</td>
                <td className="mono">{r.ingest_time}</td>
                <td>
                  <span
                    className={`tag ${
                      r.status === 'ACTIVE' || r.status === 'PLACED'
                        ? 'tag-ok'
                        : 'tag-bad'
                    }`}
                  >
                    {r.status}
                  </span>
                </td>
                {isPayment && <td className="mono">{r.amount}</td>}
                <td>
                  <DecisionBadge included={r.correct_included} />
                  <div className="reason">{reasons[r.correct_decision]}</div>
                </td>
                <td>
                  <DecisionBadge included={r.naive_included} naive />
                  <div className="reason muted">
                    {r.naive_decision === r.correct_decision
                      ? '与正确逻辑一致'
                      : reasons[r.naive_decision]}
                  </div>
                </td>
              </tr>
            )
          })}
          {filtered.length === 0 && (
            <tr>
              <td colSpan={isPayment ? 8 : 7} className="muted center">
                没有符合筛选条件的记录
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <p className="legend">
        高亮行 = 同一记录在正确逻辑与「全历史直接关联」基线下判定不同，
        即时间泄漏 / 修订倒灌发生的位置。
      </p>
    </div>
  )
}

function DecisionBadge({ included, naive = false }) {
  const cls = included
    ? naive
      ? 'badge badge-naive-in'
      : 'badge badge-ok'
    : naive
      ? 'badge badge-naive-out'
      : 'badge badge-out'
  return (
    <span className={cls}>
      {included ? '纳入计算' : '排除'}
      {naive && '（基线）'}
    </span>
  )
}
