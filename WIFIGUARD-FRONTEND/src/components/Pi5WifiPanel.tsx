import { useEffect, useState } from "react";
import { Wifi, Cable, ShieldCheck } from "lucide-react";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { connectWifi, finishWifi, scanWifi, useWifiStatus, type WifiNetwork, type WifiTransaction } from "@/api/local-network";

const ACTIVE = ["connecting", "awaiting_confirmation", "committing", "rolling_back"];

export function Pi5WifiPanel() {
  const query = useWifiStatus();
  const { user } = useAuth();
  const data = query.data;
  const [networks, setNetworks] = useState<WifiNetwork[]>([]);
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [security, setSecurity] = useState<"wpa-psk" | "sae">("wpa-psk");
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [localTransaction, setLocalTransaction] = useState<WifiTransaction | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => { setPassword(""); setNetworks([]); setSsid(""); setLocalTransaction(null); }, [user?.id]);
  useEffect(() => { if (data?.transaction) setLocalTransaction(data.transaction); }, [data?.transaction]);
  useEffect(() => { if (!data?.secure_credentials_transport) setPassword(""); }, [data?.secure_credentials_transport]);
  if (!data && query.isError) {
    const ownershipError = query.error instanceof ApiError && ["NOT_FOUND", "LOCAL_DEVICE_NOT_BOUND", "FORBIDDEN"].includes(query.error.code);
    return <section className="device-operation-panel space-y-2" aria-label="Pi5 Wi-Fi 설정 안내">
      <h2 className="font-semibold">Wi-Fi 연결</h2>
      <p role="alert" className="text-sm text-warning">{ownershipError
        ? "현재 계정에서 Pi5 네트워크를 관리할 수 없습니다. 수신기를 연결한 계정으로 로그인해 주세요."
        : "Wi-Fi 설정 상태를 불러오지 못했습니다. Pi5 연결과 서비스 상태를 확인해 주세요."}</p>
      <p className="text-xs text-muted">공유기 설정은 해당 Pi5에 연결된 장치의 소유자 또는 시설 관리자만 변경할 수 있습니다.</p>
    </section>;
  }
  if (!data?.enabled) return null;
  const owner = user?.service === "HOME" || user?.role === "ROOT";
  const tx = localTransaction;
  const pending = tx != null && ACTIVE.includes(tx.phase);
  const online = !query.isError && data.available && data.updated_at_unix != null && now / 1000 - data.updated_at_unix < 20;
  const canChange = owner && online && data.edge_stopped && !busy && !pending;
  const wifi = data.interfaces.find((i) => i.name === "wlan0");
  const wired = data.interfaces.find((i) => i.name === "eth0");
  const remaining = tx ? Math.max(0, Math.ceil(tx.deadline - now / 1000)) : 0;
  async function perform(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); }
    catch { setMessage("요청을 완료하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요."); }
    finally { setBusy(false); await query.refetch(); }
  }
  function connect(event: React.FormEvent) {
    event.preventDefault();
    if (!canChange || !data?.secure_credentials_transport) return;
    const credentials = { ssid, password, security, hidden };
    setPassword("");
    void perform(async () => {
      const result = await connectWifi(credentials);
      setLocalTransaction(result.transaction);
      setMessage("임시 연결되었습니다. 아래 무선 주소를 확인한 뒤 연결을 저장하세요.");
    });
  }
  return (
    <section className="device-operation-panel space-y-4" aria-label="Pi5 Wi-Fi 설정">
      <div className="section-heading"><div><h2 className="flex items-center gap-2"><Wifi size={20} /> Wi-Fi 연결</h2><p>랜선 없이 사용할 네트워크를 설정합니다.</p></div>
        <span className={`status-pill ${online && wifi?.connected ? "tone-success" : "tone-neutral"}`}>{!online ? "상태 확인 필요" : wifi?.connected ? "무선 연결됨" : "무선 연결 대기"}</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
        <div><p className="flex items-center gap-2 font-medium"><Wifi size={16} /> {data.ssid || "연결된 Wi-Fi 없음"}</p>
          <p className="text-muted break-all mt-1">무선 주소: {wifi?.addresses.join(" · ") || "아직 없음"}</p>
          <p className="text-xs text-muted mt-1">재부팅 후 자동 연결: {data.autoconnect ? "설정됨" : "미설정"}</p></div>
        <div><p className="flex items-center gap-2 font-medium"><Cable size={16} /> 유선 {wired?.connected ? "연결됨" : "연결 안 됨"}</p>
          <p className="text-muted break-all mt-1">{wired?.addresses.join(" · ") || "유선 주소 없음"}</p></div>
      </div>
      {!online && <p role="alert" className="text-warning text-sm">네트워크 관리 서비스의 응답을 확인해 주세요. 표시된 주소는 이전 상태일 수 있습니다.</p>}
      {!data.edge_stopped && <p className="text-sm text-muted">Wi-Fi 검색·변경 전 ‘모니터링 중지’를 눌러 주세요. 수신 중에는 설정을 변경하지 않습니다.</p>}
      {pending && tx && <div className="rounded-lg border border-warning p-4 space-y-3" role="status">
        <p>Wi-Fi 임시 연결 · {remaining}초 안에 확인하세요.</p>
        <p className="text-sm">무선 주소로 접속할 수 있는지 확인하세요. 확인하지 않으면 이전 Wi-Fi 설정으로 돌아갑니다. 유선 설정은 변경하지 않습니다.</p>
        <div className="operation-buttons">
          <button className="primary-button" disabled={busy || !online || tx.phase !== "awaiting_confirmation" || remaining === 0} onClick={() => void perform(async () => { const value = await finishWifi("confirm", tx.id); setLocalTransaction(value.transaction); setMessage("연결을 저장했습니다. 재부팅 후에도 자동으로 연결합니다."); })}>연결 확인 · 저장</button>
          <button className="secondary-button" disabled={busy || !online} onClick={() => void perform(async () => { const value = await finishWifi("rollback", tx.id); setLocalTransaction(value.transaction); setMessage("이전 Wi-Fi 설정으로 되돌렸습니다."); })}>되돌리기</button>
        </div></div>}
      {tx?.phase === "rolled_back" && <p className="text-sm text-warning">임시 연결이 종료되었습니다. 이전 네트워크 상태를 확인하세요.</p>}
      {owner && !pending && <>
        <div className="operation-buttons"><button className="secondary-button" disabled={!canChange} onClick={() => void perform(async () => setNetworks((await scanWifi()).networks))}>주변 Wi-Fi 검색</button></div>
        {networks.length > 0 && <div className="flex flex-wrap gap-2" aria-label="검색된 Wi-Fi">{networks.map((network) => <button type="button" key={network.ssid} disabled={!canChange || network.security === "unsupported"} className="secondary-button" onClick={() => { setSsid(network.ssid); setSecurity(network.security as "wpa-psk" | "sae"); setPassword(""); }}>
          {network.ssid} · {network.signal}%{network.security === "unsupported" ? " · 미지원 보안 방식" : ""}</button>)}</div>}
        {!data.secure_credentials_transport ? <div className="text-sm text-muted flex gap-2"><ShieldCheck size={18} className="shrink-0" /><p>비밀번호 보호를 위해 일반 HTTP 화면에서는 입력을 받지 않습니다. PC의 보안 연결용 GUI 또는 HTTPS로 접속해 주세요.</p></div> :
          <form onSubmit={connect} className="space-y-3" autoComplete="off">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <label className="text-sm">Wi-Fi 이름<input aria-label="Wi-Fi 이름" value={ssid} onChange={(e) => setSsid(e.target.value)} maxLength={32} disabled={!canChange} required className="block w-full mt-1 bg-background border border-border rounded px-3 py-2" /></label>
              <label className="text-sm">공유기 비밀번호<input aria-label="공유기 비밀번호" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={8} maxLength={64} disabled={!canChange} required className="block w-full mt-1 bg-background border border-border rounded px-3 py-2" /></label>
              <label className="text-sm">보안 방식<select aria-label="보안 방식" value={security} onChange={(e) => setSecurity(e.target.value as "wpa-psk" | "sae")} disabled={!canChange} className="block w-full mt-1 bg-background border border-border rounded px-3 py-2"><option value="wpa-psk">WPA2 개인용</option><option value="sae">WPA3 개인용</option></select></label>
              <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} disabled={!canChange} />숨겨진 Wi-Fi</label>
            </div>
            <button className="primary-button" disabled={!canChange} type="submit">{busy ? "연결 중…" : "임시 연결하기"}</button>
            <p className="text-xs text-muted">비밀번호는 화면 저장소나 운영 로그에 기록하지 않습니다. Pi5의 보호된 임시 네트워크 설정에 보관하며, 연결을 저장하지 않으면 삭제합니다.</p>
          </form>}
      </>}
      {message && <p role="status" className="text-sm">{message}</p>}
      <p className="text-xs text-muted">무선 연결 표시만으로 랜선 없는 운영이 검증된 것은 아닙니다. 설치 후 랜선 제거·재접속·센서 연속 수신을 확인해야 합니다.</p>
    </section>
  );
}
