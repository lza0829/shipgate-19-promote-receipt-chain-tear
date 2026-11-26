import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Apps() {
  const [rows, setRows] = useState([])
  const [name, setName] = useState('')
  const [owner, setOwner] = useState('')
  const [err, setErr] = useState('')
  const load = () => api('/应用').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const create = async () => {
    setErr('')
    try {
      await api('/应用', { method: 'POST', body: JSON.stringify({ 名称: name, 负责人: owner, 状态: '启用' }) })
      setName(''); setOwner(''); load()
    } catch (e) { setErr(e.message) }
  }
  const chain = rows[0]?.环境链 || ['测', '预', '产']
  return (
    <div className="panel">
      <h2>应用名册</h2>
      {err && <p className="err">{err}</p>}
      <p className="hint">名册按环境链 {chain.join(' → ')} 列出各环境版本戳；跨环落产只改真实落地那一列。</p>
      <div className="row">
        <label>名称<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>负责人<input value={owner} onChange={(e) => setOwner(e.target.value)} /></label>
        <button type="button" onClick={create}>新建应用</button>
        <button type="button" className="secondary" onClick={load}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>编号</th><th>名称</th><th>负责人</th><th>状态</th>
              {chain.map((c) => <th key={c}>{c} 版本</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}>
                <td>{r.编号}</td><td>{r.名称}</td><td>{r.负责人}</td><td>{r.状态}</td>
                {chain.map((c) => <td key={c}>{r[c] || '-'}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
