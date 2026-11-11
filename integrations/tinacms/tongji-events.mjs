// Load the site's consent-aware Baidu Tongji integration before calling these hooks.
const host = 'www.tinacms.com.cn';

function recordGoal(path) {
  if (typeof window === 'undefined' || window.location.hostname !== host) return;
  window._hmt?.push(['_trackPageview', path]);
}

// Call this after the clipboard promise resolves, or after a successful fallback.
export function trackInstallCopySuccess(command) {
  if (command === 'npx create-tina-app@latest') {
    recordGoal('/__analytics/conversion/install-copy-success');
  }
}

export function trackEmailClick(href) {
  if (href.split('?')[0].toLowerCase() === 'mailto:infochina@ssw.com.au') {
    recordGoal('/__analytics/conversion/email-click');
  }
}
