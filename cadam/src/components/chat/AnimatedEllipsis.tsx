import { cn } from '@/lib/utils';

interface AnimatedEllipsisProps {
  className?: string;
  dotClassName?: string;
  size?: 'sm' | 'md' | 'lg';
  color?:
    | 'primary'
    | 'secondary'
    | 'muted'
    | 'accent'
    | 'gray'
    | 'sq-neutral';
}

export function AnimatedEllipsis({
  className,
  dotClassName,
  size = 'md',
  color = 'primary',
}: AnimatedEllipsisProps) {
  // Size mapping
  const sizeMap = {
    sm: 'w-1 h-1',
    md: 'w-2 h-2',
    lg: 'w-3 h-3',
  };

  // Color mapping
  const colorMap = {
    primary: 'bg-primary',
    secondary: 'bg-secondary',
    muted: 'bg-muted-foreground',
    accent: 'bg-accent',
    gray: 'bg-gray-500',
    'sq-neutral': 'bg-sq-neutral-500',
  };

  const dotSize = sizeMap[size];
  const dotColor = colorMap[color];

  return (
    <>
      {color === 'sq-neutral' && (
        <style
          dangerouslySetInnerHTML={{
            __html: `
            @keyframes sqNeutralPulse {
              0%, 66.66%, 100% {
                background-color: rgb(107 114 128); /* sq-neutral-500 */
              }
              33.33% {
                background-color: rgb(245 245 245); /* sq-neutral-100 */
              }
            }
          `,
          }}
        />
      )}
      <div className={cn('flex items-center gap-1', className)}>
        {[...Array(3)].map((_, i) => (
          <div
            key={i}
            className={cn(
              'rounded-full',
              dotSize,
              color === 'sq-neutral'
                ? 'bg-sq-neutral-500'
                : `animate-pulse ${dotColor}`,
              color !== 'sq-neutral' && 'animate-pulse',
              dotClassName,
            )}
            style={{
              ...(color === 'sq-neutral' && {
                animation: `sqNeutralPulse 1.2s ease-in-out ${(2 - i) * -0.4}s infinite`,
              }),
              ...(color !== 'sq-neutral' && {
                animationDelay: `${i * 200}ms`,
              }),
            }}
          />
        ))}
      </div>
    </>
  );
}
