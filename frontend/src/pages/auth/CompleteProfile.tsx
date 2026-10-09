import { useState } from 'react';
import { AuthLayout } from '../../components/auth/AuthLayout';
import { DashboardArt } from '../../components/illustrations/AuthArt';
import { CompleteProfileForm } from '../../components/auth/GoogleButton';
import { useSession } from '../../contexts/SessionContext';
import type { DemoAccount } from '../../data/demoAccount';

interface CompleteProfileProps {
  /** The freshly-provisioned Google account awaiting completion. */
  account: DemoAccount | null;
  onDone: (account: DemoAccount) => void;
  onCancel: () => void;
}

/**
 * First-run profile completion for fresh Google sign-ups. The backend holds
 * the gate (needs_profile / profile_completed); this screen collects
 * name + job title, calls the completion API itself, and hands the finished
 * account back to App routing.
 */
export function CompleteProfile({ account, onDone, onCancel }: CompleteProfileProps) {
  const [busy] = useState(false);
  const { clearProfileGate, refresh } = useSession();

  return (
    <AuthLayout
      art={<DashboardArt className="h-auto w-[300px]" />}
      artTitle="Set up your workspace"
      artText="One last step. Tell us who you are and DemandIQ will tailor your forecasts, dashboards and default views to your role.">
      <h1 className="text-display-xs font-bold leading-tight tracking-tight text-slate-900">
        Complete your profile
      </h1>
      <p className="mt-1 text-body-xs text-slate-500">
        {account?.email
          ? `Signed in as ${account.email}. Finish setting up your account to continue.`
          : 'Finish setting up your account to continue.'}
      </p>

      <div className="mt-5">
        <CompleteProfileForm
          embedded
          initialName={account?.name ?? ''}
          onDone={({ name, job_title }) => {
            if (!account) {
              onCancel();
              return;
            }
            clearProfileGate();
            void refresh().catch(() => undefined);
            onDone({
              ...account,
              name,
              initials: name
                .trim()
                .split(/\s+/)
                .map((p) => p[0])
                .join('')
                .slice(0, 2)
                .toUpperCase() || 'U',
              blurb: job_title ?? account.blurb,
            });
          }}
        />
      </div>

      <button
        type="button"
        onClick={onCancel}
        disabled={busy}
        className="mt-3 block w-full rounded text-center text-meta font-medium text-slate-500 transition-colors duration-150 ease-out hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        Use a different account
      </button>
    </AuthLayout>
  );
}
