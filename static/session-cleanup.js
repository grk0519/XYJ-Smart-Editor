(() => {
  let inAppNavigation = false;
  const storageKey = 'xml-editor-tab-id';
  let tabId = sessionStorage.getItem(storageKey);
  if (!tabId) {
    tabId = crypto.randomUUID();
    sessionStorage.setItem(storageKey, tabId);
  }

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  if (!csrfToken) {
    return;
  }

  const postBeacon = (endpoint) => {
    const body = new URLSearchParams({ csrf_token: csrfToken, tab_id: tabId });
    if (!navigator.sendBeacon(endpoint, body)) {
      fetch(endpoint, {
        method: 'POST',
        body,
        credentials: 'same-origin',
        keepalive: true,
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      }).catch(() => {});
    }
  };

  postBeacon('/session-activity');
  const activityTimer = window.setInterval(() => postBeacon('/session-activity'), 30000);

  document.addEventListener('submit', () => {
    inAppNavigation = true;
  }, true);

  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }

    const destination = new URL(link.href, window.location.href);
    if (destination.origin === window.location.origin && !link.hasAttribute('download') && link.target !== '_blank') {
      inAppNavigation = true;
    }
  }, true);

  window.addEventListener('pageshow', () => {
    inAppNavigation = false;
  });

  window.addEventListener('pagehide', event => {
    if (inAppNavigation || event.persisted) {
      return;
    }
    window.clearInterval(activityTimer);
    postBeacon('/session-close');
  });
})();
