const SYNQIT_PRIVACY_URL = 'https://synqit.in/privacy';

/** Synqit CAD shares the Synqit privacy policy; this page just points there. */
export function PrivacyPolicyView() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-sq-bg-dark p-4">
      <div className="w-full max-w-xl">
        <div className="rounded-lg bg-sq-bg-secondary-dark p-8 text-center shadow-md">
          <img
            src={`${import.meta.env.BASE_URL}synqit-wordmark.svg`}
            alt="Synqit"
            className="mx-auto mb-6 h-8"
          />
          <h1 className="text-2xl font-semibold text-white">Privacy Policy</h1>
          <p className="mt-4 text-sq-text-secondary">
            Synqit CAD is part of Synqit. The Synqit Privacy Policy describes
            what we collect and how it is used.
          </p>
          <a
            href={SYNQIT_PRIVACY_URL}
            target="_blank"
            rel="noreferrer"
            className="mt-6 inline-block text-sq-blue hover:underline"
          >
            Read the Synqit Privacy Policy
          </a>
        </div>
      </div>
    </div>
  );
}
