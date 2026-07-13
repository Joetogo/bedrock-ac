'use client';
import { useRef, useState } from 'react';
import { Mic, ArrowRight } from 'lucide-react';

export function Composer({ disabled, onSend }: { disabled: boolean; onSend: (t: string) => void }) {
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement | null>(null);

  const grow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText('');
    if (ref.current) ref.current.style.height = 'auto';
  };

  return (
    <>
      <div className="ngb-composer">
        {/* Voice input — affordance only for now (speech-to-text lands later). */}
        <button className="ngb-mic" type="button" title="Voice input — coming soon" aria-label="Voice input (coming soon)">
          <Mic size={20} />
        </button>
        <div className="ngb-cinput">
          <textarea
            ref={ref}
            value={text}
            onChange={(e) => { setText(e.target.value); grow(e.target); }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
            rows={1}
            placeholder="Ask about your rooms, calls, or tests…  (Enter to send)"
          />
          <div className="ngb-chints"><b>Enter</b> send · <b>Shift+Enter</b> newline</div>
        </div>
        <button className="ngb-send" onClick={submit} disabled={disabled} aria-label="Send">
          Send <ArrowRight size={16} />
        </button>
      </div>
      <div className="ngb-cfooter">
        <span>READ-ONLY · SECRETS SERVER-SIDE</span>
        <span>NEAT-GRAPH-BEDROCK · 15–40S</span>
      </div>
    </>
  );
}
