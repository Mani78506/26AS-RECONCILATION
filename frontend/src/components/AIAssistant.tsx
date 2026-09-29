import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Bot, Check, Copy, Loader2, Send, Sparkles, X } from "lucide-react";
import { api, getErrorMessage } from "../services/api";
import type { AIContextInfo, Run } from "../types";

const SUGGESTIONS = ["Summarize this reconciliation", "Explain the major exceptions", "Show high-value mismatches", "Why are transactions identity unmapped?", "What actions should I take?", "Give me a management summary"];
type Message = { id: string; role: "user" | "assistant"; content: string; timestamp: string };

function MarkdownText({ text }: { text: string }) {
  return <div className="ai-markdown">{text.split(/\n{2,}/).map((block, index) => {
    const lines = block.split("\n");
    if (lines.every((line) => /^[-*]\s+/.test(line))) return <ul key={index}>{lines.map((line, i) => <li key={i}>{line.replace(/^[-*]\s+/, "")}</li>)}</ul>;
    if (/^#{1,3}\s+/.test(block)) return <h4 key={index}>{block.replace(/^#{1,3}\s+/, "")}</h4>;
    return <p key={index}>{block}</p>;
  })}</div>;
}

export function AIAssistant({ run, open, onClose, initialResultId }: { run: Run | null; open: boolean; onClose: () => void; initialResultId?: string | null }) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [context, setContext] = useState<AIContextInfo | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [loadingContext, setLoadingContext] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastQuestion, setLastQuestion] = useState("");
  const [resultId, setResultId] = useState<string | null>(initialResultId || null);
  const listRef = useRef<HTMLDivElement>(null);
  const loadedRunId = useRef<string | null>(null);

  useEffect(() => { if (initialResultId) setResultId(initialResultId); }, [initialResultId]);
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" }); }, [messages, loading]);
  useEffect(() => {
    if (!open || !run) return;
    if (loadedRunId.current === run.run_id && context) return;
    setMessages([]); setConversationId(null); setContext(null); setError(""); setLastQuestion(""); setLoadingContext(true);
    api.aiContext(run.run_id).then((response) => {
      loadedRunId.current = run.run_id;
      setContext(response.context);
      setMessages([{ id: "intro", role: "assistant", timestamp: new Date().toISOString(), content: "Hi. I can explain the completed reconciliation for this run. I keep Books Customers separate from 26AS Deductors and their TANs while explaining matches, exceptions, claimability, and next steps." }]);
    }).catch((err) => setError(getErrorMessage(err, "The completed reconciliation could not be connected to AI."))).finally(() => setLoadingContext(false));
  }, [open, run?.run_id]);

  const ask = async (value: string) => {
    const text = value.trim();
    if (!text || !run || !context || loading) return;
    setMessages((items) => [...items, { id: crypto.randomUUID(), role: "user", content: text, timestamp: new Date().toISOString() }]);
    setQuestion(""); setError(""); setLastQuestion(text); setLoading(true);
    try {
      const response = await api.aiChat({ run_id: run.run_id, question: text, conversation_id: conversationId || undefined, ...(resultId ? { result_id: resultId } : {}) });
      setConversationId(response.conversation_id); setContext(response.context);
      setMessages((items) => [...items, { id: crypto.randomUUID(), role: "assistant", content: response.answer, timestamp: new Date().toISOString() }]);
      setResultId(null);
    } catch (err) { setError(getErrorMessage(err, "AI analysis could not be generated. Your reconciliation results are unaffected.")); }
    finally { setLoading(false); }
  };
  const submit = (event: FormEvent) => { event.preventDefault(); ask(question); };
  const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(question); } };
  const copy = async (content: string) => { try { await navigator.clipboard.writeText(content); } catch { /* Clipboard permission is optional. */ } };

  if (!open) return null;
  return <div className="ai-assistant" role="dialog" aria-modal="true" aria-label="AI reconciliation assistant">
    <div className="ai-assistant-head"><div><span className="eyebrow"><Sparkles size={12} /> AI ASSISTANT</span><h2>Reconciliation Copilot</h2><p>{context ? `${context.client_name} · FY ${context.financial_year || "—"}` : "Connecting to reconciliation…"}</p>{context && <div className="ai-connected"><Check size={12} /> Connected to completed reconciliation · {context.result_count} results · {context.books_count} Books · {context.statement_count} 26AS</div>}</div><button className="icon-button" aria-label="Close AI assistant" onClick={onClose}><X size={17} /></button></div>
    {!run ? <div className="ai-empty"><Bot size={24} /><b>No reconciliation selected</b><p>Choose a completed run from the workspace selector first.</p></div> : loadingContext ? <div className="ai-empty"><Loader2 className="spin" size={20} /><b>Connecting to reconciliation…</b></div> : <>
      {resultId && <div className="ai-selection">Selected transaction ready for explanation <button onClick={() => setResultId(null)} aria-label="Clear selected transaction">×</button></div>}
      <div className="ai-messages" ref={listRef}>{messages.map((message) => <article key={message.id} className={`ai-message ${message.role}`}><div className="ai-message-label"><b>{message.role === "user" ? "You" : "Reconciliation Copilot"}</b>{message.role === "assistant" && <button onClick={() => copy(message.content)} aria-label="Copy AI answer"><Copy size={12} /></button>}</div>{message.role === "assistant" ? <MarkdownText text={message.content} /> : <p>{message.content}</p>}</article>)}{loading && <article className="ai-message assistant ai-typing"><Loader2 className="spin" size={14} /> AI is thinking…</article>}</div>
      {!loading && messages.length <= 1 && <div className="ai-suggestions"><span>Suggested</span>{SUGGESTIONS.map((item) => <button key={item} onClick={() => ask(item)}>{item}</button>)}</div>}
      {error && <div className="ai-error"><span>{error}</span>{lastQuestion && <button onClick={() => ask(lastQuestion)}>Retry</button>}</div>}
      <form className="ai-composer" onSubmit={submit}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={keyDown} placeholder="Ask about this reconciliation…" maxLength={2000} disabled={loading || !context} /><button className="primary-button" type="submit" disabled={!question.trim() || loading || !context}><Send size={14} /> Send</button></form>
    </>}
  </div>;
}
