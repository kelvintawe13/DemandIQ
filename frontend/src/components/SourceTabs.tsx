import { FileSpreadsheetIcon, FileImageIcon, ListIcon, CreditCardIcon, type LucideIcon } from "lucide-react";
interface Tab {
  id: string;
  label: string;
  shortLabel: string;
  icon: LucideIcon;
}
const tabs: Tab[] = [{
  id: 'excel',
  label: 'Excel/CSV',
  shortLabel: 'Excel/CSV',
  icon: FileSpreadsheetIcon
}, {
  id: 'image',
  label: 'Image (Handwritten)',
  shortLabel: 'Image',
  icon: FileImageIcon
}, {
  id: 'manual',
  label: 'Manual Form',
  shortLabel: 'Manual',
  icon: ListIcon
}, {
  id: 'pos',
  label: 'POS/MoMo',
  shortLabel: 'POS/MoMo',
  icon: CreditCardIcon
}];
interface SourceTabsProps {
  activeTabId: string;
  onSelect: (id: string) => void;
}
export function SourceTabs({
  activeTabId,
  onSelect
}: SourceTabsProps) {
  return <div role="tablist" aria-label="Import source" className="grid grid-cols-2 gap-1.5 rounded-xl border border-slate-200 bg-white p-1.5 sm:gap-2 sm:p-2 md:grid-cols-4">
      {tabs.map((tab) => {
      const Icon = tab.icon;
      const isActive = tab.id === activeTabId;
      return <button key={tab.id} role="tab" type="button" aria-selected={isActive} onClick={() => onSelect(tab.id)} className={['flex min-w-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-2 py-2.5 text-control transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:gap-2.5 sm:px-3 sm:py-3 sm:text-body', isActive ? 'bg-brand-600 font-semibold text-white' : 'font-medium text-slate-700 hover:bg-slate-100'].join(' ')}>
            <Icon className="h-[18px] w-[18px] shrink-0" strokeWidth={1.75} />
            <span className="truncate md:hidden">{tab.shortLabel}</span>
            <span className="hidden truncate md:inline">{tab.label}</span>
          </button>;
    })}
    </div>;
}