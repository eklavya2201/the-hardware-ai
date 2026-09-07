// Minimal typings for Google Identity Services (https://accounts.google.com/gsi/client).
// Only the surface GoogleSignInButton uses.
export {};

declare global {
  interface GoogleCredentialResponse {
    credential: string;
    select_by?: string;
  }
  interface GoogleIdConfiguration {
    client_id: string;
    callback: (response: GoogleCredentialResponse) => void;
    auto_select?: boolean;
    cancel_on_tap_outside?: boolean;
    ux_mode?: 'popup' | 'redirect';
  }
  interface GsiButtonConfiguration {
    type?: 'standard' | 'icon';
    theme?: 'outline' | 'filled_blue' | 'filled_black';
    size?: 'large' | 'medium' | 'small';
    text?: 'signin_with' | 'signup_with' | 'continue_with' | 'signin';
    shape?: 'rectangular' | 'pill' | 'circle' | 'square';
    logo_alignment?: 'left' | 'center';
    width?: number;
  }
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: GoogleIdConfiguration) => void;
          renderButton: (parent: HTMLElement, options: GsiButtonConfiguration) => void;
          prompt: () => void;
          cancel: () => void;
        };
      };
    };
  }
}
