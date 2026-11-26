import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api'

const modeLabel = (it) => {
  if (it.跨环) return `跨环 ${(it.跳过环境 || []).join('→')}`
  return it.方式 || '可晋升'
}

export default function Promote() {
  const [rows, setRows] = useState([])
  const [rid, setRid] = useState('')
  const [policy, setPolicy] = useState(null)
  const [cand, setCand] = useState(null)
  const [candErr, setCandErr] = useState('')
  const [env, setEnv] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = async (keep = true) => {
    const all = await api('/发版单')
    const active = all.filter((r) => ['已入闸', '晋升中'].includes(r.状态))
    setRows(active)
    setPolicy(await api('/晋升/策略'))
    if (!keep || !rid) {
      const next = active[0] ? String(active[0].编号) : ''
      setRid(next)
    }
  }

  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])

  useEffect(() => {
    if (!rid) { setCand(null); return }
    setCandErr('')
    api(`/发版单/${rid}/晋升候选`)
      .then((c) => {
        setCand(c)
        const allowed = c.可晋升 || []
        // 默认选中下一环；没有可晋升目标时清空，避免拿旧值硬点。
        setEnv((cur) => (allowed.some((x) => x.环境 === cur) ? cur : (allowed[0]?.环境 || '')))
      })
      .catch((e) => setCandErr(e.message))
  }, [rid])

  const selected = useMemo(
    () => (cand?.可晋升 || []).find((x) => x.环境 === env) || null,
    [cand, env],
  )

  const go = async () => {
    setErr(''); setMsg('')
    if (!selected) { setErr('当前没有可晋升目标（见右侧拒因）'); return }
    try {
      const res = await api(`/发版单/${rid}/晋升`, {
        method: 'POST',
        body: JSON.stringify({ 目标环境: env }),
      })
      setMsg(
        `已${res.晋升.方式}晋升 ${res.晋升.起环境 || '未落地'}→${res.晋升.止环境}，` +
        `事件序号 ${res.晋升.序号}，单状态 ${res.状态}`,
      )
      await load()
      setRid(String(res.编号))
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>环境晋升</h2>
      {err && <p className="err">{err}</p>}
      {msg && <p className="ok">{msg}</p>}
      {policy && (
        <p className="hint">
          晋升策略：<b>{policy.取值}</b>。{policy.说明}
          （跨环条件：{policy.跨环条件.join('、')}）
        </p>
      )}
      <div className="row">
        <label>发版单
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {rows.map((r) => (
              <option key={r.编号} value={r.编号}>
                {r.发版单号} · {r.应用} · 当前 {r.当前环境 || '未落地'}
                {r.下一环 ? ` · 下一环 ${r.下一环}` : ''}
              </option>
            ))}
          </select>
        </label>
        <label>目标环境
          <select value={env} onChange={(e) => setEnv(e.target.value)} disabled={!selected}>
            {(cand?.可晋升 || []).map((x) => (
              <option key={x.环境} value={x.环境}>
                {x.环境}（{modeLabel(x)}）
              </option>
            ))}
            {!selected && <option value="">无可晋升目标</option>}
          </select>
        </label>
        <button type="button" onClick={go} disabled={!selected}>晋升</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      {candErr && <p className="err">{candErr}</p>}
      {cand && (
        <div className="split">
          <div>
            <h3>可晋升（与写入侧同规则）</h3>
            <div className="table-wrap">
              <table>
                <thead><tr><th>目标</th><th>方式</th><th>跳过</th></tr></thead>
                <tbody>
                  {(cand.可晋升 || []).map((x) => (
                    <tr key={x.环境}>
                      <td>{x.环境}</td>
                      <td>{x.跨环 ? <span className="tag cross">跨环</span> : <span className="tag">逐级</span>}</td>
                      <td>{(x.跳过环境 || []).join('、') || '-'}</td>
                    </tr>
                  ))}
                  {(cand.可晋升 || []).length === 0 && <tr><td colSpan="3">无</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <h3>不可晋升 · 拒因</h3>
            <div className="table-wrap">
              <table>
                <thead><tr><th>目标</th><th>拒因码</th><th>说明</th></tr></thead>
                <tbody>
                  {(cand.不可晋升 || []).map((x) => (
                    <tr key={x.环境}><td>{x.环境}</td><td>{x.拒因码}</td><td>{x.说明}</td></tr>
                  ))}
                  {(cand.不可晋升 || []).length === 0 && <tr><td colSpan="3">无</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead><tr><th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>当前环境</th><th>下一环</th><th>最近跨环</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.目标版本}</td><td>{r.状态}</td>
                <td>{r.当前环境 || '-'}</td><td>{r.下一环 || '-'}</td>
                <td>{r.最近跨环 ? <span className="tag cross">跨环</span> : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
