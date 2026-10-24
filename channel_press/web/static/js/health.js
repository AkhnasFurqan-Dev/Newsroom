document.querySelectorAll('[data-health-check]').forEach(button => {
  button.addEventListener('click', async () => {
    const output = document.getElementById('system-health');
    output.textContent = 'Checking connections…';
    button.disabled = true;
    try {
      const response = await fetch('/system/check', {
        method: 'POST', body: new FormData(button.closest('form'))
      });
      if (!response.ok) throw new Error('Connection check unavailable; retry shortly.');
      output.textContent = JSON.stringify(await response.json(), null, 2);
    } catch (error) {
      output.textContent = error.message;
    } finally { button.disabled = false; }
  });
});
