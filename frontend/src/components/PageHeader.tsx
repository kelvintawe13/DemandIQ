import { ArrowLeftIcon, type LucideIcon } from "lucide-react";
import { HeaderActions } from "./chrome/HeaderActions";
import { SyncButton } from "./chrome/SyncButton";
import { AgentLauncher } from "./agent/AgentLauncher";
import { useNavigation } from "../contexts/NavigationContext";
import { AnimatedTitle } from "./ui/AnimatedTitle";

interface PageHeaderProps {
  title: string;
  subtitle: string;
  icon?: LucideIcon;
}

export function PageHeader({
  title,
  subtitle,
  icon: Icon
}: PageHeaderProps) {
  const {
    back,
    canGoBack
  } = useNavigation();
  return <header className="shrink-0 border-b border-slate-200 bg-slate-50 px-3 pb-2.5 pt-2.5 sm:px-4 md:border-b-0 md:pb-0 md:pt-4 lg:px-5">
      {/* Compact bar: only where the sidebar is hidden */}
      <div className="mb-2 flex items-center justify-between gap-2 md:hidden">
        <span className="flex min-w-0 items-center gap-1">
          {canGoBack && <button type="button" onClick={back} aria-label="Go back" className="tap-expand -ml-1 rounded-lg p-1.5 text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-200/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
              <ArrowLeftIcon className="h-[18px] w-[18px]" strokeWidth={2} />
            </button>}
          <svg viewBox="0 0 24 24" className="h-5 w-5 shrink-0 text-brand-600" aria-hidden="true" fill="currentColor">
            <rect x="2" y="9" width="3.6" height="12" rx="1.2" />
            <rect x="7.6" y="4" width="3.6" height="17" rx="1.2" />
            <rect x="13.2" y="12" width="3.6" height="9" rx="1.2" opacity="0.65" />
          </svg>
          <span className="font-display truncate text-h5 tracking-tight text-brand-800">
            Demand<span className="font-display-italic text-brand-500">IQ</span>
          </span>
        </span>

        <span className="flex shrink-0 items-center gap-0.5">
          <AgentLauncher />
          <SyncButton compact />
          <HeaderActions />
        </span>
      </div>

      <div className="flex items-start justify-between gap-2 md:gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2 md:gap-3">
          {Icon && <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-700 text-white md:h-10 md:w-10">
              <Icon className="h-4 w-4 md:h-[18px] md:w-[18px]" strokeWidth={2} />
            </span>}
          <div className="min-w-0 flex-1">
            <AnimatedTitle
              as="h1"
              className="truncate text-h3 leading-tight tracking-tight text-slate-900 sm:text-h3-lg md:text-h2"
            >
              {title}
            </AnimatedTitle>
            <p className="animate-subtitle-in mt-0.5 hidden truncate text-micro text-slate-500 min-[420px]:block md:whitespace-normal md:text-meta">
              {subtitle}
            </p>
          </div>
        </div>

        <div className="hidden shrink-0 items-center gap-1 lg:flex">
          <AgentLauncher />
          <SyncButton />
          <HeaderActions />
        </div>
      </div>
    </header>;
}
