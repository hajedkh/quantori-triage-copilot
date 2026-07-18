import { useEffect, useRef, useState } from "react";
import { Send, Wrench, AlertTriangle, MessageCircle, X } from "lucide-react";
import type { ChatMessage, ChatPreview, RunStatus } from "../types";
import { askChat, steer, type ChatEvent } from "../api";
import BrandSpinner from "./BrandSpinner";

interface Props {
  runId: string | null;
  status: RunStatus; // reused from RunState — "idle" doubles as "setup" here
  lastSteerAck: { message: string; ts: number } | null;
  onCiteRank: (rank: number) => void;
  // true on the setup screen: renders inline below the form instead of
  // floating. Same mounted component either way — the conversation carries
  // over when a run starts and it switches to floating.
  docked: boolean;
}

const GREETING =
  "Triage Copilot. I can help you pick a target, check your library, and " +
  "once the run starts I'll explain every decision the agents make. What " +
  "are you screening?";

function chipsFor(status: RunStatus): string[] {
  if (status === "idle") return ["How do I start a run?", "What does the diversity setting do?"];
  if (status === "running")
    return ["What has it done so far?", "How many survivors?", "What thresholds did it use?", "How many did PAINS drop?"];
  if (status === "awaiting_approval")
    return [
      "Why did #1 rank first?",
      "Which scaffolds dominate the top 20?",
      "Diversify across scaffolds",
      "How many known actives did we recover?",
      "Show me neighbours of #3",
    ];
  return [];
}

function placeholderFor(status: RunStatus): string {
  if (status === "running") return "Ask what's happening, or type an instruction to queue…";
  if (status === "exported") return "Run exported — chat is read-only.";
  return "Ask the copilot…";
}

// While a run is "running" the copilot can still answer read-only questions
// live (get_run_status/get_agent_trace/etc. are available at that status —
// see chat_tools.py::tools_for_status) — only mutate tools are gated behind
// awaiting_approval, and the chat LLM has no queue/steer tool of its own at
// all. So chat is the safe default at "running": it can only read, never
// silently claim to have changed anything. Steering the agents is the rare,
// deliberate case — only route to /steer when the text is clearly an
// imperative directive; anything else (including phrasing that isn't a
// textbook question) goes to chat instead of being silently swallowed into
// the queue with the canned "queued…" line.
const INSTRUCTION_START =
  /^(tighten|loosen|widen|narrow|raise|lower|increase|decrease|use|prefer|favou?r|ignore|skip|drop|exclude|include|focus|weight|boost|penalize|de?prioriti[sz]e|rerun|recompute|recheck|reconsider|set|apply|switch|change|adjust|filter|rank|screen|stop|pause|retry|queue|instruct)\b/i;

function looksLikeInstruction(text: string): boolean {
  const t = text.trim();
  if (t.endsWith("?")) return false;
  return INSTRUCTION_START.test(t);
}

// Turns "#3" / "rank 3" mentions into clickable citations that scroll the
// shortlist table to that row.
function renderWithCitations(text: string, onCite: (rank: number) => void) {
  const parts = text.split(/(#\d+)/g);
  return parts.map((p, i) => {
    const m = p.match(/^#(\d+)$/);
    if (m) {
      const rank = parseInt(m[1], 10);
      return (
        <button key={i} className="chat-cite" onClick={() => onCite(rank)}>
          #{rank}
        </button>
      );
    }
    return <span key={i}>{p}</span>;
  });
}

function ToolChip({ tool, args, status }: { tool: string; args: Record<string, unknown>; status: string }) {
  const argStr = Object.keys(args || {}).length ? JSON.stringify(args) : "";
  return (
    <div className={"chat-tool-chip " + status}>
      {status === "error" ? <AlertTriangle size={11} /> : <Wrench size={11} />}
      <span className="chat-tool-name">{tool}</span>
      {argStr && <span className="chat-tool-args">{argStr}</span>}
    </div>
  );
}

function PreviewCard({ preview, onApply, onCancel }: { preview: ChatPreview; onApply: () => void; onCancel: () => void }) {
  const entering = preview.entering_top20 as string[] | undefined;
  const leaving = preview.leaving_top20 as string[] | undefined;
  return (
    <div className="chat-preview-card fadeup">
      <div className="chat-preview-title">Preview — not applied yet</div>
      {entering && entering.length > 0 && (
        <div className="chat-preview-row">
          <span className="k">entering top 20</span>
          <span className="v">{entering.length} molecule(s)</span>
        </div>
      )}
      {leaving && leaving.length > 0 && (
        <div className="chat-preview-row">
          <span className="k">leaving top 20</span>
          <span className="v">{leaving.length} molecule(s)</span>
        </div>
      )}
      <div className="chat-preview-actions">
        <button className="btn primary" onClick={onApply}>
          Apply
        </button>
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export default function ChatPanel({ runId, status, lastSteerAck, onCiteRank, docked }: Props) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: "assistant", content: GREETING }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [pendingSteer, setPendingSteer] = useState<{ text: string; acked: boolean } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // A synchronous guard, not state — `sending` state can lag a tick behind a
  // rapid double-fire (e.g. Enter + a click landing in the same event burst),
  // which was letting a single message trigger two full chat turns.
  const inFlightRef = useRef(false);
  const wasDocked = useRef(docked);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, open]);

  // The moment the run starts, the docked card morphs into the floating
  // widget — keep it visibly open through that transition instead of
  // dropping the conversation the operator was just looking at.
  useEffect(() => {
    if (wasDocked.current && !docked) setOpen(true);
    wasDocked.current = docked;
  }, [docked]);

  // A steer message is only "confirmed" once its own event comes back on
  // the pipeline's own SSE stream — never mark it applied before that.
  useEffect(() => {
    if (!lastSteerAck || !pendingSteer) return;
    if (lastSteerAck.message === pendingSteer.text) {
      setPendingSteer((s) => (s ? { ...s, acked: true } : s));
    }
  }, [lastSteerAck, pendingSteer]);

  const runChatTurn = async (text: string) => {
    if (!runId) return;
    setSending(true);
    const assistant: ChatMessage = { role: "assistant", content: "", toolCalls: [], streaming: true };
    setMessages((m) => [...m, { role: "user", content: text }, assistant]);

    const onEvent = (e: ChatEvent) => {
      if (e.type === "tool_call" && e.payload?.tool) {
        const p = e.payload;
        let preview: ChatPreview | null = null;
        try {
          const parsed = JSON.parse(p.result_summary);
          if (parsed && parsed.preview === true) preview = { toolName: p.tool, ...parsed };
        } catch {
          /* result wasn't JSON (e.g. a plain error string) */
        }
        setMessages((m) => {
          const i = m.length - 1;
          const last = m[i];
          if (!last || last.role !== "assistant") return m;
          const next = [...m];
          next[i] = {
            ...last,
            toolCalls: [...(last.toolCalls || []), { tool: p.tool, args: p.args, result_summary: p.result_summary, status: p.status }],
            preview: preview || last.preview,
          };
          return next;
        });
      } else if (e.type === "chat_token") {
        setMessages((m) => {
          const i = m.length - 1;
          const last = m[i];
          if (!last || last.role !== "assistant") return m;
          const next = [...m];
          next[i] = { ...last, content: last.content + e.payload };
          return next;
        });
      } else if (e.type === "chat_done") {
        setMessages((m) => {
          const i = m.length - 1;
          const last = m[i];
          if (!last || last.role !== "assistant") return m;
          const next = [...m];
          next[i] = { ...last, streaming: false };
          return next;
        });
      }
    };

    try {
      await askChat(runId, text, onEvent);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `Chat request failed: ${String(err)}` }]);
    } finally {
      setSending(false);
      inFlightRef.current = false;
    }
  };

  const runSteerTurn = async (text: string) => {
    if (!runId) return;
    setSending(true);
    setMessages((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "queued — reaches the agent at its next decision point" },
    ]);
    setPendingSteer({ text, acked: false });
    try {
      await steer(runId, text);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `Couldn't queue that: ${String(err)}` }]);
      setPendingSteer(null);
    } finally {
      setSending(false);
      inFlightRef.current = false;
    }
  };

  const send = (text?: string) => {
    const value = (text ?? input).trim();
    if (!value || inFlightRef.current || !runId) return;
    inFlightRef.current = true;
    setInput("");
    if (status === "running" && looksLikeInstruction(value)) {
      runSteerTurn(value);
    } else {
      runChatTurn(value);
    }
  };

  const readOnly = status === "exported";
  const chips = chipsFor(status);

  const body = (
    <>
      <div className="chat-list" ref={listRef}>
        {messages.map((m, i) => (
          <div key={i} className={"chat-msg " + m.role}>
            <div className="chat-msg-body">
              {m.streaming ? (
                // No partial-text-plus-cursor reveal — stay on the "thinking"
                // mark for the whole streaming window and pop the complete
                // message in at once when chat_done lands.
                <span className="chat-thinking">
                  <BrandSpinner size={14} label="thinking" />
                  thinking…
                </span>
              ) : (
                renderWithCitations(m.content, onCiteRank)
              )}
            </div>
            {m.toolCalls?.map((tc, j) => (
              <ToolChip key={j} tool={tc.tool} args={tc.args} status={tc.status} />
            ))}
            {m.preview && (
              <PreviewCard
                preview={m.preview}
                onApply={() => send("yes, apply")}
                onCancel={() =>
                  setMessages((prev) => prev.map((msg) => (msg === m ? { ...msg, preview: null } : msg)))
                }
              />
            )}
          </div>
        ))}
      </div>

      {chips.length > 0 && !readOnly && (
        <div className="chat-chips">
          {chips.map((c) => (
            <button key={c} className="chip" disabled={sending} onClick={() => send(c)}>
              {c}
            </button>
          ))}
        </div>
      )}

      <div className="chat-input-row">
        <input
          type="text"
          value={input}
          disabled={readOnly || !runId}
          placeholder={placeholderFor(status)}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <button className="chat-send" disabled={readOnly || sending || !input.trim()} onClick={() => send()}>
          <Send size={14} />
        </button>
      </div>
      {pendingSteer && !pendingSteer.acked && <div className="chat-steer-status">queued…</div>}
      {pendingSteer && pendingSteer.acked && <div className="chat-steer-status ok">✓ reached the agent</div>}
    </>
  );

  if (docked) {
    return (
      <section className="chat-dock fadeup">
        <div className="chat-dock-h">
          <div className="chat-dock-badge">
            <MessageCircle size={17} />
          </div>
          <div className="chat-dock-copy">
            <h3>Triage Copilot</h3>
            <p>Ask about a target, sanity-check your library, or find out how this pipeline works.</p>
          </div>
        </div>
        {body}
      </section>
    );
  }

  return (
    <>
      <button
        className={"chat-fab" + (sending ? " active" : "") + (open ? " is-open" : "")}
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Close chat" : "Open Triage Copilot chat"}
        title={open ? "Close chat" : "Triage Copilot"}
      >
        {open ? <X size={20} /> : <MessageCircle size={20} />}
        {!open && <span className="chat-fab-ring" />}
      </button>

      {open && (
        <aside className="chat-panel chat-floating">
          <div className="panel-h chat-panel-h">
            <h3>Triage Copilot</h3>
            <button className="chat-panel-close" onClick={() => setOpen(false)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          {body}
        </aside>
      )}
    </>
  );
}