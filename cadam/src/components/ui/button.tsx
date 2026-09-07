import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        black:
          'bg-sq-neutral-950 text-sq-neutral-10 hover:bg-sq-neutral-900 duration-150 ease-out',
        dark: 'bg-sq-neutral-700 text-sq-text-primary hover:bg-sq-neutral-800 duration-150 ease-out',
        light:
          'bg-sq-neutral-10 text-sq-neutral-950 hover:bg-sq-neutral-100 duration-150 ease-out',
        default:
          'bg-sq-background-2 text-sq-text-primary shadow hover:bg-sq-neutral-800 hover:text-sq-neutral-100',
        destructive:
          'bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90',
        outline:
          'border border-sq-neutral-700 bg-sq-bg-secondary-dark text-sq-text-primary shadow-sm hover:bg-sq-neutral-800 hover:text-sq-neutral-100',
        secondary:
          'bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        link: 'text-primary underline-offset-4 hover:underline',
        sq_dark:
          'text-sq-text-tertiary border-2 border-transparent transition-all duration-200 ease-in-out [@media(hover:hover)]:hover:bg-sq-neutral-950 [@media(hover:hover)]:hover:text-sq-neutral-10',
        sq_dark_collapsed:
          'text-sq-text-tertiary border-2 border-transparent transition-all duration-200 ease-in-out [@media(hover:hover)]:hover:bg-sq-neutral-950 [@media(hover:hover)]:hover:text-sq-neutral-10',
        sq_dark_collapsed_avatar:
          'text-sq-text-tertiary border-2 border-transparent transition-all duration-200 ease-in-out [@media(hover:hover)]:hover:bg-sq-neutral-950',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 rounded-md px-3 text-xs',
        lg: 'h-10 rounded-md px-8',
        xl: 'h-12 rounded-lg px-10 text-base font-normal',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
