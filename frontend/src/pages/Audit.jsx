import React, { useEffect, useState } from 'react'
import { api } from '../api'

const KINDS = ['', '晋升', '发版单', '回滚', '应用', '放行窗', '回滚窗', '设置']
const ENVS = ['', '测', '预', '产']

export default function Audit() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [kind, setKind] = useState('晋升')
  const [env, setEnv] = useState('')
  const [refId, setRefId] = useState('')
  const [kw, setKw] = useState('')

  const load = () => {
    const q = new URLSearchParams()
    if (kind) q.set('类别', kind)
    if (env) q.set('环境', env)
    if (refId) q.set('关联编号', refId)
    if (kw) q.set('关键字', kw)
    api(`/审计?${q.toString()}`).then(setRows).catch((e) => setErr(e.message))
  }
  useEffect(() => { load() }, [])

  return (
    <div className="panel">
      <h2>审计流水</h2>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>类别
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{k || '全部'}</option>)}
          </select>
        </label>
        <label>目标环境
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            {ENVS.map((v) => <option key={v} value={v}>{v || '全部'}</option>)}
          </select>
        </label>
        <label>关联编号<input value={refId} onChange={(e) => setRefId(e.target.value)} placeholder="发版单编号" /></label>
        <label>关键字<input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="动作/备注" /></label>
        <button type="button" onClick={load}>查询</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>类别</th><th>动作</th><th>目标环境</th><th>关联</th><th>备注</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}>
                <td>{r.时间}</td><td>{r.类别}</td><td>{r.动作}</td>
                <td>{r.环境 || '-'}</td><td>{r.关联编号 || '-'}</td><td>{r.备注}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan="6">无匹配记录</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
