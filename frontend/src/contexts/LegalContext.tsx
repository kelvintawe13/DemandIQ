import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState } from
'react';
import { LegalModal } from '../components/legal/LegalModal';
import { ConsentBanner } from '../components/legal/ConsentBanner';
import type { LegalDocument } from '../data/legal';

type DocId = LegalDocument['id'];

export interface CookieChoice {
  preferences: boolean;
  analytics: boolean;
}

interface LegalContextValue {
  /** Opens the legal reader on a specific document. */
  openLegal: (id: DocId) => void;
  /** Re-opens the consent prompt so a decision can be changed. */
  reviewConsent: () => void;
  consent?: CookieChoice;
}

const LegalContext = createContext<LegalContextValue>({
  openLegal: () => undefined,
  reviewConsent: () => undefined
});

const STORAGE_KEY = 'demandiq.consent.v1';

function readStored(): CookieChoice | undefined {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) as CookieChoice : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Owns both legal surfaces: the reader modal (terms / privacy / cookies) and
 * the consent prompt, which is shown exactly once per device until a choice
 * is recorded.
 */
export function LegalProvider({ children }: {children: React.ReactNode;}) {
  const [docId, setDocId] = useState<DocId>();
  const [consent, setConsent] = useState<CookieChoice | undefined>(undefined);
  const [promptOpen, setPromptOpen] = useState(false);

  useEffect(() => {
    const stored = readStored();
    if (stored) setConsent(stored);else
    setPromptOpen(true);
  }, []);

  const decide = useCallback((choice: CookieChoice) => {
    setConsent(choice);
    setPromptOpen(false);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(choice));
    } catch {

      /* storage unavailable — the choice still applies for this session */}
  }, []);

  const value = useMemo(
    () => ({
      openLegal: (id: DocId) => setDocId(id),
      reviewConsent: () => setPromptOpen(true),
      consent
    }),
    [consent]
  );

  return (
    <LegalContext.Provider value={value}>
      {children}
      <LegalModal
        openId={docId}
        onClose={() => setDocId(undefined)}
        onSelect={setDocId} />
      
      <ConsentBanner
        open={promptOpen && !docId}
        onDecide={decide}
        onReadPolicy={() => setDocId('cookies')}
        onDismiss={() => setPromptOpen(false)}
        current={consent} />
      
    </LegalContext.Provider>);

}

export function useLegal() {
  return useContext(LegalContext);
}