import {useEffect,useState} from "react";
type ModelRole={provider:string;model:string;credential_required:boolean;credential_present:boolean;verification_state:string;dimensions:number|null;dimensions_state:string};
type Configuration={selected_preset:string;available_presets:string[];roles:Record<string,ModelRole>;fingerprint:string;requires_reindex:boolean;bounded_validations?:{state:string;max_requests:number;used_requests:number;stop_code:string|null}[]};
async function read(path:string,init?:RequestInit){
 const r=await fetch('/api/v1'+path,init);
 if(!r.ok)throw new Error(`模型配置请求失败 ${r.status}`);
 return r.json();
}
export default function ModelConfiguration({project,role}:{project:string;role?:string}){
 const [state,setState]=useState<Configuration|null>(null),[preset,setPreset]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[note,setNote]=useState('');
 useEffect(()=>{let stop=false;setState(null);setError('');setNote('');
  if(project)void read(`/projects/${project}/model-configuration`).then((s:Configuration)=>{if(!stop){setState(s);setPreset(s.selected_preset);}}).catch(e=>{if(!stop)setError(String(e));});
  return()=>{stop=true;};
 },[project]);
 async function save(){if(!state)return;setBusy(true);setError('');setNote('');
  const csrf=document.cookie.split('; ').find(x=>x.startsWith('ss_csrf='))?.split('=')[1]??'';
  try{const result=await read(`/projects/${project}/model-configuration`,{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({preset,expected_fingerprint:state.fingerprint})});
   setState(await read(`/projects/${project}/model-configuration`));setNote(result.requires_reindex?'配置已保存。向量索引需重建，当前活动索引保留；未启用推理。':'配置已保存，仅影响后续任务；未启用推理。');
  }catch(e){setError(String(e));}finally{setBusy(false);}
 }
 const names:Record<string,string>={chat:'聊天 / 证据问答 / 摘要',translation:'翻译',embedding:'向量化',rerank:'重排',parser:'PDF / OCR'};
 const stopReasons:Record<string,string>={chatanywhere_preflight_403:'历史额度查询返回 403；不代表聊天接口不可用。',provider_transport_outcome_unknown:'连接或回执异常，结果未知；已停止，未自动重发。',rate_limited:'免费接口返回 429 限流；已停止，未自动重试。'};
 return <section className="task-panel" style={{marginBottom:24}}>
  <div className="section-top"><h2>统一模型配置</h2><span className="outline-tag">常规推理关闭 · 独立联调限额</span></div>
  <p>配置与实际可用性分开显示。填写 Key 不代表权限或免费额度已验证；合成演示固定使用 mock。</p>
  {!project?<p>登录并选择项目后查看。</p>:state?<>
   <div className="probe-controls"><select aria-label="模型配置组" value={preset} disabled={busy} onChange={e=>setPreset(e.target.value)}>{state.available_presets.map(p=><option key={p} value={p}>{p}</option>)}</select><button className="primary" disabled={busy||role!=="researcher"} onClick={()=>void save()}>{busy?'保存中…':'保存配置组'}</button></div>
   {Object.entries(state.roles).map(([role,m])=><div className="service" key={role} style={{marginTop:10,overflowWrap:'anywhere'}}><div><strong>{names[role]??role}</strong><small>{m.provider} · {m.model}</small><small>{m.credential_required?(m.credential_present?'Key 已填写':'Key 未填写'):'无需云端 Key'} · {m.verification_state==='synthetic_only'?'仅合成验证':'未验证服务、模型权限或安装状态'}{role==='embedding'?` · 维度：${m.dimensions??'待首个有效响应确认'}`:''}</small></div></div>)}
   {state.bounded_validations?.map((v,i)=><p role="status" key={i}>受控接口授权批次：{{active:"待执行",stopped:"已停止",completed:"已完成",expired:"已过期"}[v.state]??v.state} · 已占用名额 {v.used_requests}/{v.max_requests} · {v.stop_code?(stopReasons[v.stop_code]??v.stop_code):"只允许已授权的合成文本范围。"}</p>)}
   {state.requires_reindex&&<p>更换向量模型后需重建索引，完成前保留原索引。</p>}
  </>:<p>正在读取配置…</p>}
  {note&&<p role="status">{note}</p>}{error&&<p role="alert" className="error">{error}</p>}
 </section>;
}
