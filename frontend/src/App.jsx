import React, { useEffect, useMemo, useState } from 'react'
import { api } from './api.js'
import LineageTable from './components/LineageTable.jsx'

const fmtTs = (v) => (v == null ? '—' : v)
const fmtNum = (v) => (v == null ? '—' : String(v))

function DiffCell({ correct, naive, leak }) {
  const differ = leak
  return (
    <div className={`cmp ${differ ? 'cmp-diff' : ''}`}>
      <span className="cmp-side cmp-correct" title="点时间正确结果">
        {correct}
      </span>
      <span className="cmp-arrow" aria-hidden>
        ⇄
      </span>
      <span className="cmp-side cmp-naive" title="全历史直接关联（错误基线）">
        {naive}
      </span>
    </div>
  )
}

export default function App() {
  const [samples, setSamples] = useState(null)
  const [error, setError] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [tab, setTab] = useState('payments')

  useEffect(() => {
    api
      .samples()
      .then((rows) => {
        setSamples(rows)
        if (rows.length) setSelectedId(rows[0].sample_id)
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!selectedId) return
    setDetail(null)
    api.sample(selectedId).then(setDetail).catch((e) => setError(e.message))
  }, [selectedId])

  const leakageCount = useMemo(
    () =>
      (samples || []).filter((s) => s.feature_leakage || s.label_leakage)
        .length,
    [samples],
  )

  if (error) {
    return (
      <div className="page">
        <div className="error-box">加载失败：{error}</div>
      </div>
    )
  }
  if (!samples) return <div className="page">加载中…</div>

  const openFeature = (id, kind) => {
    setSelectedId(id)
    setTab(kind)
  }

  return (
    <div className="page">
      <header className="hero">
        <h1>点时间特征预览 · 时间泄漏核查</h1>
        <p className="subtitle">
          特征仅使用预测时刻 <code>ingest_time ≤ t</code> 已可用的最新修订；
          特征窗口 <code>[t−7d, t)</code> 左含右不含；标签窗口{' '}
          <code>[t, t+7d)</code>，观察不足则标签留空（未成熟）。
          右侧浅色列为「把全部历史直接关联」的错误基线，用于并排比对。
        </p>
        <div className="summary-pills">
          <span className="pill">样本数 {samples.length}</span>
          <span className="pill pill-warn">
            存在泄漏分歧的样本 {leakageCount}
          </span>
        </div>
      </header>

      <div className="table-wrap">
        <table className="sample-table">
          <thead>
            <tr>
              <th rowSpan={2}>样本 / 用户</th>
              <th rowSpan={2}>场景</th>
              <th rowSpan={2}>预测时刻 t</th>
              <th rowSpan={2}>观察截止</th>
              <th colSpan={3}>特征（正确 ⇄ 错误基线）</th>
              <th rowSpan={2}>标签（正确 ⇄ 错误基线）</th>
            </tr>
            <tr>
              <th>近7天支付次数</th>
              <th>近7天支付金额</th>
              <th>最近支付时间</th>
            </tr>
          </thead>
          <tbody>
            {samples.map((s) => {
              const selected = s.sample_id === selectedId
              return (
                <tr
                  key={s.sample_id}
                  className={selected ? 'row-selected' : ''}
                  onClick={() => setSelectedId(s.sample_id)}
                >
                  <td>
                    <strong>{s.sample_id}</strong>
                    <br />
                    <span className="muted">{s.user_id}</span>
                  </td>
                  <td className="scenario">{s.scenario}</td>
                  <td className="mono">{fmtTs(s.prediction_time)}</td>
                  <td className="mono">{fmtTs(s.observation_cutoff)}</td>
                  <td>
                    <button
                      className="cell-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        openFeature(s.sample_id, 'payments')
                      }}
                    >
                      <DiffCell
                        correct={fmtNum(s.pay_count_7d)}
                        naive={fmtNum(s.naive_pay_count_7d)}
                        leak={s.feature_leakage}
                      />
                    </button>
                  </td>
                  <td>
                    <button
                      className="cell-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        openFeature(s.sample_id, 'payments')
                      }}
                    >
                      <DiffCell
                        correct={fmtNum(s.pay_amount_7d)}
                        naive={fmtNum(s.naive_pay_amount_7d)}
                        leak={s.feature_leakage}
                      />
                    </button>
                  </td>
                  <td>
                    <button
                      className="cell-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        openFeature(s.sample_id, 'payments')
                      }}
                    >
                      <DiffCell
                        correct={fmtTs(s.last_pay_time)}
                        naive={fmtTs(s.naive_last_pay_time)}
                        leak={s.feature_leakage}
                      />
                    </button>
                  </td>
                  <td>
                    <button
                      className="cell-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        openFeature(s.sample_id, 'orders')
                      }}
                    >
                      <LabelCell s={s} />
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {detail ? (
        <DetailPanel detail={detail} tab={tab} setTab={setTab} />
      ) : (
        <div className="detail-empty">正在载入样本明细…</div>
      )}

      <footer className="foot">
        数据由 DuckDB 批量计算并导出 Parquet（<code>data/parquet/</code>）；
        每行记录的纳入 / 排除判定见下钻面板。
      </footer>
    </div>
  )
}

function LabelCell({ s }) {
  const correct = s.label_mature ? fmtNum(s.label_order_7d) : '未成熟（空）'
  const naive = fmtNum(s.naive_label_order_7d)
  return (
    <div className={`cmp ${s.label_leakage ? 'cmp-diff' : ''}`}>
      <span
        className={`cmp-side cmp-correct ${
          s.label_mature ? '' : 'cmp-immature'
        }`}
        title={s.label_mature ? '点时间正确标签' : '观察范围未覆盖完整标签窗口'}
      >
        {correct}
      </span>
      <span className="cmp-arrow" aria-hidden>
        ⇄
      </span>
      <span className="cmp-side cmp-naive" title="全历史直接关联（错误基线）">
        {naive}
      </span>
    </div>
  )
}

function DetailPanel({ detail, tab, setTab }) {
  const { sample, payment_lineage, order_lineage, reasons } = detail
  return (
    <section className="detail">
      <h2>
        样本 {sample.sample_id}（{sample.user_id}）下钻
        <span className="muted normal"> — {sample.scenario}</span>
      </h2>

      <div className="window-bar">
        <div>
          <span className="muted">预测时刻 t：</span>
          <code>{sample.prediction_time}</code>
        </div>
        <div>
          <span className="muted">特征窗口：</span>
          <code>[t−7d, t) 左含右不含</code>
        </div>
        <div>
          <span className="muted">观察截止：</span>
          <code>{sample.observation_cutoff}</code>
          {!sample.label_mature && (
            <span className="tag tag-immature">标签未成熟</span>
          )}
        </div>
      </div>

      <div className="tabs">
        <button
          className={tab === 'payments' ? 'tab tab-active' : 'tab'}
          onClick={() => setTab('payments')}
        >
          支付记录（特征依据 · {payment_lineage.length} 个修订版本）
        </button>
        <button
          className={tab === 'orders' ? 'tab tab-active' : 'tab'}
          onClick={() => setTab('orders')}
        >
          订单记录（标签依据 · {order_lineage.length} 个修订版本）
        </button>
      </div>

      {tab === 'payments' ? (
        <LineageTable
          rows={payment_lineage}
          kind="payment"
          reasons={reasons.payment}
        />
      ) : (
        <LineageTable
          rows={order_lineage}
          kind="order"
          reasons={reasons.order}
        />
      )}
    </section>
  )
}
