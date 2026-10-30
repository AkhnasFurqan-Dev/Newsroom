document.querySelectorAll('[data-health-check]').forEach(button => {
  button.addEventListener('click', async () => {
    const output = document.getElementById('system-health');
    output.innerHTML = '<div class="small muted">Checking connections…</div>';
    button.disabled = true;
    try {
      const response = await fetch('/system/check', {
        method: 'POST',
        body: new FormData(button.closest('form')),
        headers: { 'X-Requested-With': 'fetch' },
      });
      if (!response.ok) throw new Error('Connection check unavailable; retry shortly.');
      output.innerHTML = await response.text();
    } catch (error) {
      output.textContent = error.message;
    } finally { button.disabled = false; }
  });
});
