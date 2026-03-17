// Auto-redirect to trailing slash if missing
if (window.location.pathname !== '/' && 
    !window.location.pathname.endsWith('/') && 
    !window.location.pathname.includes('.')) {
  window.location.replace(window.location.pathname + '/' + window.location.search);
}
