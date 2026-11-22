import { useEffect, useState } from 'react';
import { api } from './researchApi';
import { cloudAvailable, localAvailable, type ExecutionConfiguration, type ExternalState } from './execution';
export default function useExecution(project: string) {
  const [configuration, setConfiguration] = useState<ExecutionConfiguration | null>(null);
  const [external, setExternal] = useState<ExternalState | null>(null);
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    let stopped = false;
    setConfiguration(null);
    setExternal(null);
    setChecked(false);
    if (!project) return;
    async function load() {
      try {
        const [c, e] = await Promise.all([
          api(`/projects/${project}/model-configuration`),
          api('/system/external-services'),
        ]);
        if (!stopped) {
          setConfiguration(c);
          setExternal(e);
          setChecked(true);
        }
      } catch {
        if (!stopped) {
          setConfiguration(null);
          setExternal(null);
          setChecked(true);
        }
      }
    }
    void load();
    const timer = setInterval(() => void load(), 30000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [project]);
  return {
    checked,
    configuration,
    localAvailable: (roles: string[]) => localAvailable(configuration, roles),
    available: (roles: string[]) => cloudAvailable(configuration, external, roles),
    notice: checked
      ? '本地生成需模型和服务就绪；MinerU 使用已授权云端解析。云端生成仍需独立额度准入。'
      : '正在核对执行条件，可先阅读原文。',
  };
}
