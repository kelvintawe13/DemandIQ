import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  SparklesIcon,
  XIcon,
  SendIcon,
  RotateCcwIcon,
  ArrowRightIcon,
  ShieldCheckIcon,
  TrendingUpIcon,
  TriangleAlertIcon,
  ActivityIcon,
  FileTextIcon,
  MapPinIcon,
  BookmarkIcon,
  CopyIcon,
  CheckIcon,
  MessageSquareIcon,
  LightbulbIcon,
  ChevronDownIcon,
  GaugeIcon } from
'lucide-react';
import { useAgent } from '../../contexts/AgentContext';
import type { AgentMessage } from '../../contexts/AgentContext';
import { useSession } from '../../contexts/SessionContext';
import { EmptyState } from '../ui/EmptyState';
import type { AgentAnswer } from '../../data/agent';

interface AgentPanelProps {
  activeNavId: string;
  onNavigate: (id: string) => void;
}

const EASE = [0.23, 1, 0.32, 1] as const;

type Tab = 'ask' | 'insights' | 'saved';

const toneStyles = {
  risk: { icon: TriangleAlertIcon, chip: 'bg-amber-100 text-amber-800' },
  opportunity: { icon: TrendingUpIcon, chip: 'bg-brand-100 text-brand-800' },
  signal: { icon: ActivityIcon, chip: 'bg-slate-100 text-slate-700' }
};

function Answer({
  answer,
  onNavigate,
  onClose,
  onPin,
  pinned




}: {answer: AgentAnswer;onNavigate: (id: string) => void;onClose: () => void;onPin?: () => void;pinned?: boolean;}) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    const text = [answer.headline, ...answer.body].join('\n\n');
    navigator.clipboard?.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="overflow-hidden rounded-xl rounded-tl-sm border border-slate-200 bg-white">
      <div>
        <div className="p-3">
          <p className="text-meta font-semibold leading-snug text-slate-900">
            {answer.headline}
          </p>
          {answer.body.map((paragraph) =>
          <p
            key={paragraph}
            className="mt-1.5 text-caption leading-relaxed text-slate-600">

              {paragraph}
            </p>
          )}

          {answer.action &&
          <button
            type="button"
            onClick={() => {
              onNavigate(answer.action!.route);
              onClose();
            }}
            className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-2.5 py-1.5 text-micro font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">

              {answer.action.label}
              <ArrowRightIcon className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          }
        </div>

        <div
          className="bg-slate-50/70 p-3 border-t border-slate-200">

          {answer.figures &&
          <dl className="flex flex-wrap gap-2">
              {answer.figures.map((figure) =>
            <div key={figure.label} className="min-w-0">
                  <dt className="text-fine uppercase tracking-wide text-slate-500">
                    {figure.label}
                  </dt>
                  <dd className="text-control font-bold tabular-nums tracking-tight text-slate-900">
                    {figure.value}
                  </dd>
                </div>
            )}
            </dl>
          }

          <p className="mt-2.5 text-fine font-semibold uppercase tracking-wide text-slate-500">
            Sources
          </p>
          <ul className="mt-1 flex flex-col gap-1">
            {answer.sources.map((source) =>
            <li key={source.label} className="flex items-start gap-1.5 text-fine">
                <FileTextIcon
                className="mt-0.5 h-3 w-3 shrink-0 text-slate-500"
                strokeWidth={1.9} />
              
                <span className="min-w-0">
                  <span className="font-medium text-slate-700">{source.label}</span>
                  <span className="block text-slate-500">{source.detail}</span>
                </span>
              </li>
            )}
          </ul>
        </div>
      </div>

      <div className="flex items-center gap-1 border-t border-slate-100 px-2 py-1.5">
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-fine font-medium text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          {copied ?
          <CheckIcon className="h-3.5 w-3.5 text-brand-600" strokeWidth={2.4} /> :

          <CopyIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
          }
          {copied ? 'Copied' : 'Copy'}
        </button>
        {onPin &&
        <button
          type="button"
          onClick={onPin}
          className={[
          'inline-flex items-center gap-1 rounded-md px-2 py-1 text-fine font-medium transition-colors duration-150 ease-out hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          pinned ? 'text-brand-700' : 'text-slate-500 hover:text-slate-700'].
          join(' ')}>
          
            <BookmarkIcon
            className="h-3.5 w-3.5"
            strokeWidth={1.9}
            fill={pinned ? 'currentColor' : 'none'} />
          
            {pinned ? 'Saved' : 'Save'}
          </button>
        }
      </div>
    </div>);

}

export function AgentPanel({ activeNavId, onNavigate }: AgentPanelProps) {
  const {
    open,
    setOpen,
    userOpenedRef,
    messages,
    ask,
    reset,
    thinking,
    scope,
    setScope,
    scopeOptions,
    pinned,
    pin,
    unpin,
    quota,
    remaining,
    exhausted,
    quotaPct,
    insights,
    promptsForRoute,
    error,
    quotaError,
    insightsError,
    greeting,
    sessionId,
    openSession,
  } = useAgent();
  const { user } = useSession();
  const reduced = useReducedMotion();
  const [tab, setTab] = useState<Tab>('ask');
  const [draft, setDraft] = useState('');
  const [scopeOpen, setScopeOpen] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);

  const prompts = promptsForRoute(activeNavId);
  const openedOnce = useRef(false);

  // Open a session the first time the USER opens the panel, so the greeting on
  // screen is the one the API composes (scoped to the user and location) rather
  // than the local fallback. Deliberately NOT on mount: AgentContext explains
  // that creating a session per page view burned the concurrent-session
  // allowance and 409'd. This fires on an explicit user action, at most once,
  // and only when no session exists yet; ask() still creates one lazily if this
  // call fails.
  //
  // `userOpenedRef` is what keeps the rail honest: on a viewport that docks it
  // the panel is open from the first render, and without this a page load would
  // create a session nobody asked for. Only a gesture (the launcher, Ctrl+J, or
  // asking) spends a session.
  useEffect(() => {
    // `exhausted` is excluded on purpose: opening a panel with no allowance
    // left would 429 here and render an error before the user asked anything.
    // The composer already reports exhaustion in context.
    if (!open || !userOpenedRef.current || sessionId || exhausted || openedOnce.current) return;
    openedOnce.current = true;
    void openSession();
  }, [open, userOpenedRef, sessionId, exhausted, openSession]);

  useEffect(() => {
    threadRef.current?.scrollTo({
      top: threadRef.current.scrollHeight,
      behavior: reduced ? 'auto' : 'smooth'
    });
  }, [messages, reduced]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, setOpen]);

  const submit = (text: string) => {
    if (exhausted) return;
    void ask(text);
    setDraft('');
    setTab('ask');
  };

  const isPinned = (message: AgentMessage) =>
  pinned.some((item) => item.id === message.id || item.turnId === (message as any).turnId);

  const tabs: {id: Tab;label: string;icon: typeof MessageSquareIcon;count?: number;}[] =
  [
  { id: 'ask', label: 'Ask', icon: MessageSquareIcon },
  { id: 'insights', label: 'Insights', icon: LightbulbIcon, count: insights.length },
  { id: 'saved', label: 'Saved', icon: BookmarkIcon, count: pinned.length }];


  const body = (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <header className="shrink-0 border-b border-slate-200 px-3.5 py-3">
        <div className="flex items-start gap-2.5">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-700">
            <SparklesIcon className="h-[18px] w-[18px] text-white" strokeWidth={1.9} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-body-xs font-bold tracking-tight text-slate-900">
              DemandIQ Assistant
            </p>
            <p className="flex items-center gap-1 truncate text-fine text-slate-500">
              <ShieldCheckIcon
              className="h-3 w-3 shrink-0 text-brand-600"
              strokeWidth={2} />
            
              {/* The API's `tier` is the caller's role value (`owner`,
                  `administrator`, `analyst`, `operator`; see `_QUOTA_TIERS` in
                  routers/agent.py), so the session's already-normalised label is
                  the right thing to show: "owner seat" reads as a raw token next
                  to a UI that calls that person an Owner. */}
              {user.name} · {user.role} seat
            </p>
          </div>
          <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Close assistant"
          className="tap-expand rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">

            <XIcon className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>

        <div className="relative mt-2.5">
          <button
          type="button"
          onClick={() => setScopeOpen(!scopeOpen)}
          aria-expanded={scopeOpen}
          className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-micro font-medium text-slate-700 transition-colors duration-150 ease-out hover:border-brand-300 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
            <MapPinIcon className="h-3.5 w-3.5 shrink-0 text-brand-600" strokeWidth={2} />
            <span className="truncate">{scope}</span>
            <ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={2} />
          </button>

          {scopeOpen &&
        <ul
          role="listbox"
          className="absolute left-0 top-[calc(100%+4px)] z-10 w-[220px] overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-[0_12px_30px_rgba(15,23,42,0.12)]">
          
              {scopeOptions.map((item) =>
          <li key={item}>
                  <button
              type="button"
              role="option"
              aria-selected={item === scope}
              onClick={() => {
                setScope(item);
                setScopeOpen(false);
              }}
              className={[
              'flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-caption transition-colors duration-150 ease-out hover:bg-slate-50',
              item === scope ? 'font-semibold text-brand-700' : 'text-slate-700'].
              join(' ')}>
              
                    {item}
                    {item === scope && <CheckIcon className="h-3.5 w-3.5" strokeWidth={2.4} />}
                  </button>
                </li>
          )}
            </ul>
        }
        </div>
      </header>

      <div
      role="tablist"
      aria-label="Assistant views"
      className="flex shrink-0 gap-1 border-b border-slate-200 px-2 py-1.5">
      
        {tabs.map((item) => {
        const Icon = item.icon;
        const active = tab === item.id;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => setTab(item.id)}
            className={[
            'inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-micro font-semibold transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
            active ? 'bg-slate-100 text-slate-900' : 'text-slate-500 hover:bg-slate-50'].
            join(' ')}>
            
              <Icon className="h-3.5 w-3.5" strokeWidth={2} />
              {item.label}
              {typeof item.count === 'number' && item.count > 0 &&
            <span className="rounded-full bg-slate-200 px-1.5 text-nano font-bold text-slate-600">
                  {item.count}
                </span>
            }
            </button>);

      })}
      </div>

      <div ref={threadRef} className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
        {error && tab === 'ask' && messages.length === 0 && (
          <div className="mb-3">
            <EmptyState
              variant={error.variant === 'offline' ? 'offline' : error.variant === 'empty' ? 'empty' : 'error'}
              title={error.title}
              description={error.description}
              size="sm"
            />
          </div>
        )}
        {quotaError && (
          <div className="mb-3">
            <EmptyState variant={quotaError.variant === 'offline' ? 'offline' : 'error'} title={quotaError.title} description={quotaError.description} size="sm" />
          </div>
        )}

        {tab === 'insights' && (
          <>
            {insightsError ? (
              <EmptyState variant={insightsError.variant === 'offline' ? 'offline' : 'error'} title={insightsError.title} description={insightsError.description} size="sm" action={{ label: 'Retry', onClick: () => window.location.reload() }} />
            ) : insights.length === 0 ? (
              <EmptyState variant="empty" size="sm" title="No insights yet" description="Proactive cards will appear here once your workspace has enough demand history. Ask a question to get started." hints={["Forecasts need 30 days of history", "Insights update as new demand is ingested"]} />
            ) : (
              <ul className="flex flex-col gap-2">
                {insights.map((insight, index) => {
                  const { icon: Icon, chip } = toneStyles[insight.tone] ?? toneStyles.signal;
                  return (
                    <motion.li
                      key={insight.id}
                      initial={reduced ? false : { opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.24, ease: EASE, delay: index * 0.05 }}>
                      <button
                        type="button"
                        onClick={() => {
                          if (insight.route) {
                            onNavigate(insight.route);
                            setOpen(false);
                          }
                        }}
                        className="w-full rounded-xl border border-slate-200 p-2.5 text-left transition-colors duration-150 ease-out hover:border-brand-300 hover:bg-brand-50/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                        <span className="flex items-start gap-2">
                          <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${chip}`}>
                            <Icon className="h-3.5 w-3.5" strokeWidth={2} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-caption font-semibold leading-snug text-slate-900">{insight.title}</span>
                            <span className="mt-0.5 block text-fine leading-relaxed text-slate-500">{insight.detail}</span>
                          </span>
                        </span>
                      </button>
                    </motion.li>
                  );
                })}
              </ul>
            )}
          </>
        )}

        {tab === 'saved' && (
      pinned.length === 0 ?
      <EmptyState variant="empty" size="sm" title="Nothing saved yet" description="Save an answer to keep it while you work. Useful when you are comparing a forecast against what you actually prepared." /> :

      <ul className="flex flex-col gap-3">
              {pinned.map((item) =>
        <li key={item.id}>
                  <p className="mb-1 truncate text-fine font-medium text-slate-500">
                    {item.question} · {item.scope}
                  </p>
                  <Answer
            answer={item.answer}
            onNavigate={onNavigate}
            onClose={() => setOpen(false)}
            onPin={() => unpin(item.id)}
            pinned />
          
                </li>
        )}
            </ul>)
      }

        {tab === 'ask' && (
      messages.length === 0 ?
      <div>
              <p className="text-caption leading-relaxed text-slate-600">
                {greeting}
              </p>
              <p className="mt-2 text-caption leading-relaxed text-slate-600">
                You are asking about{' '}
                <span className="font-semibold text-slate-900">{scope}</span>.
              </p>
              <ul className="mt-3 flex flex-col gap-1.5">
                {prompts.map((prompt) =>
          <li key={prompt}>
                    <button
              type="button"
              onClick={() => submit(prompt)}
              disabled={thinking || exhausted}
              className="flex w-full items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 text-left text-caption text-slate-700 transition-colors duration-150 ease-out hover:border-brand-300 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50">
              
                      {prompt}
                      <ArrowRightIcon
                className="h-3.5 w-3.5 shrink-0 text-slate-300"
                strokeWidth={2} />
              
                    </button>
                  </li>
          )}
              </ul>
              <p className="mt-3 rounded-lg bg-slate-50 px-2.5 py-2 text-fine leading-relaxed text-slate-500">
                I cannot price a menu, plan staffing or estimate profit; that data is
                not in DemandIQ. I will say so rather than guess.
              </p>
            </div> :

      <ul className="flex flex-col gap-3">
              <AnimatePresence initial={false}>
                {messages.map((message) =>
          <motion.li
            key={message.id}
            initial={reduced ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: EASE }}
            className={message.author === 'user' ? "flex justify-end items-end gap-3" : "flex justify-start items-start gap-3"}>
            
                    {message.author === 'user' ?
            <div className="flex max-w-[86%] flex-col items-end">
              <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 text-xs text-slate-500">
                <span className="text-xs font-medium">You</span>
              </span>
              <p className="rounded-xl rounded-br-sm bg-brand-700 px-3 py-2 text-caption leading-relaxed text-white">
                {message.text}
              </p>
            </div> :
            message.pending ?
            <div className="inline-flex items-center gap-1.5 rounded-xl rounded-tl-sm border border-slate-200 bg-white px-3 py-2.5">
              <SparklesIcon className="h-3 w-3 text-brand-600" strokeWidth={1.5} />
              <span className="text-xs font-medium text-brand-600">Assistant</span>
                        {[0, 1, 2].map((dot) =>
              <motion.span
                key={dot}
                className="h-1.5 w-1.5 rounded-full bg-slate-400"
                animate={reduced ? undefined : { opacity: [0.3, 1, 0.3] }}
                transition={{
                  duration: 0.9,
                  repeat: Infinity,
                  delay: dot * 0.15,
                  ease: 'linear'
                }} />

              )}
                        <span className="ml-1 text-fine text-slate-500">
                          Reading your data…
                        </span>
                      </div> :

            message.answer &&
            <Answer
              answer={message.answer}
              onNavigate={onNavigate}
              onClose={() => setOpen(false)}
              onPin={() =>
              isPinned(message) ? unpin(message.id) : pin(message)
              }
              pinned={isPinned(message)} />


            }
                  </motion.li>
          )}
              {/* Conversation history - last 3 Q&A pairs */}


              </AnimatePresence>
            </ul>)
      }
      </div>

      <div className="shrink-0 border-t border-slate-200 px-3.5 py-2.5">
        {exhausted ?
      <EmptyState variant="empty" size="sm" title="Daily allowance reached" description={`You have used all ${quota.limit} questions on your ${user.role} seat. An administrator can issue more from AI Assistant settings.`} /> :

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit(draft);
        }}
        className="flex items-end gap-2">
        
            <label htmlFor="agent-input" className="sr-only">
              Ask the assistant
            </label>
            <input
          id="agent-input"
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={`Ask about ${scope.toLowerCase()}…`}
          className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-meta text-slate-900 outline-none transition-colors duration-150 ease-out placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25" />
        
            <button
          type="submit"
          disabled={!draft.trim() || thinking}
          aria-label="Send"
          className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-lg bg-brand-700 text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300">
          
              <SendIcon className="h-4 w-4" strokeWidth={2} />
            </button>
          </form>
      }

        <div className="mt-2.5 flex items-center gap-2">
          <GaugeIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={1.9} />
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
            <div
            className={[
            'h-full rounded-full transition-[width] duration-300 ease-out',
            quotaPct >= 90 ? 'bg-amber-500' : 'bg-brand-500'].
            join(' ')}
            style={{ width: `${quotaPct}%` }} />
          
          </div>
          <span className="shrink-0 text-fine tabular-nums text-slate-500">
            {remaining} left today
          </span>
          {messages.length > 0 &&
        <button
          type="button"
          onClick={() => void reset()}
          aria-label="Clear conversation"
          className="shrink-0 rounded p-1 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
              <RotateCcwIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
            </button>
        }
        </div>
      </div>
    </div>
  );


  return (
    <AnimatePresence>
      {open &&
      <>
          <motion.div
          key="agent-scrim"
          initial={reduced ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.16 }}
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-40 bg-slate-900/40 min-[1180px]:hidden"
          aria-hidden="true" />
        
          <motion.aside
          key="agent-panel"
          aria-label="DemandIQ Assistant"
          // Slide on overlay widths, but only FADE when docked: a slide
          // against the static grid re-flows the page each frame and the
          // docked panel visibly flickers/jumps on open.
          //
          // One breakpoint only, and it is `min-[1180px]` — the same number as
          // RAIL_MIN_WIDTH in AgentContext, which decides whether the panel is
          // open on load. Below it the panel is a drawer over the page; at or
          // above it the page keeps its width and the rail sits beside it.
          initial={reduced ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={reduced ? undefined : { opacity: 0 }}
          transition={{ duration: 0.18, ease: EASE }}
          // Fixed width: 320px for both drawer and docked modes
          className={[
          'fixed inset-y-0 right-0 z-50 w-full max-w-[320px] border-l border-slate-200 shadow-[-12px_0_40px_rgba(15,23,42,0.12)] min-[1180px]:static min-[1180px]:z-auto min-[1180px]:max-w-none min-[1180px]:shadow-none min-[1180px]:w-[320px]'].
          join(' ')}>
          
            {body}
          </motion.aside>
        </>
      }
    </AnimatePresence>);

}
