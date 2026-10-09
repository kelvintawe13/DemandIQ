import { motion, useReducedMotion } from "framer-motion";
import { useDialogBehaviour } from "./ui/useDialogBehaviour";
import { XIcon, DatabaseIcon, GaugeIcon, SlidersHorizontalIcon, UsersIcon, BrainCircuitIcon, PlugZapIcon, GitPullRequestIcon, ArrowRightIcon, InfoIcon, type LucideIcon } from "lucide-react";
interface WelcomeModalProps {
  open: boolean;
  /** `signup` gets set-up framing; `signin` gets a shorter welcome back. */
  kind: 'signup' | 'signin';
  name: string;
  role: string;
  onClose: () => void;
  onNavigate: (id: string) => void;
}
interface Task {
  id: string;
  label: string;
  detail: string;
  icon: LucideIcon;
  route: string;
}
const operatorTasks: Task[] = [{
  id: 'import',
  label: 'Bring in your demand history',
  detail: 'Connect MoMo or POS, upload a spreadsheet, or photograph the day book.',
  icon: DatabaseIcon,
  route: 'ingestion'
}, {
  id: 'forecast',
  label: 'See tomorrow’s forecast',
  detail: 'Expected covers with a 90% range, and what drove the number.',
  icon: GaugeIcon,
  route: 'forecast'
}, {
  id: 'policy',
  label: 'Choose how much risk to carry',
  detail: 'Turn the forecast into a prep quantity you are comfortable defending.',
  icon: SlidersHorizontalIcon,
  route: 'settings'
}, {
  id: 'team',
  label: 'Bring your team in',
  detail: 'Invite managers per location with the access they need.',
  icon: UsersIcon,
  route: 'settings'
}];
const adminTasks: Task[] = [{
  id: 'models',
  label: 'Review what is in production',
  detail: 'Compare LightGBM and CatBoost against the baselines on held-out data.',
  icon: BrainCircuitIcon,
  route: 'admin-models'
}, {
  id: 'connectors',
  label: 'Check the inbound channels',
  detail: 'POS, MoMo, files and the partner API, all normalised to one schema.',
  icon: PlugZapIcon,
  route: 'admin-connectors'
}, {
  id: 'approvals',
  label: 'Clear the approvals queue',
  detail: 'Promotions and policy changes waiting on a second sign-off.',
  icon: GitPullRequestIcon,
  route: 'admin-approvals'
}, {
  id: 'policies',
  label: 'Tune the preparation policy',
  detail: 'See the surplus against unmet-demand trade-off before you change it.',
  icon: SlidersHorizontalIcon,
  route: 'admin-policies'
}];
const EASE = [0.23, 1, 0.32, 1] as const;

/**
 * Shown once after a successful sign-in or sign-up. It orients the person
 * rather than congratulating them: every card is a real destination.
 */
export function WelcomeModal({
  open,
  kind,
  name,
  role,
  onClose,
  onNavigate
}: WelcomeModalProps) {
  const reduced = useReducedMotion();
  // `aria-modal` was declared without the behaviour behind it: no Escape, no
  // Tab trap, no focus return. This is the same contract `Modal` gives.
  const panelRef = useDialogBehaviour(open, onClose);
  if (!open) return null;
  const isAdmin = role === 'Administrator';
  const tasks = isAdmin ? adminTasks : operatorTasks;
  const firstName = name.split(' ')[0];
  return <div className="fixed inset-0 z-[68] flex items-end justify-center sm:items-center sm:p-4">
      <motion.div className="absolute inset-0 bg-slate-900/55" onClick={onClose} aria-hidden="true" initial={reduced ? false : {
      opacity: 0
    }} animate={{
      opacity: 1
    }} transition={{
      duration: 0.2,
      ease: EASE
    }} />

      <motion.div ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={kind === 'signup' ? 'Welcome to DemandIQ' : 'Welcome back'} initial={reduced ? false : {
      opacity: 0,
      y: 24,
      scale: 0.98
    }} animate={{
      opacity: 1,
      y: 0,
      scale: 1
    }} transition={{
      duration: 0.28,
      ease: EASE
    }} className="relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-2xl bg-white shadow-xl outline-none sm:max-w-lg sm:rounded-2xl dark:bg-slate-900">
        <div className="relative overflow-hidden bg-brand-800 px-5 py-6 text-white sm:px-6">
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(255,255,255,0.16),transparent_60%)]" />
          <button type="button" onClick={onClose} aria-label="Close" className="absolute right-3 top-3 rounded-lg p-1.5 text-white/70 transition-colors duration-150 ease-out hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-white/60">
            <XIcon className="h-4 w-4" strokeWidth={2} />
          </button>

          <motion.span initial={reduced ? false : {
          scale: 0.96,
          opacity: 0
        }} animate={{
          scale: 1,
          opacity: 1
        }} transition={{
          duration: 0.3,
          ease: EASE,
          delay: 0.06
        }} className="relative flex h-11 w-11 items-center justify-center rounded-xl bg-white/15">
            <InfoIcon className="h-5 w-5" strokeWidth={1.9} />
          </motion.span>

          <h2 className="relative mt-3 text-h3 font-bold tracking-tight sm:text-h3-lg">
            {kind === 'signup' ? `Welcome to DemandIQ, ${firstName}` : `Welcome back, ${firstName}`}
          </h2>
          <p className="relative mt-1.5 max-w-[44ch] text-meta leading-relaxed text-brand-100">
            {kind === 'signup' ? 'Your workspace is ready. Four things get you from raw records to a prep number you can defend.' : isAdmin ? 'Here’s where the console stands since you were last in.' : 'Picking up where you left off. Here’s what’s worth a look today.'}
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
          <ul className="flex flex-col gap-2">
            {tasks.map((task, index) => {
            const Icon = task.icon;
            return <motion.li key={task.id} initial={reduced ? false : {
              opacity: 0,
              y: 10
            }} animate={{
              opacity: 1,
              y: 0
            }} transition={{
              duration: 0.24,
              ease: EASE,
              delay: 0.1 + index * 0.05
            }}>
                  <button type="button" onClick={() => {
                onNavigate(task.route);
                onClose();
              }} className="group flex w-full items-start gap-3 rounded-xl border border-slate-200 px-3 py-2.5 text-left transition-colors duration-150 ease-out hover:border-brand-400 hover:bg-brand-50/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:border-slate-700 dark:hover:bg-slate-800">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 dark:bg-slate-800">
                      <Icon className="h-[18px] w-[18px] text-brand-600" strokeWidth={1.9} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-body-xs font-semibold text-slate-900 dark:text-slate-100">
                        {task.label}
                      </span>
                      <span className="block text-micro leading-relaxed text-slate-500 dark:text-slate-400">
                        {task.detail}
                      </span>
                    </span>
                    <ArrowRightIcon className="mt-2 h-4 w-4 shrink-0 text-slate-300 transition-colors duration-150 ease-out group-hover:text-brand-600" strokeWidth={2} />
                  </button>
                </motion.li>;
          })}
          </ul>

          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2.5 text-micro leading-relaxed text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            Forecasts are estimates with a stated confidence range, not guarantees. The
            surplus figures you’ll see are simulated exposure, not measured waste.
          </p>
        </div>

        <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 sm:px-5 dark:border-slate-700">
          <span className="text-micro text-slate-500 dark:text-slate-400">
            Signed in as {role}
          </span>
          <button type="button" onClick={onClose} className="rounded-lg bg-brand-700 px-4 py-2 text-body-xs font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
            {kind === 'signup' ? 'Start exploring' : 'Continue'}
          </button>
        </footer>
      </motion.div>
    </div>;
}