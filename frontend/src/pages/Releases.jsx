import React, { useEffect, useState } from 'react'
import { api } from '../api'

function Detail({ detail }) {
  if (!detail) return null
  return (
    <div className="detail">
      <div className="detail-head">
        <b>{detail.发版单号}</b> · {detail.应用} · {detail.目标版本}
        <span className={`tag ${detail.状态 === '已放行' ? 'ok' : ''}`}>{detail.状态}</span>
        <span>当前环境 {detail.当前环境 || '未落地'}</span>
        <span>下一环 {detail.下一环 || '已到链尾'}</span>
        {detail.落地环境 && <span>落地环境 {detail.落地环境}</span>}
        {detail.最近跨环 ? <span className="tag cross">最近晋升跨环</span> : null}
      </div>
      <div className="split">
        <div>
          <h3>环境名册</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>环境</th><th>版本</th><th>更新时间</th></tr></thead>
              <tbody>
                {(detail.名册 || []).map((m) => (
                  <tr key={m.环境} className={m.当前 ? 'current' : ''}>
                    <td>{m.环境}{m.当前 ? ' · 当前' : ''}</td>
                    <td>{m.版本 || '-'}</td>
                    <td>{m.更新时间 || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h3>晋升事件</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>序号</th><th>起→止</th><th>决定</th><th>跨环</th><th>拒因码</th><th>时间</th></tr></thead>
              <tbody>
                {(detail.晋升事件 || []).map((e) => (
                  <tr key={e.编号}>
                    <td>{e.序号}</td>
                    <td>{e.起环境 || '未落地'}→{e.止环境 || '-'}</td>
                    <td>{e.决定}</td>
                    <td>{e.跨环 ? <span className="tag cross">跨环{e.跳过环境 ? ` · 跳过${e.跳过环境}` : ''}</span> : '-'}</td>
                    <td>{e.拒因码 || '-'}</td>
                    <td>{e.时间}</td>
                  </tr>
                ))}
                {(detail.晋升事件 || []).length === 0 && <tr><td colSpan="6">尚无晋升事件</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

const surface_panel = true
export default function Releases() {
  const [rows, setRows] = useState([])
  const [apps, setApps] = useState([])
  const [appId, setAppId] = useState('')
  const [ver, setVer] = useState('')
  const [digest, setDigest] = useState('')
  const [err, setErr] = useState('')
  const [openId, setOpenId] = useState(null)
  const [detail, setDetail] = useState(null)

  const load = async () => {
    setRows(await api('/发版单'))
    const a = await api('/应用')
    setApps(a)
    if (!appId && a[0]) setAppId(String(a[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])

  useEffect(() => {
    if (!openId) { setDetail(null); return }
    api(`/发版单/${openId}`).then(setDetail).catch((e) => setErr(e.message))
  }, [openId, rows])

  const create = async () => {
    setErr('')
    try {
      await api('/发版单', {
        method: 'POST',
        body: JSON.stringify({ 应用编号: Number(appId), 目标版本: ver, 制品摘要: digest }),
      })
      setVer(''); setDigest(''); load()
    } catch (e) { setErr(e.message) }
  }
  const pipeline = async (id, status) => {
    setErr('')
    try {
      await api(`/发版单/${id}/流水线`, { method: 'POST', body: JSON.stringify({ 流水线: status }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  const admit = async (id) => {
    setErr('')
    try {
      await api(`/发版单/${id}/入闸`, { method: 'POST', body: '{}' })
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>发版单</h2>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>应用
          <select value={appId} onChange={(e) => setAppId(e.target.value)}>
            {apps.map((a) => <option key={a.编号} value={a.编号}>{a.名称}</option>)}
          </select>
        </label>
        <label>目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} placeholder="1.4.0" /></label>
        <label>制品摘要<input value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="sha256:..." /></label>
        <button type="button" onClick={create}>申请入闸</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>流水线</th><th>当前环境</th><th>下一环</th><th>操作</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={openId === r.编号 ? 'current' : ''}>
                <td><button type="button" className="link" onClick={() => setOpenId(r.编号 === openId ? null : r.编号)}>{r.发版单号}</button></td>
                <td>{r.应用}</td><td>{r.目标版本}</td><td>{r.状态}</td><td>{r.流水线}</td>
                <td>{r.当前环境 || '-'}</td><td>{r.下一环 || '-'}</td>
                <td>
                  <button type="button" className="secondary" onClick={() => pipeline(r.编号, '绿')}>流水线绿</button>{' '}
                  <button type="button" className="danger" onClick={() => pipeline(r.编号, '红')}>流水线红</button>{' '}
                  <button type="button" onClick={() => admit(r.编号)}>入闸</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Detail detail={detail} />
    </div>
  )
}
