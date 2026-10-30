import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { CheckCircle2, Info, XCircle } from "lucide-react";

type ToastTone = "ok" | "info" | "bad";
interface ToastItem { id: number; tone: ToastTone; text: string }

const Ctx = createContext<(tone: ToastTone, text: string) => void>(() => {});
export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((tone: ToastTone, text: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, tone, text }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), tone === "bad" ? 9000 : 5000);
  }, []);
  const icon = { ok: <CheckCircle2 className="size-4 text-emerald-700" />, info: <Info className="size-4 text-amber-700" />,
    bad: <XCircle className="size-4 text-rose-700" /> };
  return (
    <Ctx.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(92vw,24rem)] flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} role="status" className="panel pointer-events-auto flex items-start gap-2 px-3 py-2.5 text-sm">
            <span className="mt-0.5">{icon[t.tone]}</span>
            <span className="text-fg">{t.text}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
