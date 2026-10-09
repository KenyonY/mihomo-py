import { useEffect, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Subscription = { name: string; selected: boolean; source: string; updated_at: string };
type Snapshot = {
  subscriptions: Subscription[];
  status: { running: boolean; healthy: boolean; selected: string | null; running_subscription: string | null };
};
type Edit = { name: string; source: string; existing: boolean };
const API = "/mihomo-py/api";
const KEY = "mihomo-py/secret";
const primary = "bg-emerald-400 text-slate-950 hover:bg-emerald-300";
const secondary = "border border-slate-700 hover:bg-slate-800";

function App() {
  const [secret, setSecret] = useState(localStorage.getItem(KEY) || "");
  const [password, setPassword] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [view, setView] = useState("subscriptions");

  async function request(path: string, method = "GET", body?: unknown, token = secret) {
    const response = await fetch(API + path, {
      method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store",
    });
    const value = await response.json();
    if (!response.ok) {
      if (response.status === 401) { localStorage.removeItem(KEY); setSecret(""); setSnapshot(null); }
      throw new Error(value.message + (value.suggestion ? ` ${value.suggestion}` : ""));
    }
    return value;
  }

  async function refresh(token = secret) { setSnapshot(await request("/subscriptions", "GET", undefined, token)); }
  async function operate(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (cause) { setError(cause instanceof Error ? cause.message : "请求失败，请重试。"); }
    finally { setBusy(false); }
  }
  useEffect(() => { if (secret) void operate(() => refresh()); }, []);

  async function login(event: FormEvent) {
    event.preventDefault();
    await operate(async () => {
      await refresh(password);
      localStorage.setItem(KEY, password); setSecret(password); setPassword("");
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault(); if (!edit) return;
    await operate(async () => {
      await request(edit.existing ? `/subscriptions/${encodeURIComponent(edit.name)}` : "/subscriptions",
        edit.existing ? "PATCH" : "POST", edit.existing ? { source: edit.source } : { name: edit.name, source: edit.source });
      setEdit(null); await refresh(); setNotice("订阅已保存并完成配置校验。");
    });
  }

  async function action(name: string, operation: "use" | "update") {
    await operate(async () => {
      await request(`/subscriptions/${encodeURIComponent(name)}/${operation}`, "POST");
      await refresh(); setNotice(operation === "use" ? `已选择「${name}」。` : `「${name}」已更新。`);
    });
  }

  if (!snapshot) return <main className="mx-auto flex min-h-screen max-w-md items-center px-6">
    <form onSubmit={login} className="w-full space-y-6 rounded-2xl border border-slate-800 bg-slate-900/70 p-8">
      <div><p className="text-sm text-emerald-400">mihomo-py</p><h1 className="mt-2 text-2xl font-semibold">订阅与节点管理</h1></div>
      <p className="text-sm leading-6 text-slate-400">使用管理 API 的登录密钥。登录后可添加、更新和切换订阅。</p>
      <label>登录密钥<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
      {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
      <button className={primary + " w-full"} disabled={busy}>{busy ? "正在登录…" : "登录"}</button>
    </form>
  </main>;

  const status = snapshot.status;
  return <div>
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 px-5 py-4 sm:px-8">
      <div className="flex items-center gap-6"><span className="font-semibold tracking-tight">mihomo-py</span>
        <nav className="flex gap-2">
          <button className={view === "subscriptions" ? primary : secondary} onClick={() => setView("subscriptions")}>订阅管理</button>
          <button className={view === "nodes" ? primary : secondary} onClick={() => setView("nodes")} disabled={!status.running}>节点面板</button>
        </nav>
      </div>
      <button className="text-slate-400 hover:text-slate-100" onClick={() => {
        localStorage.removeItem(KEY); localStorage.removeItem("setup/api-list");
        localStorage.removeItem("setup/active-uuid"); setSecret(""); setSnapshot(null); setView("subscriptions");
      }}>退出登录</button>
    </header>
    {view === "nodes" ? <iframe title="节点面板" src="/ui/" className="block h-[calc(100dvh-80px)] w-full border-0" /> :
      <main className="mx-auto max-w-5xl space-y-6 px-5 py-8 sm:px-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div><h1 className="text-2xl font-semibold">订阅管理</h1><p className="mt-2 text-sm text-slate-400">{snapshot.subscriptions.length} 个订阅 · 与 TUI / CLI 共用配置</p></div>
          <div className="flex gap-2"><button className={secondary} disabled={busy} onClick={() => void operate(() => refresh())}>刷新</button>
            <button className={primary} disabled={busy} onClick={() => { setError(""); setEdit({ name: "", source: "", existing: false }); }}>添加订阅</button></div>
        </div>
        <section className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-5">
          <div className="text-sm"><span className={status.running ? "text-emerald-300" : "text-slate-400"}>{status.running ? "● 内核运行中" : "○ 内核已停止"}</span>
            <span className="ml-4 text-slate-400">{status.running_subscription ? `正在使用：${status.running_subscription}` : "选择订阅后即可启动"}</span></div>
          <button className={secondary} disabled={busy || (!status.running && !status.selected)} onClick={() => void operate(async () => {
            await request(status.running ? "/core/stop" : "/core/start", "POST"); await refresh();
          })}>{status.running ? "停止内核" : "启动内核"}</button>
        </section>
        {busy && <p role="status" className="text-sm text-emerald-300">正在处理，完成后会刷新列表…</p>}
        {error && <p role="alert" className="rounded-lg border border-rose-900 bg-rose-950/40 p-4 text-sm text-rose-200">{error}</p>}
        {notice && <p role="status" className="text-sm text-emerald-300">{notice}</p>}
        {snapshot.subscriptions.length === 0 && <section className="rounded-xl border border-dashed border-slate-700 py-16 text-center text-slate-400">暂无订阅，添加一个订阅地址即可开始。</section>}
        <div className="space-y-3">{snapshot.subscriptions.map(sub => <article key={sub.name} className="rounded-xl border border-slate-800 bg-slate-900/60 p-5" aria-label={`订阅 ${sub.name}`}>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="min-w-0"><div className="flex items-center gap-3"><h2 className="break-all font-medium">{sub.name}</h2>
              {sub.selected && <span className="rounded-full bg-emerald-400/10 px-2 py-1 text-xs text-emerald-300">已选中</span>}</div>
              <p className="mt-2 break-all text-sm text-slate-400">{sub.source}</p><p className="mt-1 text-xs text-slate-500">更新于 {new Date(sub.updated_at).toLocaleString()}</p></div>
            <div className="flex flex-wrap gap-2">
              <button className={secondary} disabled={busy || sub.selected} onClick={() => void action(sub.name, "use")}>切换</button>
              <button className={secondary} disabled={busy} onClick={() => void action(sub.name, "update")}>更新</button>
              <button className={secondary} disabled={busy} onClick={() => void operate(async () => {
                const result = await request(`/subscriptions/${encodeURIComponent(sub.name)}/source`);
                setEdit({ name: sub.name, source: result.source, existing: true });
              })}>修改来源</button>
              <button className="text-rose-300 hover:bg-rose-950" disabled={busy || sub.name === status.running_subscription} onClick={() => setRemoving(sub.name)}>删除</button>
            </div>
          </div>
        </article>)}</div>
        <p className="text-xs leading-5 text-slate-500">运行中切换或更新配置会重启内核。当前正在使用的订阅需先切换或停止内核才能删除。</p>
      </main>}
    {(edit || removing) && <div className="fixed inset-0 z-10 flex items-center justify-center overflow-y-auto bg-black/70 p-5">
      <section role="dialog" aria-modal="true" aria-label={edit ? (edit.existing ? "修改订阅来源" : "添加订阅") : "删除订阅"} className="w-full max-w-lg space-y-5 rounded-2xl border border-slate-700 bg-slate-900 p-6">
        {edit ? <form onSubmit={save} className="space-y-5">
          <h2 className="text-lg font-semibold">{edit.existing ? "修改订阅来源" : "添加订阅"}</h2>
          <label>订阅名称<input required autoFocus maxLength={64} disabled={edit.existing || busy} value={edit.name} onChange={e => setEdit({ ...edit, name: e.target.value })} /></label>
          <label>订阅地址或服务器 YAML 路径<input required type="text" autoComplete="off" disabled={busy} value={edit.source} onChange={e => setEdit({ ...edit, source: e.target.value })} placeholder="https://example.com/subscribe" /></label>
          {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
          <div className="flex justify-end gap-2"><button type="button" className={secondary} disabled={busy} onClick={() => { setEdit(null); setError(""); }}>取消</button><button className={primary} disabled={busy}>{busy ? "正在校验…" : "保存"}</button></div>
        </form> : <>
          <h2 className="text-lg font-semibold">删除订阅</h2><p className="break-all text-sm text-slate-300">确认删除「{removing}」及其缓存原文？</p>
          {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
          <div className="flex justify-end gap-2"><button className={secondary} disabled={busy} onClick={() => { setRemoving(null); setError(""); }}>取消</button>
            <button className="bg-rose-500 text-white hover:bg-rose-400" disabled={busy} onClick={() => void operate(async () => {
              await request(`/subscriptions/${encodeURIComponent(removing!)}`, "DELETE"); setRemoving(null); await refresh(); setNotice("订阅已删除。");
            })}>确认删除</button></div>
        </>}
      </section>
    </div>}
  </div>;
}

createRoot(document.getElementById("root")!).render(<App />);
