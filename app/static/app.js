// Minimal JS — mostly server-rendered
document.addEventListener('DOMContentLoaded', () => {
    // Auto-dismiss triggered notification
    const params = new URLSearchParams(window.location.search);
    if (params.get('triggered') === '1') {
        const banner = document.createElement('div');
        banner.className = 'fixed top-4 right-4 z-50 bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 px-4 py-3 rounded-xl text-xs font-semibold shadow-lg';
        banner.textContent = 'Scrape pipeline started in background. Refresh in a few minutes.';
        document.body.appendChild(banner);
        setTimeout(() => banner.remove(), 6000);
        // Clean URL
        window.history.replaceState({}, '', window.location.pathname);
    }
});
