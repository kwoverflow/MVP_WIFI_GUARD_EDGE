import { usePresentation } from "./usePresentation";

export function DemoModeBadge() {
  const { demo } = usePresentation();
  return demo ? (
    <span
      className="demo-mode-badge"
      title="실제 센서가 아닌 데모 입력을 사용합니다. 외부 알림은 발송되지 않습니다."
    >
      데모 모드
    </span>
  ) : null;
}
