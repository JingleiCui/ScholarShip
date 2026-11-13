import { useEffect, useState } from "react";

type Doc = {id: string; title: string; active_index_id: string | null; active_parse_id: string | null};
type Citation = {id: string; quote: string; page: number; version_id: string; parse_id: string};
type Result = {synthetic: boolean; answer?: string; limitations?: string; insufficient_evidence?: boolean; citations?: Citation[]};
type Run = {id: string; status: string; result_json?: Result; error_json?: {code: string}};
type Located = {text: string; quote: string; start: number; end: number; page: number; bbox: number[]; original_pdf_available: boolean; version_id: string};
async function request(path: string, body?: unknown) {
  const csrf = document.cookie.split("; ").find(x=>x.startsWith("ss_csrf="))?.split("=")[1] ?? "";
  const r = await fetch("/api/v1"+path, body === undefined ? undefined : {
    method: "POST", headers: {"Content-Type":"application/json","X-CSRF-Token":csrf,"Idempotency-Key":crypto.randomUUID()}, body:JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`请求失败 ${r.status}：${(await r.text()).slice(0,160)}`);
  return r.json();
}
export default function OfflineResearch({project,userId,role}:{project:string;userId?:string;role?:string}) {
  const [docs,setDocs]=useState<Doc[]>([]), [selected,setSelected]=useState<string[]>([]);
  const [question,setQuestion]=useState("Compare Alpha Beta accuracy");
  const [run,setRun]=useState<Run|null>(null), [error,setError]=useState(""),[sending,setSending]=useState(false);
  const [located,setLocated]=useState<Located|null>(null);
  const enabled=!!userId && !!project && role==="researcher";
  const busy=sending || (!!run && ["queued","running"].includes(run.status));
  useEffect(()=>{
    let cancelled=false;
    setDocs([]);setSelected([]);setRun(null);setError("");setLocated(null);
    if (project && userId) void Promise.all([
      request(`/offline/projects/${project}/documents`), request(`/offline/projects/${project}/runs`)
    ]).then(([d, history]:[Doc[],Run[]])=>{
      if (!cancelled) {setDocs(d);setSelected(d.filter(x=>x.active_index_id).map(x=>x.id));setRun(history[0]??null);}
    }).catch(e=>{if(!cancelled)setError(String(e));});
    return ()=>{cancelled=true;};
  },[project,userId]);
  useEffect(()=>{
    if (!run || !["queued","running"].includes(run.status)) return;
    let cancelled=false;
    const t=setInterval(()=>{
      void request(`/runs/${run.id}`).then(async (r:Run)=>{
        if(cancelled)return;
        if(r.status==="succeeded") {
          const d:Doc[]=await request(`/offline/projects/${project}/documents`);
          if(!cancelled) {setDocs(d);setSelected(old=>old.length?old:d.filter(x=>x.active_index_id).map(x=>x.id));}
        }
        if(!cancelled) setRun(r);
      }).catch(e=>{if(!cancelled)setError(String(e));});
    },1000);
    return ()=>{cancelled=true;clearInterval(t);};
  },[run?.id,run?.status,project]);
  async function start(build:boolean) {
    setSending(true);setError("");setLocated(null);
    try {setRun(await request(`/offline/projects/${project}/${build?"build":"questions"}`,build?{}:{question,version_ids:selected}));}
    catch(e){setError(String(e));} finally {setSending(false);}
  }
  const labels:Record<string,string>={queued:"等待后台处理",running:"正在执行离线流程",succeeded:"已完成 · 合成数据",failed:"任务失败",waiting_user:"需核对未知回执"};
  return <section className="task-panel offline-research">
    <div className="section-top"><h2>离线证据问答演示</h2><span className="outline-tag">合成数据 · 不调用真实模型</span></div>
    <p>使用两份固定合成文档，检查解析版本、混合检索、问答持久化和引用定位。这里的数字与回答不代表科研结论。</p>
    <button className="primary" disabled={!enabled||busy} onClick={()=>void start(true)}>{busy?"任务处理中…":"准备合成文档与索引"}</button>
    {!enabled && <p>请使用研究账号登录并选择项目；评审账号可查看本人空间的已有结果。</p>}
    <fieldset disabled={busy} className="offline-sources"><legend>限定文献范围</legend>
      {docs.length===0?<p>尚无合成索引，请先准备文档。</p>:docs.map(d=><label key={d.id}>
        <input type="checkbox" checked={selected.includes(d.id)} disabled={!d.active_index_id} onChange={e=>setSelected(old=>e.target.checked?[...old,d.id]:old.filter(x=>x!==d.id))}/>
        {d.title}<small>{d.active_index_id?`就绪 · 世代 ${d.active_index_id.slice(0,8)}`:"待构建"}</small>
      </label>)}
    </fieldset>
    <form onSubmit={e=>{e.preventDefault();void start(false);}}>
      <label htmlFor="offline-question">问题（演示按词语重叠选证据）</label>
      <textarea id="offline-question" value={question} maxLength={1000} onChange={e=>setQuestion(e.target.value)} rows={2}/>
      <button className="primary" disabled={!enabled||busy||!selected.length||!question.trim()}>运行离线问答</button>
    </form>
    {run && <div role="status" className="run-heading"><strong>{labels[run.status]??run.status}</strong><code>{run.id.slice(0,8)}</code></div>}
    {run?.error_json && <p className="error">{run.error_json.code}</p>}
    {run?.result_json?.answer && <div className="offline-answer"><h3>{run.result_json.insufficient_evidence?"证据不足":"合成回答"}</h3>
      <p style={{whiteSpace:"pre-wrap"}}>{run.result_json.answer}</p><small>{run.result_json.limitations}</small>
      <div className="offline-citations">{run.result_json.citations?.map((c,i)=><button key={c.id} className="text-button" onClick={()=>{
        void request(`/offline/evidence/${c.id}`).then(setLocated).catch(e=>setError(String(e)));
      }}>证据 {i+1} · 第 {c.page} 页 ↗</button>)}</div>
    </div>}
    {located && <aside className="offline-evidence" aria-label="证据原文定位"><h3>合成原文 · 第 {located.page} 页</h3>
      <p>{Array.from(located.text).slice(0,located.start).join("")}<mark>{Array.from(located.text).slice(located.start,located.end).join("")}</mark>{Array.from(located.text).slice(located.end).join("")}</p>
      <small>版面坐标（pt）：{located.bbox.join(", ")}；引文区间按 Unicode 码点记录。</small>
      {located.original_pdf_available && <a href={`/api/v1/paper-versions/${located.version_id}/file#page=${located.page}`} target="_blank" rel="noreferrer">打开原始 PDF 对应页</a>}
    </aside>}
    {error && <div className="error" role="alert">{error}</div>}
  </section>;
}
