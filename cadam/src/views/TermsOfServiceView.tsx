const SYNQIT_TERMS_URL = 'https://synqit.in/terms';

/** Synqit CAD shares the Synqit terms; this page just points there. */
export function TermsOfServiceView() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-sq-bg-dark p-4">
      <div className="w-full max-w-xl">
        <div className="rounded-lg bg-sq-bg-secondary-dark p-8 text-center shadow-md">
          <img
            src={`${import.meta.env.BASE_URL}/synqit-wordmark.svg`}
            alt="Synqit"
            className="mx-auto mb-6 h-8"
          />
          <h1 className="text-2xl font-semibold text-white">Terms of Service</h1>
          <p className="mt-4 text-sq-text-secondary">
            Synqit CAD is part of Synqit. The Synqit Terms of Service apply to
            your use of this app.
          </p>
          <a
            href={SYNQIT_TERMS_URL}
            target="_blank"
            rel="noreferrer"
            className="mt-6 inline-block text-sq-blue hover:underline"
          >
            Read the Synqit Terms of Service
          </a>
        </div>
      </div>
    </div>
  );
}
