import { useEffect, useRef } from 'react';
import { useSharedSpinnerVerb } from '@/hooks/useSharedSpinnerVerb';

type Props = {
  showLoadingText?: boolean;
};

/** Pulsing Synqit mark plus the shared "verb…" line. No Lottie dependency. */
const Loader = ({ showLoadingText = false }: Props) => {
  const dot2 = useRef<HTMLSpanElement>(null);
  const dot3 = useRef<HTMLSpanElement>(null);
  const sharedVerb = useSharedSpinnerVerb(showLoadingText);

  useEffect(() => {
    // ANIMATE LAST TWO DOTS WITH DELAYS AND INTERVALS
    const interval = setInterval(() => {
      dot2.current?.classList.toggle('opacity-0');
      setTimeout(() => {
        dot3.current?.classList.toggle('opacity-0');
      }, 300);
      setTimeout(() => {
        dot2.current?.classList.toggle('opacity-0');
        dot3.current?.classList.toggle('opacity-0');
      }, 600);
    }, 900);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex flex-col items-center justify-center">
      <div className="relative flex h-32 w-32 items-center justify-center">
        <div className="absolute inset-0 animate-ping rounded-full bg-sq-blue/10" />
        <img
          src={`${import.meta.env.BASE_URL}synqit-logo.png`}
          alt="Synqit"
          className="h-16 w-16 animate-pulse object-contain"
        />
      </div>
      {showLoadingText && (
        <div className="mt-2 text-sm text-sq-text-secondary">
          {sharedVerb}
          <span>.</span>
          <span ref={dot2}>.</span>
          <span ref={dot3}>.</span>
        </div>
      )}
    </div>
  );
};

export default Loader;
