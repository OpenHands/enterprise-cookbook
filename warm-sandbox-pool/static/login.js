document.getElementById('login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const error = document.getElementById('login-error');
    error.style.display = 'none';

    const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: document.getElementById('code-input').value })
    });

    if (response.ok) {
        window.location.reload();
        return;
    }
    const data = await response.json();
    error.textContent = data.error;
    error.style.display = 'block';
});
