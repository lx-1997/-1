/* Add the separately deployed macro page to the existing public terminal. */
(() => {
  const add = () => {
    const bar = document.querySelector('.bbt-side-menu');
    if (!bar) return false;
    if (!bar.querySelector('[data-macro-entry]')) {
      const link = document.createElement('a');
      link.href = '/macro/';
      link.className = 'bbt-side-item';
      link.title = '宏观雷达';
      const icon = document.createElement('span');
      icon.className = 'bbt-side-icon';
      icon.textContent = '◉';
      const label = document.createElement('span');
      label.textContent = '宏观雷达';
      link.append(icon, label);
      link.dataset.macroEntry = 'true';
      link.style.cssText = 'text-decoration:none;color:inherit';
      bar.append(link);
    }
    return true;
  };
  if (add()) return;
  const observer = new MutationObserver(() => { if (add()) observer.disconnect(); });
  observer.observe(document.body, { childList: true, subtree: true });
  window.setTimeout(() => observer.disconnect(), 30000);
})();
