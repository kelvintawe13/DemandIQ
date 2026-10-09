import { MessageSquareIcon } from 'lucide-react';
import { useAgent } from '../../contexts/AgentContext';

/**
 * Header trigger for the assistant. Carries a count of proactive insights the
 * user hasn't looked at yet, cleared the first time the panel opens.
 */
export function AgentLauncher() {
  const { open, setOpen, unread, clearUnread } = useAgent();
  // State-aware, because `aria-expanded` is already on the button: a control
  // that reports itself expanded while being named "Open" contradicts itself.
  // It matters more now that the rail is open by default on wide screens, where
  // this button's job is to collapse it.
  const label = open ? 'Close DemandIQ Assistant (Ctrl+J)' : 'Open DemandIQ Assistant (Ctrl+J)';

  return (
    <button
      type="button"
      aria-label={label}
      title="DemandIQ Assistant · Ctrl+J"
      aria-expanded={open}
      onClick={() => {
        setOpen(!open);
        clearUnread();
      }}
      className={[
      'relative inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2 py-1.5 text-meta font-semibold transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:px-2.5',
      open ?
      'bg-brand-700 text-white hover:bg-brand-800' :
      'border border-brand-200 bg-brand-50 text-brand-800 hover:bg-brand-100'].
      join(' ')}>
      
      <MessageSquareIcon className="h-4 w-4" strokeWidth={2} />
      <span className="hidden sm:inline">Assistant</span>
      {!open && unread > 0 &&
      <span className="absolute -right-1 -top-1 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-amber-500 px-1 text-nano font-bold text-white">
          {unread}
        </span>
      }
    </button>);

}