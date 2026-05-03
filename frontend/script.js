const API_BASE = 'http://localhost:8000/api/v1';
const TOKEN_KEY = 'raglabel_token';
let currentUserId = null;
let currentExample = null;
let sessionActive = false;
const originalFetch = window.fetch;

window.fetch = async function(url, options = {}) {
    if (url.includes('/auth/') || url.endsWith('.css') || url.endsWith('.js')) {
      return originalFetch(url, options);
    }

    const token = localStorage.getItem(TOKEN_KEY);
    
    if (token) {
        options.headers = { ...options.headers, 'Authorization': `Bearer ${token}` };
    }
    return originalFetch(url, options);
};

function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem('currentUserId');
    window.location.href = 'login.html';
}

document.addEventListener('DOMContentLoaded', () => { // DOM-tree in-code ready!
    const storedUserId = localStorage.getItem('currentUserId');

    if (!window.location.pathname.includes('login.html') && !storedUserId) {
        window.location.href = 'login.html';
        return;
    }

    currentUserId = storedUserId;
    if (window.location.pathname.includes('login.html')) {
        const form = document.getElementById('authForm');
        const toggle = document.getElementById('toggleMode');
        const formTitle = document.getElementById('formTitle');
        const submitBtn = document.getElementById('submitBtn');
        const errorMsg = document.getElementById('errorMsg');
        let isLoginMode = true;
        toggle.addEventListener('click', () => {
            isLoginMode = !isLoginMode;
            formTitle.textContent = isLoginMode ? 'Вход' : 'Регистрация';
            submitBtn.textContent = isLoginMode ? 'Войти' : 'Зарегистрироваться';
            toggle.textContent = isLoginMode ? 'Нет аккаунта? Регистрация.[*]' : 'Уже есть аккаунт? Войти[].';
            errorMsg.textContent = '';
        });
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            errorMsg.textContent = '';

            const username = document.getElementById('username').value.trim();
            const password = document.getElementById('password').value;
            const endpoint = isLoginMode ? '/auth/login' : '/auth/register';
            try {
                const response = await fetch(`${API_BASE}${endpoint}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || 'ServerError!');
                }
                if (isLoginMode) {
                    localStorage.setItem(TOKEN_KEY, data.access_token);
                    localStorage.setItem('currentUserId', data.user_id);
                }

                window.location.href = 'index.html';
            } catch (err) {
                errorMsg.textContent = err.message;
            }
        });
        
        return;
    }

    const $ = (id) => document.getElementById(id);
    const show = (el) => el.classList.remove('hidden');
    const hide = (el) => el.classList.add('hidden');
    const setActive = (el) => el.classList.add('active');
    const setInactive = (el) => el.classList.remove('active');

    function addMessage(content, isBot = true, meta = {}) {
      const container = $('messages');
      const msg = document.createElement('div');
      msg.className = `message ${isBot ? 'bot' : 'user'}`;
      let html = `<div class="label">${isBot ? '🤖 Ассистент' : '👤 Вы'}</div>`;
      html += `<div class="content">${content}</div>`;
      if (meta.confidence !== undefined) {
        html += `<div class="confidence">Уверенность = ${(meta.confidence * 100).toFixed(0)}%</div>`;
      }
      msg.innerHTML = html;
      container.appendChild(msg);
      container.scrollTop = container.scrollHeight;

      return msg;
    }

    async function apiRequest(endpoint, method = 'GET',  body = null) {
      try {
        const options = { method, headers: { 'Content-Type': 'application/json' } };
        if (body) options.body = JSON.stringify(body);
        const response = await fetch(`${API_BASE}${endpoint}`, options);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.message || 'Error from API-request!');
        
        return data;
      } catch (error) {
        console.error('API Error:', error);
        throw error;
      }
    }

    function updateProgress(current, target) {
      const percent = Math.min(100, (current / target) * 100);
      $('progressFill').style.width = `${percent}%`;
      $('progressText').textContent = `${current}/${target} обработано`;
    }

    async function startSession() {
      if (!currentUserId) { window.location.href = 'login.html'; return; }
      const targetCount = parseInt($('targetCount').value);
      const errorEl = $('setupError');
      hide(errorEl);
      try {
        const result = await apiRequest('/session/start', 'POST', { target_count: targetCount });
        sessionActive = true;
        setInactive($('setupPanel'));
        setActive($('chatContainer'));
        updateProgress(0, targetCount);
        addMessage(`Сессия запущена! Задача: разметить <b>${targetCount}</b> примеров.(*)`, true);
        addMessage('Загрузка первого примера...', true);
        await getNextExample();
      } catch (error) {
        show(errorEl);
        errorEl.textContent = error.message;
      }
    }

    async function getNextExample() {
      if (!sessionActive || !currentUserId) return;
      const messagesEl = $('messages');
      const loading = document.createElement('div');
      loading.className = 'loading';
      loading.textContent = '(*) Генерация примера...';
      messagesEl.appendChild(loading);
      try {
        const result = await apiRequest('/next_example', 'POST');
        loading.remove();
        currentExample = result;
        addMessage(`<b>Пример:</b> ${result.question}<br><br><b>Суждение:</b> ${result.answer}`, true, { confidence: result.confidence });
        $('feedbackButtons').style.display = 'flex';
        setInactive($('textFeedback'));
        $('feedbackText').value = '';
      } catch (error) {
        loading.remove();
        addMessage(`Ошибка ${error.message}!`, true);
      }
    }

    async function sendFeedback(type) {
      if (!currentExample || !currentUserId){
        return;
      }
      try {
        const result = await apiRequest('/feedback', 'POST', { feedback_type: type });
        const labels = { like: '👍 Верно!', dislike: '👎 Неверно!', text: '✏️ Предоставлено разъяснение!' };
        addMessage(labels[type] || type, false);
        const [current, target] = result.progress.split('/').map(Number);
        updateProgress(current, target);
        if (current >= target) {
          $('feedbackButtons').style.display = 'none';
          setActive($('exportPanel'));
          addMessage('🎉 Все примеры размечены! Нажмите кнопку ниже для экспорта.', true);
          sessionActive = false;
        } else {
          addMessage('Загружаю следующий пример...', true);
          await getNextExample();
        }
      } catch (error) {
        addMessage(`Ошибка отправки фидбека ${error.message}!`, true);
      }
    }

    function toggleTextEdit() {
      const panel = $('textFeedback');
      panel.classList.toggle('active');
      if (panel.classList.contains('active')) $('feedbackText').focus();
    }

    async function sendTextFeedback() {
      const text = $('feedbackText').value.trim();
      
      if (!text) {
        alert('Введите расширенный отзыв');
        return;
      }

      try {
        const result = await apiRequest('/feedback', 'POST', { feedback_type: 'text', text_feedback: text });
        addMessage(`<b>Текст отзыва:</b><br>${text}.`, false);
        if (result.warning) addMessage(`⚠️ ${result.warning}`, true);
        const [current, target] = result.progress.split('/').map(Number);
        updateProgress(current, target);
        setInactive($('textFeedback'));
        $('feedbackText').value = '';
        if (current >= target) {
          $('feedbackButtons').style.display = 'none';
          setActive($('exportPanel'));
          addMessage('* Все примеры обработаны! Нажмите кнопку для экспорта в датасет! *', true);
          sessionActive = false;
        }
        else {
          addMessage('Загрузка следующего примера...', true);
          await getNextExample();
        }
      } catch (error) {
        addMessage(`Ошибка ${error.message}!`, true);
      }
    }

    async function uploadDocument() {
      const fileInput = $('documentFile');
      const statusEl = $('uploadStatus');
      const file = fileInput.files[0];
      if (!file) {
        statusEl.textContent = 'Выберите файл для загрузки данных'; statusEl.style.color = '#f39c12';
        return;
      }
      statusEl.textContent = '[Загрузка и обработка файлов...]';
      statusEl.style.color = '#666';
      const formData = new FormData();
      formData.append('file', file);
      try {
        const response = await fetch(`${API_BASE}/upload`, { method: 'POST', body: formData });
        const result = await response.json();
        if (response.ok){
          statusEl.innerHTML = `OK!${result.message}`; statusEl.style.color = '#27ae60';
        }
        else {
          statusEl.textContent = `Ошибка ${result.detail || 'Неизвестная ошибка!'}`; statusEl.style.color = '#e74c3c';
        }
      } catch (error) {
        statusEl.textContent = `Network error: ${error.message}!`;
        statusEl.style.color = '#e74c3c';
        console.error('Upload error', error);
      }
    }
    
    async function exportDataset() {
      if (!currentUserId) {
        return;
      }
      try {
        const result = await apiRequest('/export', 'POST', { format: 'jsonl' });
        const blob = new Blob([result.data], { type: 'application/jsonl' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `golden_set_${currentUserId}_${Date.now()}.jsonl`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        addMessage(`{Датасет сформирован: ${result.count} записей.}`, true);        
      } catch (error) {
        addMessage(`Ошибка экспорта ${error.message}!`, true);
      }
    }

    fetch('http://localhost:8000/health')
      .then(r => r.json())
      .then(data => { if (data.status === 'ok') console.log('[Thats okey!]'); })
      .catch(() => {
        const errorEl = $('setupError');
        show(errorEl);
        errorEl.textContent = 'Error while connecting to / http://localhost:8000/';
      });

    window.startSession = startSession;
    window.sendFeedback = sendFeedback;
    window.sendTextFeedback = sendTextFeedback;
    window.uploadDocument = uploadDocument;
    window.exportDataset = exportDataset;
    window.toggleTextEdit = toggleTextEdit;
});